"""Herramientas de presupuestos (Fase 3.2).

Tools expuestas al LLM (aíslan por `user_id` autenticado; ninguna toca la BD):

- create_budget
- list_budgets
- get_budget_status
- update_budget
- delete_budget

Reglas clave:
- Un presupuesto mensual puede ser general (category=None) o por categoría.
- El gasto se calcula con transacciones POSTED (expense); una transferencia
  o ingreso nunca cuenta como gasto.
- Un gasto con tarjeta de crédito cuenta una vez; pagar la tarjeta es una
  transferencia que NO vuelve a contabilizarse.
- `year`/`month` se toman de la zona horaria del usuario; si no se indican,
  se usa el mes actual.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.tools.agenda import _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money
from asistente.tools.registry import ToolDefinition, ToolResult


# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #
class BudgetCreateArgs(BaseModel):
    amount: str = Field(min_length=1, max_length=40, description='"1200", "350", "300"')
    year: int | None = Field(default=None, ge=2000, le=2100, description="Año (por defecto el actual)")
    month: int | None = Field(default=None, ge=1, le=12, description="Mes 1-12 (por defecto el actual)")
    category: str | None = Field(
        default=None, max_length=64, description="Alimentación, Transporte, Ocio... (None = general)"
    )
    name: str | None = Field(default=None, max_length=200, description="Nombre opcional del presupuesto")


class BudgetIdArgs(BaseModel):
    budget_id: int = Field(gt=0)


class BudgetListArgs(BaseModel):
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)


class BudgetUpdateArgs(BaseModel):
    budget_id: int = Field(gt=0)
    amount: str | None = Field(default=None, min_length=1, max_length=40)
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)
    category: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=200)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _current_month(user_tz: str) -> tuple[int, int]:
    from asistente.shared.timeutil import now_in_timezone

    now = now_in_timezone(user_tz)
    return now.year, now.month


def _to_status_dict(status: dict) -> dict[str, Any]:
    budget = status["budget"]
    return {
        "id": budget.id,
        "name": budget.name,
        "category": budget.category,
        "year": budget.year,
        "month": budget.month,
        "budget_amount": fmt_amount(budget.amount),
        "spent_amount": fmt_amount(status["spent"]),
        "remaining_amount": fmt_amount(status["remaining"]),
        "percent_used": fmt_amount(Decimal(str(status["percent_used"]))),
        "status": status["status"],
    }


# --------------------------------------------------------------------------- #
# Handlers                                                                     #
# --------------------------------------------------------------------------- #
async def _create_budget(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    user_tz = await _user_timezone(ctx)
    year, month = args.year, args.month
    if year is None or month is None:
        cur_year, cur_month = _current_month(user_tz)
        year = year if year is not None else cur_year
        month = month if month is not None else cur_month
    name = args.name or (args.category or "Presupuesto mensual")
    try:
        budget = await ctx.services.budgets.create(
            user_id=ctx.user_id,
            name=name,
            amount=amount,
            year=year,
            month=month,
            category=args.category,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": budget.id,
            "name": budget.name,
            "category": budget.category,
            "year": budget.year,
            "month": budget.month,
            "amount": fmt_amount(budget.amount),
        },
    )


async def _list_budgets(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetListArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    year, month = args.year, args.month
    if year is None or month is None:
        cur_year, cur_month = _current_month(user_tz)
        year = year if year is not None else cur_year
        month = month if month is not None else cur_month
    try:
        statuses = await ctx.services.budgets.list_with_status(ctx.user_id, year=year, month=month, timezone=user_tz)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "year": year,
            "month": month,
            "budgets": [_to_status_dict(s) for s in statuses],
        },
    )


async def _get_budget_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetIdArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        status = await ctx.services.budgets.get_status(ctx.user_id, args.budget_id, timezone=user_tz)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data=_to_status_dict(status))


async def _update_budget(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetUpdateArgs(**kwargs)
    fields: dict[str, Any] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.category is not None:
        fields["category"] = args.category
    if args.year is not None:
        fields["year"] = args.year
    if args.month is not None:
        fields["month"] = args.month
    if args.amount is not None:
        amount = _parse_money(args.amount)
        if isinstance(amount, ToolResult):
            return amount
        fields["amount"] = amount
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    try:
        budget = await ctx.services.budgets.update(ctx.user_id, args.budget_id, **fields)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": budget.id,
            "name": budget.name,
            "category": budget.category,
            "year": budget.year,
            "month": budget.month,
            "amount": fmt_amount(budget.amount),
        },
    )


async def _delete_budget(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetIdArgs(**kwargs)
    try:
        await ctx.services.budgets.delete(ctx.user_id, args.budget_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": args.budget_id, "deleted": True})


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
BUDGET_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_budget",
        description=(
            "Crea un presupuesto mensual (general o por categoría). "
            "'Quiero gastar como máximo 300 € en alimentación este mes' → amount=300, category='Alimentación'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "Importe, p. ej. '300'"},
                "year": {"type": "integer", "default": None, "description": "Año (por defecto el actual)"},
                "month": {"type": "integer", "default": None, "description": "Mes 1-12 (por defecto el actual)"},
                "category": {
                    "type": "string",
                    "default": None,
                    "description": "Alimentación, Transporte, Ocio... None = general",
                },
                "name": {"type": "string", "default": None},
            },
            "required": ["amount"],
        },
        handler=_create_budget,
        args_model=BudgetCreateArgs,
    ),
    ToolDefinition(
        name="list_budgets",
        description=(
            "Lista los presupuestos del mes (con su estado: gastado, restante, porcentaje usado). "
            "'¿Cómo voy con mis presupuestos?' → esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "default": None},
                "month": {"type": "integer", "default": None},
            },
            "required": [],
        },
        handler=_list_budgets,
        args_model=BudgetListArgs,
    ),
    ToolDefinition(
        name="get_budget_status",
        description="Estado de un presupuesto concreto: gastado, restante, porcentaje y warning/exceeded.",
        input_schema={
            "type": "object",
            "properties": {"budget_id": {"type": "integer"}},
            "required": ["budget_id"],
        },
        handler=_get_budget_status,
        args_model=BudgetIdArgs,
    ),
    ToolDefinition(
        name="update_budget",
        description="Actualiza el importe, nombre, categoría, año o mes de un presupuesto.",
        input_schema={
            "type": "object",
            "properties": {
                "budget_id": {"type": "integer"},
                "amount": {"type": "string", "default": None},
                "year": {"type": "integer", "default": None},
                "month": {"type": "integer", "default": None},
                "category": {"type": "string", "default": None},
                "name": {"type": "string", "default": None},
            },
            "required": ["budget_id"],
        },
        handler=_update_budget,
        args_model=BudgetUpdateArgs,
    ),
    ToolDefinition(
        name="delete_budget",
        description="Elimina un presupuesto (no borra movimientos; requiere confirmación).",
        input_schema={
            "type": "object",
            "properties": {"budget_id": {"type": "integer"}},
            "required": ["budget_id"],
        },
        handler=_delete_budget,
        requires_confirmation=True,
        args_model=BudgetIdArgs,
    ),
]
