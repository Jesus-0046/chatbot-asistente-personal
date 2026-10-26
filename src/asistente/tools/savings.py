"""Herramientas de objetivos de ahorro (Fase 3.2).

Tools expuestas al LLM (aíslan por `user_id` autenticado; ninguna toca la BD):

- create_savings_goal
- list_savings_goals
- get_savings_goal_status
- record_savings_contribution
- cancel_savings_goal

Reglas clave:
- Un objetivo es una META; NO es una cuenta y no cambia saldos.
- El ahorro real son los saldos de cuentas type=savings.
- Una contribución es metadato: nunca crea gasto ni transferencia.
- Si se vincula a una transferencia, la transferencia ya es el movimiento
  financiero real y su importe debe coincidir.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.tools.agenda import _resolve_datetime, _tz, _user_timezone
from asistente.tools.agenda import _ResolvedNode as _R
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money, _resolve_occurred
from asistente.tools.registry import ToolDefinition, ToolResult


# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #
class GoalCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Nombre del objetivo, p. ej. 'coche'")
    target_amount: str = Field(min_length=1, max_length=40, description="Meta del objetivo, p. ej. '3000'")
    deadline: str | None = Field(
        default=None, max_length=200, description="Fecha tope, p. ej. '2027-08-31' o 'para agosto'"
    )


class GoalIdArgs(BaseModel):
    goal_id: int = Field(gt=0)


class GoalListArgs(BaseModel):
    status: str | None = Field(default=None, description="active (por defecto), completed, cancelled")


class ContributionCreateArgs(BaseModel):
    goal_id: int = Field(gt=0)
    amount: str = Field(min_length=1, max_length=40, description='"300", "200"')
    contributed_at: str | None = Field(default=None, max_length=200, description="Cuándo se aportó (por defecto ahora)")
    note: str | None = Field(default=None, max_length=1000)
    transfer_id: int | None = Field(default=None, gt=0, description="Transferencia real vinculada (opcional)")


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _goal_status_data(status: dict) -> dict[str, Any]:
    goal = status["goal"]
    return {
        "id": goal.id,
        "name": goal.name,
        "target_amount": fmt_amount(goal.target_amount),
        "currency": goal.currency,
        "contributed_amount": fmt_amount(status["contributed"]),
        "remaining_amount": fmt_amount(status["remaining"]),
        "progress_percent": fmt_amount(status["progress"]),
        "deadline": goal.deadline.isoformat() if goal.deadline else None,
        # Estado DETERMINISTA calculado (active|completed|cancelled), nunca el
        # `status` persistido: una lectura no debe escribir la BD (Fase 3.2/3.3.1).
        "status": status["status"],
    }


def _parse_deadline(ctx: ToolContext, text: str, user_tz: str) -> ToolResult | Any:
    expr = (text or "").strip()
    try:
        # ISO: fecha o datetime. Se toma la parte de fecha en la zona local.
        iso = expr.replace("Z", "+00:00")
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            dt = dt.astimezone(_tz(user_tz))
        return dt.date()
    except ValueError:
        pass
    resolved = _resolve_datetime(ctx, expr, user_tz)
    if not isinstance(resolved, _R):
        if isinstance(resolved, ToolResult):
            return resolved
        return ToolResult(ok=False, error="No he entendido la fecha límite.")
    from asistente.shared.timeutil import to_local

    return to_local(resolved.value, user_tz).date()


# --------------------------------------------------------------------------- #
# Handlers                                                                     #
# --------------------------------------------------------------------------- #
async def _create_savings_goal(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = GoalCreateArgs(**kwargs)
    target = _parse_money(args.target_amount)
    if isinstance(target, ToolResult):
        return target
    user_tz = await _user_timezone(ctx)
    deadline = None
    if args.deadline:
        deadline = _parse_deadline(ctx, args.deadline, user_tz)
        if isinstance(deadline, ToolResult):
            return deadline
    try:
        goal = await ctx.services.savings_goals.create_goal(
            user_id=ctx.user_id,
            name=args.name,
            target_amount=target,
            deadline=deadline,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": goal.id,
            "name": goal.name,
            "target_amount": fmt_amount(goal.target_amount),
            "currency": goal.currency,
            "deadline": goal.deadline.isoformat() if goal.deadline else None,
            "status": goal.status,
        },
    )


async def _list_savings_goals(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = GoalListArgs(**kwargs)
    try:
        goals = await ctx.services.savings_goals.list_goals(ctx.user_id, status=args.status)
    except Exception as exc:
        return _ko(exc)
    results = []
    for g in goals:
        status = await ctx.services.savings_goals.get_goal_status(ctx.user_id, g.id)
        results.append(_goal_status_data(status))
    return ToolResult(ok=True, data={"goals": results})


async def _get_savings_goal_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = GoalIdArgs(**kwargs)
    try:
        status = await ctx.services.savings_goals.get_goal_status(ctx.user_id, args.goal_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data=_goal_status_data(status))


async def _record_savings_contribution(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ContributionCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.contributed_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    try:
        contrib = await ctx.services.savings_goals.record_contribution(
            user_id=ctx.user_id,
            goal_id=args.goal_id,
            amount=amount,
            contributed_at=occurred,
            note=args.note,
            transfer_id=args.transfer_id,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": contrib.id,
            "goal_id": contrib.goal_id,
            "amount": fmt_amount(contrib.amount),
            "currency": contrib.currency,
            "contributed_at": contrib.contributed_at.astimezone(_tz(user_tz)).isoformat(),
            "transfer_id": contrib.transfer_id,
        },
    )


async def _cancel_savings_goal(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = GoalIdArgs(**kwargs)
    try:
        goal = await ctx.services.savings_goals.cancel_goal(ctx.user_id, args.goal_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={"id": goal.id, "name": goal.name, "status": goal.status},
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
SAVINGS_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_savings_goal",
        description=(
            "Crea un objetivo de ahorro (una META, no una cuenta). "
            "'Quiero ahorrar 3000 € para el coche' → name='coche', target_amount='3000'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre del objetivo, p. ej. 'coche'"},
                "target_amount": {"type": "string", "description": "Meta del objetivo, p. ej. '3000'"},
                "deadline": {
                    "type": "string",
                    "default": None,
                    "description": "Fecha tope, p. ej. '2027-08-31' o 'para agosto'",
                },
            },
            "required": ["name", "target_amount"],
        },
        handler=_create_savings_goal,
        args_model=GoalCreateArgs,
    ),
    ToolDefinition(
        name="list_savings_goals",
        description=(
            "Lista los objetivos de ahorro con su progreso. "
            "Por defecto devuelve solo los activos; usa status para filtrar."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["active", "completed", "cancelled"],
                    "default": None,
                    "description": "Filtro de estado (por defecto activos)",
                }
            },
            "required": [],
        },
        handler=_list_savings_goals,
        args_model=GoalListArgs,
    ),
    ToolDefinition(
        name="get_savings_goal_status",
        description=(
            "Estado de un objetivo de ahorro: contribuido, falta, progreso, deadline y estado. "
            "'¿Cuánto me falta para el coche?' → esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {"goal_id": {"type": "integer"}},
            "required": ["goal_id"],
        },
        handler=_get_savings_goal_status,
        args_model=GoalIdArgs,
    ),
    ToolDefinition(
        name="record_savings_contribution",
        description=(
            "Registra una contribución a un objetivo de ahorro. "
            "Es una asignación al objetivo, NO un gasto ni una transferencia. "
            "Si el usuario describe una transferencia real a su cuenta de ahorro, "
            "usa antes create_transfer y pásale su transfer_id aquí."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer"},
                "amount": {"type": "string", "description": "Importe aportado, p. ej. '200'"},
                "contributed_at": {"type": "string", "default": None, "description": "Cuándo (por defecto ahora)"},
                "note": {"type": "string", "default": None},
                "transfer_id": {
                    "type": "integer",
                    "default": None,
                    "description": "Transferencia real vinculada (opcional)",
                },
            },
            "required": ["goal_id", "amount"],
        },
        handler=_record_savings_contribution,
        args_model=ContributionCreateArgs,
    ),
    ToolDefinition(
        name="cancel_savings_goal",
        description=(
            "Cancela un objetivo de ahorro (estado=cancelled). "
            "No borra objetivos ni contribuciones; requiere confirmación."
        ),
        input_schema={
            "type": "object",
            "properties": {"goal_id": {"type": "integer"}},
            "required": ["goal_id"],
        },
        handler=_cancel_savings_goal,
        requires_confirmation=True,
        args_model=GoalIdArgs,
    ),
]
