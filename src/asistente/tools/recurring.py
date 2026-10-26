"""Tools de recurrencias financieras (Fase 3.3).

Tools expuestas al LLM (aíslan por `user_id` autenticado; ninguna toca la BD):

- create_recurring_transaction / update_recurring_transaction
- pause_recurring_transaction / resume_recurring_transaction
- cancel_recurring_transaction (requires_confirmation)
- list_recurring_transactions
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from asistente.domain.amounts import parse_amount
from asistente.domain.currencies import normalize_currency
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult

# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #


class RecurringCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    type: Literal["expense", "income"]
    amount: str = Field(min_length=1, max_length=40, description='"28,80", "35", "1500"')
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    account_id: int | None = Field(default=None, gt=0)
    merchant: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=64)
    frequency: Literal["daily", "weekly", "monthly", "yearly"]
    interval: int = Field(default=1, ge=1, le=365)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    month_of_year: int | None = Field(default=None, ge=1, le=12)
    start_date: str | None = Field(default=None, max_length=20, description="YYYY-MM-DD")
    end_date: str | None = Field(default=None, max_length=20, description="YYYY-MM-DD")
    next_due_at: str | None = Field(default=None, max_length=40, description="ISO datetime aware")
    timezone: str = Field(default="Europe/Madrid", max_length=64)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        return normalize_currency(v)


class RecurringUpdateArgs(BaseModel):
    recurring_id: int = Field(gt=0)
    title: str | None = Field(default=None, min_length=1, max_length=300)
    amount: str | None = Field(default=None, min_length=1, max_length=40)
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    account_id: int | None = Field(default=None, gt=0)
    merchant: str | None = Field(default=None, max_length=300)
    category: str | None = Field(default=None, max_length=64)
    frequency: Literal["daily", "weekly", "monthly", "yearly"] | None = None
    interval: int | None = Field(default=None, ge=1, le=365)
    day_of_month: int | None = Field(default=None, ge=1, le=31)
    month_of_year: int | None = Field(default=None, ge=1, le=12)
    end_date: str | None = Field(default=None, max_length=20)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_currency(v)


class RecurringIdArgs(BaseModel):
    recurring_id: int = Field(gt=0)


class ListRecurringArgs(BaseModel):
    active_only: bool = False
    include_paused: bool = True
    limit: int = Field(default=50, ge=1, le=200)


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


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    from datetime import datetime as dt

    try:
        return dt.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"Fecha no válida: '{value}'. Formato esperado: YYYY-MM-DD.") from None


def _parse_datetime_tz(value: str | None, timezone: str) -> datetime | None:
    if value is None:
        return None
    from datetime import datetime as dt

    from asistente.shared.timeutil import to_utc

    parsed = dt.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("La fecha y hora deben incluir zona horaria (ej: 2026-10-01T12:00:00+02:00).")
    return to_utc(parsed)


# --------------------------------------------------------------------------- #
# Handlers                                                                    #
# --------------------------------------------------------------------------- #
async def _create_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = RecurringCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    try:
        rec = await ctx.services.recurring.create(
            user_id=ctx.user_id,
            type=args.type,
            title=args.title,
            amount=amount,
            currency=args.currency,
            account_id=args.account_id,
            merchant=args.merchant,
            category=args.category,
            frequency=args.frequency,
            interval=args.interval,
            day_of_month=args.day_of_month,
            month_of_year=args.month_of_year,
            start_date=_parse_date(args.start_date),
            next_due_at=_parse_datetime_tz(args.next_due_at, args.timezone),
            end_date=_parse_date(args.end_date),
            timezone=args.timezone,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": rec.id,
            "type": rec.type,
            "title": rec.title,
            "amount": str(rec.amount),
            "currency": rec.currency,
            "account_id": rec.account_id,
            "merchant": rec.merchant,
            "category": rec.category,
            "frequency": rec.frequency,
            "interval": rec.interval,
            "day_of_month": rec.day_of_month,
            "month_of_year": rec.month_of_year,
            "start_date": str(rec.start_date) if rec.start_date else None,
            "end_date": str(rec.end_date) if rec.end_date else None,
            "next_due_at": rec.next_due_at.isoformat() if rec.next_due_at else None,
            "timezone": rec.timezone,
            "active": rec.active,
            "paused": rec.paused,
        },
    )


async def _update_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = RecurringUpdateArgs(**kwargs)
    fields: dict[str, Any] = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.amount is not None:
        amount = _parse_money(args.amount)
        if isinstance(amount, ToolResult):
            return amount
        fields["amount"] = amount
    if args.account_id is not None:
        fields["account_id"] = args.account_id
    if args.merchant is not None:
        fields["merchant"] = args.merchant
    if args.category is not None:
        fields["category"] = args.category
    if args.frequency is not None:
        fields["frequency"] = args.frequency
    if args.interval is not None:
        fields["interval"] = args.interval
    if args.day_of_month is not None:
        fields["day_of_month"] = args.day_of_month
    if args.month_of_year is not None:
        fields["month_of_year"] = args.month_of_year
    if args.end_date is not None:
        fields["end_date"] = _parse_date(args.end_date)
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    try:
        rec = await ctx.services.recurring.update(ctx.user_id, args.recurring_id, **fields)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": rec.id,
            "title": rec.title,
            "amount": str(rec.amount),
            "frequency": rec.frequency,
            "next_due_at": rec.next_due_at.isoformat() if rec.next_due_at else None,
        },
    )


async def _pause_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = RecurringIdArgs(**kwargs)
    try:
        rec = await ctx.services.recurring.pause(ctx.user_id, args.recurring_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": rec.id, "paused": True})


async def _resume_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = RecurringIdArgs(**kwargs)
    try:
        rec = await ctx.services.recurring.resume(ctx.user_id, args.recurring_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={"id": rec.id, "paused": False, "next_due_at": rec.next_due_at.isoformat() if rec.next_due_at else None},
    )


async def _cancel_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = RecurringIdArgs(**kwargs)
    try:
        rec, cancelled = await ctx.services.recurring.cancel(ctx.user_id, args.recurring_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": rec.id, "active": False, "cancelled_planned": cancelled})


async def _list_recurring(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ListRecurringArgs(**kwargs)
    try:
        await ctx.services.recurring.generate_upcoming_planned_transactions(ctx.user_id)
        rows = await ctx.services.recurring.list(
            ctx.user_id,
            active_only=args.active_only,
            include_paused=args.include_paused,
            limit=args.limit,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "recurring_transactions": [
                {
                    "id": r.id,
                    "type": r.type,
                    "title": r.title,
                    "amount": str(r.amount),
                    "currency": r.currency,
                    "frequency": r.frequency,
                    "interval": r.interval,
                    "next_due_at": r.next_due_at.isoformat() if r.next_due_at else None,
                    "active": r.active,
                    "paused": r.paused,
                }
                for r in rows
            ]
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
RECURRING_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_recurring_transaction",
        description="Crea una regla de recurrencia financiera (gasto o ingreso periódico). La primera ocurrencia se calcula automáticamente si no se indica.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Nombre de la recurrencia, p. ej. 'Alquiler'"},
                "type": {"type": "string", "enum": ["expense", "income"]},
                "amount": {"type": "string", "description": "Importe, p. ej. '850' o '28,50'"},
                "currency": {"type": "string", "default": "EUR"},
                "account_id": {"type": "integer", "default": None},
                "merchant": {"type": "string", "default": None},
                "category": {"type": "string", "default": None},
                "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"]},
                "interval": {"type": "integer", "default": 1, "description": "Cada N días/semanas/meses/años"},
                "day_of_month": {
                    "type": "integer",
                    "default": None,
                    "description": "Día ancla (1-31); requerido para monthly/yearly sin start_date",
                },
                "month_of_year": {
                    "type": "integer",
                    "default": None,
                    "description": "Mes ancla (1-12); requerido para yearly sin start_date",
                },
                "start_date": {"type": "string", "default": None, "description": "Primera fecha (YYYY-MM-DD)"},
                "end_date": {"type": "string", "default": None, "description": "Fecha fin inclusiva (YYYY-MM-DD)"},
                "next_due_at": {"type": "string", "default": None, "description": "Forzar próxima fecha (ISO aware)"},
                "timezone": {"type": "string", "default": "Europe/Madrid"},
            },
            "required": ["title", "type", "amount", "frequency"],
        },
        handler=_create_recurring,
        args_model=RecurringCreateArgs,
    ),
    ToolDefinition(
        name="update_recurring_transaction",
        description="Actualiza campos de una recurrencia existente.",
        input_schema={
            "type": "object",
            "properties": {
                "recurring_id": {"type": "integer"},
                "title": {"type": "string", "default": None},
                "amount": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None},
                "merchant": {"type": "string", "default": None},
                "category": {"type": "string", "default": None},
                "frequency": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": None},
                "interval": {"type": "integer", "default": None},
                "day_of_month": {"type": "integer", "default": None},
                "month_of_year": {"type": "integer", "default": None},
                "end_date": {"type": "string", "default": None},
            },
            "required": ["recurring_id"],
        },
        handler=_update_recurring,
        args_model=RecurringUpdateArgs,
    ),
    ToolDefinition(
        name="pause_recurring_transaction",
        description="Pausa una recurrencia sin cancelarla. No genera más planificados hasta que se reanude, pero conserva el estado.",
        input_schema={
            "type": "object",
            "properties": {"recurring_id": {"type": "integer"}},
            "required": ["recurring_id"],
        },
        handler=_pause_recurring,
        args_model=RecurringIdArgs,
    ),
    ToolDefinition(
        name="resume_recurring_transaction",
        description="Reanuda una recurrencia pausada. Si tiene next_due_at None (terminó), se recalcula desde hoy.",
        input_schema={
            "type": "object",
            "properties": {"recurring_id": {"type": "integer"}},
            "required": ["recurring_id"],
        },
        handler=_resume_recurring,
        args_model=RecurringIdArgs,
    ),
    ToolDefinition(
        name="cancel_recurring_transaction",
        description="Cancela una recurrencia permanentemente. Desactiva la serie, cancela los pagos futuros pendientes y conserva el histórico. Esta acción requiere confirmación.",
        input_schema={
            "type": "object",
            "properties": {"recurring_id": {"type": "integer"}},
            "required": ["recurring_id"],
        },
        handler=_cancel_recurring,
        requires_confirmation=True,
        args_model=RecurringIdArgs,
    ),
    ToolDefinition(
        name="list_recurring_transactions",
        description="Lista las reglas de recurrencia del usuario.",
        input_schema={
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "default": False},
                "include_paused": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
        handler=_list_recurring,
        args_model=ListRecurringArgs,
    ),
]
