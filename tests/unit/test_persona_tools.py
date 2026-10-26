"""Tests de las tools de personas/deudas/gastos compartidos (Fase 3.4).

Se ejecutan contra un bundle de servicios construido con los fakes de
`tests.unit.fakes` (idéntico al del fixture de dominio) y el registro real
(create_tool_registry). Incluye además un test del ROLLBACK atómico del
gasto compartido sobre SQLite real (aiosqlite).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from tests.unit.fakes import (
    FakeDebtRepo,
    FakePersonRepo,
    FakeSharedExpensePaymentRepo,
    FakeSharedExpenseRepo,
    FakeSharedExpenseShareRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.debts import DebtService
from asistente.domain.people import PersonService
from asistente.domain.shared_expenses import SharedExpenseService
from asistente.persistence.models import Base, SharedExpense, SharedExpensePayment, SharedExpenseShare
from asistente.persistence.repositories import (
    PersonRepository,
    SharedExpensePaymentRepository,
    SharedExpenseRepository,
    SharedExpenseShareRepository,
)
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None


class _Bundle:
    """Servicios con forma de `Services` (atributos) sobre los fakes."""

    def __init__(self) -> None:
        self.settings = make_settings()
        self.users = FakeUsers()
        people_repo = FakePersonRepo()
        self.people = PersonService(people_repo)
        self.debts = DebtService(FakeDebtRepo(), people_repo)
        self.shared_expenses = SharedExpenseService(
            FakeSharedExpenseRepo(),
            FakeSharedExpenseShareRepo(store={}),
            FakeSharedExpensePaymentRepo(store={}),
            people_repo,
        )


@pytest.fixture
def bundle():
    return _Bundle()


@pytest.fixture
def registry():
    return create_tool_registry()


def _ctx(bundle) -> ToolContext:
    return ToolContext(user_id=1, services=bundle)


async def _add_person(registry, ctx, name: str) -> int:
    result = await registry.execute(ctx, "add_person", {"name": name})
    assert result.ok is True, result.error
    return result.data["id"]


# --------------------------------------------------------------------------- #
# Personas                                                                    #
# --------------------------------------------------------------------------- #
async def test_add_y_list_people_roundtrip(registry, bundle) -> None:
    ctx = _ctx(bundle)
    pid = await _add_person(registry, ctx, "Juan")

    result = await registry.execute(ctx, "list_people", {})
    assert result.ok is True
    assert [p["id"] for p in result.data["people"]] == [pid]
    assert result.data["people"][0]["name"] == "Juan"


async def test_deactivate_person_requiere_confirmacion(registry, bundle) -> None:
    ctx = _ctx(bundle)
    pid = await _add_person(registry, ctx, "Juan")

    result = await registry.execute(ctx, "deactivate_person", {"person_id": pid})
    assert result.ok is False
    assert result.needs_confirmation is True

    result = await registry.execute(ctx, "deactivate_person", {"person_id": pid}, confirmed=True)
    assert result.ok is True
    assert result.data["active"] is False


# --------------------------------------------------------------------------- #
# Deudas                                                                      #
# --------------------------------------------------------------------------- #
async def test_record_debt_y_get_debts_status(registry, bundle) -> None:
    ctx = _ctx(bundle)
    pid = await _add_person(registry, ctx, "Juan")

    result = await registry.execute(
        ctx,
        "record_debt",
        {"person": pid, "direction": "owed_to_user", "amount": "50", "reason": "alquiler"},
    )
    assert result.ok is True, result.error
    assert result.data["description"] == "Registrado: Juan te debe 50.00 € (ID 1)."

    status = await registry.execute(ctx, "get_debts_status", {})
    assert status.ok is True
    assert status.data["total_owed_to_me"] == Decimal("50.00")


# --------------------------------------------------------------------------- #
# Fase 3.4.1: get_debts_status(person) — filtro por persona                    #
# --------------------------------------------------------------------------- #
async def test_get_debts_status_filtra_por_persona_id(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    maria = await _add_person(registry, ctx, "María")
    await registry.execute(ctx, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "10"})
    await registry.execute(ctx, "record_debt", {"person": maria, "direction": "user_owes", "amount": "20"})

    status = await registry.execute(ctx, "get_debts_status", {"person": juan})

    assert status.ok is True, status.error
    assert len(status.data["debts"]) == 1
    assert status.data["debts"][0]["person_id"] == juan
    assert status.data["debts"][0]["remaining"] == Decimal("10.00")
    assert status.data["total_owed_to_me"] == Decimal("10.00")
    assert status.data["total_i_owe"] == Decimal("0.00")


async def test_get_debts_status_filtra_por_nombre_normalizado(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    await _add_person(registry, ctx, "María")
    await registry.execute(ctx, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "10"})
    await registry.execute(ctx, "record_debt", {"person": "maria", "direction": "user_owes", "amount": "20"})

    status = await registry.execute(ctx, "get_debts_status", {"person": "JUAN"})

    assert status.ok is True, status.error
    assert len(status.data["debts"]) == 1
    assert status.data["debts"][0]["person"] == "Juan"


async def test_get_debts_status_excluye_a_las_otras_personas_y_usuarios(registry, bundle) -> None:
    ctx1 = _ctx(bundle)
    juan = await _add_person(registry, ctx1, "Juan")
    maria = await _add_person(registry, ctx1, "María")
    await registry.execute(ctx1, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "10"})
    await registry.execute(ctx1, "record_debt", {"person": maria, "direction": "owed_to_user", "amount": "20"})

    # Otro usuario con su propia persona y deuda.
    ctx2 = ToolContext(user_id=2, services=bundle)
    ana = await _add_person(registry, ctx2, "Ana")
    await registry.execute(ctx2, "record_debt", {"person": ana, "direction": "owed_to_user", "amount": "999"})

    # Filtrado por Juan: solo su deuda, ni María ni Ana.
    status = await registry.execute(ctx1, "get_debts_status", {"person": juan})
    assert status.ok is True, status.error
    assert [r["person_id"] for r in status.data["debts"]] == [juan]

    # Filtrado por Ana desde el user 1: la persona ni existe para él → error.
    status_ajeno = await registry.execute(ctx1, "get_debts_status", {"person": ana})
    assert status_ajeno.ok is False

    # Sin filtro: el user 1 no ve nada del user 2.
    consolidated = await registry.execute(ctx1, "get_debts_status", {})
    assert consolidated.ok is True
    assert consolidated.data["total_owed_to_me"] == Decimal("30.00")


async def test_get_debts_status_persona_inexistente_error(registry, bundle) -> None:
    ctx = _ctx(bundle)
    status = await registry.execute(ctx, "get_debts_status", {"person": 999})
    assert status.ok is False
    assert "no existe" in (status.error or "").lower()


async def test_get_debts_status_nombre_ambiguo_pide_id(registry, bundle) -> None:
    ctx = _ctx(bundle)
    await _add_person(registry, ctx, "Juan")
    await _add_person(registry, ctx, "juan")

    status = await registry.execute(ctx, "get_debts_status", {"person": "juan"})

    assert status.ok is False
    assert "ID" in (status.error or "")


async def test_get_debts_status_persona_inactiva_por_nombre_error(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    await bundle.people.deactivate(1, juan)

    status = await registry.execute(ctx, "get_debts_status", {"person": "Juan"})
    assert status.ok is False
    assert "activa" in (status.error or "")


async def test_get_debts_status_inactiva_por_id_sigue_mostrando_deudas(registry, bundle) -> None:
    """Coherente con el módulo: la resolución por ID no filtra `active` (solo la
    de búsqueda por nombre), así que el histórico de una persona inactiva sigue
    siendo consultable por id."""
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    await registry.execute(ctx, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "10"})
    await bundle.people.deactivate(1, juan)

    status = await registry.execute(ctx, "get_debts_status", {"person": juan})

    assert status.ok is True, status.error
    assert [r["person_id"] for r in status.data["debts"]] == [juan]


async def test_get_debts_status_con_persona_solo_abiertas(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    d1 = await registry.execute(ctx, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "10"})
    await registry.execute(ctx, "record_debt", {"person": juan, "direction": "owed_to_user", "amount": "20"})
    await registry.execute(ctx, "settle_debt", {"debt_id": d1.data["debt_id"]}, confirmed=True)

    status = await registry.execute(ctx, "get_debts_status", {"person": juan})

    assert status.ok is True, status.error
    assert len(status.data["debts"]) == 1
    assert status.data["debts"][0]["remaining"] == Decimal("20.00")
    assert status.data["total_owed_to_me"] == Decimal("20.00")


async def test_record_debt_por_nombre_y_moneda_normalizada(registry, bundle) -> None:
    ctx = _ctx(bundle)
    await _add_person(registry, ctx, "José")

    result = await registry.execute(
        ctx,
        "record_debt",
        {"person": "jose", "direction": "user_owes", "amount": "30,50"},
    )
    assert result.ok is True, result.error
    assert result.data["amount"] == "30.50"
    assert result.data["currency"] == "EUR"


async def test_record_debt_persona_desconocida_error_claro(registry, bundle) -> None:
    ctx = _ctx(bundle)
    result = await registry.execute(
        ctx,
        "record_debt",
        {"person": "Inexistente", "direction": "owed_to_user", "amount": "10"},
    )
    assert result.ok is False
    assert "no se encontró" in (result.error or "").lower()


async def test_settle_debt_requiere_confirmacion_y_liquida(registry, bundle) -> None:
    ctx = _ctx(bundle)
    pid = await _add_person(registry, ctx, "Juan")
    created = await registry.execute(ctx, "record_debt", {"person": pid, "direction": "owed_to_user", "amount": "50"})
    debt_id = created.data["debt_id"]

    blocked = await registry.execute(ctx, "settle_debt", {"debt_id": debt_id})
    assert blocked.needs_confirmation is True

    settled = await registry.execute(ctx, "settle_debt", {"debt_id": debt_id}, confirmed=True)
    assert settled.ok is True, settled.error
    assert settled.data["status"] == "settled"
    assert settled.data["remaining_amount"] == "0.00"


async def test_settle_debt_ya_liquidada_error(registry, bundle) -> None:
    ctx = _ctx(bundle)
    pid = await _add_person(registry, ctx, "Juan")
    created = await registry.execute(ctx, "record_debt", {"person": pid, "direction": "owed_to_user", "amount": "50"})
    debt_id = created.data["debt_id"]
    await registry.execute(ctx, "settle_debt", {"debt_id": debt_id}, confirmed=True)

    result = await registry.execute(ctx, "settle_debt", {"debt_id": debt_id}, confirmed=True)
    assert result.ok is False
    assert "liquidada" in (result.error or "")


# --------------------------------------------------------------------------- #
# Gastos compartidos                                                          #
# --------------------------------------------------------------------------- #
async def test_create_shared_expense_banda_completa(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    await _add_person(registry, ctx, "María")

    result = await registry.execute(
        ctx,
        "create_shared_expense",
        {
            "title": "Cena del sábado",
            "total_amount": "84",
            "participant": [
                {"is_user": True},
                {"person_id": juan},
                {"person_name": "maria"},
            ],
            "paid_by": "user",
        },
    )
    assert result.ok is True, result.error
    assert result.data["total_amount"] == "84.00"
    expense_id = result.data["expense_id"]

    status = await registry.execute(ctx, "get_shared_expense_status", {"period": "este mes"})
    assert status.ok is True, status.error
    expenses = status.data["expenses"]
    assert len(expenses) == 1
    expense = expenses[0]
    assert expense["expense_id"] == expense_id
    # El usuario pagó 84 y reparte 28 → le deben 56.
    assert expense["user_balance"] == Decimal("-56.00")


async def test_create_shared_expense_nombre_ambiguo_error(registry, bundle) -> None:
    ctx = _ctx(bundle)
    await _add_person(registry, ctx, "Juan")
    await _add_person(registry, ctx, "juan")

    result = await registry.execute(
        ctx,
        "create_shared_expense",
        {
            "title": "Cena",
            "total_amount": "40",
            "participant": [{"is_user": True}, {"person_name": "juan"}],
            "paid_by": "user",
        },
    )
    assert result.ok is False
    assert "ID" in (result.error or "")


async def test_cancel_shared_expense_requiere_confirmacion(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    created = await registry.execute(
        ctx,
        "create_shared_expense",
        {
            "title": "Cena",
            "total_amount": "40",
            "participant": [{"is_user": True}, {"person_id": juan}],
            "paid_by": "user",
        },
    )
    expense_id = created.data["expense_id"]

    blocked = await registry.execute(ctx, "cancel_shared_expense", {"expense_id": expense_id})
    assert blocked.needs_confirmation is True

    cancelled = await registry.execute(ctx, "cancel_shared_expense", {"expense_id": expense_id}, confirmed=True)
    assert cancelled.ok is True
    assert cancelled.data["status"] == "cancelled"


async def test_create_shared_expense_pagos_que_no_suman_error(registry, bundle) -> None:
    ctx = _ctx(bundle)
    juan = await _add_person(registry, ctx, "Juan")
    result = await registry.execute(
        ctx,
        "create_shared_expense",
        {
            "title": "Cena",
            "total_amount": "84",
            "participant": [{"is_user": True}, {"person_id": juan}],
            "paid_by": "user",
            "payments": [{"who": "user", "amount": "40"}],
        },
    )
    assert result.ok is False
    assert "suma" in (result.error or "")
    assert "84" in (result.error or "")


# --------------------------------------------------------------------------- #
# Atomicidad del gasto compartido (BD real, aiosqlite)                        #
# --------------------------------------------------------------------------- #
@pytest.fixture
async def _shared_db():
    from asistente.persistence.db import Database

    database = Database(url="sqlite+aiosqlite:///:memory:")
    database.connect()
    async with database.engine.begin() as conn:
        needed = [
            Base.metadata.tables[name]
            for name in [
                "people",
                "shared_expenses",
                "shared_expense_shares",
                "shared_expense_payments",
            ]
        ]
        await conn.run_sync(Base.metadata.create_all, tables=needed)
    yield database
    await database.close()


async def test_create_shared_expense_rollback_atomico_bd_real(_shared_db) -> None:
    """ROLLBACK total de SharedExpenseService.create (expense + shares + payments).

    Fuerza una excepción en el `before_commit` (el ÚNICO commit del flujo, el
    de `Database.transaction()`). Debe quedar TODO sin insertar.
    """
    db = _shared_db
    people_repo = PersonRepository(db)
    people_svc = PersonService(people_repo)
    expense_repo = SharedExpenseRepository(db)
    share_repo = SharedExpenseShareRepository(db)
    payment_repo = SharedExpensePaymentRepository(db)
    service = SharedExpenseService(expense_repo, share_repo, payment_repo, people_repo)

    juan = await people_svc.create(user_id=1, name="Juan")
    maria = await people_svc.create(user_id=1, name="María")

    def _forzar_error(session) -> None:
        raise RuntimeError("fallo forzado antes del commit exterior")

    event.listen(Session, "before_commit", _forzar_error, once=True)
    try:
        with pytest.raises(RuntimeError, match="fallo forzado"):
            await service.create(
                user_id=1,
                title="Cena",
                total_amount=Decimal("84.00"),
                participants=[
                    {"is_user": True},
                    {"person_id": juan.id},
                    {"person_id": maria.id},
                ],
                paid_by="user",
                occurred_at=datetime(2026, 9, 15, 20, 0, tzinfo=UTC),
            )
    finally:
        event.remove(Session, "before_commit", _forzar_error)

    async with db.session() as session:
        expenses = (await session.execute(select(func.count()).select_from(SharedExpense))).scalar_one()
        shares = (await session.execute(select(func.count()).select_from(SharedExpenseShare))).scalar_one()
        payments = (await session.execute(select(func.count()).select_from(SharedExpensePayment))).scalar_one()
    assert expenses == 0
    assert shares == 0
    assert payments == 0
