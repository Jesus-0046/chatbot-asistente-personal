"""Tests del servicio de dominio de vehículos (Fase 4).

Cubren: CRUD, validaciones, resolución por nombre, repostajes con transacción
asociada, mantenimientos con/sin coste, seguros (informativo), estado y
caducidades, y aislamiento entre usuarios.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from asistente.shared.errors import NotFoundError


async def _make_account(services, user_id: int = 1, *, name: str = "Banco") -> int:
    account = await services["accounts"].create(
        user_id=user_id, name=name, type="bank", initial_balance=Decimal("1000")
    )
    return account.id


async def _make_vehicle(services, user_id: int = 1, *, name: str = "Auris", plate: str = "1234ABC") -> int:
    vehicle = await services["vehicles"].create(
        user_id=user_id, name=name, brand="Toyota", model="Auris", plate=plate, year=2019
    )
    return vehicle.id


async def test_crear_vehiculo_feliz(services) -> None:
    vehicle = await services["vehicles"].create(
        user_id=1, name="Mi coche", brand="Toyota", model="Auris", plate="1234ABC", year=2019, fuel_type="gasolina"
    )

    assert vehicle.user_id == 1
    assert vehicle.name == "Mi coche"
    assert vehicle.active is True
    assert vehicle.current_odometer is None


async def test_nombre_vacio_rechazado(services) -> None:
    with pytest.raises(ValueError, match="vacío"):
        await services["vehicles"].create(user_id=1, name="   ")


async def test_anio_invalido_rechazado(services) -> None:
    with pytest.raises(ValueError, match="1900"):
        await services["vehicles"].create(user_id=1, name="Coche", year=1890)


async def test_odometro_negativo_rechazado(services) -> None:
    with pytest.raises(ValueError, match="odómetro"):
        await services["vehicles"].create(user_id=1, name="Coche", current_odometer=Decimal("-5"))


async def test_resolve_por_id_y_por_nombre(services) -> None:
    vid = await _make_vehicle(services)

    by_id = await services["vehicles"].resolve(1, vid)
    assert by_id.id == vid

    by_name = await services["vehicles"].resolve(1, "Auris")
    assert by_name.id == vid


async def test_resolve_nombre_ambiguo_pide_id(services) -> None:
    await _make_vehicle(services, name="Clio", plate="AAA")
    await _make_vehicle(services, name="Clio", plate="BBB")

    with pytest.raises(ValueError, match="ID"):
        await services["vehicles"].resolve(1, "Clio")


async def test_resolve_nombre_inexistente(services) -> None:
    with pytest.raises(NotFoundError, match="Clio"):
        await services["vehicles"].resolve(1, "Clio")


async def test_update_vehiculo(services) -> None:
    vid = await _make_vehicle(services)
    updated = await services["vehicles"].update(1, vid, name="Auris 2", current_odometer=Decimal("182000"))

    assert updated.name == "Auris 2"
    assert updated.current_odometer == Decimal("182000")


async def test_deactivate_vehicle_desaparece_de_list(services) -> None:
    vid = await _make_vehicle(services)
    await services["vehicles"].deactivate(1, vid)

    assert len(await services["vehicles"].list(1)) == 0
    with pytest.raises(ValueError, match="desactivado"):
        await services["vehicles"].deactivate(1, vid)


async def test_vehiculo_ajeno_no_accesible(services) -> None:
    vid = await _make_vehicle(services, user_id=1)

    with pytest.raises(NotFoundError):
        await services["vehicles"].get(2, vid)


async def test_record_fuel_crea_transaccion_y_avanza_odometro(services) -> None:
    await _make_account(services)
    vid = await _make_vehicle(services)

    result = await services["vehicles"].record_fuel(
        user_id=1,
        vehicle=await services["vehicles"].get(1, vid),
        amount=Decimal("45.80"),
        occurred_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        liters=Decimal("42.0"),
        odometer=Decimal("182000"),
        fuel_type="gasolina",
    )

    assert result["transaction_id"] is not None
    assert result["odometer_advanced"] is True
    txn = await services["transactions"].get(1, result["transaction_id"])
    assert txn.category == "Transporte"
    assert txn.merchant == "Combustible"
    assert txn.amount == Decimal("45.80")
    assert txn.occurred_at == datetime(2026, 9, 15, 10, 0, tzinfo=UTC)

    vehicle = await services["vehicles"].get(1, vid)
    assert vehicle.current_odometer == Decimal("182000")


async def test_record_fuel_no_retrocede_odometro(services) -> None:
    await _make_account(services)
    vid = await _make_vehicle(services)
    await services["vehicles"].record_fuel(
        user_id=1, vehicle=await services["vehicles"].get(1, vid), amount=Decimal("40"), odometer=Decimal("182000")
    )
    result = await services["vehicles"].record_fuel(
        user_id=1, vehicle=await services["vehicles"].get(1, vid), amount=Decimal("20"), odometer=Decimal("180000")
    )

    assert result["odometer_advanced"] is False
    vehicle = await services["vehicles"].get(1, vid)
    assert vehicle.current_odometer == Decimal("182000")


async def test_record_fuel_importe_no_positivo_rechazado(services) -> None:
    await _make_account(services)
    vid = await _make_vehicle(services)
    with pytest.raises(ValueError, match="mayor que cero"):
        await services["vehicles"].record_fuel(
            user_id=1, vehicle=await services["vehicles"].get(1, vid), amount=Decimal("0")
        )


async def test_record_fuel_sin_cuentas_error(services) -> None:
    vid = await _make_vehicle(services)
    with pytest.raises(Exception, match="cuenta"):
        await services["vehicles"].record_fuel(
            user_id=1, vehicle=await services["vehicles"].get(1, vid), amount=Decimal("40")
        )


async def test_record_maintenance_sin_coste_no_crea_transaccion(services) -> None:
    vid = await _make_vehicle(services)
    result = await services["vehicles"].record_maintenance(
        user_id=1,
        vehicle=await services["vehicles"].get(1, vid),
        maintenance_type="itv",
        description="Pasar la ITV",
        occurred_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )

    assert result["transaction_id"] is None
    assert len(await services["transactions"].list(1)) == 0


async def test_record_maintenance_con_coste_crea_transaccion(services) -> None:
    await _make_account(services)
    vid = await _make_vehicle(services)
    result = await services["vehicles"].record_maintenance(
        user_id=1,
        vehicle=await services["vehicles"].get(1, vid),
        maintenance_type="revision",
        description="Cambio de aceite",
        occurred_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        cost=Decimal("95.50"),
        next_due_odometer=Decimal("190000"),
    )

    assert result["transaction_id"] is not None
    txn = await services["transactions"].get(1, result["transaction_id"])
    assert txn.category == "Transporte"
    assert txn.merchant == "Mantenimiento coche"


async def test_record_maintenance_tipo_invalido_rechazado(services) -> None:
    vid = await _make_vehicle(services)
    with pytest.raises(ValueError, match="Tipo de mantenimiento"):
        await services["vehicles"].record_maintenance(
            user_id=1, vehicle=await services["vehicles"].get(1, vid), maintenance_type="pintura"
        )


async def test_add_insurance_informativo(services) -> None:
    vid = await _make_vehicle(services)
    insurance = await services["vehicles"].add_insurance(
        user_id=1,
        vehicle=await services["vehicles"].get(1, vid),
        company="Mapfre",
        policy_number="P-123",
        insurance_type="a todo riesgo",
        amount=Decimal("350"),
        billing_period="yearly",
        start_date=datetime(2026, 1, 1).date(),
        end_date=datetime(2026, 12, 31).date(),
    )

    assert insurance.company == "Mapfre"
    assert insurance.amount == Decimal("350")
    assert insurance.active is True
    assert len(await services["transactions"].list(1)) == 0


async def test_add_insurance_fechas_invalidas_rechazado(services) -> None:
    vid = await _make_vehicle(services)
    with pytest.raises(ValueError, match="fin"):
        await services["vehicles"].add_insurance(
            user_id=1,
            vehicle=await services["vehicles"].get(1, vid),
            start_date=datetime(2026, 12, 31).date(),
            end_date=datetime(2026, 1, 1).date(),
        )


async def test_update_insurance(services) -> None:
    vid = await _make_vehicle(services)
    insurance = await services["vehicles"].add_insurance(
        user_id=1, vehicle=await services["vehicles"].get(1, vid), company="Mapfre"
    )
    updated = await services["vehicles"].update_insurance(1, insurance.id, company="Allianz", active=False)

    assert updated.company == "Allianz"
    assert updated.active is False


async def test_get_status_incluye_gasto_y_proximas(services) -> None:
    await _make_account(services)
    vid = await _make_vehicle(services)
    vehicle = await services["vehicles"].get(1, vid)
    now = datetime.now(UTC)
    await services["vehicles"].record_fuel(
        user_id=1, vehicle=vehicle, amount=Decimal("45.80"), occurred_at=now - timedelta(days=5)
    )
    await services["vehicles"].record_maintenance(
        user_id=1,
        vehicle=vehicle,
        maintenance_type="revision",
        description="Cambio de aceite",
        occurred_at=now - timedelta(days=5),
        cost=Decimal("95.50"),
        next_due_at=now + timedelta(days=10),
    )

    status = await services["vehicles"].get_status(1, vid)

    assert status["vehicle"].id == vid
    assert len(status["fuel_entries"]) == 1
    assert status["spend_last_30_days"]["total"] == Decimal("141.30")
    assert len(status["upcoming"]) == 1


async def test_get_expirations_ventana_30_dias(services) -> None:
    vid = await _make_vehicle(services)
    vehicle = await services["vehicles"].get(1, vid)
    await services["vehicles"].add_insurance(
        user_id=1,
        vehicle=vehicle,
        company="Mapfre",
        end_date=datetime.now(UTC).date() + timedelta(days=10),
    )
    await services["vehicles"].add_insurance(
        user_id=1,
        vehicle=vehicle,
        company="Otro",
        end_date=datetime.now(UTC).date() + timedelta(days=400),
    )

    data = await services["vehicles"].get_expirations(1)

    assert data["horizon_days"] == 30
    assert len(data["expirations"]) == 1
    companies = [i.company for i in data["expirations"][0]["insurance"]]
    assert companies == ["Mapfre"]


async def test_fuel_ajeno_aislado_por_usuario(services) -> None:
    await _make_account(services, user_id=1, name="Banco A")
    await _make_account(services, user_id=2, name="Banco B")
    vid = await _make_vehicle(services, user_id=1)
    vehicle = await services["vehicles"].get(1, vid)
    await services["vehicles"].record_fuel(user_id=1, vehicle=vehicle, amount=Decimal("40"))

    entries = await services["vehicle_maint_repo"].list_for_user(1)
    assert entries == []
    entries_2 = await services["fuel_repo"].list_for_user(2)
    assert entries_2 == []

    with pytest.raises(NotFoundError):
        await services["vehicles"].get_status(2, vid)
