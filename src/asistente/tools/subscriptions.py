"""Tools de suscripciones (Fase 3.3).

Tools expuestas al LLM (aíslan por `user_id` autenticado):

- create_subscription
- update_subscription
- cancel_subscription (requires_confirmation)
- list_subscriptions
"""

from __future__ import annotations

from datetime import datetime
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


class SubscriptionCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    amount: str = Field(min_length=1, max_length=40, description='"13,99" o "1500"')
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    billing_period: Literal["daily", "weekly", "monthly", "yearly"]
    account_id: int | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=64)
    next_billing_date: str | None = Field(default=None, max_length=40, description="ISO datetime aware")
    timezone: str = Field(default="Europe/Madrid", max_length=64)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        return normalize_currency(v)


class SubscriptionUpdateArgs(BaseModel):
    subscription_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    amount: str | None = Field(default=None, min_length=1, max_length=40)
    account_id: int | None = Field(default=None, gt=0)
    category: str | None = Field(default=None, max_length=64)
    billing_period: Literal["daily", "weekly", "monthly", "yearly"] | None = None
    next_billing_date: str | None = Field(default=None, max_length=40)


class SubscriptionIdArgs(BaseModel):
    subscription_id: int = Field(gt=0)


class ListSubscriptionsArgs(BaseModel):
    active_only: bool | None = None
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


def _parse_datetime_tz(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("La fecha deben incluir zona horaria (ej: 2026-10-01T12:00:00+02:00).")
    return parsed


# --------------------------------------------------------------------------- #
# Handlers                                                                    #
# --------------------------------------------------------------------------- #
async def _create_subscription(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SubscriptionCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    try:
        next_at = _parse_datetime_tz(args.next_billing_date)
    except ValueError as exc:
        return _ko(exc)
    try:
        sub = await ctx.services.subscriptions.create(
            user_id=ctx.user_id,
            name=args.name,
            amount=amount,
            currency=args.currency,
            billing_period=args.billing_period,
            account_id=args.account_id,
            category=args.category,
            next_billing_date=next_at,
            timezone=args.timezone,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": sub.id,
            "name": sub.name,
            "amount": str(sub.amount),
            "currency": sub.currency,
            "billing_period": sub.billing_period,
            "next_billing_date": sub.next_billing_date.isoformat() if sub.next_billing_date else None,
            "account_id": sub.account_id,
            "category": sub.category,
            "active": sub.active,
        },
    )


async def _update_subscription(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SubscriptionUpdateArgs(**kwargs)
    fields: dict[str, Any] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.amount is not None:
        amount = _parse_money(args.amount)
        if isinstance(amount, ToolResult):
            return amount
        fields["amount"] = amount
    if args.account_id is not None:
        fields["account_id"] = args.account_id
    if args.category is not None:
        fields["category"] = args.category
    if args.billing_period is not None:
        fields["billing_period"] = args.billing_period
    if args.next_billing_date is not None:
        try:
            fields["next_billing_date"] = _parse_datetime_tz(args.next_billing_date)
        except ValueError as exc:
            return _ko(exc)
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    try:
        sub = await ctx.services.subscriptions.update(ctx.user_id, args.subscription_id, **fields)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": sub.id,
            "name": sub.name,
            "amount": str(sub.amount),
            "billing_period": sub.billing_period,
            "next_billing_date": sub.next_billing_date.isoformat() if sub.next_billing_date else None,
        },
    )


async def _cancel_subscription(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SubscriptionIdArgs(**kwargs)
    try:
        sub = await ctx.services.subscriptions.cancel(ctx.user_id, args.subscription_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": sub.id, "name": sub.name, "active": sub.active})


async def _list_subscriptions(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ListSubscriptionsArgs(**kwargs)
    try:
        rows = await ctx.services.subscriptions.list(ctx.user_id, active_only=args.active_only)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "subscriptions": [
                {
                    "id": s.id,
                    "name": s.name,
                    "amount": str(s.amount),
                    "currency": s.currency,
                    "billing_period": s.billing_period,
                    "next_billing_date": s.next_billing_date.isoformat() if s.next_billing_date else None,
                    "category": s.category,
                    "active": s.active,
                }
                for s in rows
            ]
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
SUBSCRIPTION_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_subscription",
        description=(
            "Crea una suscripción (Netflix, Spotify, alquiler...). Crea la recurrencia subyacente automáticamente. "
            "Las suscripciones NO son gastos confirmados: aparecen como pagos planificados y solo afectan saldos cuando se confirman."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre, p. ej. 'Netflix' o 'Alquiler piso'"},
                "amount": {"type": "string", "description": "Importe, p. ej. '13,99'"},
                "currency": {"type": "string", "default": "EUR"},
                "billing_period": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"]},
                "account_id": {"type": "integer", "default": None},
                "category": {"type": "string", "default": None},
                "next_billing_date": {"type": "string", "default": None, "description": "ISO aware datetime"},
                "timezone": {"type": "string", "default": "Europe/Madrid"},
            },
            "required": ["name", "amount", "billing_period"],
        },
        handler=_create_subscription,
        args_model=SubscriptionCreateArgs,
    ),
    ToolDefinition(
        name="update_subscription",
        description="Actualiza nombre, importe, cuenta, categoría o periodo de facturación de una suscripción.",
        input_schema={
            "type": "object",
            "properties": {
                "subscription_id": {"type": "integer"},
                "name": {"type": "string", "default": None},
                "amount": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None},
                "category": {"type": "string", "default": None},
                "billing_period": {"type": "string", "enum": ["daily", "weekly", "monthly", "yearly"], "default": None},
                "next_billing_date": {"type": "string", "default": None},
            },
            "required": ["subscription_id"],
        },
        handler=_update_subscription,
        args_model=SubscriptionUpdateArgs,
    ),
    ToolDefinition(
        name="cancel_subscription",
        description=(
            "Cancela una suscripción. Desactiva la recurrencia asociada, cancela los pagos futuros pendientes y "
            "conserva el histórico de pagos ya confirmados. Esta acción requiere confirmación."
        ),
        input_schema={
            "type": "object",
            "properties": {"subscription_id": {"type": "integer"}},
            "required": ["subscription_id"],
        },
        handler=_cancel_subscription,
        requires_confirmation=True,
        args_model=SubscriptionIdArgs,
    ),
    ToolDefinition(
        name="list_subscriptions",
        description="Lista las suscripciones del usuario con su estado, periodo y próxima fecha de facturación.",
        input_schema={
            "type": "object",
            "properties": {
                "active_only": {"type": "boolean", "default": None},
                "limit": {"type": "integer", "default": 50},
            },
            "required": [],
        },
        handler=_list_subscriptions,
        args_model=ListSubscriptionsArgs,
    ),
]
