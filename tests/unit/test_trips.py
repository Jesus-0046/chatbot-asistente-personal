"""Tests de dominio: TripService (Fase 6).

Cubren CRUD, validación de fechas, checklist, vínculo financiero (solo con
movimientos reales del usuario) y vínculo de agenda (tarea/evento/recordatorio).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.shared.errors import BusinessError, NotFoundError


def d(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


async def test_create_and_get(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa", destination="Lisboa", start_at=d(3), end_at=d(10))
    assert trip.id >= 1
    assert trip.normalized_name == "lisboa"
    assert (await trips.get(1, trip.id)).name == "Lisboa"


async def test_get_de_otro_usuario_no_existe(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    with pytest.raises(NotFoundError):
        await trips.get(2, trip.id)


async def test_find_by_name_usa_normalizacion(services) -> None:
    trips = services["trips"]
    await trips.create(user_id=1, name="Lisboa")
    assert len(await trips.find_by_name(1, "  lisboa ")) == 1
    assert await trips.find_by_name(1, "   ") == []


async def test_list_filtra_por_estado(services) -> None:
    trips = services["trips"]
    a = await trips.create(user_id=1, name="A")
    await trips.create(user_id=1, name="B")
    await trips.cancel(1, a.id)
    assert len(await trips.list_trips(1)) == 2
    assert len(await trips.list_trips(1, status="cancelled")) == 1
    with pytest.raises(ValueError):
        await trips.list_trips(1, status="inventado")


async def test_update_cancel_complete(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    updated = await trips.update(1, trip.id, destination="Oporto", notes="con Ana")
    assert updated.destination == "Oporto"
    completed = await trips.complete(1, trip.id)
    assert completed.status == "completed"
    with pytest.raises(BusinessError):
        await trips.complete(1, trip.id)


async def test_no_se_puede_actualizar_cancelado(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    await trips.cancel(1, trip.id)
    with pytest.raises(BusinessError):
        await trips.update(1, trip.id, name="Otro")
    with pytest.raises(BusinessError):
        await trips.complete(1, trip.id)


async def test_validacion_de_fechas(services) -> None:
    trips = services["trips"]
    with pytest.raises(ValueError):
        await trips.create(user_id=1, name="X", start_at=d(10), end_at=d(3))
    with pytest.raises(ValueError):
        await trips.create(user_id=1, name="X", start_at=datetime(2026, 8, 3))  # naive
    with pytest.raises(ValueError):
        await trips.create(user_id=1, name="   ")


async def test_checklist_flow(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    item = await trips.add_checklist_item(1, trip.id, "Pasaporte")
    await trips.add_checklist_item(1, trip.id, "Cargador")
    assert await trips.pending_checklist_count(1, trip.id) == 2
    await trips.toggle_checklist_item(1, item.id, True)
    assert await trips.pending_checklist_count(1, trip.id) == 1
    await trips.delete_checklist_item(1, item.id)
    assert len(await trips.list_checklist(1, trip.id)) == 1
    with pytest.raises(NotFoundError):
        await trips.delete_checklist_item(1, item.id)


async def test_link_transactions_y_resumen_financiero(services, transaction_repo) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    await transaction_repo.create(
        user_id=1, type="expense", amount=Decimal("120"), occurred_at=d(3), category="Alimentación"
    )
    await transaction_repo.create(
        user_id=1, type="expense", amount=Decimal("30"), occurred_at=d(4), category="Transporte"
    )
    await transaction_repo.create(user_id=1, type="income", amount=Decimal("50"), occurred_at=d(5))
    assert await trips.link_transactions(1, trip.id, [1, 2, 3]) == 3

    summary = await trips.financial_summary(1, trip.id)
    assert summary["total_spent"] == "150.00"
    assert summary["total_income"] == "50.00"
    assert summary["net"] == "-100.00"
    assert summary["spent_by_category"]["Alimentación"] == "120.00"

    assert await trips.unlink_transactions(1, trip.id, [3]) == 1
    assert (await trips.financial_summary(1, trip.id))["total_income"] == "0.00"


async def test_link_transaction_ajena_falla(services, transaction_repo) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    await transaction_repo.create(user_id=2, type="expense", amount=Decimal("10"), occurred_at=d(3))
    with pytest.raises(NotFoundError):
        await trips.link_transactions(1, trip.id, [1])


async def test_link_task_event_reminder(services) -> None:
    trips = services["trips"]
    trip = await trips.create(user_id=1, name="Lisboa")
    task = await services["tasks"].create(user_id=1, title="Hacer maleta")
    event = await services["events"].create(user_id=1, title="Vuelo", start_at_utc=d(3, 8))
    reminder = await services["reminders"].create(user_id=1, message="Avísame", remind_at_utc=d(3, 7))

    linked_task = await trips.link_task(1, task.id, trip.id)
    linked_event = await trips.link_event(1, event.id, trip.id)
    linked_reminder = await trips.link_reminder(1, reminder.id, trip.id)
    assert linked_task.trip_id == trip.id
    assert linked_event.trip_id == trip.id
    assert linked_reminder.trip_id == trip.id
