"""Repositorio de personas (scoped por user_id).

Una persona pertenece a UN único usuario. `normalized_name` se mantiene en el
dominio y aquí se indexa para búsqueda/resolución por lenguaje natural.
"""

from __future__ import annotations

from sqlalchemy import select

from asistente.persistence.models import Person
from asistente.persistence.repositories.base import Repository


class PersonRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        normalized_name: str,
        active: bool = True,
    ) -> Person:
        async with self._session() as session:
            row = Person(
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
                active=active,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, person_id: int) -> Person | None:
        async with self._session() as session:
            result = await session.execute(select(Person).where(Person.id == person_id, Person.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_normalized_name(self, user_id: int, normalized_name: str) -> list[Person]:
        stmt = (
            select(Person)
            .where(Person.user_id == user_id, Person.normalized_name == normalized_name, Person.active.is_(True))
            .order_by(Person.created_at.asc())
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        *,
        only_active: bool = True,
        limit: int = 100,
    ) -> list[Person]:
        stmt = select(Person).where(Person.user_id == user_id)
        if only_active:
            stmt = stmt.where(Person.active.is_(True))
        stmt = stmt.order_by(Person.name.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, person_id: int, **fields) -> Person | None:
        async with self._session() as session:
            row = await session.get(Person, person_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await session.flush()
            await session.refresh(row)
            return row
