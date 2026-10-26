"""Tests de las tools de agenda (Fase 2) ejecutadas vía el registro.

Usan un `ToolContext` con `services` falsos (users/settings reales para los
defaults) y verifcan: happy paths, ambigüedad de fechas, errores de
recurrencia semanal, y aislamiento de argumentos.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from tests.unit.helpers import make_settings

from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry

TZ_MAD = ZoneInfo("Europe/Madrid")


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None  # → default_timezone del settings


class FakeEvents:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.deleted: list[int] = []
        self.events: dict[int, _Fake] = {}

    async def create(self, **kwargs) -> _Fake:
        self.created.append(kwargs)
        event = _Fake(
            id=len(self.created),
            title=kwargs.get("title"),
            kind=kwargs.get("kind", "event"),
            start_at=kwargs.get("start_at_utc"),
            end_at=kwargs.get("end_at_utc"),
            all_day=kwargs.get("all_day", False),
            location=kwargs.get("location"),
            priority=kwargs.get("priority", "medium"),
            person=kwargs.get("person"),
            category=kwargs.get("category"),
            recurrence_id=kwargs.get("recurrence_id") or (1 if kwargs.get("rule") is not None else None),
            rule_spec=kwargs.get("rule"),
        )
        self.events[event.id] = event
        return event

    async def get(self, user_id: int, event_id: int) -> _Fake:
        return self.events[event_id]

    async def delete(self, user_id: int, event_id: int) -> None:
        self.deleted.append(event_id)


class FakeTasks:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.completed: list[int] = []

    async def create(self, **kwargs) -> _Fake:
        self.created.append(kwargs)
        return _Fake(
            id=len(self.created),
            title=kwargs.get("title"),
            due_at=kwargs.get("due_at_utc"),
            priority=kwargs.get("priority", "medium"),
            recurrence_id=kwargs.get("recurrence_id"),
            status="pending",
        )

    async def list(self, user_id: int, *, status=None, limit=100) -> list[_Fake]:
        return []

    async def complete(self, user_id: int, task_id: int) -> _Fake:
        self.completed.append(task_id)
        return _Fake(id=task_id, status="pending", recurrence_id=1)

    async def cancel(self, user_id: int, task_id: int) -> _Fake:
        return _Fake(id=task_id, status="cancelled")


class FakeReminders:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, **kwargs) -> _Fake:
        self.created.append(kwargs)
        return _Fake(
            id=len(self.created),
            message=kwargs.get("message"),
            remind_at=kwargs.get("remind_at_utc")
            or kwargs.get("remind_at")
            or datetime(2026, 10, 5, 8, 0, tzinfo=ZoneInfo("UTC")),
            event_id=kwargs.get("event_id"),
            task_id=kwargs.get("task_id"),
            status="pending",
        )

    async def list_all(self, user_id: int, *, status=None, limit=100) -> list[_Fake]:
        return []

    async def cancel(self, user_id: int, reminder_id: int) -> _Fake:
        return _Fake(id=reminder_id, status="cancelled")


class FakeAgenda:
    def __init__(self) -> None:
        self.calls: list[tuple[int, datetime, datetime]] = []

    async def during_period(self, user_id, start_utc, end_utc, *, timezone, include_cancelled=False):
        self.calls.append((user_id, start_utc, end_utc))
        return {
            "events": [],
            "tasks": [],
            "reminders": [],
            "period_start": start_utc.isoformat(),
            "period_end": end_utc.isoformat(),
            "timezone": timezone,
        }


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    services = _Fake(
        users=FakeUsers(),
        settings=settings,
        events=FakeEvents(),
        tasks=FakeTasks(),
        reminders=FakeReminders(),
        agenda=FakeAgenda(),
    )
    return services


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=7, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict, *, confirmed: bool = True):
    return await registry.execute(ctx, name, args, confirmed=confirmed)


async def test_create_event_happy_path(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {
            "title": "Reunión con Ana",
            "when": "2026-10-05T10:00:00",
            "kind": "event",
            "person": "Ana",
            "priority": "urgente",
        },
    )

    assert res.ok, res.error
    assert res.data["person"] == "Ana"
    assert res.data["priority"] == "high"
    assert res.data["recurring"] is False
    created = services.events.created[-1]
    assert created["user_id"] == 7
    # La hora se interpreta en la zona del usuario y se persiste en UTC.
    assert created["start_at_utc"].isoformat() == "2026-10-05T08:00:00+00:00"
    assert created["timezone"] == "Europe/Madrid"


async def test_create_event_birthday_es_dia_completo(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {"title": "Cumple de Luca", "when": "2026-10-05T09:00:00", "kind": "birthday", "person": "Luca"},
    )

    assert res.ok, res.error
    created = services.events.created[-1]
    assert created["all_day"] is True
    assert created["kind"] == "birthday"


async def test_create_event_con_recurrencia_semanal(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {"title": "Gimnasio", "when": "2026-10-05T09:00:00", "recurrence": "todos los martes"},
    )

    assert not res.ok
    assert "no cae en los días" in res.error


async def test_create_event_con_recurrencia_coherente(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {"title": "Gimnasio", "when": "2026-10-06T09:00:00", "recurrence": "todos los martes"},
    )

    assert res.ok, res.error
    assert res.data["recurring"] is True


async def test_create_event_recurrente_sin_fecha_final_serie_indefinida(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {"title": "Gimnasio", "when": "2026-10-06T09:00:00", "recurrence": "todos los martes"},
    )

    assert res.ok, res.error
    rule = services.events.created[-1]["rule"]
    assert rule is not None and rule.end_at_utc is None


async def test_create_event_recurrente_con_fecha_final_limita_la_serie(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {
            "title": "Curso de inglés",
            "when": "2026-10-06T09:00:00",
            "recurrence": "todos los martes",
            "recurrence_until": "2026-10-30",
        },
    )

    assert res.ok, res.error
    rule = services.events.created[-1]["rule"]
    assert rule is not None
    # "2026-10-30 00:00" en Europe/Madrid (CET, DST termina el 25/10) → 2026-10-29T23:00Z.
    assert rule.end_at_utc.isoformat() == "2026-10-29T23:00:00+00:00"


async def test_create_event_recurrente_con_fecha_final_anterior_al_inicio_rechazada(registry, ctx) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {
            "title": "Gimnasio",
            "when": "2026-10-06T09:00:00",
            "recurrence": "todos los martes",
            "recurrence_until": "2026-10-01",
        },
    )

    assert not res.ok
    assert "fin de la serie" in res.error


async def test_create_event_10_a_11_semanal_separa_duracion_y_serie(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_event",
        {
            "title": "Clase de yoga",
            "when": "2026-10-06T10:00:00",
            "end_when": "2026-10-06T11:00:00",
            "recurrence": "cada semana",
        },
    )

    assert res.ok, res.error
    created = services.events.created[-1]
    assert created["end_at_utc"].isoformat() == "2026-10-06T09:00:00+00:00"  # duración 10–11
    assert created["rule"].end_at_utc is None  # la serie sigue indefinida
    assert created["rule"].start_at_utc.isoformat() == "2026-10-06T08:00:00+00:00"  # 10:00 CEST


async def test_ambiguity_devuelve_error_para_que_el_llm_pregunte(registry, ctx, monkeypatch) -> None:
    # "el viernes" con hoy viernes es ambiguo: hoy o el viernes que viene.
    now_local = datetime(2026, 9, 18, 10, 0, tzinfo=TZ_MAD)  # 2026-09-18 es viernes

    captured: dict = {}

    def fake_now_local(user_tz: str) -> datetime:
        captured["tz"] = user_tz
        return now_local

    monkeypatch.setattr("asistente.tools.agenda._now_local", fake_now_local)

    res = await _run(registry, ctx, "create_event", {"title": "Cena", "when": "el viernes"})

    assert not res.ok
    assert "ambigua" in res.error
    assert "Pregunta al usuario" in res.error


async def test_create_task_fecha_interpretada_en_utc(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_task", {"title": "Enviar informe", "due_when": "2026-10-05T17:00"})

    assert res.ok, res.error
    created = services.tasks.created[-1]
    assert created["due_at_utc"].isoformat() == "2026-10-05T15:00:00+00:00"


async def test_complete_task_devuelve_tarea(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "complete_task", {"task_id": 4})

    assert res.ok, res.error
    assert services.tasks.completed == [4]


async def test_create_reminder_suelto_requiere_when(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_reminder", {"message": "Aviso"})

    assert not res.ok
    assert "cuándo" in res.error


async def test_create_reminder_vinculado_a_evento(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_reminder", {"message": "Vas tarde", "event_id": 3, "offset_minutes": 15})

    assert res.ok, res.error
    created = services.reminders.created[-1]
    assert created["event_id"] == 3
    assert created["offset_minutes"] == 15


async def test_delete_event_requiere_confirmacion(registry, ctx) -> None:
    res = await _run(registry, ctx, "delete_event", {"event_id": 2}, confirmed=False)

    assert not res.ok
    assert res.needs_confirmation is True


async def test_get_agenda_usa_el_periodo(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "get_agenda", {"period": "esta semana"})

    assert res.ok, res.error
    assert "events" in res.data
    assert services.agenda.calls[0][0] == 7


async def test_argumentos_invalidos_devuelven_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_task", {"title": ""})

    assert not res.ok
    assert res.error
