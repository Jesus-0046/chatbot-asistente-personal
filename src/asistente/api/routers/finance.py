"""Finanzas (Fase 10.0): /api/v1/accounts, /transactions, /transfers,
/recurring, /planned, /subscriptions, /finance/summary y /finance/overview."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize, serialize_many

router = APIRouter(tags=["finance"])

_MAX_LIST = 200


class AccountCreate(BaseModel):
    name: str
    type: str
    currency: str = "EUR"
    initial_balance: Decimal = Decimal("0.00")
    sort_order: int = 0
    set_default: bool = False


class AccountPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    sort_order: int | None = None


class TransactionCreate(BaseModel):
    type: str
    amount: Decimal
    currency: str = "EUR"
    account_id: int | None = None
    merchant: str | None = None
    category: str | None = None
    occurred_at: datetime | None = None
    note: str | None = None


class TransferCreate(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: Decimal
    currency: str = "EUR"
    occurred_at: datetime | None = None
    note: str | None = None


class RecurringCreate(BaseModel):
    type: str
    title: str
    amount: Decimal
    currency: str = "EUR"
    account_id: int | None = None
    merchant: str | None = None
    category: str | None = None
    frequency: str
    interval: int = 1
    day_of_month: int | None = None
    month_of_year: int | None = None
    start_date: date | None = None
    next_due_at: datetime | None = None
    end_date: date | None = None
    timezone: str = "Europe/Madrid"
    paused: bool = False


_RECURRING_PATCH_FIELDS = (
    "title",
    "amount",
    "currency",
    "account_id",
    "merchant",
    "category",
    "frequency",
    "interval",
    "day_of_month",
    "month_of_year",
    "start_date",
    "next_due_at",
    "end_date",
    "timezone",
    "paused",
)


class PlannedCreate(BaseModel):
    type: str
    amount: Decimal
    currency: str = "EUR"
    account_id: int | None = None
    merchant: str | None = None
    category: str | None = None
    planned_at: datetime
    title: str | None = None
    note: str | None = None


class PlannedConvert(BaseModel):
    occurred_at: datetime | None = None
    merchant: str | None = None
    note: str | None = None


class SubscriptionCreate(BaseModel):
    name: str
    amount: Decimal
    currency: str = "EUR"
    billing_period: str
    account_id: int | None = None
    category: str | None = None
    next_billing_date: datetime | None = None
    timezone: str = "Europe/Madrid"


_SUBSCRIPTION_PATCH_FIELDS = ("name", "amount", "currency", "billing_period", "account_id", "category", "timezone")


# --------------------------------------------------------------------------- #
# Cuentas                                                                      #
# --------------------------------------------------------------------------- #
@router.get("/accounts")
async def list_accounts(
    current: CurrentUserDeps,
    services: ServicesDeps,
    only_active: bool = False,
) -> dict:
    accounts = await services.accounts.list_for_user(current.user_id, only_active=only_active)
    return {"items": serialize_many(accounts)}


@router.post("/accounts", status_code=status.HTTP_201_CREATED)
async def create_account(body: AccountCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    account = await services.accounts.create(
        user_id=current.user_id,
        name=body.name,
        type=body.type,
        currency=body.currency,
        initial_balance=body.initial_balance,
        sort_order=body.sort_order,
        set_default=body.set_default,
    )
    return serialize(account)


@router.get("/accounts/{account_id}")
async def get_account(account_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.accounts.get(current.user_id, account_id))


@router.patch("/accounts/{account_id}")
async def update_account(account_id: int, body: AccountPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.accounts.update(current.user_id, account_id, **fields))


@router.post("/accounts/{account_id}/default")
async def set_default_account(account_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.accounts.set_default(current.user_id, account_id))


@router.post("/accounts/{account_id}/deactivate")
async def deactivate_account(account_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.accounts.deactivate(current.user_id, account_id))


# --------------------------------------------------------------------------- #
# Movimientos                                                                  #
# --------------------------------------------------------------------------- #
@router.get("/transactions")
async def list_transactions(
    current: CurrentUserDeps,
    services: ServicesDeps,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    account_id: int | None = None,
    transaction_type: str | None = Query(default=None, alias="type"),
    category: str | None = None,
    merchant: str | None = None,
    limit: int = Query(default=50, ge=1, le=_MAX_LIST),
    offset: int = Query(default=0, ge=0),
) -> dict:
    items = await services.transactions.list(
        current.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        account_id=account_id,
        transaction_type=transaction_type,
        category=category,
        merchant=merchant,
        limit=limit,
        offset=offset,
    )
    return {"items": serialize_many(items), "count": len(items)}


@router.post("/transactions", status_code=status.HTTP_201_CREATED)
async def create_transaction(body: TransactionCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    tx = await services.transactions.create(
        user_id=current.user_id,
        type=body.type,
        amount=body.amount,
        currency=body.currency,
        account_id=body.account_id,
        merchant=body.merchant,
        category=body.category,
        occurred_at=body.occurred_at,
        note=body.note,
    )
    return serialize(tx)


@router.get("/transactions/{transaction_id}")
async def get_transaction(transaction_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.transactions.get(current.user_id, transaction_id))


# --------------------------------------------------------------------------- #
# Transferencias                                                               #
# --------------------------------------------------------------------------- #
@router.get("/transfers")
async def list_transfers(
    current: CurrentUserDeps,
    services: ServicesDeps,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    account_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=_MAX_LIST),
    offset: int = Query(default=0, ge=0),
) -> dict:
    items = await services.transfers.list(
        current.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        account_id=account_id,
        limit=limit,
        offset=offset,
    )
    return {"items": serialize_many(items), "count": len(items)}


@router.post("/transfers", status_code=status.HTTP_201_CREATED)
async def create_transfer(body: TransferCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    transfer = await services.transfers.create(
        user_id=current.user_id,
        from_account_id=body.from_account_id,
        to_account_id=body.to_account_id,
        amount=body.amount,
        currency=body.currency,
        occurred_at=body.occurred_at,
        note=body.note,
    )
    return serialize(transfer)


@router.get("/transfers/{transfer_id}")
async def get_transfer(transfer_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.transfers.get(current.user_id, transfer_id))


# --------------------------------------------------------------------------- #
# Recurrencias                                                                 #
# --------------------------------------------------------------------------- #
@router.get("/recurring")
async def list_recurring(
    current: CurrentUserDeps,
    services: ServicesDeps,
    active_only: bool = False,
    include_paused: bool = True,
    limit: int = Query(default=100, ge=1, le=_MAX_LIST),
) -> dict:
    items = await services.recurring.list(
        current.user_id, active_only=active_only, include_paused=include_paused, limit=limit
    )
    return {"items": serialize_many(items)}


@router.post("/recurring", status_code=status.HTTP_201_CREATED)
async def create_recurring(body: RecurringCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    item = await services.recurring.create(
        user_id=current.user_id,
        type=body.type,
        title=body.title,
        amount=body.amount,
        currency=body.currency,
        account_id=body.account_id,
        merchant=body.merchant,
        category=body.category,
        frequency=body.frequency,
        interval=body.interval,
        day_of_month=body.day_of_month,
        month_of_year=body.month_of_year,
        start_date=body.start_date,
        next_due_at=body.next_due_at,
        end_date=body.end_date,
        timezone=body.timezone,
        paused=body.paused,
    )
    return serialize(item)


@router.get("/recurring/{recurring_id}")
async def get_recurring(recurring_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.recurring.get(current.user_id, recurring_id))


@router.patch("/recurring/{recurring_id}")
async def update_recurring(recurring_id: int, body: dict, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = {key: value for key, value in body.items() if key in _RECURRING_PATCH_FIELDS}
    return serialize(await services.recurring.update(current.user_id, recurring_id, **fields))


# --------------------------------------------------------------------------- #
# Pagos planificados                                                           #
# --------------------------------------------------------------------------- #
@router.get("/planned")
async def list_planned(
    current: CurrentUserDeps,
    services: ServicesDeps,
    status_filter: str | None = Query(default=None, alias="status"),
    transaction_type: str | None = Query(default=None, alias="type"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIST),
) -> dict:
    items = await services.planned.list_planned(
        current.user_id, status=status_filter, transaction_type=transaction_type, limit=limit
    )
    return {"items": serialize_many(items)}


@router.get("/planned/upcoming")
async def planned_upcoming(
    current: CurrentUserDeps,
    services: ServicesDeps,
    days: int = Query(default=30, ge=1, le=90),
    transaction_type: str | None = None,
) -> dict:
    items = await services.planned.upcoming(current.user_id, days=days, transaction_type=transaction_type)
    return {"items": serialize_many(items)}


@router.post("/planned", status_code=status.HTTP_201_CREATED)
async def create_planned(body: PlannedCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    item = await services.planned.create(
        user_id=current.user_id,
        type=body.type,
        amount=body.amount,
        currency=body.currency,
        account_id=body.account_id,
        merchant=body.merchant,
        category=body.category,
        planned_at=body.planned_at,
        title=body.title,
        note=body.note,
    )
    return serialize(item)


@router.post("/planned/{planned_id}/convert")
async def convert_planned(
    planned_id: int, body: PlannedConvert, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    item = await services.planned.convert(
        current.user_id,
        planned_id,
        occurred_at=body.occurred_at,
        merchant=body.merchant,
        note=body.note,
    )
    return serialize(item)


@router.post("/planned/{planned_id}/cancel")
async def cancel_planned(planned_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.planned.cancel(current.user_id, planned_id))


# --------------------------------------------------------------------------- #
# Suscripciones                                                                #
# --------------------------------------------------------------------------- #
@router.get("/subscriptions")
async def list_subscriptions(
    current: CurrentUserDeps,
    services: ServicesDeps,
    active_only: bool | None = None,
) -> dict:
    items = await services.subscriptions.list(current.user_id, active_only=active_only)
    return {"items": serialize_many(items)}


@router.post("/subscriptions", status_code=status.HTTP_201_CREATED)
async def create_subscription(body: SubscriptionCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    item = await services.subscriptions.create(
        user_id=current.user_id,
        name=body.name,
        amount=body.amount,
        currency=body.currency,
        billing_period=body.billing_period,
        account_id=body.account_id,
        category=body.category,
        next_billing_date=body.next_billing_date,
        timezone=body.timezone,
    )
    return serialize(item)


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(
    subscription_id: int, body: dict, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    fields = {key: value for key, value in body.items() if key in _SUBSCRIPTION_PATCH_FIELDS}
    return serialize(await services.subscriptions.update(current.user_id, subscription_id, **fields))


# --------------------------------------------------------------------------- #
# Resúmenes financieros                                                        #
# --------------------------------------------------------------------------- #
@router.get("/finance/summary")
async def finance_summary(
    current: CurrentUserDeps,
    services: ServicesDeps,
    start_utc: datetime | None = None,
    end_utc: datetime | None = None,
    category: str | None = None,
    merchant: str | None = None,
    account_id: int | None = None,
) -> dict:
    data = await services.finance.spending_summary(
        current.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        category=category,
        merchant=merchant,
        account_id=account_id,
    )
    return dict(deep_json(data))


@router.get("/finance/overview")
async def finance_overview(
    current: CurrentUserDeps,
    services: ServicesDeps,
    timezone: str = "Europe/Madrid",
) -> dict:
    data = await services.intelligence.financial_overview(current.user_id, timezone=timezone)
    return dict(deep_json(data))
