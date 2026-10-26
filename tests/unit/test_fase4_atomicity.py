"""Tests de atomicidad Fase 4 con BD real (SQLite en memoria).

Verifican el invariante central de Fase 4: cada repostaje y cada mantenimiento
con coste crea EXACTAMENTE UNA transacción de gasto insertada en la MISMA
transacción atómica que el detalle. Si el commit exterior falla, no debe
quedar ningún movimiento huérfano ni detalle.

Se usa el listener `before_commit` de SQLAlchemy para forzar el fallo justo
en el ÚNICO commit del flujo correcto (patrón de `test_planned.py`).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from tests.unit.helpers import make_services

from asistente.persistence.db import Base, Database
from asistente.persistence.models import (
    Account,
    FuelEntry,
    Home,
    HomeMaintenance,
    Transaction,
    Vehicle,
    VehicleInsurance,
    VehicleMaintenance,
)

_TABLES = [
    Account.__table__,
    Transaction.__table__,
    FuelEntry.__table__,
    VehicleMaintenance.__table__,
    VehicleInsurance.__table__,
    Vehicle.__table__,
    HomeMaintenance.__table__,
    Home.__table__,
]


@pytest.fixture
async def services():
    database = Database(url="sqlite+aiosqlite:///:memory:")
    database.connect()
    async with database.engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_TABLES))
    services = make_services(database)
    yield services
    await database.close()


async def _make_account(services, user_id: int = 1) -> int:
    account = await services.accounts.create(
        user_id=user_id, name="Banco", type="bank", initial_balance=Decimal("1000")
    )
    return account.id


def _forzar_error(session) -> None:
    raise RuntimeError("fallo forzado antes del commit exterior")


async def _assert_sin_huella(services, vid: int) -> None:
    assert len(await services.transactions._tx.list_for_user(1)) == 0
    assert len(await services.vehicles._fuel.list_for_user(1)) == 0
    assert len(await services.vehicles._maintenance.list_for_user(1)) == 0
    vehicle = await services.vehicles._vehicles.find_one(1, vid)
    assert vehicle is not None
    assert vehicle.current_odometer is None


async def test_record_fuel_rollback_total_sin_huérfanos(services) -> None:
    await _make_account(services)
    vehicle = await services.vehicles.create(user_id=1, name="Auris")

    def _forzar(session) -> None:
        raise RuntimeError("fallo forzado")

    sa.event.listen(Session, "before_commit", _forzar, once=True)
    try:
        with pytest.raises(RuntimeError, match="fallo forzado"):
            await services.vehicles.record_fuel(
                user_id=1, vehicle=vehicle, amount=Decimal("45.80"), odometer=Decimal("182000")
            )
    finally:
        sa.event.remove(Session, "before_commit", _forzar)

    await _assert_sin_huella(services, vehicle.id)


async def test_record_maintenance_coste_rollback_total_sin_huérfanos(services) -> None:
    await _make_account(services)
    vehicle = await services.vehicles.create(user_id=1, name="Auris")

    sa.event.listen(Session, "before_commit", _forzar_error, once=True)
    try:
        with pytest.raises(RuntimeError, match="fallo forzado"):
            await services.vehicles.record_maintenance(
                user_id=1, vehicle=vehicle, maintenance_type="revision", cost=Decimal("95.50")
            )
    finally:
        sa.event.remove(Session, "before_commit", _forzar_error)

    await _assert_sin_huella(services, vehicle.id)


async def test_home_maintenance_rollback_total_sin_huérfanos(services) -> None:
    await _make_account(services)
    home = await services.homes.create(user_id=1, name="Mi piso")

    sa.event.listen(Session, "before_commit", _forzar_error, once=True)
    try:
        with pytest.raises(RuntimeError, match="fallo forzado"):
            await services.homes.record_maintenance(user_id=1, home=home, title="Reparar fuga", cost=Decimal("250"))
    finally:
        sa.event.remove(Session, "before_commit", _forzar_error)

    assert len(await services.transactions._tx.list_for_user(1)) == 0
    assert len(await services.homes._maintenance.list_for_user(1)) == 0


async def test_un_detalle_un_movimiento_exactamente(services) -> None:
    await _make_account(services)
    vehicle = await services.vehicles.create(user_id=1, name="Auris")
    home = await services.homes.create(user_id=1, name="Mi piso")

    fuel = await services.vehicles.record_fuel(user_id=1, vehicle=vehicle, amount=Decimal("45.80"))
    maint = await services.vehicles.record_maintenance(
        user_id=1, vehicle=vehicle, maintenance_type="revision", cost=Decimal("95.50")
    )
    home_maint = await services.homes.record_maintenance(
        user_id=1, home=home, title="Reparar fuga", cost=Decimal("250")
    )

    txns = await services.transactions._tx.list_for_user(1)
    assert len(txns) == 3

    ids = {t.id for t in txns}
    assert ids == {fuel["transaction_id"], maint["transaction_id"], home_maint["transaction_id"]}
    id_by_merchant = {t.merchant: t.id for t in txns}
    assert id_by_merchant["Combustible"] == fuel["transaction_id"]
    assert id_by_merchant["Mantenimiento coche"] == maint["transaction_id"]
    assert id_by_merchant["Mantenimiento casa"] == home_maint["transaction_id"]

    fuel_entries = await services.vehicles._fuel.list_for_user(1)
    assert len(fuel_entries) == 1
    assert fuel_entries[0].transaction_id == fuel["transaction_id"]
    assert fuel_entries[0].amount == Decimal("45.80")

    vehicle_maints = await services.vehicles._maintenance.list_for_user(1)
    assert len(vehicle_maints) == 1
    assert vehicle_maints[0].transaction_id == maint["transaction_id"]

    home_maints = await services.homes._maintenance.list_for_user(1)
    assert len(home_maints) == 1
    assert home_maints[0].transaction_id == home_maint["transaction_id"]
