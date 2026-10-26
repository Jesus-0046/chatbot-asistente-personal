"""Repositorios de viajes (Fase 6), scoped por user_id.

Un viaje nunca se borra físicamente si tiene histórico: se `cancel` o
`complete` (cambios de estado). Los ítems de checklist (`trip_checklist_items`)
tienen FK RESTRICT hacia `trips`, así que un borrado físico solicitaría que no
existan ítems asociados; en la app viajes no se borran físicamente.

Los métodos aceptan `session` para participar en transacciones compuestas.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

from asistente.persistence.models import Trip, TripChecklistItem
from asistente.persistence.repositories.base import Repository

_TRIP_FIELDS = (
    "name",
    "normalized_name",
    "destination",
    "start_at",
    "end_at",
    "status",
    "trip_type",
    "timezone",
    "budget",
    "notes",
)

_OPEN_STATUSES = ("planned", "active")


class TripRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        normalized_name: str,
        destination: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        status: str = "planned",
        trip_type: str = "other",
        timezone: str = "Europe/Madrid",
        budget=None,
        notes: str | None = None,
        session=None,
    ) -> Trip:
        async with self._session(session) as s:
            row = Trip(
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
                destination=destination,
                start_at=start_at,
                end_at=end_at,
                status=status,
                trip_type=trip_type,
                timezone=timezone,
                budget=budget,
                notes=notes,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, trip_id: int) -> Trip | None:
        async with self.db.session() as session:
            result = await session.execute(select(Trip).where(Trip.id == trip_id, Trip.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_normalized_name(self, user_id: int, normalized_name: str) -> list[Trip]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Trip)
                .where(Trip.user_id == user_id, Trip.normalized_name == normalized_name)
                .order_by(Trip.created_at.asc())
            )
            return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Trip]:
        stmt = select(Trip).where(Trip.user_id == user_id)
        if status:
            stmt = stmt.where(Trip.status == status)
        stmt = stmt.order_by(Trip.start_at.is_(None), Trip.start_at.asc(), Trip.created_at.desc()).limit(limit)
        async with self.db.session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_in_window(
        self,
        user_id: int,
        start_utc: datetime,
        end_utc: datetime,
        *,
        statuses: tuple[str, ...] = _OPEN_STATUSES,
        limit: int = 100,
    ) -> list[Trip]:
        """Viajes abiertos (planned/active) que empiezan en [start_utc, end_utc)."""
        stmt = (
            select(Trip)
            .where(
                Trip.user_id == user_id,
                Trip.status.in_(statuses),
                Trip.start_at.is_not(None),
                Trip.start_at >= start_utc,
                Trip.start_at < end_utc,
            )
            .order_by(Trip.start_at.asc())
            .limit(limit)
        )
        async with self.db.session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, trip_id: int, *, session=None, **fields) -> Trip | None:
        allowed = {k: v for k, v in fields.items() if k in _TRIP_FIELDS}
        if not allowed:
            return await self.find_one(user_id, trip_id)
        async with self._session(session) as s:
            row = await s.get(Trip, trip_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in allowed.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row


class TripChecklistItemRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        trip_id: int,
        text: str,
        is_done: bool = False,
        session=None,
    ) -> TripChecklistItem:
        async with self._session(session) as s:
            row = TripChecklistItem(
                user_id=user_id,
                trip_id=trip_id,
                text=text,
                is_done=is_done,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, item_id: int) -> TripChecklistItem | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(TripChecklistItem).where(TripChecklistItem.id == item_id, TripChecklistItem.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_trip(self, user_id: int, trip_id: int, *, limit: int = 200) -> list[TripChecklistItem]:
        async with self.db.session() as session:
            result = await session.execute(
                select(TripChecklistItem)
                .where(TripChecklistItem.user_id == user_id, TripChecklistItem.trip_id == trip_id)
                .order_by(TripChecklistItem.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def set_done(self, user_id: int, item_id: int, is_done: bool) -> TripChecklistItem | None:
        async with self.db.session() as session:
            result = await session.execute(
                update(TripChecklistItem)
                .where(TripChecklistItem.id == item_id, TripChecklistItem.user_id == user_id)
                .values(is_done=is_done)
                .returning(TripChecklistItem.id)
            )
            found = result.scalar_one_or_none()
            await session.commit()
            if found is None:
                return None
            return await self.find_one(user_id, item_id)

    async def count_open(self, user_id: int, trip_id: int) -> int:
        async with self.db.session() as session:
            result = await session.execute(
                select(TripChecklistItem.id).where(
                    TripChecklistItem.user_id == user_id,
                    TripChecklistItem.trip_id == trip_id,
                    TripChecklistItem.is_done.is_(False),
                )
            )
            return len(list(result.scalars().all()))

    async def delete(self, user_id: int, item_id: int) -> bool:
        from sqlalchemy import delete as sa_delete

        async with self.db.session() as session:
            result = await session.execute(
                sa_delete(TripChecklistItem)
                .where(TripChecklistItem.id == item_id, TripChecklistItem.user_id == user_id)
                .returning(TripChecklistItem.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found
