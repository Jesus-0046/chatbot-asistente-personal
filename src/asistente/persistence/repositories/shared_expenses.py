"""Repositorios de gastos compartidos y sus participaciones.

`SharedExpense` y `SharedExpenseShare` se insertan SIEMPRE en la misma
transacción (`SharedExpenseRepository.transaction()`): un gasto compartido no
existe sin sus shares. El borrado físico de shares es CASCADE; las personas
referenciadas tienen ON DELETE RESTRICT (no se eliminan si tienen histórico).
"""

from __future__ import annotations

from sqlalchemy import or_, select, update

from asistente.persistence.models import SharedExpense, SharedExpensePayment, SharedExpenseShare
from asistente.persistence.repositories.base import Repository


class SharedExpenseRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        title: str,
        total_amount,
        currency: str = "EUR",
        paid_by_person_id: int | None = None,
        paid_by_user: bool = False,
        occurred_at,
        note: str | None = None,
        session=None,
    ) -> SharedExpense:
        async with self._session(session) as s:
            row = SharedExpense(
                user_id=user_id,
                title=title,
                total_amount=total_amount,
                currency=currency,
                paid_by_person_id=paid_by_person_id,
                paid_by_user=paid_by_user,
                occurred_at=occurred_at,
                note=note,
                status="active",
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, expense_id: int) -> SharedExpense | None:
        async with self._session() as session:
            result = await session.execute(
                select(SharedExpense).where(SharedExpense.id == expense_id, SharedExpense.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        occurred_from=None,
        occurred_until=None,
        person_id: int | None = None,
        limit: int = 200,
    ) -> list[SharedExpense]:
        stmt = select(SharedExpense).where(SharedExpense.user_id == user_id)
        if status is not None:
            stmt = stmt.where(SharedExpense.status == status)
        if occurred_from is not None:
            stmt = stmt.where(SharedExpense.occurred_at >= occurred_from)
        if occurred_until is not None:
            stmt = stmt.where(SharedExpense.occurred_at < occurred_until)
        if person_id is not None:
            stmt = (
                stmt.outerjoin(
                    SharedExpenseShare,
                    SharedExpenseShare.shared_expense_id == SharedExpense.id,
                )
                .outerjoin(
                    SharedExpensePayment,
                    SharedExpensePayment.shared_expense_id == SharedExpense.id,
                )
                .where(
                    or_(
                        SharedExpenseShare.person_id == person_id,
                        SharedExpensePayment.person_id == person_id,
                    )
                )
            )
        stmt = stmt.distinct().order_by(SharedExpense.occurred_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def settle_cancel(self, user_id: int, expense_id: int) -> SharedExpense | None:
        """Cancela un gasto compartido activo. Pasa `status=cancelled`."""
        async with self._session() as session:
            row = await session.get(SharedExpense, expense_id)
            if row is None or row.user_id != user_id:
                return None
            if row.status != "active":
                return row
            await session.execute(
                update(SharedExpense).where(SharedExpense.id == expense_id).values(status="cancelled")
            )
            await session.flush()
            await session.refresh(row)
            return row


class SharedExpenseShareRepository(Repository):
    async def create(
        self,
        *,
        shared_expense_id: int,
        person_id: int | None = None,
        is_user: bool = False,
        share_amount,
        session=None,
    ) -> SharedExpenseShare:
        """Inserta una participación. Si se pasa `session`, forma parte de la
        transacción del gasto compartido (mismo commit)."""
        async with self._session(session) as s:
            row = SharedExpenseShare(
                shared_expense_id=shared_expense_id,
                person_id=person_id,
                is_user=is_user,
                share_amount=share_amount,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def list_for_expense(self, shared_expense_id: int, *, session=None) -> list[SharedExpenseShare]:
        stmt = (
            select(SharedExpenseShare)
            .where(SharedExpenseShare.shared_expense_id == shared_expense_id)
            .order_by(SharedExpenseShare.created_at.asc())
        )
        async with self._session(session) as s:
            result = await s.execute(stmt)
            return list(result.scalars().all())


class SharedExpensePaymentRepository(Repository):
    async def create(
        self,
        *,
        shared_expense_id: int,
        person_id: int | None = None,
        is_user: bool = False,
        amount,
        session=None,
    ) -> SharedExpensePayment:
        """Inserta una aportación. Si se pasa `session`, forma parte de la
        transacción del gasto compartido (mismo commit)."""
        async with self._session(session) as s:
            row = SharedExpensePayment(
                shared_expense_id=shared_expense_id,
                person_id=person_id,
                is_user=is_user,
                amount=amount,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def list_for_expense(self, shared_expense_id: int, *, session=None) -> list[SharedExpensePayment]:
        stmt = (
            select(SharedExpensePayment)
            .where(SharedExpensePayment.shared_expense_id == shared_expense_id)
            .order_by(SharedExpensePayment.created_at.asc())
        )
        async with self._session(session) as s:
            result = await s.execute(stmt)
            return list(result.scalars().all())
