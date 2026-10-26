"""Tests de las tools de pagos planificados (Fase 3.3) vía el registro.

Verifican creación manual, sensible defaults, flujo confirmar→convertir,
cancelación con gating, listado/upcoming con generación perezosa
y errores de dominio claros.
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


# --------------------------------------------------------------------------- #
# create_planned_transaction                                                    #
# --------------------------------------------------------------------------- #
async def test_create_planned_happy_path(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_planned_transaction",
        {
            "type": "expense",
            "amount": "28,80",
            "merchant": "Netflix",
            "planned_at": "2026-10-15T12:00:00+02:00",
        },
    )

    assert res.ok
    data = res.data
    assert data["merchant"] == "Netflix"
    assert data["amount"] == "28.80"
    assert data["status"] == "planned"


async def test_create_planned_fecha_naive_rechazada(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_planned_transaction",
        {"type": "expense", "amount": "10", "planned_at": "2026-10-15T12:00:00"},
    )

    assert not res.ok
    assert "zona horaria" in res.error


async def test_create_planned_sin_cuentas_mensaje_claro(registry, ctx) -> None:
    res = await _run(
        registry,
        ctx,
        "create_planned_transaction",
        {"type": "expense", "amount": "10", "planned_at": "2026-10-15T12:00:00+02:00"},
    )

    assert not res.ok
    assert "ninguna cuenta" in res.error


async def test_create_planned_importe_invalido_rechazado(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(
        registry,
        ctx,
        "create_planned_transaction",
        {"type": "expense", "amount": "abc", "planned_at": "2026-10-15T12:00:00+02:00"},
    )

    assert not res.ok
    assert "importe" in res.error.lower()


async def test_create_planned_crea_dos_y_respeta_aislamiento(registry, ctx, services) -> None:
    await _add_account(services, user_id=_UID, name="Banco 7")
    await _add_account(services, user_id=99, name="Banco B")

    res = await _run(
        registry,
        ctx,
        "create_planned_transaction",
        {"type": "income", "amount": "1500", "planned_at": "2026-11-01T12:00:00+02:00"},
    )

    assert res.ok
    rows_u1 = await services.planned.list_planned(_UID)
    rows_u2 = await services.planned.list_planned(99)
    assert len(rows_u1) == 1
    assert rows_u2 == []


# --------------------------------------------------------------------------- #
# confirm_planned_transaction                                                   #
# --------------------------------------------------------------------------- #
async def _seed_planned(services, user_id: int = _UID) -> int:
    account_id = await _add_account(services, user_id=user_id)
    planned = await services.planned.create(
        user_id=user_id,
        type="expense",
        amount=Decimal("28.80"),
        merchant="Netflix",
        planned_at=datetime(2026, 10, 15, 12, 0, tzinfo=UTC),
        account_id=account_id,
    )
    return planned.id


async def test_confirmar_convierte_a_transaccion(registry, ctx, services) -> None:
    planned_id = await _seed_planned(services)

    res = await _run(registry, ctx, "confirm_planned_transaction", {"planned_id": planned_id})

    assert res.ok
    assert res.data["status"] == "converted"
    assert res.data["converted_transaction_id"] is not None
    posted = [t for t in services.transactions._store if t.id == res.data["converted_transaction_id"]]
    assert len(posted) == 1
    assert posted[0].status == "posted"


async def test_confirmar_transaccion_inexistente_mensaje_claro(registry, ctx, services) -> None:
    await _add_account(services)

    res = await _run(registry, ctx, "confirm_planned_transaction", {"planned_id": 999})

    assert not res.ok
    assert "no existe" in res.error


async def test_confirmar_no_pide_confirmacion_de_registro(registry, ctx, services) -> None:
    definition = registry.get("confirm_planned_transaction")
    assert definition is not None
    assert definition.requires_confirmation is False


# --------------------------------------------------------------------------- #
# cancel_planned_transaction                                                    #
# --------------------------------------------------------------------------- #
async def test_cancelar_planificado_con_gating(registry, ctx, services) -> None:
    planned_id = await _seed_planned(services)

    definition = registry.get("cancel_planned_transaction")
    assert definition.requires_confirmation is True

    bloqueado = await _run(registry, ctx, "cancel_planned_transaction", {"planned_id": planned_id})
    assert not bloqueado.ok
    assert bloqueado.needs_confirmation is True

    res = await registry.execute(ctx, "cancel_planned_transaction", {"planned_id": planned_id}, confirmed=True)
    assert res.ok
    assert res.data["status"] == "cancelled"


async def test_cancelar_planificado_ya_convertido_mensaje_claro(registry, ctx, services) -> None:
    planned_id = await _seed_planned(services)
    await services.planned.convert(_UID, planned_id)

    res = await registry.execute(ctx, "cancel_planned_transaction", {"planned_id": planned_id}, confirmed=True)

    assert not res.ok
    assert "pendiente" in res.error or "cancelar" in res.error


# --------------------------------------------------------------------------- #
# list / upcoming                                                              #
# --------------------------------------------------------------------------- #
async def test_list_planificados_por_periodo(registry, ctx, services) -> None:
    await _seed_planned(services)

    res = await _run(registry, ctx, "list_planned_transactions", {"period": "este mes"})

    assert res.ok
    assert len(res.data["planned_transactions"]) == 1
    assert res.data["planned_transactions"][0]["merchant"] == "Netflix"


async def test_list_planificados_filtro_status(registry, ctx, services) -> None:
    planned_id = await _seed_planned(services)
    await services.planned.convert(_UID, planned_id)

    res = await _run(registry, ctx, "list_planned_transactions", {"period": "este mes", "status": "converted"})

    assert res.ok
    assert len(res.data["planned_transactions"]) == 1


async def test_upcoming_genera_y_devuelve_por_tipo(registry, ctx, services) -> None:
    await _add_account(services)
    await services.recurring.create(
        user_id=_UID,
        type="expense",
        title="Alquiler",
        amount=Decimal("850"),
        frequency="monthly",
        day_of_month=1,
        next_due_at=datetime.now(UTC) - timedelta(days=5),
    )

    res = await _run(registry, ctx, "get_upcoming_payments", {"days": 30})

    assert res.ok
    assert len(res.data["upcoming_payments"]) >= 1
    assert res.data["upcoming_payments"][0]["title"] == "Alquiler"


async def test_upcoming_income_genera_solo_ingresos(registry, ctx, services) -> None:
    await _add_account(services)
    await services.recurring.create(
        user_id=_UID,
        type="income",
        title="Nómina",
        amount=Decimal("2000"),
        frequency="monthly",
        day_of_month=1,
        next_due_at=datetime.now(UTC) - timedelta(days=5),
    )

    res = await _run(registry, ctx, "get_upcoming_income", {"days": 30})

    assert res.ok
    assert len(res.data["upcoming_income"]) >= 1
    assert res.data["upcoming_income"][0]["title"] == "Nómina"
