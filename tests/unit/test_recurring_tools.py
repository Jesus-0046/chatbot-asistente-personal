"""Tests de las tools de recurrencias (Fase 3.3) vía el registro.

Usan un `ToolContext` con los servicios reales de dominio sobre fakes en
memoria y verifican happy paths, validaciones puras, errores de dominio claros
y el gating de confirmación para las acciones sensibles.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
        return None  # → default_timezone del settings ("Europe/Madrid")


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    planned_store: list = []
    recurring_repo = FakeRecurringTransactionRepo(recurring_store=[], planned_store=planned_store)
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


async def _add_accounts(services):
    await _add_account(services)
    extra = await _add_account(services, name="Ahorro")
    return extra


# --------------------------------------------------------------------------- #
# create_recurring_transaction                                                  #
# --------------------------------------------------------------------------- #
async def test_create_recurring_happy_path(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {
            "title": "Alquiler",
            "type": "expense",
            "amount": "850",
            "frequency": "monthly",
            "day_of_month": 1,
        },
    )

    assert res.ok
    data = res.data
    assert data["title"] == "Alquiler"
    assert data["amount"] == "850.00"
    assert data["frequency"] == "monthly"
    assert data["day_of_month"] == 1
    assert data["active"] is True


async def test_create_recurring_normaliza_importe_y_moneda(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {
            "title": "Nómina",
            "type": "income",
            "amount": "1.500,50",
            "currency": "eur",
            "frequency": "monthly",
            "day_of_month": 30,
        },
    )

    assert res.ok
    assert res.data["amount"] == "1500.50"
    assert res.data["currency"] == "EUR"


async def test_create_recurring_importe_invalido_rechazado(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {"title": "X", "type": "expense", "amount": "-5", "frequency": "daily"},
    )

    assert not res.ok
    assert "importe" in res.error.lower() or "mayor" in res.error.lower()


async def test_create_recurring_mensual_sin_ancla_mensaje_claro(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {"title": "X", "type": "expense", "amount": "10", "frequency": "monthly"},
    )

    assert not res.ok
    assert "día ancla" in res.error or "fecha de inicio" in res.error


async def test_create_recurring_sin_cuentas_mensaje_claro(registry, ctx) -> None:
    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {"title": "X", "type": "expense", "amount": "10", "frequency": "daily"},
    )

    assert not res.ok
    assert "ninguna cuenta" in res.error


async def test_create_recurring_dia_fuera_de_rango_rechazado(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {"title": "X", "type": "expense", "amount": "10", "frequency": "monthly", "day_of_month": 32},
    )

    assert not res.ok
    assert res.error  # validación pydantic: día fuera de rango


async def test_create_recurring_aislamiento_entre_usuarios(registry, ctx, services) -> None:
    await _add_account(services, user_id=_UID)
    await _add_account(services, user_id=99, name="Banco B")

    res = await _run(
        registry,
        ctx,
        "create_recurring_transaction",
        {"title": "Luz", "type": "expense", "amount": "40", "frequency": "monthly", "day_of_month": 5},
    )

    assert res.ok
    rows_u1 = await services.recurring.list(_UID)
    rows_u2 = await services.recurring.list(99)
    assert len(rows_u1) == 1
    assert rows_u2 == []


# --------------------------------------------------------------------------- #
# update_recurring_transaction                                                  #
# --------------------------------------------------------------------------- #
async def _seed_recurring(services, user_id=_UID) -> int:
    await _add_account(services, user_id=user_id)
    rec = await services.recurring.create(
        user_id=user_id,
        type="expense",
        title="Alquiler",
        amount=Decimal("850"),
        frequency="monthly",
        day_of_month=1,
        next_due_at=datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    return rec.id


async def test_update_recurring_titulo_y_importe(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)

    res = await _run(
        registry,
        ctx,
        "update_recurring_transaction",
        {"recurring_id": rec_id, "title": "Alquiler 2027", "amount": "900"},
    )

    assert res.ok
    assert res.data["title"] == "Alquiler 2027"
    assert res.data["amount"] == "900.00"


async def test_update_recurring_sin_campos_mensaje_claro(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)

    res = await _run(registry, ctx, "update_recurring_transaction", {"recurring_id": rec_id})

    assert not res.ok
    assert "nada que actualizar" in res.error


async def test_update_recurring_inexistente_mensaje_claro(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(registry, ctx, "update_recurring_transaction", {"recurring_id": 999, "title": "Nada"})

    assert not res.ok
    assert "no existe" in res.error


# --------------------------------------------------------------------------- #
# pause / resume / cancel                                                       #
# --------------------------------------------------------------------------- #
async def test_pause_recurring(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)

    res = await _run(registry, ctx, "pause_recurring_transaction", {"recurring_id": rec_id})

    assert res.ok
    assert res.data["paused"] is True


async def test_resume_recurring(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)
    await services.recurring.pause(_UID, rec_id)

    res = await _run(registry, ctx, "resume_recurring_transaction", {"recurring_id": rec_id})

    assert res.ok
    assert res.data["paused"] is False


async def test_cancel_recurring_y_gating_de_confirmacion(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)

    definition = registry.get("cancel_recurring_transaction")
    assert definition is not None
    assert definition.requires_confirmation is True

    bloqueado = await _run(registry, ctx, "cancel_recurring_transaction", {"recurring_id": rec_id})
    assert not bloqueado.ok
    assert bloqueado.needs_confirmation is True

    res = await registry.execute(ctx, "cancel_recurring_transaction", {"recurring_id": rec_id}, confirmed=True)

    assert res.ok
    assert res.data["active"] is False
    assert res.data["cancelled_planned"] == 0


async def test_list_recurrings_genera_y_agrupa(registry, ctx, services) -> None:
    await _seed_recurring(services)

    res = await _run(registry, ctx, "list_recurring_transactions", {})

    assert res.ok
    assert len(res.data["recurring_transactions"]) == 1
    assert res.data["recurring_transactions"][0]["title"] == "Alquiler"


async def test_list_recurring_activas_solo(registry, ctx, services) -> None:
    rec_id = await _seed_recurring(services)
    res_all = await _run(registry, ctx, "list_recurring_transactions", {"active_only": False})
    await services.recurring.cancel(_UID, rec_id)

    res = await _run(registry, ctx, "list_recurring_transactions", {"active_only": True})

    assert res.ok
    assert len(res_all.data["recurring_transactions"]) == 1
    assert res.data["recurring_transactions"] == []
