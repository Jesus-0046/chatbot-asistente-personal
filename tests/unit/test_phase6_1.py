"""Tests de Fase 6.1: correcciones y cierre de Fase 6.

Cubre:
- Trip: trip_type, timezone, budget
- Financial summary con budget
- Link/unlink de tareas, eventos, recordatorios
- get_trip con ítems vinculados
- Importancia threshold
- Quiet hours adicionales
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.domain.proactivity import in_quiet_hours
from asistente.shared.errors import BusinessError, NotFoundError


def d(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


# ── Trip: trip_type ──────────────────────────────────────────────────


async def test_trip_type_valido(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", trip_type="business")
    assert trip.trip_type == "business"


async def test_trip_type_default(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    assert trip.trip_type == "other"


async def test_trip_type_invalido(services) -> None:
    with pytest.raises(ValueError, match="Tipo de viaje no válido"):
        await services["trips"].create(user_id=1, name="Lisboa", trip_type="invalido")


async def test_trip_type_update(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    updated = await services["trips"].update(1, trip.id, trip_type="family")
    assert updated.trip_type == "family"


async def test_trip_type_update_invalido(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    with pytest.raises(ValueError, match="Tipo de viaje no válido"):
        await services["trips"].update(1, trip.id, trip_type="invalido")


# ── Trip: timezone ───────────────────────────────────────────────────


async def test_trip_timezone(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", timezone="Atlantic/Azores")
    assert trip.timezone == "Atlantic/Azores"


async def test_trip_timezone_default(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    assert trip.timezone == "Europe/Madrid"


async def test_trip_timezone_invalida(services) -> None:
    with pytest.raises(BusinessError):
        await services["trips"].create(user_id=1, name="Lisboa", timezone="ZonaInvalida/NoExiste")


async def test_trip_timezone_update(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    updated = await services["trips"].update(1, trip.id, timezone="Atlantic/Azores")
    assert updated.timezone == "Atlantic/Azores"


# ── Trip: budget ─────────────────────────────────────────────────────


async def test_trip_budget(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("500.00"))
    assert trip.budget == Decimal("500.00")


async def test_trip_budget_cero(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("0"))
    assert trip.budget == Decimal("0")


async def test_trip_budget_negativo_error(services) -> None:
    with pytest.raises(ValueError, match="negativo"):
        await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("-100"))


async def test_trip_budget_null(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    assert trip.budget is None


async def test_trip_budget_update(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    updated = await services["trips"].update(1, trip.id, budget=Decimal("750.00"))
    assert updated.budget == Decimal("750.00")


async def test_trip_budget_clear(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("500"))
    updated = await services["trips"].update(1, trip.id, budget=None)
    assert updated.budget is None


# ── Financial summary con budget ─────────────────────────────────────


async def test_financial_summary_con_budget(services, transaction_repo) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("1000.00"))
    await transaction_repo.create(user_id=1, type="expense", amount=Decimal("300.00"), occurred_at=d(3))
    await services["trips"].link_transactions(1, trip.id, [1])
    summary = await services["trips"].financial_summary(1, trip.id)
    assert Decimal(summary["budget"]) == Decimal("1000.00")
    assert Decimal(summary["remaining_budget"]) == Decimal("700.00")
    assert Decimal(summary["budget_consumed_percentage"]) == Decimal("30.00")


async def test_financial_summary_budget_cero_con_gasto(services, transaction_repo) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("0.00"))
    await transaction_repo.create(user_id=1, type="expense", amount=Decimal("50.00"), occurred_at=d(3))
    await services["trips"].link_transactions(1, trip.id, [1])
    summary = await services["trips"].financial_summary(1, trip.id)
    assert Decimal(summary["budget"]) == Decimal("0.00")
    assert Decimal(summary["budget_consumed_percentage"]) == Decimal("100.00")


async def test_financial_summary_budget_cero_sin_gasto(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("0.00"))
    summary = await services["trips"].financial_summary(1, trip.id)
    assert Decimal(summary["budget_consumed_percentage"]) == Decimal("0.00")


async def test_financial_summary_sin_budget(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    summary = await services["trips"].financial_summary(1, trip.id)
    assert summary["budget"] is None
    assert summary["remaining_budget"] is None
    assert summary["budget_consumed_percentage"] is None


async def test_financial_summary_budget_parcial(services, transaction_repo) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("100.00"))
    await transaction_repo.create(user_id=1, type="expense", amount=Decimal("150.00"), occurred_at=d(3))
    await services["trips"].link_transactions(1, trip.id, [1])
    summary = await services["trips"].financial_summary(1, trip.id)
    assert Decimal(summary["remaining_budget"]) == Decimal("-50.00")
    assert Decimal(summary["budget_consumed_percentage"]) == Decimal("100.00")


# ── Link/unlink tasks ───────────────────────────────────────────────


async def test_link_unlink_task(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    task = await services["tasks"].create(user_id=1, title="Hacer maleta")
    linked = await services["trips"].link_task(1, task.id, trip.id)
    assert linked.trip_id == trip.id
    unlinked = await services["trips"].unlink_task(1, task.id)
    assert unlinked.trip_id is None


async def test_unlink_task_no_vinculada(services) -> None:
    task = await services["tasks"].create(user_id=1, title="Hacer maleta")
    with pytest.raises(BusinessError, match="no está vinculada"):
        await services["trips"].unlink_task(1, task.id)


async def test_link_task_otro_usuario(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    task = await services["tasks"].create(user_id=2, title="Tarea ajena")
    with pytest.raises(NotFoundError):
        await services["trips"].link_task(1, task.id, trip.id)


async def test_unlink_task_inexistente(services) -> None:
    with pytest.raises(NotFoundError):
        await services["trips"].unlink_task(1, 9999)


# ── Link/unlink events ──────────────────────────────────────────────


async def test_link_unlink_event(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    event = await services["events"].create(user_id=1, title="Vuelo", start_at_utc=d(3))
    linked = await services["trips"].link_event(1, event.id, trip.id)
    assert linked.trip_id == trip.id
    unlinked = await services["trips"].unlink_event(1, event.id)
    assert unlinked.trip_id is None


async def test_unlink_event_no_vinculado(services) -> None:
    event = await services["events"].create(user_id=1, title="Vuelo", start_at_utc=d(3))
    with pytest.raises(BusinessError, match="no está vinculado"):
        await services["trips"].unlink_event(1, event.id)


async def test_link_event_otro_usuario(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    event = await services["events"].create(user_id=2, title="Ajeno", start_at_utc=d(3))
    with pytest.raises(NotFoundError):
        await services["trips"].link_event(1, event.id, trip.id)


# ── Link/unlink reminders ───────────────────────────────────────────


async def test_link_unlink_reminder(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    reminder = await services["reminders"].create(user_id=1, message="Avísame", remind_at_utc=d(3, 7))
    linked = await services["trips"].link_reminder(1, reminder.id, trip.id)
    assert linked.trip_id == trip.id
    unlinked = await services["trips"].unlink_reminder(1, reminder.id)
    assert unlinked.trip_id is None


async def test_unlink_reminder_no_vinculado(services) -> None:
    reminder = await services["reminders"].create(user_id=1, message="Avísame", remind_at_utc=d(3, 7))
    with pytest.raises(BusinessError, match="no está vinculado"):
        await services["trips"].unlink_reminder(1, reminder.id)


async def test_link_reminder_otro_usuario(services) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa")
    reminder = await services["reminders"].create(user_id=2, message="Ajeno", remind_at_utc=d(3, 7))
    with pytest.raises(NotFoundError):
        await services["trips"].link_reminder(1, reminder.id, trip.id)


# ── get_trip_detail ─────────────────────────────────────────────────


async def test_get_trip_detail(services, transaction_repo) -> None:
    trip = await services["trips"].create(user_id=1, name="Lisboa", budget=Decimal("500.00"))
    await services["trips"].add_checklist_item(1, trip.id, "Pasaporte")
    task = await services["tasks"].create(user_id=1, title="Hacer maleta")
    await services["trips"].link_task(1, task.id, trip.id)
    event = await services["events"].create(user_id=1, title="Vuelo", start_at_utc=d(3))
    await services["trips"].link_event(1, event.id, trip.id)
    await transaction_repo.create(user_id=1, type="expense", amount=Decimal("120"), occurred_at=d(3))
    await services["trips"].link_transactions(1, trip.id, [1])

    detail = await services["trips"].get_trip_detail(1, trip.id)
    assert detail["trip"].name == "Lisboa"
    assert len(detail["checklist"]) == 1
    assert len(detail["tasks"]) == 1
    assert len(detail["events"]) == 1
    assert Decimal(detail["financial_summary"]["budget"]) == Decimal("500.00")


# ── Importance threshold ─────────────────────────────────────────────


async def test_importance_threshold_default(services) -> None:
    proactivity = services["proactivity"]
    settings = await proactivity.get_settings(1)
    assert settings["importance_threshold"] == 3


async def test_importance_threshold_update(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.update_settings(1, importance_threshold=1)
    settings = await proactivity.get_settings(1)
    assert settings["importance_threshold"] == 1


async def test_importance_threshold_invalido(services) -> None:
    proactivity = services["proactivity"]
    with pytest.raises(BusinessError, match="umbral de importancia"):
        await proactivity.update_settings(1, importance_threshold=4)
    with pytest.raises(BusinessError, match="umbral de importancia"):
        await proactivity.update_settings(1, importance_threshold=0)


# ── Quiet hours adicionales ──────────────────────────────────────────


def test_quiet_hours_noche():
    now = datetime(2026, 8, 3, 23, 30, tzinfo=UTC)
    assert in_quiet_hours(now, "22:00", "08:00") is True


def test_quiet_hours_madrugada():
    now = datetime(2026, 8, 4, 3, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "22:00", "08:00") is True


def test_quiet_hours_fuera():
    now = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "22:00", "08:00") is False


def test_quiet_hours_start_igual_end():
    now = datetime(2026, 8, 3, 14, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "12:00", "12:00") is False


def test_quiet_hours_diurno():
    now = datetime(2026, 8, 3, 11, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "09:00", "17:00") is True


def test_quiet_hours_diurno_fuera():
    now = datetime(2026, 8, 3, 18, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "09:00", "17:00") is False


def test_quiet_hours_cambio_dia():
    now = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
    assert in_quiet_hours(now, "22:00", "08:00") is True


def test_quiet_hours_timezone_diferente():
    from asistente.shared.timeutil import resolve_timezone

    tz = resolve_timezone("America/New_York")
    now = datetime(2026, 8, 3, 22, 30, tzinfo=tz)
    assert in_quiet_hours(now, "22:00", "08:00") is True
