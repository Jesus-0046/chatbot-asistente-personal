"""Tests de las tools de la vivienda (Fase 4) vía el registro.

Verifican: CRUD, mantenimientos pending/done, transacción única con coste,
próximos mantenimientos, confirmación para desactivar y aislamiento.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakeHomeMaintenanceRepo,
    FakeHomeRepo,
    FakeTransactionRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.homes import HomeService
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
        return None


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    tx_svc = TransactionService(transactions, accounts)
    homes = HomeService(FakeHomeRepo(), FakeHomeMaintenanceRepo(), tx_svc)
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        accounts=accounts,
        transactions=transactions,
        homes=homes,
    )


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=_UID, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict, *, confirmed: bool = True):
    return await registry.execute(ctx, name, args, confirmed=confirmed)


async def _add_account(services, user_id: int = _UID, *, name: str = "Banco") -> int:
    account = await services.accounts.create(
        user_id=user_id, name=name, type="bank", initial_balance=Decimal("1000"), is_default=True
    )
    return account.id


async def _add_home(services, user_id: int = _UID, *, name: str = "Mi piso") -> int:
    home = await services.homes.create(user_id=user_id, name=name, home_type="piso")
    return home.id


async def test_add_home_happy_path(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "add_home", {"name": "Mi piso", "home_type": "piso"})

    assert res.ok
    assert res.data["name"] == "Mi piso"


async def test_add_home_tipo_invalido(registry, ctx) -> None:
    res = await _run(registry, ctx, "add_home", {"name": "X", "home_type": "castillo"})

    assert not res.ok
    assert "Tipo de vivienda" in res.error


async def test_list_homes_y_update(registry, ctx, services) -> None:
    hid = await _add_home(services)
    listed = await _run(registry, ctx, "list_homes", {})
    assert listed.ok
    assert [h["id"] for h in listed.data["homes"]] == [hid]

    updated = await _run(registry, ctx, "update_home", {"home_id": hid, "address": "Plaza Mayor 1"})
    assert updated.ok


async def test_deactivate_home_requiere_confirmacion(registry, ctx, services) -> None:
    hid = await _add_home(services)

    res = await _run(registry, ctx, "deactivate_home", {"home_id": hid}, confirmed=False)
    assert not res.ok
    assert res.needs_confirmation is True

    res = await _run(registry, ctx, "deactivate_home", {"home_id": hid}, confirmed=True)
    assert res.ok
    assert res.data["active"] is False


async def test_record_maintenance_pendiente(registry, ctx, services) -> None:
    hid = await _add_home(services)
    res = await _run(
        registry,
        ctx,
        "record_home_maintenance",
        {"home": hid, "title": "Revisar la caldera", "next_due_at": "2026-12-01"},
    )

    assert res.ok
    assert res.data["status"] == "pending"
    assert res.data["transaction_id"] is None
    assert len(services.transactions._store) == 0


async def test_record_maintenance_con_coste(registry, ctx, services) -> None:
    await _add_account(services)
    hid = await _add_home(services)
    res = await _run(
        registry,
        ctx,
        "record_home_maintenance",
        {"home": hid, "title": "Reparar fuga", "cost": "250"},
    )

    assert res.ok
    assert res.data["status"] == "done"
    assert res.data["transaction_id"] is not None
    stored = services.transactions._store[0]
    assert stored.category == "Vivienda"
    assert stored.merchant == "Mantenimiento casa"


async def test_record_maintenance_sin_titulo(registry, ctx, services) -> None:
    hid = await _add_home(services)
    res = await _run(registry, ctx, "record_home_maintenance", {"home": hid, "title": "  "})

    assert not res.ok
    assert "título" in res.error


async def test_get_home_status(registry, ctx, services) -> None:
    await _add_account(services)
    hid = await _add_home(services)
    home = await services.homes.get(_UID, hid)
    await services.homes.record_maintenance(
        user_id=_UID, home=home, title="Pendiente", next_due_at=datetime.now(UTC) + timedelta(days=20)
    )
    await services.homes.record_maintenance(user_id=_UID, home=home, title="Hecha", cost=Decimal("120"))

    res = await _run(registry, ctx, "get_home_status", {"home": hid})

    assert res.ok
    assert len(res.data["pending"]) == 1
    assert res.data["spend_last_30_days"]["total"] == "120.00"


async def test_get_home_upcoming(registry, ctx, services) -> None:
    hid = await _add_home(services)
    home = await services.homes.get(_UID, hid)
    await services.homes.record_maintenance(
        user_id=_UID, home=home, title="Próximo", next_due_at=datetime.now(UTC) + timedelta(days=10)
    )
    res = await _run(registry, ctx, "get_home_upcoming", {})

    assert res.ok
    assert [m["title"] for m in res.data["upcoming"]] == ["Próximo"]


async def test_aislamiento_entre_usuarios(registry, ctx, services) -> None:
    await _add_home(services)
    await _add_home(services, user_id=999, name="Ajeno")

    res = await _run(registry, ctx, "list_homes", {})

    assert res.ok
    assert len(res.data["homes"]) == 1
