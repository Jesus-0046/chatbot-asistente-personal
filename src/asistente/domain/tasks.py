"""Servicio de dominio de tareas (Fase 2 / 2.1).

Estados: pending | completed | cancelled. Las tareas recurrentes se guardan
como una fila + regla: al completarlas, `due_at` avanza a la siguiente
ocurrencia y quedan `pending` de nuevo.

Sincronización de recordatorios (2.1): cuando una tarea avanza a su siguiente
ocurrencia (o cambia de fecha), los recordatorios vinculados `pending` se
re-apuntan a la nueva ocurrencia conservando su offset; cuando la tarea se
completa o cancela definitivamente (sin más ocurrencias), sus recordatorios
pendientes se cancelan para no avisar de una ocurrencia ya terminada. Los
recordatorios ya enviados/cancelados se dejan intactos (no se duplican).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from asistente.persistence.models import Task
from asistente.persistence.repositories import RecurrenceRuleRepository, ReminderRepository, TaskRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.recurrence import RecurrenceSpec, next_occurrence, to_utc
from asistente.shared.timeutil import utc_now


class TaskService:
    def __init__(
        self,
        task_repo: TaskRepository,
        recurrence_repo: RecurrenceRuleRepository,
        reminder_repo: ReminderRepository,
    ) -> None:
        self._tasks = task_repo
        self._rules = recurrence_repo
        self._reminders = reminder_repo

    async def create_batch(self, *, user_id: int, tasks: list[dict[str, Any]]) -> list[Task]:
        """Crea varias tareas en UNA transacción (todas o ninguna).

        Garantiza atomicidad en flujos que materializan varias tareas de una
        vez (p. ej. `apply_extracted_data` con kind=tasks): si una tarea
        falla, ninguna se persiste. Cada ítem acepta `title`, `due_at` (UTC
        aware) y `priority`; el resto usa valores por defecto.
        """
        result: list[Task] = []
        async with self._tasks.transaction() as session:
            for item in tasks:
                row = await self._tasks.create(
                    user_id=user_id,
                    title=item["title"],
                    due_at=item.get("due_at"),
                    timezone=item.get("timezone") or "Europe/Madrid",
                    priority=item.get("priority") or "medium",
                    session=session,
                )
                result.append(row)
            return result

    async def create(
        self,
        *,
        user_id: int,
        title: str,
        description: str | None = None,
        due_at_utc: datetime | None = None,
        timezone: str = "Europe/Madrid",
        priority: str = "medium",
        rule: RecurrenceSpec | None = None,
    ) -> Task:
        rule_id: int | None = None
        if rule is not None and due_at_utc is not None:
            rule_row = await self._rules.create(
                user_id=user_id,
                freq=rule.freq,
                interval=rule.interval,
                days_of_week=list(rule.days_of_week) if rule.days_of_week else None,
                start_at=to_utc(due_at_utc),
                timezone=timezone,
                end_at=to_utc(rule.end_at_utc) if rule.end_at_utc is not None else None,
            )
            rule_id = rule_row.id
        return await self._tasks.create(
            user_id=user_id,
            title=title,
            description=description,
            due_at=to_utc(due_at_utc) if due_at_utc else None,
            timezone=timezone,
            priority=priority,
            recurrence_id=rule_id,
        )

    async def get(self, user_id: int, task_id: int) -> Task:
        task = await self._tasks.find_one(user_id, task_id)
        if task is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")
        return task

    async def list(self, user_id: int, *, status: str | None = None, limit: int = 100) -> list[Task]:
        return await self._tasks.list_for_user(user_id, status=status, limit=limit)

    async def update(self, user_id: int, task_id: int, **fields) -> Task:
        if "due_at" in fields and fields["due_at"] is not None:
            fields["due_at"] = to_utc(fields["due_at"])
        updated = await self._tasks.update(user_id, task_id, **fields)
        if updated is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")
        if "due_at" in fields and fields["due_at"] is not None:
            await self._reminders.resync_task_pending(user_id, task_id, fields["due_at"], 60)
        return updated

    async def complete(self, user_id: int, task_id: int) -> Task:
        """Completa la tarea. Si es recurrente, avanza a la siguiente
        ocurrencia y vuelve a `pending`; si no hay más ocurrencias, queda
        `completed`. En ambos casos sincroniza los recordatorios vinculados."""
        task = await self.get(user_id, task_id)
        if task.status != "pending":
            raise NotFoundError("La tarea no está pendiente: ya fue completada o cancelada.")
        completed_at = utc_now()
        updated = await self._tasks.complete(user_id, task_id, completed_at)
        if updated is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")

        nxt_utc = await self._next_occurrence(user_id, task)
        if nxt_utc is not None:
            # Sigue recurrente: vuelve a pending con el nuevo due_at (UTC aware)
            # y re-apunta sus recordatorios pendientes a la nueva ocurrencia.
            await self._tasks.update(user_id, task_id, status="pending", due_at=nxt_utc)
            await self._reminders.resync_task_pending(user_id, task_id, nxt_utc, 60)
            updated = await self.get(user_id, task_id)
        else:
            # Terminada definitivamente: los avisos pendientes ya no tienen sentido.
            await self._reminders.cancel_task_pending(user_id, task_id)
        return updated

    async def cancel(self, user_id: int, task_id: int) -> Task:
        updated = await self._tasks.update(user_id, task_id, status="cancelled")
        if updated is None:
            raise NotFoundError("La tarea no existe o no te pertenece.")
        await self._reminders.cancel_task_pending(user_id, task_id)
        return updated

    async def delete(self, user_id: int, task_id: int) -> None:
        if not await self._tasks.delete(user_id, task_id):
            raise NotFoundError("La tarea no existe o no te pertenece.")

    async def _next_occurrence(self, user_id: int, task: Task) -> datetime | None:
        """Siguiente `due_at` (UTC aware) o None si la serie ha terminado."""
        if task.recurrence_id is None or task.due_at is None:
            return None
        rule = await self._rules.find_one(user_id, task.recurrence_id)
        if rule is None:
            return None
        spec = RecurrenceSpec(
            id=rule.id,
            freq=rule.freq,
            interval=rule.interval,
            days_of_week=tuple((rule.days_of_week or {}).get("days") or ()),
            start_at_utc=rule.start_at,
            end_at_utc=rule.end_at,
            timezone=rule.timezone,
        )
        nxt = next_occurrence(spec, after_utc=task.due_at)
        if nxt is None:
            return None
        return to_utc(nxt)
