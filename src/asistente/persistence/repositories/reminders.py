"""Repositorio de recordatorios (scoped por user_id).

Claim atómico (Fase 2.1)
------------------------
`claim_due` marca los recordatorios reclamados como `processing` y les fija
`claimed_at` DENTRO de una transacción y devuelve esos mismos registros. Así:

- Dos pollers concurrentes NO pueden reclamar el mismo recordatorio: el primero
  en marcar la fila como `processing` la saca del conjunto de candidatas; el
  segundo `claim_due` no la ve. En PostgreSQL, además, `FOR UPDATE SKIP LOCKED`
  evita cola entre los dos pasos (select de ids + update), pero el diseño NO
  depende del lock SQL: el estado `processing` persiste tras liberarse el lock.
- Si el proceso muere tras el claim y antes del envío, `claimed_at` queda
  viejo y `claim_due` la recupera pasada la duración del lease (timeout).
- Las operaciones de liquidación (`mark_sent`/`advance`/`cancel_claimed`/
  `release`) exigen `status == 'processing'` Y que `claimed_at` coincida con
  el del worker que reclamó: un lease expirado y re-clamado por otro worker
  jamás se liquida por el worker zombie (optimistic lock sobre el claim).

En dialectos sin soporte (sqlite en tests/in-memory) `FOR UPDATE` es no-op y
los tests usan repos falsos, no el camino PG.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy import delete as sa_delete

from asistente.persistence.models import Reminder
from asistente.persistence.repositories.base import Repository

_DUE_STATUSES = ("pending", "processing")


class ReminderRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        message: str,
        remind_at: datetime,
        event_id: int | None = None,
        task_id: int | None = None,
        timezone: str = "Europe/Madrid",
        offset_minutes: int | None = None,
        trip_id: int | None = None,
    ) -> Reminder:
        async with self.db.session() as session:
            row = Reminder(
                user_id=user_id,
                message=message,
                remind_at=remind_at,
                event_id=event_id,
                task_id=task_id,
                timezone=timezone,
                offset_minutes=offset_minutes,
                trip_id=trip_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, reminder_id: int) -> Reminder | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(Reminder).where(Reminder.id == reminder_id, Reminder.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[Reminder]:
        async with self.db.session() as session:
            stmt = select(Reminder).where(Reminder.user_id == user_id)
            if status:
                stmt = stmt.where(Reminder.status == status)
            stmt = stmt.order_by(Reminder.remind_at.asc()).limit(limit)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_event(self, user_id: int, event_id: int, *, status: str | None = None) -> list[Reminder]:
        async with self.db.session() as session:
            stmt = select(Reminder).where(Reminder.user_id == user_id, Reminder.event_id == event_id)
            if status:
                stmt = stmt.where(Reminder.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_task(self, user_id: int, task_id: int, *, status: str | None = None) -> list[Reminder]:
        async with self.db.session() as session:
            stmt = select(Reminder).where(Reminder.user_id == user_id, Reminder.task_id == task_id)
            if status:
                stmt = stmt.where(Reminder.status == status)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def claim_due(self, now_utc: datetime, limit: int, *, lease: timedelta) -> list[Reminder]:
        """Reclama recordatorios vencidos, de forma atómica y persistente.

        - `pending` con `remind_at <= now_utc`;
        - `processing` cuyo lease ha expirado (`claimed_at < now_utc - lease`),
          recuperándolos de un proceso muerto.

        Los devuelve en estado `processing` con `claimed_at = now_utc`.
        """
        expired_before = now_utc - lease
        async with self.db.session() as session:
            stmt = (
                select(Reminder.id)
                .where(
                    or_(
                        and_(Reminder.status == "pending", Reminder.remind_at <= now_utc),
                        and_(
                            Reminder.status == "processing",
                            Reminder.claimed_at.is_not(None),
                            Reminder.claimed_at < expired_before,
                        ),
                    )
                )
                .order_by(Reminder.remind_at.asc())
                .limit(limit)
            )
            if session.get_bind().dialect.name == "postgresql":
                stmt = stmt.with_for_update(skip_locked=True)
            ids = list((await session.execute(stmt)).scalars().all())
            if not ids:
                return []
            upd = (
                update(Reminder)
                .where(Reminder.id.in_(ids))
                .values(status="processing", claimed_at=now_utc)
                .returning(Reminder)
            )
            rows = (await session.execute(upd)).scalars().all()
            await session.commit()
            return list(rows)

    async def _settle(
        self,
        user_id: int,
        reminder_id: int,
        values: dict,
        *,
        claimed_at: datetime,
    ) -> Reminder | None:
        """Aplica un cambio de estado solo si el claim sigue siendo del worker."""
        async with self.db.session() as session:
            result = await session.execute(
                update(Reminder)
                .where(
                    Reminder.id == reminder_id,
                    Reminder.user_id == user_id,
                    Reminder.status == "processing",
                    Reminder.claimed_at == claimed_at,
                )
                .values(**values)
                .returning(Reminder)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def advance(
        self,
        user_id: int,
        reminder_id: int,
        new_remind_at: datetime,
        *,
        claimed_at: datetime,
    ) -> Reminder | None:
        """Re-programa un recordatorio recurrente a su próxima ocurrencia."""
        return await self._settle(
            user_id,
            reminder_id,
            {"status": "pending", "remind_at": new_remind_at, "claimed_at": None},
            claimed_at=claimed_at,
        )

    async def mark_sent(
        self,
        user_id: int,
        reminder_id: int,
        sent_at: datetime,
        *,
        claimed_at: datetime,
    ) -> Reminder | None:
        return await self._settle(
            user_id,
            reminder_id,
            {"status": "sent", "sent_at": sent_at, "claimed_at": None},
            claimed_at=claimed_at,
        )

    async def cancel_claimed(
        self,
        user_id: int,
        reminder_id: int,
        *,
        claimed_at: datetime,
    ) -> Reminder | None:
        """Cancela un recordatorio reclamado (vencido más allá de la ventana)."""
        return await self._settle(
            user_id,
            reminder_id,
            {"status": "cancelled", "claimed_at": None},
            claimed_at=claimed_at,
        )

    async def release(
        self,
        user_id: int,
        reminder_id: int,
        *,
        claimed_at: datetime,
    ) -> Reminder | None:
        """Devuelve a `pending` un reclamo cuyo envío falló (reintento)."""
        return await self._settle(
            user_id,
            reminder_id,
            {"status": "pending", "claimed_at": None},
            claimed_at=claimed_at,
        )

    async def set_trip(self, user_id: int, reminder_id: int, trip_id: int | None) -> Reminder | None:
        """Vincula un recordatorio a un viaje (Fase 6)."""
        async with self.db.session() as session:
            result = await session.execute(
                update(Reminder)
                .where(Reminder.id == reminder_id, Reminder.user_id == user_id)
                .values(trip_id=trip_id)
                .returning(Reminder)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def cancel(self, user_id: int, reminder_id: int) -> Reminder | None:
        """Cancela un recordatorio pendiente o en proceso (acción de usuario)."""
        async with self.db.session() as session:
            result = await session.execute(
                update(Reminder)
                .where(
                    Reminder.id == reminder_id,
                    Reminder.user_id == user_id,
                    Reminder.status.in_(_DUE_STATUSES),
                )
                .values(status="cancelled", claimed_at=None)
                .returning(Reminder)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def resync_task_pending(self, user_id: int, task_id: int, new_due_utc: datetime, default_offset: int) -> int:
        """Re-apunta recordatorios pendientes de una tarea a su nueva ocurrencia."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Reminder).where(
                    Reminder.user_id == user_id, Reminder.task_id == task_id, Reminder.status == "pending"
                )
            )
            rows = list(result.scalars().all())
            for r in rows:
                offset = r.offset_minutes if r.offset_minutes is not None else default_offset
                r.remind_at = new_due_utc - timedelta(minutes=offset)
            await session.commit()
            return len(rows)

    async def resync_event_pending(
        self, user_id: int, event_id: int, new_start_utc: datetime, default_offset: int
    ) -> int:
        """Re-apunta recordatorios pendientes de un evento a su nueva fecha."""
        async with self.db.session() as session:
            result = await session.execute(
                select(Reminder).where(
                    Reminder.user_id == user_id, Reminder.event_id == event_id, Reminder.status == "pending"
                )
            )
            rows = list(result.scalars().all())
            for r in rows:
                offset = r.offset_minutes if r.offset_minutes is not None else default_offset
                r.remind_at = new_start_utc - timedelta(minutes=offset)
            await session.commit()
            return len(rows)

    async def cancel_task_pending(self, user_id: int, task_id: int) -> int:
        """Cancela los recordatorios pendientes de una tarea terminada."""
        async with self.db.session() as session:
            result = await session.execute(
                update(Reminder)
                .where(Reminder.user_id == user_id, Reminder.task_id == task_id, Reminder.status == "pending")
                .values(status="cancelled", claimed_at=None)
                .returning(Reminder.id)
            )
            ids = list(result.scalars().all())
            await session.commit()
            return len(ids)

    async def cancel_event_pending(self, user_id: int, event_id: int) -> int:
        """Cancela los recordatorios pendientes de un evento cancelado."""
        async with self.db.session() as session:
            result = await session.execute(
                update(Reminder)
                .where(Reminder.user_id == user_id, Reminder.event_id == event_id, Reminder.status == "pending")
                .values(status="cancelled", claimed_at=None)
                .returning(Reminder.id)
            )
            ids = list(result.scalars().all())
            await session.commit()
            return len(ids)

    async def delete(self, user_id: int, reminder_id: int) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                sa_delete(Reminder)
                .where(Reminder.id == reminder_id, Reminder.user_id == user_id)
                .returning(Reminder.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found

    async def list_for_trip(
        self, user_id: int, trip_id: int, *, status: str = "pending", limit: int = 500
    ) -> list[Reminder]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Reminder)
                .where(Reminder.user_id == user_id, Reminder.trip_id == trip_id, Reminder.status == status)
                .order_by(Reminder.remind_at.asc(), Reminder.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())
