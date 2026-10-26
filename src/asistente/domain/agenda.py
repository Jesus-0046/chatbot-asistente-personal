"""Servicio de dominio de agenda (Fase 2).

Composición para `get_agenda`: eventos (con expansión de recurrencias),
tareas pendientes y recordatorios activos dentro de un periodo, siempre en la
zona horaria del usuario. Devuelve dicts listos para JSON (el LLM solo ve
datos, nunca acceso directo a la BD).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from asistente.persistence.repositories import (
    EventRepository,
    RecurrenceRuleRepository,
    ReminderRepository,
    TaskRepository,
)
from asistente.shared.recurrence import RecurrenceSpec, iter_occurrences
from asistente.shared.timeutil import to_local, to_utc


class AgendaService:
    def __init__(
        self,
        event_repo: EventRepository,
        task_repo: TaskRepository,
        reminder_repo: ReminderRepository,
        recurrence_repo: RecurrenceRuleRepository,
    ) -> None:
        self._events = event_repo
        self._tasks = task_repo
        self._reminders = reminder_repo
        self._rules = recurrence_repo

    async def during_period(
        self,
        user_id: int,
        start_utc: datetime,
        end_utc: datetime,
        *,
        timezone: str,
        include_cancelled: bool = False,
    ) -> dict:
        """Agenda completa en [start_utc, end_utc) en `timezone` del usuario."""
        tz = timezone
        events = await self._events.list_in_range(user_id, end_utc, include_cancelled=include_cancelled)
        tasks = await self._tasks.list_due_in_range(user_id, start_utc, end_utc)
        reminders = await self._reminders.list_for_user(user_id, status="pending", limit=200)

        event_items = await self._expand_events(user_id, events, start_utc, end_utc, tz)
        event_items.sort(key=lambda e: e["start_at_utc"])

        task_items = [
            {
                "id": t.id,
                "title": t.title,
                "due_at": to_local(t.due_at, tz).isoformat() if t.due_at else None,
                "priority": t.priority,
                "status": t.status,
            }
            for t in tasks
        ]

        reminder_items = [
            {
                "id": r.id,
                "message": r.message,
                "remind_at": to_local(r.remind_at, tz).isoformat(),
                "status": r.status,
                "linked_event_id": r.event_id,
                "linked_task_id": r.task_id,
            }
            for r in reminders
            if start_utc <= r.remind_at < end_utc
        ]

        return {
            "period_start": start_utc.isoformat(),
            "period_end": end_utc.isoformat(),
            "timezone": tz,
            "events": event_items,
            "tasks": task_items,
            "reminders": reminder_items,
        }

    async def _expand_events(
        self,
        user_id: int,
        events,
        start_utc: datetime,
        end_utc: datetime,
        timezone: str,
    ) -> list[dict]:
        out: list[dict] = []
        local_tz = ZoneInfo(timezone)
        local_start = start_utc.astimezone(local_tz) if start_utc.tzinfo else start_utc
        local_end = end_utc.astimezone(local_tz) if end_utc.tzinfo else end_utc
        for ev in events:
            base = {
                "id": ev.id,
                "title": ev.title,
                "kind": ev.kind,
                "all_day": ev.all_day,
                "location": ev.location,
                "priority": ev.priority,
                "timezone": ev.timezone,
                "category": ev.category,
                "recurring": ev.recurrence_id is not None,
                "start_at_utc": to_utc(ev.start_at),
            }
            if ev.recurrence_id is not None:
                rule = await self._rules.find_one(user_id, ev.recurrence_id)
                if rule is not None:
                    spec = RecurrenceSpec(
                        id=rule.id,
                        freq=rule.freq,
                        interval=rule.interval,
                        days_of_week=tuple((rule.days_of_week or {}).get("days") or ()),
                        start_at_utc=rule.start_at,
                        end_at_utc=rule.end_at,
                        timezone=rule.timezone,
                    )
                    for occ in iter_occurrences(spec, local_start, local_end):
                        item = dict(base)
                        item["start_at_utc"] = to_utc(occ)
                        item["start_at"] = to_local(occ, timezone).isoformat()
                        item["occurrence"] = True
                        item["occurrence_utc"] = occ.isoformat()
                        out.append(item)
                    continue
            if start_utc <= ev.start_at < end_utc:
                item = dict(base)
                item["start_at"] = to_local(ev.start_at, timezone).isoformat()
                item["occurrence"] = False
                out.append(item)
        return out
