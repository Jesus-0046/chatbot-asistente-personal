"""Repositorios del módulo de hábitos (Fase 4), scoped por user_id.

Todo listado filtra por `user_id`. `create`/`update`/`log` aceptan `session`
para que el registro de un hábito y su log se inserten en la misma transacción
si se desea.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from asistente.persistence.models import Habit, HabitLog
from asistente.persistence.repositories.base import Repository


class HabitRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        normalized_name: str,
        description: str | None = None,
        unit: str | None = None,
        target_per_period=None,
        period: str = "week",
        active: bool = True,
        session=None,
    ) -> Habit:
        async with self._session(session) as s:
            row = Habit(
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
                description=description,
                unit=unit,
                target_per_period=target_per_period,
                period=period,
                active=active,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, habit_id: int) -> Habit | None:
        async with self._session() as session:
            result = await session.execute(select(Habit).where(Habit.id == habit_id, Habit.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_normalized_name(self, user_id: int, normalized_name: str) -> list[Habit]:
        stmt = (
            select(Habit)
            .where(Habit.user_id == user_id, Habit.normalized_name == normalized_name, Habit.active.is_(True))
            .order_by(Habit.created_at.asc())
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(self, user_id: int, *, only_active: bool = True, limit: int = 100) -> list[Habit]:
        stmt = select(Habit).where(Habit.user_id == user_id)
        if only_active:
            stmt = stmt.where(Habit.active.is_(True))
        stmt = stmt.order_by(Habit.name.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, habit_id: int, *, session=None, **fields) -> Habit | None:
        async with self._session(session) as s:
            row = await s.get(Habit, habit_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row


class HabitLogRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        habit_id: int,
        value=None,
        logged_at: datetime,
        note: str | None = None,
        session=None,
    ) -> HabitLog:
        async with self._session(session) as s:
            row = HabitLog(
                user_id=user_id,
                habit_id=habit_id,
                value=value,
                logged_at=logged_at,
                note=note,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, log_id: int) -> HabitLog | None:
        async with self._session() as session:
            result = await session.execute(select(HabitLog).where(HabitLog.id == log_id, HabitLog.user_id == user_id))
            return result.scalar_one_or_none()

    async def count_in_window(
        self,
        user_id: int,
        habit_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
    ) -> int:
        async with self._session() as session:
            result = await session.execute(
                select(func.count(HabitLog.id)).where(
                    HabitLog.user_id == user_id,
                    HabitLog.habit_id == habit_id,
                    HabitLog.logged_at >= start_utc,
                    HabitLog.logged_at < end_utc,
                )
            )
            return int(result.scalar_one() or 0)

    async def sum_in_window(
        self,
        user_id: int,
        habit_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
    ) -> Decimal:
        async with self._session() as session:
            result = await session.execute(
                select(func.coalesce(func.sum(HabitLog.value), 0)).where(
                    HabitLog.user_id == user_id,
                    HabitLog.habit_id == habit_id,
                    HabitLog.logged_at >= start_utc,
                    HabitLog.logged_at < end_utc,
                )
            )
            return Decimal(result.scalar_one() or 0)

    async def list_for_habit(self, user_id: int, habit_id: int, *, limit: int = 20) -> list[HabitLog]:
        stmt = (
            select(HabitLog)
            .where(HabitLog.user_id == user_id, HabitLog.habit_id == habit_id)
            .order_by(HabitLog.logged_at.desc(), HabitLog.id.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())
