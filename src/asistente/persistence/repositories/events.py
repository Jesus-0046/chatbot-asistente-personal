"""Repositorio de eventos (scoped por user_id).

Las fechas se guardan como UTC aware (`DateTime(timezone=True)`); las
consultas de rango reciben límites ya convertidos a UTC.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from asistente.persistence.models import Event
from asistente.persistence.repositories.base import Repository

_EVENT_DEFAULT_FIELDS = (
    "title",
    "description",
    "kind",
    "start_at",
    "end_at",
    "all_day",
    "timezone",
    "location",
    "status",
    "priority",
    "recurrence_id",
    "person",
    "category",
    "trip_id",
)


class EventRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        title: str,
        start_at: datetime,
        description: str | None = None,
        kind: str = "event",
        end_at: datetime | None = None,
        all_day: bool = False,
        timezone: str = "Europe/Madrid",
        location: str | None = None,
        status: str = "scheduled",
        priority: str = "medium",
        recurrence_id: int | None = None,
        person: str | None = None,
        category: str | None = None,
        trip_id: int | None = None,
        session=None,
    ) -> Event:
        async with self._session(session) as s:
            row = Event(
                user_id=user_id,
                title=title,
                start_at=start_at,
                description=description,
                kind=kind,
                end_at=end_at,
                all_day=all_day,
                timezone=timezone,
                location=location,
                status=status,
                priority=priority,
                recurrence_id=recurrence_id,
                person=person,
                category=category,
                trip_id=trip_id,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, event_id: int) -> Event | None:
        async with self.db.session() as session:
            result = await session.execute(select(Event).where(Event.id == event_id, Event.user_id == user_id))
            return result.scalar_one_or_none()

    async def list_in_range(
        self,
        user_id: int,
        end_utc: datetime,
        *,
        include_cancelled: bool = False,
        limit: int = 500,
    ) -> list[Event]:
        """Eventos relevantes hasta `end_utc` (sin cota inferior).

        Incluye eventos base de recurrencias anteriores al rango; la expasión
        de ocurrencias la hace `AgendaService` (el repo no materializa nada).
        """
        async with self.db.session() as session:
            stmt = select(Event).where(Event.user_id == user_id, Event.start_at < end_utc)
            if not include_cancelled:
                stmt = stmt.where(Event.status != "cancelled")
            stmt = stmt.order_by(Event.start_at).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Event]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.user_id == user_id, Event.status != "cancelled")
                .order_by(Event.start_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def update(self, user_id: int, event_id: int, **fields) -> Event | None:
        allowed = {k: v for k, v in fields.items() if k in _EVENT_DEFAULT_FIELDS}
        if not allowed:
            return await self.find_one(user_id, event_id)
        async with self.db.session() as session:
            result = await session.execute(
                update(Event)
                .where(Event.id == event_id, Event.user_id == user_id)
                .values(**allowed)
                .returning(Event.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, event_id)

    async def delete(self, user_id: int, event_id: int) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                sa_delete(Event).where(Event.id == event_id, Event.user_id == user_id).returning(Event.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found

    async def list_for_trip(
        self, user_id: int, trip_id: int, *, status: str = "scheduled", limit: int = 500
    ) -> list[Event]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.user_id == user_id, Event.trip_id == trip_id, Event.status == status)
                .order_by(Event.start_at.asc(), Event.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
