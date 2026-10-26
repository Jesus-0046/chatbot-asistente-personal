"""Tests de las tools de vehículos (Fase 4) vía el registro.

Verifican: alta/listado/actualización, repostajes (crean su transacción),
mantenimientos con/sin coste, seguros informativos, caducidades, estados,
confirmación para desactivar y mensajes de error claros.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakeFuelEntryRepo,
    FakeTransactionRepo,
    FakeVehicleInsuranceRepo,
    FakeVehicleMaintenanceRepo,
    FakeVehicleRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.transactions import TransactionService
from asistente.domain.vehicles import VehicleService
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
    vehicles = VehicleService(
        FakeVehicleRepo(),
        FakeFuelEntryRepo(),
        FakeVehicleMaintenanceRepo(),
        FakeVehicleInsuranceRepo(),
        tx_svc,
    )
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        accounts=accounts,
        transactions=transactions,
        vehicles=vehicles,
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


async def _add_vehicle(services, user_id: int = _UID, *, name: str = "Auris", plate: str = "1234ABC") -> int:
    vehicle = await services.vehicles.create(
        user_id=user_id, name=name, brand="Toyota", model="Auris", plate=plate, year=2019
    )
    return vehicle.id


# --------------------------------------------------------------------------- #
# add_vehicle / list_vehicles / update_vehicle                                #
# --------------------------------------------------------------------------- #
async def test_add_vehicle_happy_path(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "add_vehicle", {"name": "Auris", "plate": "1234ABC", "year": 2019})

    assert res.ok
    assert res.data["name"] == "Auris"
    assert res.data["vehicle_id"] is not None


async def test_add_vehicle_odometro_invalido(registry, ctx) -> None:
    res = await _run(registry, ctx, "add_vehicle", {"name": "X", "current_odometer": "muchos"})

    assert not res.ok
    assert "númerico" in res.error or "número" in res.error


async def test_list_vehicles(registry, ctx, services) -> None:
    await _add_vehicle(services)
    res = await _run(registry, ctx, "list_vehicles", {})

    assert res.ok
    assert len(res.data["vehicles"]) == 1


async def test_update_vehicle(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    res = await _run(registry, ctx, "update_vehicle", {"vehicle_id": vid, "name": "Auris 2"})

    assert res.ok
    assert res.data["name"] == "Auris 2"


async def test_deactivate_vehicle_requiere_confirmacion(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)

    res = await _run(registry, ctx, "deactivate_vehicle", {"vehicle_id": vid}, confirmed=False)

    assert not res.ok
    assert res.needs_confirmation is True

    res = await _run(registry, ctx, "deactivate_vehicle", {"vehicle_id": vid}, confirmed=True)
    assert res.ok
    assert res.data["active"] is False


# --------------------------------------------------------------------------- #
# record_fuel                                                                 #
# --------------------------------------------------------------------------- #
async def test_record_fuel_crea_transaccion(registry, ctx, services) -> None:
    await _add_account(services)
    vid = await _add_vehicle(services)

    res = await _run(
        registry,
        ctx,
        "record_fuel",
        {"vehicle": vid, "amount": "45,80", "liters": "42", "occurred_at": "2026-10-05T12:00:00+02:00"},
    )

    assert res.ok
    assert res.data["transaction_id"] is not None
    assert res.data["amount"] == "45.80"
    txn = res.data["transaction_id"]
    stored = next(t for t in services.transactions._store if t.id == txn)
    assert stored.category == "Transporte"
    assert stored.merchant == "Combustible"


async def test_record_fuel_sin_cuentas_error_claro(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    res = await _run(registry, ctx, "record_fuel", {"vehicle": vid, "amount": "45"})

    assert not res.ok
    assert "cuenta" in res.error


async def test_record_fuel_importe_invalido(registry, ctx, services) -> None:
    await _add_account(services)
    vid = await _add_vehicle(services)
    res = await _run(registry, ctx, "record_fuel", {"vehicle": vid, "amount": "no sé"})

    assert not res.ok
    assert "importe" in res.error.lower() or "decimal" in res.error.lower()


async def test_record_fuel_por_nombre(registry, ctx, services) -> None:
    await _add_account(services)
    await _add_vehicle(services)
    res = await _run(registry, ctx, "record_fuel", {"vehicle": "Auris", "amount": "40"})

    assert res.ok


# --------------------------------------------------------------------------- #
# record_vehicle_maintenance                                                  #
# --------------------------------------------------------------------------- #
async def test_record_maintenance_sin_coste_no_crea_movimiento(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    res = await _run(
        registry,
        ctx,
        "record_vehicle_maintenance",
        {"vehicle": vid, "maintenance_type": "itv", "description": "Pasar la ITV", "next_due_at": "2026-12-01"},
    )

    assert res.ok
    assert res.data["transaction_id"] is None
    assert len(services.transactions._store) == 0


async def test_record_maintenance_con_coste_crea_movimiento(registry, ctx, services) -> None:
    await _add_account(services)
    vid = await _add_vehicle(services)
    res = await _run(
        registry,
        ctx,
        "record_vehicle_maintenance",
        {"vehicle": vid, "maintenance_type": "revision", "description": "Cambio de aceite", "cost": "95,50"},
    )

    assert res.ok
    assert res.data["cost"] == "95.50"
    assert res.data["transaction_id"] is not None


async def test_record_maintenance_tipo_invalido(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    res = await _run(registry, ctx, "record_vehicle_maintenance", {"vehicle": vid, "maintenance_type": "pintura"})

    assert not res.ok
    assert "Tipo de mantenimiento" in res.error


# --------------------------------------------------------------------------- #
# Seguros                                                                     #
# --------------------------------------------------------------------------- #
async def test_add_vehicle_insurance_informativo(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    res = await _run(
        registry,
        ctx,
        "add_vehicle_insurance",
        {
            "vehicle": vid,
            "company": "Mapfre",
            "end_date": "2027-03-15",
            "amount": "350",
            "billing_period": "yearly",
        },
    )

    assert res.ok
    assert res.data["company"] == "Mapfre"
    assert len(services.transactions._store) == 0


async def test_update_vehicle_insurance(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    ins = await services.vehicles.add_insurance(user_id=_UID, vehicle=await services.vehicles.get(_UID, vid))
    res = await _run(registry, ctx, "update_vehicle_insurance", {"insurance_id": ins.id, "company": "Allianz"})

    assert res.ok
    assert res.data["company"] == "Allianz"


# --------------------------------------------------------------------------- #
# Estados                                                                     #
# --------------------------------------------------------------------------- #
async def test_get_vehicle_status(registry, ctx, services) -> None:
    await _add_account(services)
    vid = await _add_vehicle(services)
    await services.vehicles.record_fuel(
        user_id=_UID, vehicle=await services.vehicles.get(_UID, vid), amount=Decimal("40")
    )

    res = await _run(registry, ctx, "get_vehicle_status", {"vehicle": vid})

    assert res.ok
    assert res.data["vehicle"]["id"] == vid
    assert len(res.data["last_fuel_entries"]) == 1


async def test_get_vehicle_expirations(registry, ctx, services) -> None:
    vid = await _add_vehicle(services)
    await services.vehicles.add_insurance(
        user_id=_UID,
        vehicle=await services.vehicles.get(_UID, vid),
        company="Mapfre",
        end_date=(datetime.now(UTC).date() + timedelta(days=10)),
    )
    res = await _run(registry, ctx, "get_vehicle_expirations", {})

    assert res.ok
    assert len(res.data["expirations"]) == 1


async def test_aislamiento_entre_usuarios(registry, ctx, services) -> None:
    await _add_account(services)
    await _add_vehicle(services)
    await _add_vehicle(services, user_id=999, name="Ajeno")

    res = await _run(registry, ctx, "list_vehicles", {})

    assert res.ok
    assert len(res.data["vehicles"]) == 1
