"""Tests de sincronización de recordatorios vinculados a tareas (Fase 2.1).

Regresión del bug "recordatorio de tarea recurrente" (Fase 2.1 §2): al
completar (avanzar) o cancelar una tarea recurrente, los recordatorios
pendientes vinculados deben re-apuntarse a la nueva ocurrencia (mismo offset)
o cancelarse cuando la tarea termina de verdad; nunca quedarse apuntando a la
ocurrencia ya completada y nunca duplicarse.
"""

from __future__ import annotations

from asistente.shared.recurrence import RecurrenceSpec


def _aware(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


def _daily_rule() -> RecurrenceSpec:
    return RecurrenceSpec(
        id=None,
        freq="daily",
        interval=1,
        start_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
    )


async def test_completar_tarea_recurrente_avanza_y_resincroniza(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Regar las plantas",
        due_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_daily_rule(),
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=60)
    assert reminder.remind_at.isoformat() == "2026-09-14T06:00:00+00:00"

    after = await services["tasks"].complete(1, task.id)

    assert after.status == "pending"
    assert after.due_at.isoformat() == "2026-09-15T07:00:00+00:00"
    moved = await services["reminders"].get(1, reminder.id)
    assert moved.status == "pending"
    assert moved.remind_at.isoformat() == "2026-09-15T06:00:00+00:00"
    # No se duplica: sigue siendo UNA sola fila.
    assert len(await services["reminders"].list_all(1)) == 1


async def test_varios_recordatorios_se_reaplican_con_su_propio_offset(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Presentación",
        due_at_utc=_aware("2026-09-14T10:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=RecurrenceSpec(
            id=None,
            freq="daily",
            interval=1,
            start_at_utc=_aware("2026-09-14T10:00:00+00:00"),
            timezone="Europe/Madrid",
        ),
    )
    r_30 = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=30)
    r_60 = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=60)

    await services["tasks"].complete(1, task.id)

    assert (await services["reminders"].get(1, r_30.id)).remind_at.isoformat() == "2026-09-15T09:30:00+00:00"
    assert (await services["reminders"].get(1, r_60.id)).remind_at.isoformat() == "2026-09-15T09:00:00+00:00"
    assert len(await services["reminders"].list_all(1)) == 2


async def test_fin_de_recurrencia_completa_definitivamente_y_cancela_recordatorios(services) -> None:
    rule = RecurrenceSpec(
        id=None,
        freq="daily",
        interval=1,
        start_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T23:59:59+00:00"),
        timezone="Europe/Madrid",
    )
    task = await services["tasks"].create(
        user_id=1,
        title="Dientes de león",
        due_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=rule,
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=60)

    snapshots = []
    for _ in range(3):
        res = await services["tasks"].complete(1, task.id)
        snapshots.append((res.due_at.isoformat() if res.due_at else None, res.status))

    assert [s for s, _ in snapshots] == [
        "2026-09-15T07:00:00+00:00",  # avanza
        "2026-09-16T07:00:00+00:00",  # avanza (última ocurrencia)
        "2026-09-16T07:00:00+00:00",  # ya no hay más: queda completada (due sin cambiar)
    ]
    final = await services["tasks"].get(1, task.id)
    assert final.status == "completed"
    assert len(await services["reminders"].list_all(1)) == 1
    assert (await services["reminders"].get(1, reminder.id)).status == "cancelled"


async def test_completar_tarea_no_recurrente_cancela_recordatorios(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Hacer la compra",
        due_at_utc=_aware("2026-09-14T07:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=30)

    await services["tasks"].complete(1, task.id)

    assert (await services["tasks"].get(1, task.id)).status == "completed"
    assert (await services["reminders"].get(1, reminder.id)).status == "cancelled"


async def test_cancelar_tarea_cancela_sus_recordatorios_pendientes(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Entrega informe",
        due_at_utc=_aware("2026-09-20T17:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=15)

    await services["tasks"].cancel(1, task.id)

    assert (await services["tasks"].get(1, task.id)).status == "cancelled"
    assert (await services["reminders"].get(1, reminder.id)).status == "cancelled"


async def test_actualizar_due_at_reaplica_los_recordatorios(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Evento X",
        due_at_utc=_aware("2026-09-20T17:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=30)

    await services["tasks"].update(1, task.id, due_at=_aware("2026-09-22T17:00:00+00:00"))

    assert (await services["reminders"].get(1, reminder.id)).remind_at.isoformat() == "2026-09-22T16:30:00+00:00"
    assert len(await services["reminders"].list_all(1)) == 1


async def test_cancelar_tarea_no_toca_recordatorios_ya_no_activos(services) -> None:
    task = await services["tasks"].create(
        user_id=1,
        title="Entrega informe",
        due_at_utc=_aware("2026-09-20T17:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    sent = await services["reminders"].create(user_id=1, task_id=task.id, offset_minutes=15)
    await services["reminders"].cancel(1, sent.id)

    await services["tasks"].cancel(1, task.id)

    assert (await services["reminders"].get(1, sent.id)).status == "cancelled"

    # No se crea ningún recordatorio nuevo (la cancelación no duplica).
    assert len(await services["reminders"].list_all(1)) == 1
