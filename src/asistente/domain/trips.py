"""Servicio de dominio de viajes (Fase 6).

Un viaje es de UN usuario; nunca se borra físicamente si tiene histórico
(movimientos, tareas, eventos...): se `cancel` o `complete` (cambios de estado
que conservan los vínculos). Los ítems de checklist pertenecen al viaje y se
guardan en `trip_checklist_items`.

Integración financiera: los GASTOS de un viaje son `Transaction` reales que se
vinculan con `trip_id`. `link_transactions`/`unlink_transactions` cambian el
`trip_id` SOLO de movimientos del propio usuario; `financial_summary` agrega
el gasto/ingreso del viaje a partir de esas transacciones (nunca crea
movimientos por sí mismo).

Además, un viaje puede agrupar tareas/eventos/recordatorios existentes
(`link_task`/`link_event`/`link_reminder`) reutilizando sus tablas y la columna
`trip_id` añadida en Fase 6.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from asistente.domain.people import normalize_name
from asistente.persistence.models import Event, Reminder, Task, Trip, TripChecklistItem
from asistente.persistence.repositories import (
    EventRepository,
    ReminderRepository,
    TaskRepository,
    TransactionRepository,
    TripChecklistItemRepository,
    TripRepository,
)
from asistente.shared.errors import BusinessError, NotFoundError
from asistente.shared.timeutil import resolve_timezone

TRIP_STATUSES = ("planned", "active", "completed", "cancelled")
TRIP_TYPES = ("leisure", "business", "family", "other")
_MISSING = object()


class TripService:
    def __init__(
        self,
        trip_repo: TripRepository,
        checklist_repo: TripChecklistItemRepository,
        transaction_repo: TransactionRepository,
        tasks: TaskRepository,
        events: EventRepository,
        reminders: ReminderRepository,
    ) -> None:
        self._trips = trip_repo
        self._checklist = checklist_repo
        self._tx = transaction_repo
        self._tasks = tasks
        self._events = events
        self._reminders = reminders

    # ------------------------------------------------------------------ #
    # CRUD de viaje                                                        #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        destination: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        notes: str | None = None,
        status: str = "planned",
        trip_type: str = "other",
        timezone: str | None = None,
        budget=None,
    ) -> Trip:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre del viaje no puede estar vacío.")
        self._validate_dates(start_at, end_at)
        if status not in TRIP_STATUSES:
            raise ValueError(f"Estado de viaje no válido: {status}.")
        if trip_type not in TRIP_TYPES:
            raise ValueError(f"Tipo de viaje no válido: {trip_type}. Debe ser: {', '.join(TRIP_TYPES)}.")
        if timezone is not None:
            resolve_timezone(timezone)
        if budget is not None:
            from decimal import Decimal

            budget = Decimal(str(budget))
            if budget < 0:
                raise ValueError("El presupuesto del viaje no puede ser negativo.")
        resolved_tz = timezone or "Europe/Madrid"
        return await self._trips.create(
            user_id=user_id,
            name=(name or "").strip()[:300],
            normalized_name=normalized[:300],
            destination=(destination or "").strip()[:300] or None,
            start_at=start_at,
            end_at=end_at,
            status=status,
            trip_type=trip_type,
            timezone=resolved_tz,
            budget=budget,
            notes=notes,
        )

    async def get(self, user_id: int, trip_id: int) -> Trip:
        trip = await self._trips.find_one(user_id, trip_id)
        if trip is None:
            raise NotFoundError("El viaje no existe o no te pertenece.")
        return trip

    async def list_trips(self, user_id: int, *, status: str | None = None, limit: int = 100) -> list[Trip]:
        if status and status not in TRIP_STATUSES:
            raise ValueError(f"Estado de viaje no válido: {status}.")
        return await self._trips.list_for_user(user_id, status=status, limit=limit)

    async def find_by_name(self, user_id: int, name: str) -> list[Trip]:
        normalized = normalize_name(name)
        if not normalized:
            return []
        return await self._trips.find_by_normalized_name(user_id, normalized)

    async def update(
        self,
        user_id: int,
        trip_id: int,
        *,
        name: str | None = None,
        destination: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        notes: str | None = None,
        trip_type: str | None = None,
        timezone: str | None = None,
        budget: object = _MISSING,
    ) -> Trip:
        trip = await self.get(user_id, trip_id)
        if trip.status == "cancelled":
            raise BusinessError("No se puede actualizar un viaje cancelado.")
        fields: dict = {}
        if name is not None:
            normalized = normalize_name(name)
            if not normalized:
                raise ValueError("El nombre del viaje no puede estar vacío.")
            fields["name"] = (name or "").strip()[:300]
            fields["normalized_name"] = normalized[:300]
        if destination is not None:
            fields["destination"] = (destination or "").strip()[:300] or None
        if notes is not None:
            fields["notes"] = notes
        if trip_type is not None:
            if trip_type not in TRIP_TYPES:
                raise ValueError(f"Tipo de viaje no válido: {trip_type}. Debe ser: {', '.join(TRIP_TYPES)}.")
            fields["trip_type"] = trip_type
        if timezone is not None:
            resolve_timezone(timezone)
            fields["timezone"] = timezone
        if budget is not _MISSING:
            if budget is None:
                fields["budget"] = None
            else:
                from decimal import Decimal

                budget_val = Decimal(str(budget))
                if budget_val < 0:
                    raise ValueError("El presupuesto del viaje no puede ser negativo.")
                fields["budget"] = budget_val
        new_start = start_at if start_at is not None else trip.start_at
        new_end = end_at if end_at is not None else trip.end_at
        if start_at is not None or end_at is not None:
            self._validate_dates(new_start, new_end)
        if start_at is not None:
            fields["start_at"] = start_at
        if end_at is not None:
            fields["end_at"] = end_at
        if not fields:
            return trip
        updated = await self._trips.update(user_id, trip_id, **fields)
        if updated is None:
            raise NotFoundError("El viaje no existe o no te pertenece.")
        return updated

    async def cancel(self, user_id: int, trip_id: int) -> Trip:
        trip = await self.get(user_id, trip_id)
        if trip.status == "cancelled":
            raise BusinessError("El viaje ya está cancelado.")
        updated = await self._trips.update(user_id, trip_id, status="cancelled")
        assert updated is not None
        return updated

    async def complete(self, user_id: int, trip_id: int) -> Trip:
        trip = await self.get(user_id, trip_id)
        if trip.status == "completed":
            raise BusinessError("El viaje ya está completado.")
        if trip.status == "cancelled":
            raise BusinessError("No se puede completar un viaje cancelado.")
        updated = await self._trips.update(user_id, trip_id, status="completed")
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Checklist                                                            #
    # ------------------------------------------------------------------ #
    async def add_checklist_item(self, user_id: int, trip_id: int, text: str) -> TripChecklistItem:
        await self.get(user_id, trip_id)
        text = (text or "").strip()
        if not text:
            raise ValueError("El texto del ítem de checklist no puede estar vacío.")
        return await self._checklist.create(user_id=user_id, trip_id=trip_id, text=text[:500])

    async def list_checklist(self, user_id: int, trip_id: int) -> list[TripChecklistItem]:
        await self.get(user_id, trip_id)
        return await self._checklist.list_for_trip(user_id, trip_id)

    async def toggle_checklist_item(self, user_id: int, item_id: int, is_done: bool) -> TripChecklistItem:
        item = await self._checklist.find_one(user_id, item_id)
        if item is None:
            raise NotFoundError("El ítem de checklist no existe o no te pertenece.")
        updated = await self._checklist.set_done(user_id, item_id, is_done)
        assert updated is not None
        return updated

    async def delete_checklist_item(self, user_id: int, item_id: int) -> None:
        if not await self._checklist.delete(user_id, item_id):
            raise NotFoundError("El ítem de checklist no existe o no te pertenece.")

    async def pending_checklist_count(self, user_id: int, trip_id: int) -> int:
        return await self._checklist.count_open(user_id, trip_id)

    # ------------------------------------------------------------------ #
    # Vínculo financiero (transacciones)                                   #
    # ------------------------------------------------------------------ #
    async def link_transactions(self, user_id: int, trip_id: int, transaction_ids: list[int]) -> int:
        await self.get(user_id, trip_id)
        linked = await self._tx.set_trip(user_id, [t for t in transaction_ids if t], trip_id)
        if linked < len([t for t in transaction_ids if t]):
            raise NotFoundError("Algunos movimientos no existen o no te pertenecen.")
        return linked

    async def unlink_transactions(self, user_id: int, trip_id: int, transaction_ids: list[int]) -> int:
        await self.get(user_id, trip_id)
        unlinked = await self._tx.set_trip(user_id, [t for t in transaction_ids if t], None)
        if unlinked < len([t for t in transaction_ids if t]):
            raise NotFoundError("Algunos movimientos no existen o no te pertenecen.")
        return unlinked

    async def get_trip_transactions(self, user_id: int, trip_id: int) -> list:
        await self.get(user_id, trip_id)
        return await self._tx.list_for_trip(user_id, trip_id)

    async def financial_summary(self, user_id: int, trip_id: int) -> dict:
        trip = await self.get(user_id, trip_id)
        txns = await self._tx.list_for_trip(user_id, trip_id)
        total_spent = Decimal("0.00")
        total_income = Decimal("0.00")
        by_category: dict[str, Decimal] = {}
        for t in txns:
            if t.type == "expense":
                total_spent += t.amount
                by_category[t.category or "Otros"] = by_category.get(t.category or "Otros", Decimal("0.00")) + t.amount
            elif t.type == "income":
                total_income += t.amount
        result: dict = {
            "trip_id": trip_id,
            "transaction_count": len(txns),
            "total_spent": str(total_spent),
            "total_income": str(total_income),
            "net": str(total_income - total_spent),
            "spent_by_category": {k: str(v) for k, v in sorted(by_category.items())},
            "currency": "EUR",
        }
        budget = trip.budget
        if budget is not None:
            budget_dec = Decimal(str(budget))
            remaining = budget_dec - total_spent
            result["budget"] = str(budget_dec)
            result["remaining_budget"] = str(remaining)
            if budget_dec > 0:
                consumed_pct = (total_spent / budget_dec) * 100
                result["budget_consumed_percentage"] = str(min(consumed_pct, Decimal("100.00")))
            else:
                result["budget_consumed_percentage"] = "100.00" if total_spent > 0 else "0.00"
        else:
            result["budget"] = None
            result["remaining_budget"] = None
            result["budget_consumed_percentage"] = None
        return result

    # ------------------------------------------------------------------ #
    # Vínculo de agenda (tareas / eventos / recordatorios)                 #
    # ------------------------------------------------------------------ #
    async def link_task(self, user_id: int, task_id: int, trip_id: int) -> Task:
        await self.get(user_id, trip_id)
        task = await self._tasks.find_one(user_id, task_id)
        if task is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")
        updated = await self._tasks.update(user_id, task_id, trip_id=trip_id)
        assert updated is not None
        return updated

    async def link_event(self, user_id: int, event_id: int, trip_id: int) -> Event:
        await self.get(user_id, trip_id)
        event = await self._events.find_one(user_id, event_id)
        if event is None:
            raise NotFoundError("El evento no existe o no te pertenece.")
        updated = await self._events.update(user_id, event_id, trip_id=trip_id)
        assert updated is not None
        return updated

    async def link_reminder(self, user_id: int, reminder_id: int, trip_id: int) -> Reminder:
        await self.get(user_id, trip_id)
        reminder = await self._reminders.find_one(user_id, reminder_id)
        if reminder is None:
            raise NotFoundError("El recordatorio no existe o no te pertenece.")
        updated = await self._reminders.set_trip(user_id, reminder_id, trip_id)
        assert updated is not None
        return updated

    async def unlink_task(self, user_id: int, task_id: int) -> Task:
        task = await self._tasks.find_one(user_id, task_id)
        if task is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")
        if task.trip_id is None:
            raise BusinessError("La tarea no está vinculada a ningún viaje.")
        updated = await self._tasks.update(user_id, task_id, trip_id=None)
        assert updated is not None
        return updated

    async def unlink_event(self, user_id: int, event_id: int) -> Event:
        event = await self._events.find_one(user_id, event_id)
        if event is None:
            raise NotFoundError("El evento no existe o no te pertenece.")
        if event.trip_id is None:
            raise BusinessError("El evento no está vinculado a ningún viaje.")
        updated = await self._events.update(user_id, event_id, trip_id=None)
        assert updated is not None
        return updated

    async def unlink_reminder(self, user_id: int, reminder_id: int) -> Reminder:
        reminder = await self._reminders.find_one(user_id, reminder_id)
        if reminder is None:
            raise NotFoundError("El recordatorio no existe o no te pertenece.")
        if reminder.trip_id is None:
            raise BusinessError("El recordatorio no está vinculado a ningún viaje.")
        updated = await self._reminders.set_trip(user_id, reminder_id, None)
        assert updated is not None
        return updated

    async def get_trip_detail(self, user_id: int, trip_id: int) -> dict:
        trip = await self.get(user_id, trip_id)
        checklist = await self._checklist.list_for_trip(user_id, trip_id)
        tasks = await self._tasks.list_for_trip(user_id, trip_id, limit=50)
        events = await self._events.list_for_trip(user_id, trip_id, limit=50)
        reminders = await self._reminders.list_for_trip(user_id, trip_id, limit=50)
        summary = await self.financial_summary(user_id, trip_id)
        return {
            "trip": trip,
            "checklist": checklist,
            "tasks": tasks,
            "events": events,
            "reminders": reminders,
            "financial_summary": summary,
        }

    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_dates(start_at: datetime | None, end_at: datetime | None) -> None:
        for dt in (start_at, end_at):
            if dt is not None and dt.tzinfo is None:
                raise ValueError("Las fechas del viaje deben incluir zona horaria.")
        if start_at is not None and end_at is not None and end_at < start_at:
            raise ValueError("La fecha de fin no puede ser anterior al inicio del viaje.")
