"""Repositorio de recurrencias financieras (scoped por user_id).

La generación de ocurrencias es idempotente y por ocurrencia atómica:
`create_occurrence_and_advance` materializa el planificado en la fecha
`planned_at` y avanza `next_due_at` en LA MISMA transacción. Si la ocurrencia
ya existe (nunca deben coexistir dos planificados de la misma recurrencia y
fecha), NO crea nada y solo devuelve None (el avance queda como esté).

La unicidad (recurring_transaction_id, planned_at) con índice único actúa como
cinturón de seguridad ante dos generadores concurrentes.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from asistente.persistence.models import PlannedTransaction, RecurringTransaction
from asistente.persistence.repositories.base import Repository


class RecurringTransactionRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        type: str,
        title: str,
        amount: Decimal,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str = "Otros",
        frequency: str,
        interval: int = 1,
        day_of_month: int | None = None,
        month_of_year: int | None = None,
        start_date=None,
        next_due_at: datetime | None = None,
        end_date=None,
        timezone: str = "Europe/Madrid",
        active: bool = True,
        paused: bool = False,
        session=None,
    ) -> RecurringTransaction:
        async with self._session(session) as s:
            row = RecurringTransaction(
                user_id=user_id,
                type=type,
                title=title,
                amount=amount,
                currency=currency,
                account_id=account_id,
                merchant=merchant,
                category=category,
                frequency=frequency,
                interval=interval,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                start_date=start_date,
                next_due_at=next_due_at,
                end_date=end_date,
                timezone=timezone,
                active=active,
                paused=paused,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, recurring_id: int) -> RecurringTransaction | None:
        async with self._session() as session:
            result = await session.execute(
                select(RecurringTransaction).where(
                    RecurringTransaction.id == recurring_id,
                    RecurringTransaction.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        active_only: bool = False,
        include_paused: bool = True,
        limit: int = 100,
    ) -> list[RecurringTransaction]:
        stmt = select(RecurringTransaction).where(RecurringTransaction.user_id == user_id)
        if active_only:
            stmt = stmt.where(RecurringTransaction.active.is_(True))
        if not include_paused:
            stmt = stmt.where(RecurringTransaction.paused.is_(False))
        stmt = stmt.order_by(RecurringTransaction.id.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_due(self, user_id: int, now_utc: datetime, *, limit: int = 500) -> list[RecurringTransaction]:
        """Recurrencias activas (no pausadas) con alguna ocurrencia vencida."""
        stmt = (
            select(RecurringTransaction)
            .where(
                RecurringTransaction.user_id == user_id,
                RecurringTransaction.active.is_(True),
                RecurringTransaction.paused.is_(False),
                RecurringTransaction.next_due_at.is_not(None),
                RecurringTransaction.next_due_at <= now_utc,
            )
            .order_by(RecurringTransaction.next_due_at.asc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, recurring_id: int, *, session=None, **fields) -> RecurringTransaction | None:
        async with self._session(session) as s:
            row = await s.get(RecurringTransaction, recurring_id)
            if row is None or row.user_id != user_id:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            await s.flush()
            await s.refresh(row)
            return row

    async def create_occurrence_and_advance(
        self,
        recurring: RecurringTransaction,
        *,
        planned_at_utc: datetime,
        next_due_utc: datetime | None,
    ) -> PlannedTransaction | None:
        """Materializa la ocurrencia actual y avanza `next_due_at`, en una transacción.

        Devuelve el planificado recién creado, o None si ya existía (o si otro
        generador concurrente lo ganó).
        """
        planned: PlannedTransaction | None = None
        async with self.db.session() as session:
            existing_row = await session.execute(
                select(PlannedTransaction).where(
                    PlannedTransaction.user_id == recurring.user_id,
                    PlannedTransaction.recurring_transaction_id == recurring.id,
                    PlannedTransaction.planned_at == planned_at_utc,
                )
            )
            existing = existing_row.scalar_one_or_none()
            try:
                if existing is None:
                    planned = PlannedTransaction(
                        user_id=recurring.user_id,
                        type=recurring.type,
                        title=recurring.title,
                        amount=recurring.amount,
                        currency=recurring.currency,
                        account_id=recurring.account_id,
                        merchant=recurring.merchant,
                        category=recurring.category,
                        planned_at=planned_at_utc,
                        recurring_transaction_id=recurring.id,
                        status="planned",
                    )
                    session.add(planned)
                row = await session.get(RecurringTransaction, recurring.id)
                if row is not None:
                    row.next_due_at = next_due_utc
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
        if planned is None:
            return None
        return planned

    async def deactivate(self, user_id: int, recurring_id: int) -> bool:
        async with self.db.session() as session:
            recurring = await session.get(RecurringTransaction, recurring_id)
            if recurring is None or recurring.user_id != user_id:
                return False
            recurring.active = False
            recurring.paused = False
            await session.commit()
            return True
