"""Repositorio de tareas (scoped por user_id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from asistente.persistence.models import Task
from asistente.persistence.repositories.base import Repository

_TASK_DEFAULT_FIELDS = ("title", "description", "due_at", "timezone", "status", "priority", "recurrence_id", "trip_id")


class TaskRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        title: str,
        description: str | None = None,
        due_at: datetime | None = None,
        timezone: str = "Europe/Madrid",
        status: str = "pending",
        priority: str = "medium",
        recurrence_id: int | None = None,
        trip_id: int | None = None,
        session: AsyncSession | None = None,
    ) -> Task:
        async with self._session(session) as session:
            row = Task(
                user_id=user_id,
                title=title,
                description=description,
                due_at=due_at,
                timezone=timezone,
                status=status,
                priority=priority,
                recurrence_id=recurrence_id,
                trip_id=trip_id,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, task_id: int) -> Task | None:
        async with self.db.session() as session:
            result = await session.execute(select(Task).where(Task.id == task_id, Task.user_id == user_id))
            return result.scalar_one_or_none()

    async def list_pending(self, user_id: int, *, limit: int = 100) -> list[Task]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Task)
                .where(Task.user_id == user_id, Task.status == "pending")
                .order_by(Task.created_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def list_due_in_range(
        self,
        user_id: int,
        start_utc: datetime,
        end_utc: datetime,
        *,
        limit: int = 100,
    ) -> list[Task]:
        """Tareas pendientes con vencimiento en [start_utc, end_utc)."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Task)
                .where(
                    Task.user_id == user_id,
                    Task.status == "pending",
                    Task.due_at.is_not(None),
                    Task.due_at >= start_utc,
                    Task.due_at < end_utc,
                )
                .order_by(Task.due_at.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def list_for_user(self, user_id: int, *, status: str | None = None, limit: int = 100) -> list[Task]:
        async with self.db.session() as session:
            stmt = select(Task).where(Task.user_id == user_id)
            if status:
                stmt = stmt.where(Task.status == status)
            stmt = stmt.order_by(Task.due_at.is_(None), Task.due_at.asc(), Task.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, task_id: int, **fields) -> Task | None:
        allowed = {k: v for k, v in fields.items() if k in _TASK_DEFAULT_FIELDS}
        if not allowed:
            return await self.find_one(user_id, task_id)
        async with self.db.session() as session:
            result = await session.execute(
                update(Task).where(Task.id == task_id, Task.user_id == user_id).values(**allowed).returning(Task.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, task_id)

    async def complete(self, user_id: int, task_id: int, completed_at: datetime) -> Task | None:
        async with self.db.session() as session:
            result = await session.execute(
                update(Task)
                .where(Task.id == task_id, Task.user_id == user_id, Task.status == "pending")
                .values(status="completed", completed_at=completed_at)
                .returning(Task.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, task_id)

    async def delete(self, user_id: int, task_id: int) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                sa_delete(Task).where(Task.id == task_id, Task.user_id == user_id).returning(Task.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found

    async def list_for_trip(
        self, user_id: int, trip_id: int, *, status: str = "pending", limit: int = 500
    ) -> list[Task]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Task)
                .where(Task.user_id == user_id, Task.trip_id == trip_id, Task.status == status)
                .order_by(Task.due_at.asc(), Task.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
