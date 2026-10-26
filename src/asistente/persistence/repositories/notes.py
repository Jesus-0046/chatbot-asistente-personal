"""Repositorio de notas (scoped por user_id)."""

from __future__ import annotations

from sqlalchemy import select, update

from asistente.persistence.models import Note
from asistente.persistence.repositories.base import Repository


class NoteRepository(Repository):
    async def create(self, *, user_id: int, title: str, body: str) -> Note:
        async with self.db.session() as session:
            row = Note(user_id=user_id, title=title, body=body, pinned=False)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, note_id: int) -> Note | None:
        async with self.db.session() as session:
            result = await session.execute(select(Note).where(Note.id == note_id, Note.user_id == user_id))
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int, *, limit: int = 50) -> list[Note]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Note)
                .where(Note.user_id == user_id)
                .order_by(Note.pinned.desc(), Note.updated_at.desc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def set_pinned(self, user_id: int, note_id: int, pinned: bool) -> Note | None:
        async with self.db.session() as session:
            result = await session.execute(
                update(Note).where(Note.id == note_id, Note.user_id == user_id).values(pinned=pinned).returning(Note.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, note_id)

    async def delete(self, user_id: int, note_id: int) -> bool:
        async with self.db.session() as session:
            from sqlalchemy import delete as sa_delete

            result = await session.execute(
                sa_delete(Note).where(Note.id == note_id, Note.user_id == user_id).returning(Note.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found
