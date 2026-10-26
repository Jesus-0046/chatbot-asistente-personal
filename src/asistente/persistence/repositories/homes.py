"""Repositorios del módulo de casa (Fase 4), scoped por user_id.

Todo listado filtra por `user_id`. `create`/`update` aceptan `session` para
formar parte de una transacción más amplia (HomeMaintenance + Transaction).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from asistente.persistence.models import Home, HomeMaintenance
from asistente.persistence.repositories.base import Repository


class HomeRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        normalized_name: str,
        home_type: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        active: bool = True,
        session=None,
    ) -> Home:
        async with self._session(session) as s:
            row = Home(
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
                home_type=home_type,
                address=address,
                notes=notes,
                active=active,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, home_id: int) -> Home | None:
        async with self._session() as session:
            result = await session.execute(select(Home).where(Home.id == home_id, Home.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_normalized_name(self, user_id: int, normalized_name: str) -> list[Home]:
        stmt = (
            select(Home)
            .where(Home.user_id == user_id, Home.normalized_name == normalized_name, Home.active.is_(True))
            .order_by(Home.created_at.asc())
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(self, user_id: int, *, only_active: bool = True, limit: int = 100) -> list[Home]:
        stmt = select(Home).where(Home.user_id == user_id)
        if only_active:
            stmt = stmt.where(Home.active.is_(True))
        stmt = stmt.order_by(Home.name.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, home_id: int, *, session=None, **fields) -> Home | None:
        async with self._session(session) as s:
            row = await s.get(Home, home_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row


class HomeMaintenanceRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        home_id: int,
        title: str,
        maintenance_type: str = "revision",
        occurred_at: datetime | None = None,
        cost=None,
        currency: str = "EUR",
        status: str = "pending",
        next_due_at: datetime | None = None,
        note: str | None = None,
        transaction_id: int | None = None,
        session=None,
    ) -> HomeMaintenance:
        async with self._session(session) as s:
            row = HomeMaintenance(
                user_id=user_id,
                home_id=home_id,
                title=title,
                maintenance_type=maintenance_type,
                occurred_at=occurred_at,
                cost=cost,
                currency=currency,
                status=status,
                next_due_at=next_due_at,
                note=note,
                transaction_id=transaction_id,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, maintenance_id: int) -> HomeMaintenance | None:
        async with self._session() as session:
            result = await session.execute(
                select(HomeMaintenance).where(HomeMaintenance.id == maintenance_id, HomeMaintenance.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_home(self, user_id: int, home_id: int, *, limit: int = 30) -> list[HomeMaintenance]:
        stmt = (
            select(HomeMaintenance)
            .where(HomeMaintenance.user_id == user_id, HomeMaintenance.home_id == home_id)
            .order_by(
                HomeMaintenance.occurred_at.desc().nulls_last(),
                HomeMaintenance.created_at.desc(),
            )
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[HomeMaintenance]:
        stmt = select(HomeMaintenance).where(HomeMaintenance.user_id == user_id)
        if status is not None:
            stmt = stmt.where(HomeMaintenance.status == status)
        stmt = stmt.order_by(
            HomeMaintenance.next_due_at.desc(),
            HomeMaintenance.created_at.desc(),
        ).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, maintenance_id: int, *, session=None, **fields) -> HomeMaintenance | None:
        async with self._session(session) as s:
            row = await s.get(HomeMaintenance, maintenance_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row
