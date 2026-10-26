"""Tests de los servicios de dominio de Fase 2 con repos en memoria (fakes).

Verifican reglas de negocio puras (cálculo de offsets, avance de recurrentes,
expansión de agenda) sin BD ni canales.

Claim atómico (2.1): los tests de concurrencia de claim están en
`test_claim_concurrency.py`.
"""

from __future__ import annotations

import pytest

from asistente.shared.errors import NotFoundError
from asistente.shared.recurrence import RecurrenceSpec


def _aware(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


DAILY_SPEC = RecurrenceSpec(id=None, freq="daily", interval=1, start_at_utc=_aware("2026-09-14T07:00:00+00:00"))


async def test_event_create_with_recurrence_creates_rule_and_event(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Cumple de Ana",
        start_at_utc=_aware("2026-10-05T07:00:00+00:00"),
        timezone="Europe/Madrid",
        kind="birthday",
        all_day=True,
        person="Ana",
        rule=DAILY_SPEC,
    )

    assert event.person == "Ana"
    assert event.kind == "birthday"
    assert event.recurrence_id is not None
    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule is not None
    assert rule.freq == "daily"


async def test_event_get_not_found_raises(services) -> None:
    with pytest.raises(NotFoundError):
        await services["events"].get(1, 999)


async def test_event_update_unknown_raises(services) -> None:
    with pytest.raises(NotFoundError):
        await services["events"].update(1, 999, title="x")


async def test_task_create_without_due_at_is_allowed(services) -> None:
    task = await services["tasks"].create(user_id=1, title="Comprar leche")
    assert task.due_at is None
    assert task.status == "pending"


async def test_task_complete_advances_recurring_task(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Regar las plantas",
        due_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=DAILY_SPEC,
    )
    first_due = task.due_at

    completed = await services["tasks"].complete(1, task.id)

    assert completed.status == "pending"
    assert completed.due_at > first_due
    assert completed.due_at.isoformat() == "2026-09-15T07:00:00+00:00"

    again = await services["tasks"].complete(1, task.id)
    assert again.due_at.isoformat() == "2026-09-16T07:00:00+00:00"


async def test_task_complete_finishes_non_recurring_task(services) -> None:
    task = await services["tasks"].create(user_id=1, title="Hacer la compra")
    completed = await services["tasks"].complete(1, task.id)
    assert completed.status == "completed"
    assert completed.completed_at is not None


async def test_task_complete_already_completed_raises(services) -> None:
    task = await services["tasks"].create(user_id=1, title="Hacer la compra")
    await services["tasks"].complete(1, task.id)
    with pytest.raises(NotFoundError):
        await services["tasks"].complete(1, task.id)


async def test_reminder_standalone_requires_remind_at(services) -> None:
    with pytest.raises(ValueError):
        await services["reminders"].create(user_id=1, message="Aviso")


async def test_reminder_linked_to_event_uses_offset(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Reunión",
        start_at_utc=_aware("2026-09-20T10:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, event_id=event.id)

    assert reminder.remind_at.isoformat() == "2026-09-20T09:00:00+00:00"
    assert "Reunión" in reminder.message
    assert reminder.offset_minutes == 60


async def test_reminder_linked_to_task_uses_offset(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Entrega",
        due_at_utc=_aware("2026-09-20T10:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=30)

    assert reminder.remind_at.isoformat() == "2026-09-20T09:30:00+00:00"
    assert "Entrega" in reminder.message


async def test_reminder_linked_to_task_without_due_at_raises(services) -> None:
    task = await services["tasks"].create(user_id=1, title="Sin fecha")
    with pytest.raises(ValueError):
        await services["reminders"].create(user_id=1, task_id=task.id)


async def test_reminder_linked_to_event_and_task_raises(services) -> None:
    with pytest.raises(ValueError):
        await services["reminders"].create(user_id=1, event_id=1, task_id=1)


async def test_reminder_compute_next_for_recurring_event(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Gimnasio",
        start_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=DAILY_SPEC,
    )
    reminder = await services["reminders"].create(user_id=1, event_id=event.id, offset_minutes=60)

    nxt = await services["reminders"].compute_next(1, reminder)

    assert nxt is not None
    assert nxt.isoformat() == "2026-09-15T06:00:00+00:00"


async def test_reminder_compute_next_none_for_plain(services) -> None:
    reminder = await services["reminders"].create(
        user_id=1, message="Suelto", remind_at_utc=_aware("2026-09-20T09:00:00+00:00")
    )
    assert await services["reminders"].compute_next(1, reminder) is None


async def test_agenda_during_period_expands_recurring_event(services) -> None:
    await services["events"].create(
        user_id=1,
        title="Café con Ana",
        start_at_utc=_aware("2026-09-10T07:00:00+00:00"),
        timezone="Europe/Madrid",
        kind="important_date",
        person="Ana",
        rule=DAILY_SPEC,
    )
    await services["tasks"].create(
        user_id=1,
        title="Enviar informe",
        due_at_utc=_aware("2026-09-15T08:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    await services["reminders"].create(
        user_id=1,
        message="Revisar correo",
        remind_at_utc=_aware("2026-09-15T07:30:00+00:00"),
    )
    start = _aware("2026-09-13T00:00:00+00:00")
    end = _aware("2026-09-16T00:00:00+00:00")

    agenda = await services["agenda"].during_period(1, start, end, timezone="Europe/Madrid")

    starts = [e["start_at"] for e in agenda["events"]]
    assert any(s == "2026-09-14T09:00:00+02:00" for s in starts)
    assert any(s == "2026-09-15T09:00:00+02:00" for s in starts)
    assert [t["title"] for t in agenda["tasks"]] == ["Enviar informe"]
    assert [r["message"] for r in agenda["reminders"]] == ["Revisar correo"]


async def test_agenda_filters_reminders_outside_range(services) -> None:
    await services["reminders"].create(
        user_id=1,
        message="Fuera de rango",
        remind_at_utc=_aware("2026-09-01T09:00:00+00:00"),
    )
    start = _aware("2026-09-13T00:00:00+00:00")
    end = _aware("2026-09-16T00:00:00+00:00")
    agenda = await services["agenda"].during_period(1, start, end, timezone="Europe/Madrid")
    assert agenda["reminders"] == []
