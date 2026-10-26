"""Servicio de dominio de recordatorios (Fase 2 / 2.1).

Un recordatorio puede estar vinculado a un evento (`event_id`), a una tarea
(`task_id`) o ser independiente. Los vinculados guardan `offset_minutes`
(minutos antes) y `remind_at` se calcula desde la fecha del evento/tarea.
El envío real lo gestiona `ReminderPoller` (capa scheduler) tras reclamar los
vencidos; aquí solo se modelan datos y reglas.

Claim atómico (2.1): `claim_due` marca `processing` + `claimed_at`; las
operaciones de liquidación (`mark_sent`/`advance`/`cancel_claimed`/`release`)
llevan el `claimed_at` del worker que reclamó como guarda de propiedad.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from asistente.persistence.models import Reminder
from asistente.persistence.repositories import (
    EventRepository,
    RecurrenceRuleRepository,
    ReminderRepository,
    TaskRepository,
)
from asistente.shared.errors import NotFoundError
from asistente.shared.recurrence import RecurrenceSpec, next_occurrence
from asistente.shared.timeutil import to_utc, utc_now


class ReminderService:
    def __init__(
        self,
        reminder_repo: ReminderRepository,
        event_repo: EventRepository,
        task_repo: TaskRepository,
        recurrence_repo: RecurrenceRuleRepository,
    ) -> None:
        self._reminders = reminder_repo
        self._events = event_repo
        self._tasks = task_repo
        self._rules = recurrence_repo

    async def create(
        self,
        *,
        user_id: int,
        message: str | None = None,
        remind_at_utc: datetime | None = None,
        event_id: int | None = None,
        task_id: int | None = None,
        offset_minutes: int | None = None,
    ) -> Reminder:
        if event_id is not None and task_id is not None:
            raise ValueError("Un recordatorio no puede vincular a evento y tarea a la vez.")
        timezone = "Europe/Madrid"
        resolved_offset: int | None = None
        if event_id is not None:
            event = await self._events.find_one(user_id, event_id)
            if event is None:
                raise NotFoundError("El evento de referencia no existe o no te pertenece.")
            offset = offset_minutes if offset_minutes is not None else 60
            resolved_offset = offset
            remind_at = event.start_at - timedelta(minutes=offset)
            timezone = event.timezone
            message = message or f"Recordatorio: {event.title}"
            event_id = event.id
            task_id = None
        elif task_id is not None:
            task = await self._tasks.find_one(user_id, task_id)
            if task is None:
                raise NotFoundError("La tarea de referencia no existe o no te pertenece.")
            if task.due_at is None:
                raise ValueError("No se puede vincular un recordatorio a una tarea sin fecha.")
            offset = offset_minutes if offset_minutes is not None else 60
            resolved_offset = offset
            remind_at = task.due_at - timedelta(minutes=offset)
            timezone = task.timezone
            message = message or f"Tarea: {task.title}"
            event_id = None
            task_id = task.id
        else:
            if remind_at_utc is None:
                raise ValueError("Un recordatorio independiente requiere remind_at_utc.")
            remind_at = remind_at_utc
            message = message or "Recordatorio"
            offset_minutes = None

        return await self._reminders.create(
            user_id=user_id,
            message=message,
            remind_at=remind_at,
            event_id=event_id,
            task_id=task_id,
            timezone=timezone,
            offset_minutes=resolved_offset,
        )

    async def get(self, user_id: int, reminder_id: int) -> Reminder:
        reminder = await self._reminders.find_one(user_id, reminder_id)
        if reminder is None:
            raise NotFoundError("El recordatorio no existe o no te pertenece.")
        return reminder

    async def list_all(self, user_id: int, *, status: str | None = None, limit: int = 100) -> list[Reminder]:
        return await self._reminders.list_for_user(user_id, status=status, limit=limit)

    async def cancel(self, user_id: int, reminder_id: int) -> Reminder:
        reminder = await self._reminders.cancel(user_id, reminder_id)
        if reminder is None:
            raise NotFoundError("El recordatorio no existe, no te pertenece o ya no está activo.")
        return reminder

    async def delete(self, user_id: int, reminder_id: int) -> None:
        if not await self._reminders.delete(user_id, reminder_id):
            raise NotFoundError("El recordatorio no existe o no te pertenece.")

    async def compute_next(self, user_id: int, reminder: Reminder) -> datetime | None:
        """Próximo `remind_at` si el recordatorio sigue una recurrencia.

        Se avanza a la siguiente ocurrencia a partir de la ocurrencia a la que
        pertenece este aviso (`remind_at + offset`), no desde la base de la
        serie: así un recordatorio recurrente nunca se re-programa a una
        ocurrencia ya avisada. Para recordatorios sueltos o sin recurrencia
        devuelve None (el poller los marca como enviados).
        """
        offset = reminder.offset_minutes if reminder.offset_minutes is not None else 60
        occurrence_utc = to_utc(reminder.remind_at) + timedelta(minutes=offset)

        if reminder.event_id is not None:
            event = await self._events.find_one(user_id, reminder.event_id)
            if event is None or event.recurrence_id is None:
                return None
            rule = await self._rules.find_one(user_id, event.recurrence_id)
            if rule is None:
                return None
            spec = self._spec_for(rule)
            nxt = next_occurrence(spec, after_utc=occurrence_utc)
            if nxt is None:
                return None
            return to_utc(nxt) - timedelta(minutes=offset)

        if reminder.task_id is not None:
            task = await self._tasks.find_one(user_id, reminder.task_id)
            if task is None or task.recurrence_id is None or task.due_at is None:
                return None
            rule = await self._rules.find_one(user_id, task.recurrence_id)
            if rule is None:
                return None
            spec = self._spec_for(rule)
            nxt = next_occurrence(spec, after_utc=occurrence_utc)
            if nxt is None:
                return None
            return to_utc(nxt) - timedelta(minutes=offset)

        return None

    @staticmethod
    def _spec_for(rule) -> RecurrenceSpec:
        return RecurrenceSpec(
            id=rule.id,
            freq=rule.freq,
            interval=rule.interval,
            days_of_week=tuple((rule.days_of_week or {}).get("days") or ()),
            start_at_utc=rule.start_at,
            end_at_utc=rule.end_at,
            timezone=rule.timezone,
        )

    async def claim_due(self, now_utc: datetime, limit: int, lease: timedelta) -> list[Reminder]:
        return await self._reminders.claim_due(now_utc, limit, lease=lease)

    async def advance(
        self, user_id: int, reminder_id: int, new_remind_at: datetime, *, claimed_at: datetime
    ) -> Reminder | None:
        return await self._reminders.advance(user_id, reminder_id, new_remind_at, claimed_at=claimed_at)

    async def mark_sent(self, user_id: int, reminder_id: int, *, claimed_at: datetime) -> Reminder | None:
        return await self._reminders.mark_sent(user_id, reminder_id, utc_now(), claimed_at=claimed_at)

    async def cancel_claimed(self, user_id: int, reminder_id: int, *, claimed_at: datetime) -> Reminder | None:
        return await self._reminders.cancel_claimed(user_id, reminder_id, claimed_at=claimed_at)

    async def release(self, user_id: int, reminder_id: int, *, claimed_at: datetime) -> Reminder | None:
        return await self._reminders.release(user_id, reminder_id, claimed_at=claimed_at)

    async def resync_task_pending(self, user_id: int, task_id: int, new_due_utc: datetime) -> int:
        return await self._reminders.resync_task_pending(user_id, task_id, new_due_utc, 60)

    async def resync_event_pending(self, user_id: int, event_id: int, new_start_utc: datetime) -> int:
        return await self._reminders.resync_event_pending(user_id, event_id, new_start_utc, 60)

    async def cancel_task_pending(self, user_id: int, task_id: int) -> int:
        return await self._reminders.cancel_task_pending(user_id, task_id)

    async def cancel_event_pending(self, user_id: int, event_id: int) -> int:
        return await self._reminders.cancel_event_pending(user_id, event_id)
