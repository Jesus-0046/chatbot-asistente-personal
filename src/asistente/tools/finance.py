"""Herramientas financieras (Fase 3 — núcleo financiero).

Tools expuestas al LLM (aíslan por `user_id` autenticado; ninguna toca la BD):

- create_account / list_accounts / update_account / set_default_account / deactivate_account
- create_expense / create_income / list_transactions
- create_transfer / get_transfer_history
- get_balance_overview / spending_summary

Semántica clave:
- Una tarjeta de DÉBITO no es una cuenta: comprar con ella es un gasto sobre
  la cuenta bancaria asociada (`bank`). El LLM elige esa cuenta según el
  `list_accounts`.
- Una tarjeta de CRÉDITO (`credit_card`) es una cuenta que representa DEUDA:
  comprar con ella es un GASTO sobre esa cuenta (saldo → negativo).
- PAGAR la tarjeta desde otra cuenta es una TRANSFERENCIA (banco → tarjeta de
  crédito), NUNCA un gasto (evita doble contabilización).
- Los importes viajan como texto ("28,80", "35 euros", "1500") y se convierten
  a `Decimal`; nunca floats.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from asistente.domain.amounts import fmt_amount, parse_amount, parse_amount_signed
from asistente.domain.categories import normalize_category
from asistente.domain.currencies import normalize_currency
from asistente.tools.agenda import _resolve_datetime, _resolve_period, _tz, _user_timezone
from asistente.tools.agenda import _ResolvedNode as _R
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult

# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #
AccountType = Literal["bank", "savings", "cash", "credit_card", "other"]


class AccountCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    type: AccountType
    currency: str = Field(default="EUR", min_length=1, max_length=8)
    initial_balance: str = Field(
        default="0",
        min_length=1,
        max_length=40,
        description='"28,80", "1000", "0" o "-350" (negativo solo para tarjetas de crédito)',
    )
    sort_order: int = Field(default=0, ge=0)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str) -> str:
        return normalize_currency(v)


class AccountIdArgs(BaseModel):
    account_id: int = Field(gt=0)


class AccountUpdateArgs(BaseModel):
    account_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    type: AccountType | None = None
    currency: str | None = Field(default=None, min_length=1, max_length=8)
    initial_balance: str | None = Field(default=None, min_length=1, max_length=40)
    sort_order: int | None = Field(default=None, ge=0)

    @field_validator("currency")
    @classmethod
    def _check_currency(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return normalize_currency(v)


class MovementArgs(BaseModel):
    amount: str = Field(min_length=1, max_length=40, description='"28,80", "35", "1500"')
    account_id: int | None = Field(default=None, gt=0, description="Cuenta del movimiento (si se conoce)")
    merchant: str | None = Field(default=None, max_length=300, description="Comercio, p. ej. 'Mercadona'")
    category: str | None = Field(default=None, max_length=64, description="Alimentación, Transporte, Suscripciones...")
    occurred_at: str | None = Field(default=None, max_length=200, description="Cuándo ocurrió (natural o ISO)")
    note: str | None = Field(default=None, max_length=1000)


class ListTransactionsArgs(BaseModel):
    period: str = Field(default="este mes", max_length=200, description="hoy, esta semana, este mes, este año...")
    account_id: int | None = Field(default=None, gt=0)
    type: Literal["expense", "income"] | None = None
    category: str | None = None
    merchant: str | None = None
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class TransferCreateArgs(BaseModel):
    from_account_id: int = Field(gt=0)
    to_account_id: int = Field(gt=0)
    amount: str = Field(min_length=1, max_length=40)
    occurred_at: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class TransferHistoryArgs(BaseModel):
    period: str = Field(default="este mes", max_length=200)
    account_id: int | None = Field(default=None, gt=0)
    from_account_id: int | None = Field(default=None, gt=0)
    to_account_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class SpendingSummaryArgs(BaseModel):
    period: str = Field(default="este mes", max_length=200)
    category: str | None = Field(default=None, max_length=64)
    merchant: str | None = Field(default=None, max_length=300)
    account_id: int | None = Field(default=None, gt=0)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _ko(exc: Exception) -> ToolResult:
    return ToolResult(ok=False, error=str(exc))


def _parse_money(args_value: str) -> ToolResult | Any:
    try:
        return parse_amount(args_value)
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))


def _parse_money_signed(args_value: str) -> ToolResult | Any:
    """Importe de saldo inicial: acepta 0 y negativos (tarjetas de crédito).

    El rango válido según el tipo de cuenta lo valida `AccountService`.
    """
    try:
        return parse_amount_signed(args_value)
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))


def _resolve_occurred(ctx: ToolContext, expression: str | None, user_tz: str) -> ToolResult | datetime | None:
    if not expression:
        return None
    resolved = _resolve_datetime(ctx, expression, user_tz)
    if not isinstance(resolved, _R):
        return resolved
    from asistente.shared.timeutil import to_utc

    return to_utc(resolved.value)


async def _accounts_map(ctx: ToolContext) -> dict[int, str]:
    accounts = await ctx.services.accounts.list_for_user(ctx.user_id)
    return {a.id: a.name for a in accounts}


def _acc_name(names: dict[int, str], account_id: int | None) -> str | None:
    return names.get(account_id) if account_id is not None else None


# --------------------------------------------------------------------------- #
# Cuentas                                                                     #
# --------------------------------------------------------------------------- #
async def _create_account(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AccountCreateArgs(**kwargs)
    amount = _parse_money_signed(args.initial_balance)
    if isinstance(amount, ToolResult):
        return amount
    try:
        account = await ctx.services.accounts.create(
            user_id=ctx.user_id,
            name=args.name,
            type=args.type,
            currency=args.currency,
            initial_balance=amount,
            sort_order=args.sort_order,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": account.id,
            "name": account.name,
            "type": account.type,
            "currency": account.currency,
            "initial_balance": fmt_amount(account.initial_balance),
            "is_default": account.is_default,
            "active": account.active,
        },
    )


async def _list_accounts(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    accounts = await ctx.services.accounts.list_for_user(ctx.user_id)
    return ToolResult(
        ok=True,
        data={
            "accounts": [
                {
                    "id": a.id,
                    "name": a.name,
                    "type": a.type,
                    "currency": a.currency,
                    "is_default": a.is_default,
                    "active": a.active,
                    "sort_order": a.sort_order,
                }
                for a in accounts
            ]
        },
    )


async def _update_account(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AccountUpdateArgs(**kwargs)
    fields: dict[str, Any] = {}
    if args.name is not None:
        fields["name"] = args.name
    if args.type is not None:
        fields["type"] = args.type
    if args.currency is not None:
        fields["currency"] = args.currency
    if args.sort_order is not None:
        fields["sort_order"] = args.sort_order
    if args.initial_balance is not None:
        amount = _parse_money_signed(args.initial_balance)
        if isinstance(amount, ToolResult):
            return amount
        fields["initial_balance"] = amount
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    try:
        account = await ctx.services.accounts.update(ctx.user_id, args.account_id, **fields)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={"id": account.id, "name": account.name, "type": account.type, "is_default": account.is_default},
    )


async def _set_default_account(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AccountIdArgs(**kwargs)
    try:
        account = await ctx.services.accounts.set_default(ctx.user_id, args.account_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": account.id, "name": account.name, "is_default": True})


async def _deactivate_account(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AccountIdArgs(**kwargs)
    try:
        account = await ctx.services.accounts.deactivate(ctx.user_id, args.account_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data={"id": account.id, "name": account.name, "active": False})


# --------------------------------------------------------------------------- #
# Movimientos                                                                 #
# --------------------------------------------------------------------------- #
async def _create_movement(ctx: ToolContext, args: MovementArgs, movement_type: str) -> ToolResult:
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    try:
        txn = await ctx.services.transactions.create(
            user_id=ctx.user_id,
            type=movement_type,
            amount=amount,
            account_id=args.account_id,
            merchant=args.merchant,
            category=args.category,
            occurred_at=occurred,
            note=args.note,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": txn.id,
            "type": txn.type,
            "amount": fmt_amount(txn.amount),
            "currency": txn.currency,
            "account_id": txn.account_id,
            "merchant": txn.merchant,
            "category": txn.category,
            "occurred_at": txn.occurred_at.astimezone(_tz(user_tz)).isoformat(),
        },
    )


async def _create_expense(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = MovementArgs(**kwargs)
    return await _create_movement(ctx, args, "expense")


async def _create_income(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = MovementArgs(**kwargs)
    return await _create_movement(ctx, args, "income")


async def _list_transactions(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ListTransactionsArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    rows = await ctx.services.transactions.list(
        ctx.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        account_id=args.account_id,
        transaction_type=args.type,
        category=args.category,
        merchant=args.merchant,
        limit=args.limit,
        offset=args.offset,
    )
    names = await _accounts_map(ctx)
    return ToolResult(
        ok=True,
        data={
            "period": args.period,
            "transactions": [
                {
                    "id": t.id,
                    "type": t.type,
                    "amount": fmt_amount(t.amount),
                    "currency": t.currency,
                    "account_id": t.account_id,
                    "account_name": _acc_name(names, t.account_id),
                    "merchant": t.merchant,
                    "category": t.category,
                    "occurred_at": t.occurred_at.astimezone(_tz(user_tz)).isoformat(),
                }
                for t in rows
            ],
        },
    )


# --------------------------------------------------------------------------- #
# Transferencias                                                              #
# --------------------------------------------------------------------------- #
async def _create_transfer(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TransferCreateArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    try:
        transfer = await ctx.services.transfers.create(
            user_id=ctx.user_id,
            from_account_id=args.from_account_id,
            to_account_id=args.to_account_id,
            amount=amount,
            occurred_at=occurred,
            note=args.note,
        )
    except Exception as exc:
        return _ko(exc)
    names = await _accounts_map(ctx)
    return ToolResult(
        ok=True,
        data={
            "id": transfer.id,
            "from_account_id": transfer.from_account_id,
            "from_account_name": _acc_name(names, transfer.from_account_id),
            "to_account_id": transfer.to_account_id,
            "to_account_name": _acc_name(names, transfer.to_account_id),
            "amount": fmt_amount(transfer.amount),
            "currency": transfer.currency,
            "occurred_at": transfer.occurred_at.astimezone(_tz(user_tz)).isoformat(),
        },
    )


async def _get_transfer_history(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TransferHistoryArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    rows = await ctx.services.transfers.list(
        ctx.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        account_id=args.account_id,
        from_account_id=args.from_account_id,
        to_account_id=args.to_account_id,
        limit=args.limit,
        offset=args.offset,
    )
    names = await _accounts_map(ctx)
    return ToolResult(
        ok=True,
        data={
            "period": args.period,
            "transfers": [
                {
                    "id": t.id,
                    "from_account_id": t.from_account_id,
                    "from_account_name": _acc_name(names, t.from_account_id),
                    "to_account_id": t.to_account_id,
                    "to_account_name": _acc_name(names, t.to_account_id),
                    "amount": fmt_amount(t.amount),
                    "currency": t.currency,
                    "occurred_at": t.occurred_at.astimezone(_tz(user_tz)).isoformat(),
                }
                for t in rows
            ],
        },
    )


# --------------------------------------------------------------------------- #
# Saldos y resúmenes                                                          #
# --------------------------------------------------------------------------- #
async def _get_balance_overview(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    overview = await ctx.services.finance.balance_overview(ctx.user_id)
    return ToolResult(
        ok=True,
        data={
            "accounts": [
                {
                    "id": a["id"],
                    "name": a["name"],
                    "type": a["type"],
                    "currency": a["currency"],
                    "balance": fmt_amount(a["balance"]),
                    "is_default": a["is_default"],
                    "active": a["active"],
                }
                for a in overview["accounts"]
            ],
            "patrimonio": fmt_amount(overview["patrimonio"]),
            "liquido": fmt_amount(overview["liquido"]),
            "deuda_tarjetas": fmt_amount(overview["deuda_tarjetas"]),
        },
    )


async def _spending_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SpendingSummaryArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    category = normalize_category(args.category) if args.category else None
    summary = await ctx.services.finance.spending_summary(
        ctx.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        category=category,
        merchant=args.merchant,
        account_id=args.account_id,
    )
    return ToolResult(
        ok=True,
        data={
            "period": args.period,
            "total_gastos": fmt_amount(summary["total_gastos"]),
            "total_ingresos": fmt_amount(summary["total_ingresos"]),
            "por_categoria": [
                {"categoria": item["categoria"], "total": fmt_amount(item["total"])}
                for item in summary["por_categoria"]
            ],
            "por_comercio": [
                {"comercio": item["comercio"], "total": fmt_amount(item["total"])} for item in summary["por_comercio"]
            ],
            "mayor_categoria": summary["mayor_categoria"],
            "mayor_comercio": summary["mayor_comercio"],
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
FINANCE_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_account",
        description=(
            "Crea una cuenta (banco, ahorro, efectivo, tarjeta de CRÉDITO u otra). "
            "Solo se soporta EUR. La primera cuenta creada pasa a ser la principal automáticamente. "
            "Las tarjetas de DÉBITO no son una cuenta: están asociadas a su cuenta bancaria (type=bank)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre de la cuenta, p. ej. 'Cuenta Banco' o 'Visa'"},
                "type": {
                    "type": "string",
                    "enum": ["bank", "savings", "cash", "credit_card", "other"],
                    "description": "bank=cuenta bancaria/tarjeta débito, savings=ahorro, cash=efectivo, credit_card=tarjeta de crédito (deuda), other=otra",
                },
                "currency": {
                    "type": "string",
                    "default": "EUR",
                    "description": "Solo se admite EUR; cualquier otra moneda se rechaza.",
                },
                "initial_balance": {
                    "type": "string",
                    "default": "0",
                    "description": '"1000", "0" o "-350" (negativo solo para tarjetas de crédito)',
                },
                "sort_order": {"type": "integer", "default": 0},
            },
            "required": ["name", "type"],
        },
        handler=_create_account,
        args_model=AccountCreateArgs,
    ),
    ToolDefinition(
        name="list_accounts",
        description="Lista las cuentas del usuario (nombre, tipo, activa, principal).",
        input_schema={"type": "object", "properties": {}},
        handler=_list_accounts,
    ),
    ToolDefinition(
        name="update_account",
        description="Actualiza el nombre, tipo, moneda, saldo inicial u orden de una cuenta. Solo se admite EUR.",
        input_schema={
            "type": "object",
            "properties": {
                "account_id": {"type": "integer"},
                "name": {"type": "string", "default": None},
                "type": {
                    "type": "string",
                    "enum": ["bank", "savings", "cash", "credit_card", "other"],
                    "default": None,
                },
                "currency": {
                    "type": "string",
                    "default": None,
                    "description": "Solo EUR; cualquier otra moneda se rechaza.",
                },
                "initial_balance": {"type": "string", "default": None},
                "sort_order": {"type": "integer", "default": None},
            },
            "required": ["account_id"],
        },
        handler=_update_account,
        args_model=AccountUpdateArgs,
    ),
    ToolDefinition(
        name="set_default_account",
        description="Marca una cuenta como la principal (la que se usa por defecto al registrar movimientos).",
        input_schema={"type": "object", "properties": {"account_id": {"type": "integer"}}, "required": ["account_id"]},
        handler=_set_default_account,
        args_model=AccountIdArgs,
    ),
    ToolDefinition(
        name="deactivate_account",
        description="Desactiva una cuenta (deja de recibir movimientos; no se borra). Si era la principal, se desmarca.",
        input_schema={"type": "object", "properties": {"account_id": {"type": "integer"}}, "required": ["account_id"]},
        handler=_deactivate_account,
        args_model=AccountIdArgs,
    ),
    ToolDefinition(
        name="create_expense",
        description=(
            "Registra un GASTO (expense). Importes en lenguaje natural: '28,80', '35 euros', '19'. "
            "Comprar con tarjeta de CRÉDITO es un gasto sobre esa cuenta (saldo → deuda). "
            "Comprar con tarjeta de DÉBITO es un gasto sobre la cuenta bancaria asociada (type=bank). "
            "PAGAR una tarjeta NO es un gasto: usa create_transfer (banco → tarjeta)."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "Importe, p. ej. '28,80'"},
                "account_id": {"type": "integer", "default": None},
                "merchant": {
                    "type": "string",
                    "default": None,
                    "description": "Comercio: 'Mercadona', 'Netflix', 'gasolina'",
                },
                "category": {
                    "type": "string",
                    "default": None,
                    "description": "Alimentación, Transporte, Suscripciones...",
                },
                "occurred_at": {"type": "string", "default": None, "description": "Cuándo (por defecto ahora)"},
                "note": {"type": "string", "default": None},
            },
            "required": ["amount"],
        },
        handler=_create_expense,
        args_model=MovementArgs,
    ),
    ToolDefinition(
        name="create_income",
        description="Registra un INGRESO (nómina, venta, regalo...). Importe en lenguaje natural ('1500').",
        input_schema={
            "type": "object",
            "properties": {
                "amount": {"type": "string", "description": "Importe, p. ej. '1500'"},
                "account_id": {"type": "integer", "default": None},
                "merchant": {"type": "string", "default": None},
                "category": {"type": "string", "default": None},
                "occurred_at": {"type": "string", "default": None},
                "note": {"type": "string", "default": None},
            },
            "required": ["amount"],
        },
        handler=_create_income,
        args_model=MovementArgs,
    ),
    ToolDefinition(
        name="list_transactions",
        description="Lista los movimientos de gasto/ingreso de un periodo, con filtros opcionales.",
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": "hoy, esta semana, este mes, este año...",
                },
                "account_id": {"type": "integer", "default": None},
                "type": {"type": "string", "enum": ["expense", "income"], "default": None},
                "category": {"type": "string", "default": None},
                "merchant": {"type": "string", "default": None},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": [],
        },
        handler=_list_transactions,
        args_model=ListTransactionsArgs,
    ),
    ToolDefinition(
        name="create_transfer",
        description=(
            "Mueve dinero entre dos cuentas del mismo usuario (banco → ahorro, efectivo → banco...). "
            "NO es un gasto ni un ingreso. Pagar una tarjeta de crédito ('he pagado la Visa con 100 desde mi banco') "
            "es una transferencia desde la cuenta de pago hasta la tarjeta. Importe en lenguaje natural."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "from_account_id": {"type": "integer", "description": "Cuenta origen"},
                "to_account_id": {"type": "integer", "description": "Cuenta destino"},
                "amount": {"type": "string", "description": "Importe, p. ej. '300'"},
                "occurred_at": {"type": "string", "default": None},
                "note": {"type": "string", "default": None},
            },
            "required": ["from_account_id", "to_account_id", "amount"],
        },
        handler=_create_transfer,
        args_model=TransferCreateArgs,
    ),
    ToolDefinition(
        name="get_transfer_history",
        description="Historial de traspasos entre cuentas de un periodo.",
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes"},
                "account_id": {"type": "integer", "default": None},
                "from_account_id": {"type": "integer", "default": None},
                "to_account_id": {"type": "integer", "default": None},
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": [],
        },
        handler=_get_transfer_history,
        args_model=TransferHistoryArgs,
    ),
    ToolDefinition(
        name="get_balance_overview",
        description=(
            "Saldos actuales derivados de todas las cuentas, patrimonio, dinero líquido y deuda de tarjetas. "
            "'¿Cuánto dinero tengo?' → esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_balance_overview,
    ),
    ToolDefinition(
        name="spending_summary",
        description=(
            "Resumen de gastos (e ingresos) de un periodo: total, por categoría y por comercio, y mayor desglose. "
            "'¿Cuánto he gastado este mes?', '¿cuánto en alimentación?', '¿en qué he gastado más?' → esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes"},
                "category": {
                    "type": "string",
                    "default": None,
                    "description": "Filtra por categoría: alimentación, transporte...",
                },
                "merchant": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None},
            },
            "required": [],
        },
        handler=_spending_summary,
        args_model=SpendingSummaryArgs,
    ),
]
