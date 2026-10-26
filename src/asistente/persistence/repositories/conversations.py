"""Repositorio de conversación (memoria corta)."""

from __future__ import annotations

from sqlalchemy import select

from asistente.persistence.models import ConversationMessage
from asistente.persistence.repositories.base import Repository


class ConversationRepository(Repository):
    async def add_many(
        self,
        user_id: int,
        rows: list[tuple[str, str, dict]],
    ) -> None:
        """Persiste varias filas (role, content, meta) en una sola transacción."""
        async with self.db.session() as session:
            session.add_all(
                [
                    ConversationMessage(user_id=user_id, role=role, content=content, meta=meta)
                    for role, content, meta in rows
                ]
            )
            await session.commit()

    async def recent(self, user_id: int, limit: int) -> list[ConversationMessage]:
        """Devuelve los últimos `limit` mensajes en orden cronológico."""
        async with self.db.session() as session:
            result = await session.execute(
                select(ConversationMessage)
                .where(ConversationMessage.user_id == user_id)
                .order_by(ConversationMessage.id.desc())
                .limit(limit)
            )
            rows = list(result.scalars().all())
            rows.reverse()
            return rows
