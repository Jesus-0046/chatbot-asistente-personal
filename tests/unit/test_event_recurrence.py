"""Tests de eventos recurrentes (Fase 2.1 §3 y §4).

Regresiones cubiertas:
- Separación duración ↔ serie: `event.end_at` es la duración de la Ocurrencia
  (p. ej. 10:00–11:00) y NO limita `rule.end_at`; sin `until` explícito la
  serie es indefinida (`rule.end_at is None`).
- `update_event` sobre evento recurrente: si cambia `when`, se desplaza
  `start_at`, se conserva la duración (`end_at` se desplaza igual), se
  sincroniza la regla (inicio y fin de serie) y NO se duplica la próxima
  ocurrencia.
"""

from __future__ import annotations

import pytest

from asistente.shared.recurrence import RecurrenceSpec


def _aware(iso: str):
    from datetime import datetime

    return datetime.fromisoformat(iso)


def _weekly_rule(*, until=None) -> RecurrenceSpec:
    return RecurrenceSpec(
        id=None,
        freq="weekly",
        interval=1,
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),  # miércoles 10:00 CEST
        end_at_utc=until,
        timezone="Europe/Madrid",
    )


async def test_evento_recurrente_sin_fecha_final_serie_indefinida(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Reunión semanal",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T09:00:00+00:00"),  # 10:00–11:00 (duración)
        timezone="Europe/Madrid",
        rule=_weekly_rule(),
    )

    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule is not None
    assert rule.end_at is None
    assert event.end_at.isoformat() == "2026-09-16T09:00:00+00:00"


async def test_evento_recurrente_con_fecha_final_limita_la_serie(services) -> None:
    until = _aware("2026-09-30T22:00:00+00:00")
    event = await services["events"].create(
        user_id=1,
        title="Curso de idiomas",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T09:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(until=until),
    )

    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule.end_at == until
    assert event.end_at.isoformat() != until.isoformat()  # la duración no es el fin de la serie


async def test_varias_ocurrencias_futuras_sin_duplicar(services) -> None:
    await services["events"].create(
        user_id=1,
        title="Reunión semanal",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T09:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(),
    )

    start = _aware("2026-09-16T00:00:00+00:00")
    end = _aware("2026-10-21T00:00:00+00:00")
    agenda = await services["agenda"].during_period(1, start, end, timezone="Europe/Madrid")

    starts_utc = [e["start_at_utc"] for e in agenda["events"]]
    assert len(starts_utc) == 5  # 16, 23, 30 may, 7, 14 oct
    assert len(set(starts_utc)) == 5
    assert all(e["occurrence"] for e in agenda["events"])
    assert sorted(s.isoformat() for s in starts_utc) == [
        "2026-09-16T08:00:00+00:00",
        "2026-09-23T08:00:00+00:00",
        "2026-09-30T08:00:00+00:00",
        "2026-10-07T08:00:00+00:00",
        "2026-10-14T08:00:00+00:00",
    ]


async def test_serie_con_fin_no_emite_ocurrencias_posteriores(services) -> None:
    until = _aware("2026-09-30T22:00:00+00:00")
    await services["events"].create(
        user_id=1,
        title="Curso de idiomas",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(until=until),
    )

    start = _aware("2026-09-16T00:00:00+00:00")
    end = _aware("2026-10-21T00:00:00+00:00")
    agenda = await services["agenda"].during_period(1, start, end, timezone="Europe/Madrid")

    assert len(agenda["events"]) == 3  # 16, 23 y 30 de septiembre


async def test_update_event_recurrente_desplaza_start_y_duracion(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Reunión semanal",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T09:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(),
    )

    updated = await services["events"].update(1, event.id, start_at=_aware("2026-09-23T11:00:00+00:00"))

    assert updated.start_at.isoformat() == "2026-09-23T11:00:00+00:00"
    assert updated.end_at.isoformat() == "2026-09-23T12:00:00+00:00"  # +2h: misma duración

    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule.start_at.isoformat() == "2026-09-23T11:00:00+00:00"

    start = _aware("2026-09-16T00:00:00+00:00")
    end = _aware("2026-10-14T00:00:00+00:00")
    agenda = await services["agenda"].during_period(1, start, end, timezone="Europe/Madrid")
    starts_utc = [e["start_at_utc"].isoformat() for e in agenda["events"]]
    assert starts_utc == [
        "2026-09-23T11:00:00+00:00",
        "2026-09-30T11:00:00+00:00",
        "2026-10-07T11:00:00+00:00",
    ]


async def test_update_event_recurrente_desplaza_tambien_el_fin_de_serie(services) -> None:
    until = _aware("2026-09-30T22:00:00+00:00")
    event = await services["events"].create(
        user_id=1,
        title="Curso",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(until=until),
    )

    new_start = _aware("2026-09-23T11:00:00+00:00")
    await services["events"].update(1, event.id, start_at=new_start)

    delta = new_start - _aware("2026-09-16T08:00:00+00:00")
    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule.end_at == until + delta  # el fin de serie se desplaza con el evento


async def test_update_event_recurrente_con_fin_explicito_cambia_duracion(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Reunión",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        end_at_utc=_aware("2026-09-16T09:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(),
    )

    updated = await services["events"].update(
        1,
        event.id,
        start_at=_aware("2026-09-23T11:00:00+00:00"),
        end_at=_aware("2026-09-23T14:30:00+00:00"),
    )

    assert updated.end_at.isoformat() == "2026-09-23T14:30:00+00:00"
    rule = await services["rules"].find_one(1, event.recurrence_id)
    assert rule.start_at.isoformat() == "2026-09-23T11:00:00+00:00"


async def test_update_event_fin_anterior_al_inicio_rechazado(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Reunión",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        timezone="Europe/Madrid",
    )

    with pytest.raises(ValueError):
        await services["events"].update(
            1,
            event.id,
            start_at=_aware("2026-09-23T11:00:00+00:00"),
            end_at=_aware("2026-09-23T10:00:00+00:00"),
        )


async def test_update_event_reaplica_recordatorios_pendientes(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Gimnasio",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        timezone="Europe/Madrid",
        rule=_weekly_rule(),
    )
    reminder = await services["reminders"].create(user_id=1, event_id=event.id, offset_minutes=60)
    assert reminder.remind_at.isoformat() == "2026-09-16T07:00:00+00:00"

    await services["events"].update(1, event.id, start_at=_aware("2026-09-23T11:00:00+00:00"))

    moved = await services["reminders"].get(1, reminder.id)
    assert moved.remind_at.isoformat() == "2026-09-23T10:00:00+00:00"
    assert len(await services["reminders"].list_all(1)) == 1


async def test_cancelar_evento_cancela_recordatorios_pendientes(services) -> None:
    event = await services["events"].create(
        user_id=1,
        title="Cita",
        start_at_utc=_aware("2026-09-16T08:00:00+00:00"),
        timezone="Europe/Madrid",
    )
    reminder = await services["reminders"].create(user_id=1, event_id=event.id, offset_minutes=60)

    await services["events"].cancel(1, event.id)

    assert (await services["reminders"].get(1, reminder.id)).status == "cancelled"
