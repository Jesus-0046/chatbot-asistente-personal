"""Servicio de dominio de notas (tool mínima de Fase 1)."""

from __future__ import annotations

from asistente.persistence.models import Note
from asistente.persistence.repositories import NoteRepository
from asistente.shared.errors import NotFoundError


class NoteService:
    def __init__(self, note_repo: NoteRepository) -> None:
        self._notes = note_repo

    async def create(self, *, user_id: int, title: str, body: str) -> Note:
        return await self._notes.create(user_id=user_id, title=title, body=body)

    async def get(self, user_id: int, note_id: int) -> Note:
        note = await self._notes.find_one(user_id, note_id)
        if note is None:
            raise NotFoundError("La nota no existe o no te pertenece.")
        return note

    async def list(self, user_id: int, *, limit: int = 50) -> list[Note]:
        return await self._notes.list_for_user(user_id, limit=limit)

    async def set_pinned(self, user_id: int, note_id: int, pinned: bool) -> Note:
        note = await self._notes.set_pinned(user_id, note_id, pinned)
        if note is None:
            raise NotFoundError("La nota no existe o no te pertenece.")
        return note

    async def delete(self, user_id: int, note_id: int) -> None:
        if not await self._notes.delete(user_id, note_id):
            raise NotFoundError("La nota no existe o no te pertenece.")
