"""Tests de las tools de suscripciones (Fase 3.3) vía el registro.

Verifican creación (recurrencia espejo), update sincronizado, cancelación
con gating (sub + recurrencia + planificados), listado con generación perezosa,
errores de dominio claros y aislamiento entre usuarios.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakePlannedTransactionRepo,
    FakeRecurringTransactionRepo,
    FakeSubscriptionRepo,
    FakeTransactionRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.planned import PlannedTransactionService
from asistente.domain.recurring import RecurringTransactionService
from asistente.domain.subscriptions import SubscriptionService
from asistente.domain.transactions import TransactionService
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry

_UID = 7


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None  # → default_timezone ("Europe/Madrid")


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    planned_store: list = []
    recurring_store: list = []
    recurring_repo = FakeRecurringTransactionRepo(recurring_store=recurring_store, planned_store=planned_store)
    planned_repo = FakePlannedTransactionRepo(planned_store=planned_store, tx_store=transactions._store)
    subscription_repo = FakeSubscriptionRepo()
    tx_svc = TransactionService(transactions, accounts)
    recurring = RecurringTransactionService(recurring_repo, planned_repo, accounts, subscription_repo)
    planned = PlannedTransactionService(planned_repo, tx_svc)
    subscriptions = SubscriptionService(subscription_repo, recurring)
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        accounts=accounts,
        transactions=transactions,
        recurring=recurring,
        recurring_store=recurring_store,
        planned=planned,
        subscriptions=subscriptions,
        planned_store=planned_store,
    )


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=_UID, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict):
    return await registry.execute(ctx, name, args)


async def _add_account(services, user_id: int = _UID, *, name: str = "Banco") -> int:
    account = await services.accounts.create(user_id=user_id, name=name, type="bank", initial_balance=Decimal("1000"))
    return account.id


# --------------------------------------------------------------------------- #
# create_subscription                                                           #
# --------------------------------------------------------------------------- #
async def test_create_sub_happy_path(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_subscription",
        {
            "name": "Netflix",
            "amount": "13,99",
            "billing_period": "monthly",
            "category": "Ocio",
            "next_billing_date": "2026-10-05T12:00:00+02:00",
        },
    )

    assert res.ok
    data = res.data
    assert data["name"] == "Netflix"
    assert data["amount"] == "13.99"
    assert data["currency"] == "EUR"
    assert data["billing_period"] == "monthly"
    assert data["category"] == "Ocio"
    assert data["active"] is True


async def test_create_sub_sin_cuentas_mensaje_claro(registry, ctx) -> None:
    res = await _run(
        registry,
        ctx,
        "create_subscription",
        {"name": "Spotify", "amount": "9,99", "billing_period": "monthly"},
    )

    assert not res.ok
    assert "ninguna cuenta" in res.error


async def test_create_sub_fecha_naive_rechazada(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_subscription",
        {"name": "X", "amount": "10", "billing_period": "monthly", "next_billing_date": "2026-10-05T12:00:00"},
    )

    assert not res.ok
    assert "zona horaria" in res.error


async def test_create_sub_sin_fecha_ancla_hoy(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_subscription",
        {"name": "Luz", "amount": "40", "billing_period": "monthly"},
    )

    assert res.ok
    assert res.data["next_billing_date"] is not None
    rec = await services.recurring.get(_UID, 1)
    assert rec is not None


# --------------------------------------------------------------------------- #
# update_subscription                                                           #
# --------------------------------------------------------------------------- #
async def _seed_sub(services, user_id: int = _UID, *, name: str = "Netflix") -> int:
    await _add_account(services, user_id=user_id)
    sub = await services.subscriptions.create(
        user_id=user_id,
        name=name,
        amount=Decimal("13.99"),
        billing_period="monthly",
        next_billing_date=datetime(2026, 10, 5, 12, 0, tzinfo=UTC),
    )
    return sub.id


async def test_update_sub_nombre_y_importe(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)

    res = await _run(
        registry,
        ctx,
        "update_subscription",
        {"subscription_id": sub_id, "name": "Netflix Premium", "amount": "19,99"},
    )

    assert res.ok
    assert res.data["name"] == "Netflix Premium"
    assert res.data["amount"] == "19.99"


async def test_update_sub_periodo_sincroniza_recurrencia(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)
    due_before = datetime.now(UTC) + timedelta(days=15)
    await services.recurring.update(_UID, 1, next_due_at=due_before)

    res = await _run(
        registry,
        ctx,
        "update_subscription",
        {"subscription_id": sub_id, "billing_period": "yearly"},
    )

    assert res.ok
    assert res.data["billing_period"] == "yearly"
    rec = await services.recurring.get(_UID, 1)
    assert rec.frequency == "yearly"


async def test_update_sub_siguiente_fecha_explicita_ajusta_anclas(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)

    res = await _run(
        registry,
        ctx,
        "update_subscription",
        {"subscription_id": sub_id, "billing_period": "yearly", "next_billing_date": "2027-03-15T12:00:00+01:00"},
    )

    assert res.ok
    rec = await services.recurring.get(_UID, 1)
    assert rec.day_of_month == 15
    assert rec.month_of_year == 3


async def test_update_sub_sin_campos_mensaje_claro(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)

    res = await _run(registry, ctx, "update_subscription", {"subscription_id": sub_id})

    assert not res.ok
    assert "nada que actualizar" in res.error


# --------------------------------------------------------------------------- #
# cancel_subscription                                                           #
# --------------------------------------------------------------------------- #
async def test_cancel_sub_gating(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)

    definition = registry.get("cancel_subscription")
    assert definition.requires_confirmation is True

    bloqueado = await _run(registry, ctx, "cancel_subscription", {"subscription_id": sub_id})
    assert not bloqueado.ok
    assert bloqueado.needs_confirmation is True

    res = await registry.execute(ctx, "cancel_subscription", {"subscription_id": sub_id}, confirmed=True)
    assert res.ok
    assert res.data["active"] is False
    rec = await services.recurring.get(_UID, 1)
    assert rec.active is False


# --------------------------------------------------------------------------- #
# list_subscriptions                                                            #
# --------------------------------------------------------------------------- #
async def test_list_subscriptions_generacion_perezosa(registry, ctx, services) -> None:
    accounts_id = await _add_account(services)
    await services.subscriptions.create(
        user_id=_UID,
        name="Netflix",
        amount=Decimal("13.99"),
        billing_period="daily",
        next_billing_date=datetime.now(UTC) - timedelta(days=5),
        account_id=accounts_id,
    )

    res = await _run(registry, ctx, "list_subscriptions", {})

    assert res.ok
    assert len(res.data["subscriptions"]) == 1
    assert res.data["subscriptions"][0]["name"] == "Netflix"
    assert len(services.planned_store) >= 1


async def test_list_subscriptions_active_only(registry, ctx, services) -> None:
    sub_id = await _seed_sub(services)

    all_res = await _run(registry, ctx, "list_subscriptions", {})
    await services.subscriptions.cancel(_UID, sub_id)
    active_res = await _run(registry, ctx, "list_subscriptions", {"active_only": True})

    assert len(all_res.data["subscriptions"]) == 1
    assert active_res.data["subscriptions"] == []
