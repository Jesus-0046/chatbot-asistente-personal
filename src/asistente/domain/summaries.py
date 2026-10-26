"""Servicio de dominio de resúmenes de vida (Fase 6.1).

Componen los datos ya existentes (agenda expandida, tareas, pagos planificados,
viajes, vehículos, casas, hábitos y finanzas) en vistas listas para el LLM y
el usuario:

- `daily`:      qué hay hoy (eventos, tareas con vencimiento hoy, pagos
                planificados de hoy, viajes que empiezan hoy, atrasos,
                mantenimientos próximos de vehículos/casas).
- `weekly`:     los próximos 7 días (agenda + viajes + pagos planificados +
                hábitos con progreso + vencimientos de seguro/ITV).
- `upcoming`:   próximos N días (por defecto 30), agenda y viajes ordenados.
- `pending`:    lo pendiente AHORA: tareas abiertas (con atraso marcado),
                pagos planificados sin convertir, checklist de viajes abiertos
                y recordatorios pendientes.

Nunca generan movimientos ni cambian datos: solo lectura.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from asistente.domain.agenda import AgendaService
from asistente.domain.planned import PlannedTransactionService
from asistente.persistence.repositories import TaskRepository, TripChecklistItemRepository, TripRepository
from asistente.shared.timeutil import now_in_timezone, resolve_timezone, to_local, to_utc


def _local_iso(dt: datetime | None, timezone: str) -> str | None:
    if dt is None:
        return None
    return to_local(dt, timezone).isoformat()


class SummaryService:
    def __init__(
        self,
        agenda: AgendaService,
        tasks: TaskRepository,
        planned: PlannedTransactionService,
        trips: TripRepository,
        checklist: TripChecklistItemRepository,
        vehicles=None,
        homes=None,
        habits=None,
        reminders=None,
    ) -> None:
        self._agenda = agenda
        self._tasks = tasks
        self._planned = planned
        self._trips = trips
        self._checklist = checklist
        self._vehicles = vehicles
        self._homes = homes
        self._habits = habits
        self._reminders = reminders

    async def daily(self, user_id: int, *, when_utc: datetime | None = None, timezone: str = "Europe/Madrid") -> dict:
        now = when_utc or now_in_timezone(timezone)
        local = now.astimezone(resolve_timezone(timezone))
        start_local = datetime.combine(local.date(), time.min, tzinfo=resolve_timezone(timezone))
        start_utc = to_utc(start_local)
        end_utc = to_utc(start_local + timedelta(days=1))

        agenda = await self._agenda.during_period(user_id, start_utc, end_utc, timezone=timezone)
        planned = await self._planned.list_planned(
            user_id, status="planned", start_utc=start_utc, end_utc=end_utc, limit=100
        )
        trips = await self._trips.list_in_window(user_id, start_utc, end_utc)
        tasks = await self._tasks.list_pending(user_id, limit=200)
        overdue = [t for t in tasks if t.due_at is not None and t.due_at < now]
        pending_today = [t for t in tasks if t.due_at is not None and start_utc <= t.due_at < end_utc]

        result: dict = {
            "date": str(local.date()),
            "timezone": timezone,
            "events": agenda["events"],
            "tasks_due_today": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": _local_iso(t.due_at, timezone),
                    "priority": t.priority,
                }
                for t in pending_today
            ],
            "planned_due_today": self._planned_to_dict(planned),
            "trips_starting_today": [
                {
                    "id": t.id,
                    "name": t.name,
                    "destination": t.destination,
                    "start_at": to_local(t.start_at, timezone).isoformat() if t.start_at else None,
                    "pending_checklist": await self._checklist.count_open(user_id, t.id),
                }
                for t in trips
            ],
            "overdue_tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": _local_iso(t.due_at, timezone),
                    "priority": t.priority,
                }
                for t in overdue
            ],
            "overdue_task_count": len(overdue),
        }
        vehicle_maintenance = []
        if self._vehicles is not None:
            try:
                exp = await self._vehicles.get_expirations(user_id)
                for v_item in exp.get("expirations", []):
                    for m in v_item.get("itv", []):
                        vehicle_maintenance.append(
                            {
                                "type": "vehicle_itv",
                                "vehicle_id": v_item["vehicle"].id,
                                "vehicle_name": v_item["vehicle"].name,
                                "next_due": _local_iso(m.next_due_at, timezone),
                            }
                        )
            except Exception:
                pass
        result["vehicle_maintenance_upcoming"] = vehicle_maintenance
        home_maintenance = []
        if self._homes is not None:
            try:
                upcoming = await self._homes.get_upcoming(user_id)
                for m in upcoming.get("upcoming", [])[:10]:
                    home_maintenance.append(
                        {
                            "type": "home_maintenance",
                            "home_id": m.home_id,
                            "description": m.description,
                            "next_due": _local_iso(m.next_due_at, timezone),
                            "priority": m.priority,
                        }
                    )
            except Exception:
                pass
        result["home_maintenance_upcoming"] = home_maintenance
        return result

    async def weekly(self, user_id: int, *, when_utc: datetime | None = None, timezone: str = "Europe/Madrid") -> dict:
        now = when_utc or now_in_timezone(timezone)
        end_utc = to_utc(now + timedelta(days=7))
        agenda = await self._agenda.during_period(user_id, now, end_utc, timezone=timezone)
        planned = await self._planned.list_planned(user_id, status="planned", start_utc=now, end_utc=end_utc, limit=200)
        trips = await self._trips.list_in_window(user_id, now, end_utc)
        result: dict = {
            "window": "next_7_days",
            "timezone": timezone,
            "event_count": len(agenda["events"]),
            "task_count": len(agenda["tasks"]),
            "events": agenda["events"],
            "tasks": agenda["tasks"],
            "planned": self._planned_to_dict(planned),
            "trips": [
                {
                    "id": t.id,
                    "name": t.name,
                    "destination": t.destination,
                    "start_at": to_local(t.start_at, timezone).isoformat() if t.start_at else None,
                    "end_at": to_local(t.end_at, timezone).isoformat() if t.end_at else None,
                    "pending_checklist": await self._checklist.count_open(user_id, t.id),
                }
                for t in trips
            ],
            "trip_count": len(trips),
        }
        habits_data = []
        if self._habits is not None:
            try:
                habits_progress = await self._habits.list_with_progress(user_id, timezone=timezone)
                for hp in habits_progress:
                    habit = hp.get("habit")
                    if habit is None:
                        continue
                    habits_data.append(
                        {
                            "id": habit.id,
                            "name": habit.name,
                            "period": habit.period,
                            "count": hp.get("count", 0),
                            "target": str(hp.get("target")) if hp.get("target") is not None else None,
                            "achieved": hp.get("achieved"),
                        }
                    )
            except Exception:
                pass
        result["habits"] = habits_data
        result["habit_count"] = len(habits_data)
        vehicle_expirations = []
        if self._vehicles is not None:
            try:
                exp = await self._vehicles.get_expirations(user_id)
                for v_item in exp.get("expirations", []):
                    vehicle = v_item["vehicle"]
                    ins_list = []
                    for ins in v_item.get("insurance", []):
                        ins_list.append(
                            {
                                "id": ins.id,
                                "provider": ins.provider,
                                "end_date": ins.end_date.isoformat() if ins.end_date else None,
                            }
                        )
                    itv_list = []
                    for m in v_item.get("itv", []):
                        itv_list.append(
                            {
                                "id": m.id,
                                "next_due": _local_iso(m.next_due_at, timezone),
                            }
                        )
                    vehicle_expirations.append(
                        {
                            "vehicle_id": vehicle.id,
                            "vehicle_name": vehicle.name,
                            "insurance": ins_list,
                            "itv": itv_list,
                        }
                    )
            except Exception:
                pass
        result["vehicle_expirations"] = vehicle_expirations
        return result

    async def upcoming(
        self,
        user_id: int,
        *,
        days: int = 30,
        when_utc: datetime | None = None,
        timezone: str = "Europe/Madrid",
    ) -> dict:
        now = when_utc or now_in_timezone(timezone)
        end_utc = to_utc(now + timedelta(days=max(1, days)))
        agenda = await self._agenda.during_period(user_id, now, end_utc, timezone=timezone)
        trips = await self._trips.list_in_window(user_id, now, end_utc)
        items = [
            {"when": e["start_at_utc"], "kind": "event", "title": e["title"], "at": e.get("start_at"), "id": e["id"]}
            for e in agenda["events"]
        ]
        items += [
            {
                "when": (t.get("due_at") or ""),
                "kind": "task",
                "title": t["title"],
                "at": t.get("due_at"),
                "id": t["id"],
            }
            for t in agenda["tasks"]
            if t.get("due_at")
        ]

        def _sort_key(i):
            w = i["when"]
            if isinstance(w, datetime):
                return w
            try:
                return datetime.fromisoformat(str(w))
            except (ValueError, TypeError):
                return datetime.max.replace(tzinfo=UTC)

        items.sort(key=_sort_key)
        return {
            "days": max(1, days),
            "timezone": timezone,
            "count": len(items),
            "items": items,
            "trips": [
                {
                    "id": t.id,
                    "name": t.name,
                    "destination": t.destination,
                    "start_at": to_local(t.start_at, timezone).isoformat() if t.start_at else None,
                }
                for t in trips
            ],
        }

    async def pending(self, user_id: int, *, when_utc: datetime | None = None, timezone: str = "Europe/Madrid") -> dict:
        now = when_utc or now_in_timezone(timezone)
        tasks = await self._tasks.list_pending(user_id, limit=300)
        planned = await self._planned.list_planned(user_id, status="planned", limit=300)
        overdue_tasks = [t for t in tasks if t.due_at is not None and t.due_at < now]
        total_planned = sum((Decimal(str(p.amount)) for p in planned), Decimal("0.00"))
        result: dict = {
            "timezone": timezone,
            "task_count": len(tasks),
            "overdue_task_count": len(overdue_tasks),
            "overdue_tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": _local_iso(t.due_at, timezone),
                    "priority": t.priority,
                }
                for t in overdue_tasks
            ],
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": to_local(t.due_at, timezone).isoformat() if t.due_at else None,
                    "priority": t.priority,
                }
                for t in tasks
            ],
            "planned_payment_count": len(planned),
            "planned_total_amount": str(total_planned),
            "planned": self._planned_to_dict(planned),
        }
        open_checklist = []
        try:
            trips_all = await self._trips.list_for_user(user_id, status="active")
            for trip in trips_all[:20]:
                count = await self._checklist.count_open(user_id, trip.id)
                if count > 0:
                    open_checklist.append(
                        {
                            "trip_id": trip.id,
                            "trip_name": trip.name,
                            "open_items": count,
                        }
                    )
        except Exception:
            pass
        result["open_checklist"] = open_checklist
        result["open_checklist_total"] = sum(c["open_items"] for c in open_checklist)
        pending_reminders = []
        if self._reminders is not None:
            try:
                rems = await self._reminders.list_pending(user_id, limit=20)
                for r in rems:
                    pending_reminders.append(
                        {
                            "id": r.id,
                            "message": r.message,
                            "remind_at": _local_iso(r.remind_at, timezone),
                            "event_id": r.event_id,
                            "task_id": r.task_id,
                        }
                    )
            except Exception:
                pass
        result["pending_reminders"] = pending_reminders
        result["pending_reminder_count"] = len(pending_reminders)
        return result

    @staticmethod
    def _planned_to_dict(planned_items) -> list[dict]:
        return [
            {
                "id": p.id,
                "title": p.title or p.merchant or "Pago planificado",
                "amount": str(p.amount),
                "currency": p.currency,
                "planned_at": p.planned_at.isoformat() if p.planned_at else None,
                "category": p.category,
            }
            for p in planned_items
        ]
