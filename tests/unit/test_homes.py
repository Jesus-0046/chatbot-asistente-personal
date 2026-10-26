"""Tests del servicio de dominio de la vivienda (Fase 4).

Cubren: CRUD, tipos de mantenimiento, estados pending/done, transacción única
para mantenimientos con coste, próximos, gasto de 30 días y aislamiento.
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


async def _make_home(services, user_id: int = 1, *, name: str = "Mi piso", home_type: str = "piso") -> int:
    home = await services["homes"].create(user_id=user_id, name=name, home_type=home_type, address="Calle Falsa 123")
    return home.id


async def test_crear_vivienda_feliz(services) -> None:
    home = await services["homes"].create(user_id=1, name="Mi piso", home_type="piso")

    assert home.user_id == 1
    assert home.name == "Mi piso"
    assert home.active is True


async def test_nombre_vacio_rechazado(services) -> None:
    with pytest.raises(ValueError, match="vacío"):
        await services["homes"].create(user_id=1, name="  ")


async def test_tipo_vivienda_invalido_rechazado(services) -> None:
    with pytest.raises(ValueError, match="Tipo de vivienda"):
        await services["homes"].create(user_id=1, name="Burdeos", home_type="castillo")


async def test_resolve_y_update(services) -> None:
    hid = await _make_home(services)
    resolved = await services["homes"].resolve(1, "Mi piso")
    assert resolved.id == hid

    updated = await services["homes"].update(1, hid, address="Plaza Mayor 1")
    assert updated.address == "Plaza Mayor 1"


async def test_deactivate_home(services) -> None:
    hid = await _make_home(services)
    await services["homes"].deactivate(1, hid)

    assert len(await services["homes"].list(1)) == 0
    with pytest.raises(ValueError, match="desactivada"):
        await services["homes"].deactivate(1, hid)


async def test_vivienda_ajena_no_accesible(services) -> None:
    hid = await _make_home(services, user_id=1)
    with pytest.raises(NotFoundError):
        await services["homes"].get(2, hid)


async def test_mantenimiento_pendiente_sin_transaccion(services) -> None:
    hid = await _make_home(services)
    result = await services["homes"].record_maintenance(
        user_id=1,
        home=await services["homes"].get(1, hid),
        title="Revisar la caldera",
        maintenance_type="revision",
        next_due_at=datetime(2026, 10, 1, 9, 0, tzinfo=UTC),
    )

    assert result["status"] == "pending"
    assert result["transaction_id"] is None
    assert len(await services["transactions"].list(1)) == 0


async def test_mantenimiento_realizado_con_fecha_se_marca_done(services) -> None:
    hid = await _make_home(services)
    result = await services["homes"].record_maintenance(
        user_id=1,
        home=await services["homes"].get(1, hid),
        title="Cambiar la caldera",
        occurred_at=datetime(2026, 9, 10, 9, 0, tzinfo=UTC),
    )

    assert result["status"] == "done"


async def test_mantenimiento_con_coste_crea_una_transaccion(services) -> None:
    await _make_account(services)
    hid = await _make_home(services)
    result = await services["homes"].record_maintenance(
        user_id=1,
        home=await services["homes"].get(1, hid),
        title="Reparar fuga",
        maintenance_type="reparacion",
        occurred_at=datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
        cost=Decimal("250.00"),
    )

    assert result["status"] == "done"
    assert result["transaction_id"] is not None
    txn = await services["transactions"].get(1, result["transaction_id"])
    assert txn.category == "Vivienda"
    assert txn.merchant == "Mantenimiento casa"
    assert txn.amount == Decimal("250.00")
    assert len(await services["transactions"].list(1)) == 1


async def test_mantenimiento_titulo_obligatorio(services) -> None:
    hid = await _make_home(services)
    with pytest.raises(ValueError, match="título"):
        await services["homes"].record_maintenance(user_id=1, home=await services["homes"].get(1, hid), title="  ")


async def test_mantenimiento_tipo_invalido_rechazado(services) -> None:
    hid = await _make_home(services)
    with pytest.raises(ValueError, match="Tipo de mantenimiento"):
        await services["homes"].record_maintenance(
            user_id=1, home=await services["homes"].get(1, hid), title="X", maintenance_type="podar"
        )


async def test_get_status_lista_pendientes_y_gasto(services) -> None:
    await _make_account(services)
    hid = await _make_home(services)
    home = await services["homes"].get(1, hid)
    await services["homes"].record_maintenance(
        user_id=1, home=home, title="Pendiente", next_due_at=datetime.now(UTC) + timedelta(days=20)
    )
    await services["homes"].record_maintenance(
        user_id=1,
        home=home,
        title="Reparación hecha",
        occurred_at=datetime.now(UTC) - timedelta(days=5),
        cost=Decimal("150"),
    )

    status = await services["homes"].get_status(1, hid)

    assert status["home"].id == hid
    assert len(status["pending"]) == 1
    assert status["spend_last_30_days"]["total"] == Decimal("150.00")


async def test_get_upcoming_ordena_por_fecha(services) -> None:
    hid = await _make_home(services)
    home = await services["homes"].get(1, hid)
    later = datetime.now(UTC) + timedelta(days=60)
    sooner = datetime.now(UTC) + timedelta(days=10)
    await services["homes"].record_maintenance(user_id=1, home=home, title="Lejano", next_due_at=later)
    await services["homes"].record_maintenance(user_id=1, home=home, title="Cercano", next_due_at=sooner)

    upcoming = await services["homes"].get_upcoming(1)

    assert [m.title for m in upcoming["upcoming"]] == ["Cercano", "Lejano"]


async def test_mantenimiento_ajeno_aislado(services) -> None:
    await _make_home(services, user_id=1)

    with pytest.raises(NotFoundError):
        await services["homes"].resolve(2, "Mi piso")

    assert await services["homes"].list(2) == []
