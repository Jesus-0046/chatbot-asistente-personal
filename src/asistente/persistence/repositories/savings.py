"""Repositorios de objetivos de ahorro y contribuciones (scoped por user_id).

`savings_goals` almacena las metas; `savings_contributions` las contribuciones
individuales. El progreso se calcula como SUM(amount) de contribuciones,
nunca se almacena en la tabla de objetivos.

Desde Fase 3.2/3.3.1, `savings_contributions.transfer_id` tiene un índice único
parcial (ura: una transferencia solo se vincula a UNA contribución).
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from asistente.persistence.models import SavingsContribution, SavingsGoal
from asistente.persistence.repositories.base import Repository


class SavingsGoalRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        target_amount,
        currency: str = "EUR",
        deadline=None,
        status: str = "active",
    ) -> SavingsGoal:
        async with self._session() as session:
            row = SavingsGoal(
                user_id=user_id,
                name=name,
                target_amount=target_amount,
                currency=currency,
                deadline=deadline,
                status=status,
            )
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, goal_id: int) -> SavingsGoal | None:
        async with self._session() as session:
            result = await session.execute(
                select(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[SavingsGoal]:
        stmt = select(SavingsGoal).where(SavingsGoal.user_id == user_id)
        if status is not None:
            stmt = stmt.where(SavingsGoal.status == status)
        stmt = stmt.order_by(SavingsGoal.created_at.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, goal_id: int, **fields) -> SavingsGoal | None:
        async with self._session() as session:
            row = await session.get(SavingsGoal, goal_id)
            if row is None or row.user_id != user_id:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            await session.flush()
            await session.refresh(row)
            return row


class SavingsContributionRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        goal_id: int,
        amount,
        currency: str = "EUR",
        contributed_at,
        note: str | None = None,
        transfer_id: int | None = None,
    ) -> SavingsContribution:
        async with self._session() as session:
            row = SavingsContribution(
                user_id=user_id,
                goal_id=goal_id,
                amount=amount,
                currency=currency,
                contributed_at=contributed_at,
                note=note,
                transfer_id=transfer_id,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(
                    "La transferencia ya está vinculada a otra contribución "
                    "(cada transferencia solo puede vincularse una vez)."
                ) from exc
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, contribution_id: int) -> SavingsContribution | None:
        async with self._session() as session:
            result = await session.execute(
                select(SavingsContribution).where(
                    SavingsContribution.id == contribution_id,
                    SavingsContribution.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def find_by_transfer(self, user_id: int, transfer_id: int) -> SavingsContribution | None:
        async with self._session() as session:
            result = await session.execute(
                select(SavingsContribution).where(
                    SavingsContribution.user_id == user_id,
                    SavingsContribution.transfer_id == transfer_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int, *, limit: int = 100) -> list[SavingsContribution]:
        stmt = (
            select(SavingsContribution)
            .where(SavingsContribution.user_id == user_id)
            .order_by(SavingsContribution.contributed_at.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_goal(self, user_id: int, goal_id: int, *, limit: int = 100) -> list[SavingsContribution]:
        stmt = (
            select(SavingsContribution)
            .where(SavingsContribution.user_id == user_id, SavingsContribution.goal_id == goal_id)
            .order_by(SavingsContribution.contributed_at.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def sum_for_goal(self, user_id: int, goal_id: int) -> Decimal:
        stmt = select(func.coalesce(func.sum(SavingsContribution.amount), 0)).where(
            SavingsContribution.user_id == user_id,
            SavingsContribution.goal_id == goal_id,
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return result.scalar_one()
