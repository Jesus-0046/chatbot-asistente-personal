"""Matriz de regresión IDOR (Fase 8).

Para cada superficie sensible se verifica que un ID ajeno (de otro usuario)
y un ID inexistente producen EXACTAMENTE el mismo fallo `NotFoundError` con el
mismo mensaje ("... no existe o no te pertenece"): el backend no actúa como
oráculo de existencia y nunca expone datos de otro usuario, ni en lecturas ni
en escrituras. Las operaciones se ejercitan contra repositorios reales sobre
SQLite en memoria conectada.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from tests.unit.helpers import make_connected_services

from asistente.shared.errors import NotFoundError

BOGUS = 10**9  # ID que nunca existirá en ningún esquema


def _d(days: int, hour: int = 12) -> datetime:
    return datetime.now(UTC).replace(hour=hour, minute=0, second=0, microsecond=0) + timedelta(days=days)


Op = Callable[[Any, int], Awaitable[Any]]
Builder = Callable[[Any], Awaitable[int]]


async def _make_foreign_user(services: Any) -> int:
    """Crea la cuenta y una persona para el usuario 2; devuelve el id de la cuenta.

    Nota: SQLite no traduce el índice parcial `postgresql_where` de `accounts`
    (`UNIQUE(user_id) WHERE is_default`) y lo crea como `UNIQUE(user_id)`
    simple, por lo que en los tests de repos reales sobre SQLite cada usuario
    solo puede tener UNA cuenta. PostgreSQL (producción) sí permite varias.
    """
    account = await services.accounts.create(
        user_id=2, name="Banco de Ana", type="bank", initial_balance=Decimal("1000")
    )
    await services.people.create(user_id=2, name="Ana")
    return account.id


async def _arm_cases(services: Any) -> list[tuple[str, int, Op]]:
    """Devuelve (nombre, id_ajeno, op) para todas las superficies de la matriz."""
    account_id = await _make_foreign_user(services)

    txn = await services.transactions.create(user_id=2, type="expense", amount=Decimal("10"), account_id=account_id)
    goal = await services.savings_goals.create_goal(user_id=2, name="Fondo", target_amount=Decimal("500"))
    person = await services.people.create(user_id=2, name="Luis")
    debt = await services.debts.create(user_id=2, person_id=person.id, direction="owed_to_user", amount=Decimal("30"))
    budget = await services.budgets.create(user_id=2, name="Compras", amount=Decimal("100"), year=2026, month=9)
    vehicle = await services.vehicles.create(user_id=2, name="Coche de Ana")
    trip = await services.trips.create(user_id=2, name="Viaje de Ana", start_at=_d(30))

    cases: list[tuple[str, int, Op]] = [
        ("accounts.get", account_id, lambda s, fid: s.accounts.get(1, fid)),
        ("transactions.get", txn.id, lambda s, fid: s.transactions.get(1, fid)),
        (
            "transfers.create_cuenta_ajena",
            account_id,
            lambda s, fid: s.transfers.create(
                user_id=1, from_account_id=fid, to_account_id=BOGUS + 1, amount=Decimal("10")
            ),
        ),
        (
            "savings.record_contribution",
            goal.id,
            lambda s, fid: s.savings_goals.record_contribution(user_id=1, goal_id=fid, amount=Decimal("10")),
        ),
        ("savings.cancel_goal", goal.id, lambda s, fid: s.savings_goals.cancel_goal(1, fid)),
        ("debts.settle", debt.id, lambda s, fid: s.debts.settle(1, fid)),
        ("budgets.get_status", budget.id, lambda s, fid: s.budgets.get_status(1, fid)),
        ("vehicles.get_status", vehicle.id, lambda s, fid: s.vehicles.get_status(1, fid)),
        ("trips.get", trip.id, lambda s, fid: s.trips.get(1, fid)),
    ]
    return cases


async def _capture_error(op: Op, services: Any, fid: int) -> NotFoundError:
    with pytest.raises(NotFoundError) as exc_info:
        await op(services, fid)
    return exc_info.value


@pytest.mark.asyncio
async def test_id_ajeno_e_inexistente_fallan_igual_sin_filtrar_existencia() -> None:
    services = await make_connected_services()
    cases = await _arm_cases(services)

    assert cases, "la matriz no debe quedar vacía"

    for name, foreign_id, op in cases:
        err_ajeno = await _capture_error(op, services, foreign_id)
        err_inexistente = await _capture_error(op, services, BOGUS + 2)

        assert str(err_ajeno) == str(err_inexistente), (
            f"[{name}] el mensaje para ID ajeno difiere del de ID inexistente → oráculo de existencia"
        )
        assert "no existe o no te pertenece" in str(err_ajeno), (
            f"[{name}] mensaje inesperado para ID ajeno: {err_ajeno}"
        )
