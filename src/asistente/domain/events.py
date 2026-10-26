"""Servicio de dominio de eventos (Fase 2 / 2.1).

Un evento recurrente se guarda como UNA fila + una regla de recurrencia; las
ocurrencias se materializan bajo demanda (ver `AgendaService`), nunca cientos
de filas. Las fechas viajan como UTC aware; la presentación al usuario usa la
zona guardada en la fila.

Separación duración ↔ serie (2.1):
- `event.start_at`/`event.end_at` describen la DURACIÓN de cada ocurrencia.
- `rule.start_at`/`rule.end_at` describen la SERIE: `rule.end_at` es el fin
  explícito de la recurrencia (o NULL si la serie es indefinida) y NUNCA se
  deriva de la duración del evento base.

Actualización de un evento recurrente (2.1): si cambia `start_at`, se conserva
la duración (se desplaza `end_at`), se sincroniza el inicio (y el fin, si la
serie lo tiene) de la regla para no romper la expansión, y se re-apuntan los
recordatorios vinculados `pending` a la nueva fecha manteniendo su offset.
"""

from __future__ import annotations

from datetime import datetime

from asistente.persistence.models import Event
from asistente.persistence.repositories import EventRepository, RecurrenceRuleRepository, ReminderRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.recurrence import RecurrenceSpec, to_utc


class EventService:
    def __init__(
        self,
        event_repo: EventRepository,
        recurrence_repo: RecurrenceRuleRepository,
        reminder_repo: ReminderRepository | None = None,
    ) -> None:
        self._events = event_repo
        self._rules = recurrence_repo
        self._reminders = reminder_repo

    async def create(
        self,
        *,
        user_id: int,
        title: str,
        start_at_utc: datetime,
        end_at_utc: datetime | None = None,
        timezone: str = "Europe/Madrid",
        description: str | None = None,
        location: str | None = None,
        kind: str = "event",
        all_day: bool = False,
        priority: str = "medium",
        person: str | None = None,
        category: str | None = None,
        rule: RecurrenceSpec | None = None,
    ) -> Event:
        rule_id: int | None = None
        if rule is not None:
            # `rule.end_at_utc` es el FIN DE LA SERIE (explícito u opcional);
            # la duración de la ocurrencia base es `end_at_utc` y no limita la
            # regla. Sin fin de serie explícito → rule.end_at = None.
            rule_end = to_utc(rule.end_at_utc) if rule.end_at_utc is not None else None
            rule_row = await self._rules.create(
                user_id=user_id,
                freq=rule.freq,
                interval=rule.interval,
                days_of_week=list(rule.days_of_week) if rule.days_of_week else None,
                start_at=to_utc(start_at_utc),
                timezone=timezone,
                end_at=rule_end,
            )
            rule_id = rule_row.id
        return await self._events.create(
            user_id=user_id,
            title=title,
            start_at=to_utc(start_at_utc),
            end_at=to_utc(end_at_utc) if end_at_utc else None,
            timezone=timezone,
            description=description,
            location=location,
            kind=kind,
            all_day=all_day,
            priority=priority,
            person=person,
            category=category,
            recurrence_id=rule_id,
        )

    async def get(self, user_id: int, event_id: int) -> Event:
        event = await self._events.find_one(user_id, event_id)
        if event is None:
            raise NotFoundError("El evento no existe o no te pertenece.")
        return event

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Event]:
        return await self._events.list_for_user(user_id, limit=limit)

    async def list_in_range(
        self,
        user_id: int,
        end_utc: datetime,
        *,
        include_cancelled: bool = False,
        limit: int = 500,
    ) -> list[Event]:
        return await self._events.list_in_range(user_id, end_utc, include_cancelled=include_cancelled, limit=limit)

    async def update(self, user_id: int, event_id: int, **fields) -> Event:
        event = await self.get(user_id, event_id)

        new_start_utc: datetime | None = None
        if "start_at" in fields and fields["start_at"] is not None:
            new_start_utc = to_utc(fields["start_at"])
            delta = new_start_utc - to_utc(event.start_at)
            # Conservar la duración de la ocurrencia si no se da un nuevo fin.
            if "end_at" not in fields and event.end_at is not None:
                fields["end_at"] = to_utc(event.end_at) + delta
            # Sincronizar la serie: inicio (y fin explícito) de la regla.
            if event.recurrence_id is not None:
                rule = await self._rules.find_one(user_id, event.recurrence_id)
                if rule is not None:
                    rule_updates: dict = {"start_at": new_start_utc}
                    if rule.end_at is not None:
                        rule_updates["end_at"] = to_utc(rule.end_at) + delta
                    await self._rules.update(user_id, rule.id, **rule_updates)

        if "end_at" in fields and fields["end_at"] is not None:
            fields["end_at"] = to_utc(fields["end_at"])
            new_start = new_start_utc or event.start_at
            if fields["end_at"] <= to_utc(new_start):
                raise ValueError("La fecha de fin debe ser posterior al inicio.")

        updated = await self._events.update(user_id, event_id, **fields)
        if updated is None:
            raise NotFoundError("El evento no existe o no te pertenece.")

        if new_start_utc is not None and self._reminders is not None:
            # Re-apuntar los avisos pendientes vinculados al evento manteniendo
            # su offset (no se tocan enviados/cancelados).
            await self._reminders.resync_event_pending(user_id, event_id, new_start_utc, 60)
        return updated

    async def cancel(self, user_id: int, event_id: int) -> Event:
        updated = await self._events.update(user_id, event_id, status="cancelled")
        if updated is None:
            raise NotFoundError("El evento no existe o no te pertenece.")
        if self._reminders is not None:
            await self._reminders.cancel_event_pending(user_id, event_id)
        return updated

    async def delete(self, user_id: int, event_id: int) -> None:
        if not await self._events.delete(user_id, event_id):
            raise NotFoundError("El evento no existe o no te pertenece.")
