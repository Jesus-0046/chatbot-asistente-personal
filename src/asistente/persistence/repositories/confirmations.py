"""Repositorio de confirmaciones pendientes (siempre scoped por user_id)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update

from asistente.persistence.models import PendingConfirmation
from asistente.persistence.repositories.base import Repository


class ConfirmationRepository(Repository):
    async def find_open(self, user_id: int) -> PendingConfirmation | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(PendingConfirmation)
                .where(
                    PendingConfirmation.user_id == user_id,
                    PendingConfirmation.status == "open",
                )
                .order_by(PendingConfirmation.id.desc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: int,
        topic: str,
        payload: dict,
        proposed_reply: str,
        expires_at: datetime,
    ) -> PendingConfirmation:
        async with self.db.session() as session:
            row = PendingConfirmation(
                user_id=user_id,
                topic=topic,
                payload=payload,
                proposed_reply=proposed_reply,
                status="open",
                expires_at=expires_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def expire_overdue(self, user_id: int, now: datetime) -> int:
        """Marca como `expired` las confirmaciones abiertas vencidas."""
        async with self.db.session() as session:
            result = await session.execute(
                update(PendingConfirmation)
                .where(
                    PendingConfirmation.user_id == user_id,
                    PendingConfirmation.status == "open",
                    PendingConfirmation.expires_at <= now,
                )
                .values(status="expired", resolved_at=now)
                .returning(PendingConfirmation.id)
            )
            affected = len(result.scalars().all())
            await session.commit()
            return affected

    async def set_status(self, user_id: int, confirmation_id: int, status: str, now: datetime) -> bool:
        """Actualiza el estado de una confirmación verificando propiedad del usuario.

        Compare-and-set: solo transiciona desde `open`. Así una confirmación ya
        resuelta (confirmada/cancelada/vencida) nunca se puede re-resolver, lo
        que impide la doble ejecución de una tool confirmada aunque lleguen dos
        "sí" en paralelo (solo el primero gana).
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(PendingConfirmation)
                .where(
                    PendingConfirmation.id == confirmation_id,
                    PendingConfirmation.user_id == user_id,
                    PendingConfirmation.status == "open",
                )
                .values(status=status, resolved_at=now)
                .returning(PendingConfirmation.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found
