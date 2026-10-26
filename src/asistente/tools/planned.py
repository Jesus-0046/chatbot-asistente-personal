"""Tools de pagos planificados (Fase 3.3).

Tools expuestas al LLM (aíslan por `user_id` autenticado):

- create_planned_transaction (manual)
- list_planned_transactions
- get_upcoming_payments / get_upcoming_income
- confirm_planned_transaction
- cancel_planned_transaction (requires_confirmation)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from asistente.domain.amounts import parse_amount
from asistente.domain.currencies import normalize_currency
from asistente.tools.agenda import _resolve_period, _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult

# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #


class PlannedCreateArgs(BaseModel):
    type: Literal["expense", "income"]
    amount: str = Field(min_length=1, max_length=40, description='"28,80", "35"')
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    account_id: int | None = Field(default=None, gt=0)
    merchant: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=300)
    note: str | None = Field(default=None, max_length=1000)
    planned_at: str = Field(
        min_length=1, max_length=40, description="ISO datetime aware, p. ej. 2026-10-01T12:00:00+02:00"
    )
    timezone: str = Field(default="Europe/Madrid", max_length=64)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        return normalize_currency(v)


class PlannedIdArgs(BaseModel):
    planned_id: int = Field(gt=0)


class ListPlannedArgs(BaseModel):
    status: Literal["planned", "converted", "cancelled"] | None = None
    type: Literal["expense", "income"] | None = None
    period: str = Field(default="este mes", max_length=200)
    limit: int = Field(default=50, ge=1, le=200)


class UpcomingArgs(BaseModel):
    days: int = Field(default=30, ge=1, le=180)
    type: Literal["expense", "income"] | None = None


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _ko(exc: Exception) -> ToolResult:
    return ToolResult(ok=False, error=str(exc))


def _parse_money(value: str) -> ToolResult | Decimal:
    try:
        return parse_amount(value)
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))


def _parse_datetime_tz(value: str, timezone: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("La fecha y hora deben incluir zona horaria (ej: 2026-10-01T12:00:00+02:00).")
    return parsed


# --------------------------------------------------------------------------- #
# Handlers                                                                    #
# --------------------------------------------------------------------------- #
async def _create_planned(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PlannedCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    try:
        planned_at = _parse_datetime_tz(args.planned_at, args.timezone)
    except ValueError as exc:
        return _ko(exc)
    try:
        planned = await ctx.services.planned.create(
            user_id=ctx.user_id,
            type=args.type,
            amount=amount,
            currency=args.currency,
            account_id=args.account_id,
            merchant=args.merchant,
            category=args.category,
            title=args.title,
            note=args.note,
            planned_at=planned_at,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": planned.id,
            "type": planned.type,
            "title": planned.title,
            "amount": str(planned.amount),
            "currency": planned.currency,
            "account_id": planned.account_id,
            "merchant": planned.merchant,
            "category": planned.category,
            "planned_at": planned.planned_at.isoformat(),
            "status": planned.status,
        },
    )


async def _list_planned(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ListPlannedArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    try:
        await ctx.services.recurring.generate_upcoming_planned_transactions(ctx.user_id)
        rows = await ctx.services.planned.list_planned(
            ctx.user_id,
            status=args.status,
            transaction_type=args.type,
            start_utc=start_utc,
            end_utc=end_utc,
            limit=args.limit,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "period": args.period,
            "planned_transactions": [
                {
                    "id": p.id,
                    "type": p.type,
                    "title": p.title,
                    "amount": str(p.amount),
                    "currency": p.currency,
                    "account_id": p.account_id,
                    "merchant": p.merchant,
                    "category": p.category,
                    "planned_at": p.planned_at.isoformat(),
                    "status": p.status,
                }
                for p in rows
            ],
        },
    )


async def _get_upcoming_payments(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpcomingArgs(**kwargs)
    try:
        await ctx.services.recurring.generate_upcoming_planned_transactions(ctx.user_id)
        rows = await ctx.services.planned.upcoming(
            ctx.user_id,
            days=args.days,
            transaction_type="expense",
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "days": args.days,
            "upcoming_payments": [
                {
                    "id": p.id,
                    "title": p.title,
                    "amount": str(p.amount),
                    "currency": p.currency,
                    "merchant": p.merchant,
                    "category": p.category,
                    "planned_at": p.planned_at.isoformat(),
                }
                for p in rows
            ],
        },
    )


async def _get_upcoming_income(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpcomingArgs(**kwargs)
    try:
        await ctx.services.recurring.generate_upcoming_planned_transactions(ctx.user_id)
        rows = await ctx.services.planned.upcoming(
            ctx.user_id,
            days=args.days,
            transaction_type="income",
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "days": args.days,
            "upcoming_income": [
                {
                    "id": p.id,
                    "title": p.title,
                    "amount": str(p.amount),
                    "currency": p.currency,
                    "merchant": p.merchant,
                    "planned_at": p.planned_at.isoformat(),
                }
                for p in rows
            ],
        },
    )


async def _confirm_planned(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PlannedIdArgs(**kwargs)
    try:
        converted = await ctx.services.planned.convert(ctx.user_id, args.planned_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": converted.id,
            "type": converted.type,
            "amount": str(converted.amount),
            "status": converted.status,
            "converted_transaction_id": converted.converted_transaction_id,
        },
    )


async def _cancel_planned(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PlannedIdArgs(**kwargs)
    try:
        cancelled = await ctx.services.planned.cancel(ctx.user_id, args.planned_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": cancelled.id, "status": cancelled.status})


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
PLANNED_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_planned_transaction",
        description=(
            "Crea un pago planificado manual (pendiente, NO confirma un gasto). "
            "'Voy a pagar el alquiler el día 1 de octubre' → create_planned_transaction. "
            "NO toca saldos ni presupuestos hasta que se confirme con confirm_planned_transaction."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["expense", "income"]},
                "amount": {"type": "string", "description": "Importe, p. ej. '850'"},
                "currency": {"type": "string", "default": "EUR"},
                "account_id": {"type": "integer", "default": None},
                "merchant": {"type": "string", "default": None},
                "category": {"type": "string", "default": None},
                "title": {"type": "string", "default": None},
                "note": {"type": "string", "default": None},
                "planned_at": {"type": "string", "description": "Fecha ISO aware, p. ej. 2026-10-01T12:00:00+02:00"},
                "timezone": {"type": "string", "default": "Europe/Madrid"},
            },
            "required": ["type", "amount", "planned_at"],
        },
        handler=_create_planned,
        args_model=PlannedCreateArgs,
    ),
    ToolDefinition(
        name="list_planned_transactions",
        description="Lista pagos planificados de un periodo con filtros.",
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes"},
                "status": {"type": "string", "enum": ["planned", "converted", "cancelled"], "default": None},
                "type": {"type": "string", "enum": ["expense", "income"], "default": None},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
        handler=_list_planned,
        args_model=ListPlannedArgs,
    ),
    ToolDefinition(
        name="get_upcoming_payments",
        description="Pagos planificados de gasto pendientes en los próximos N días. NO incluye gastos ya realizados.",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30, "description": "Días a mirar hacia el futuro"},
                "type": {"type": "string", "enum": ["expense", "income"], "default": None},
            },
            "required": [],
        },
        handler=_get_upcoming_payments,
        args_model=UpcomingArgs,
    ),
    ToolDefinition(
        name="get_upcoming_income",
        description="Ingresos planificados pendientes en los próximos N días.",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "default": 30},
            },
            "required": [],
        },
        handler=_get_upcoming_income,
        args_model=UpcomingArgs,
    ),
    ToolDefinition(
        name="confirm_planned_transaction",
        description=(
            "Confirma un pago planificado: lo convierte en una transacción real (gasto/ingreso) y "
            "afecta saldos y presupuestos. El planificado pasa a status=converted. "
            "'Confirmo el alquiler del día 1' → confirm_planned_transaction."
        ),
        input_schema={"type": "object", "properties": {"planned_id": {"type": "integer"}}, "required": ["planned_id"]},
        handler=_confirm_planned,
        args_model=PlannedIdArgs,
    ),
    ToolDefinition(
        name="cancel_planned_transaction",
        description="Cancela un pago planificado pendiente. Solo cancela pagos en estado planned. Esta acción requiere confirmación.",
        input_schema={"type": "object", "properties": {"planned_id": {"type": "integer"}}, "required": ["planned_id"]},
        handler=_cancel_planned,
        requires_confirmation=True,
        args_model=PlannedIdArgs,
    ),
]
