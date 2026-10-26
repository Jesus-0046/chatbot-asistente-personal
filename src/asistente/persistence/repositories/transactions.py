"""Repositorio de movimientos (scoped por user_id).

Los movimientos se guardan como UTC aware; los filtros de rango llegan ya en
UTC. Todos los listados filtran SIEMPRE por `user_id`: un usuario jamás puede
leer movimientos de otro.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update

from asistente.persistence.models import Transaction
from asistente.persistence.repositories.base import Repository


class TransactionRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        type: str,
        amount: Decimal,
        occurred_at: datetime,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str = "Otros",
        note: str | None = None,
        source: str = "manual",
        status: str = "posted",
        session=None,
    ) -> Transaction:
        async with self._session(session) as s:
            row = Transaction(
                user_id=user_id,
                type=type,
                amount=amount,
                occurred_at=occurred_at,
                currency=currency,
                account_id=account_id,
                merchant=merchant,
                category=category,
                note=note,
                source=source,
                status=status,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, transaction_id: int) -> Transaction | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(Transaction).where(Transaction.id == transaction_id, Transaction.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        account_id: int | None = None,
        transaction_type: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        async with self.db.session() as session:
            stmt = select(Transaction).where(Transaction.user_id == user_id, Transaction.status == "posted")
            if start_utc is not None:
                stmt = stmt.where(Transaction.occurred_at >= start_utc)
            if end_utc is not None:
                stmt = stmt.where(Transaction.occurred_at < end_utc)
            if account_id is not None:
                stmt = stmt.where(Transaction.account_id == account_id)
            if transaction_type is not None:
                stmt = stmt.where(Transaction.type == transaction_type)
            if category is not None:
                stmt = stmt.where(Transaction.category == category)
            if merchant is not None:
                stmt = stmt.where(Transaction.merchant.ilike(f"%{merchant}%"))
            stmt = stmt.order_by(Transaction.occurred_at.desc(), Transaction.id.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_trip(
        self, user_id: int, trip_id: int, *, status: str = "posted", limit: int = 500
    ) -> list[Transaction]:
        async with self.db.session() as session:
            result = await session.execute(
                select(Transaction)
                .where(Transaction.user_id == user_id, Transaction.trip_id == trip_id, Transaction.status == status)
                .order_by(Transaction.occurred_at.asc(), Transaction.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())

    async def set_trip(self, user_id: int, transaction_ids: list[int], trip_id: int | None) -> int:
        """Vincula/desvincula movimientos del usuario a un viaje.

        Devuelve cuántos se actualizaron (solo los que pertenecen al usuario).
        """
        if not transaction_ids:
            return 0
        async with self.db.session() as session:
            result = await session.execute(
                update(Transaction)
                .where(Transaction.user_id == user_id, Transaction.id.in_(transaction_ids))
                .values(trip_id=trip_id)
                .returning(Transaction.id)
            )
            updated = len(list(result.scalars().all()))
            await session.commit()
            return updated
