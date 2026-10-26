"""Repositorio de deudas/préstamos personales (scoped por user_id).

Una deuda relaciona al usuario con UNA persona (`people.id`, ON DELETE
RESTRICT: no se pueden borrar personas con histórico). La liquidación
(`settle`) es una única UPDATE atómica.
"""

from __future__ import annotations

from sqlalchemy import select, update

from asistente.persistence.models import Debt
from asistente.persistence.repositories.base import Repository


class DebtRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        person_id: int,
        direction: str,
        amount,
        remaining_amount,
        currency: str = "EUR",
        reason: str | None = None,
        due_at=None,
    ) -> Debt:
        async with self._session() as session:
            row = Debt(
                user_id=user_id,
                person_id=person_id,
                direction=direction,
                amount=amount,
                remaining_amount=remaining_amount,
                currency=currency,
                reason=reason,
                due_at=due_at,
                status="open",
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, debt_id: int) -> Debt | None:
        async with self._session() as session:
            result = await session.execute(select(Debt).where(Debt.id == debt_id, Debt.user_id == user_id))
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        person_id: int | None = None,
        direction: str | None = None,
        limit: int = 200,
    ) -> list[Debt]:
        stmt = select(Debt).where(Debt.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Debt.status == status)
        if person_id is not None:
            stmt = stmt.where(Debt.person_id == person_id)
        if direction is not None:
            stmt = stmt.where(Debt.direction == direction)
        stmt = stmt.order_by(Debt.created_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def settle(self, user_id: int, debt_id: int) -> Debt | None:
        """Liquida la deuda si está abierta. Devuelve la fila actualizada o None."""
        async with self._session() as session:
            row = await session.get(Debt, debt_id)
            if row is None or row.user_id != user_id:
                return None
            if row.status != "open":
                return row
            await session.execute(update(Debt).where(Debt.id == debt_id).values(status="settled", remaining_amount=0))
            await session.flush()
            await session.refresh(row)
            return row
