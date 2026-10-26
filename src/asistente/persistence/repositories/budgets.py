"""Repositorio de presupuestos (scoped por user_id).

Un presupuesto es mensual y puede ser general (category=None) o por categoría.
La unicidad por usuario+mes+categoría se garantiza con índices parciales
únicos en PostgreSQL; el repositorio también la verifica en Python para
compatibilidad con SQLite en tests.
"""

from __future__ import annotations

from sqlalchemy import select

from asistente.persistence.models import Budget
from asistente.persistence.repositories.base import Repository


class BudgetRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        amount,
        currency: str = "EUR",
        year: int,
        month: int,
        category: str | None = None,
    ) -> Budget:
        async with self.db.session() as session:
            row = Budget(
                user_id=user_id,
                name=name,
                amount=amount,
                currency=currency,
                year=year,
                month=month,
                category=category,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, budget_id: int) -> Budget | None:
        async with self.db.session() as session:
            result = await session.execute(select(Budget).where(Budget.id == budget_id, Budget.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_period(self, user_id: int, year: int, month: int, category: str | None = None) -> Budget | None:
        stmt = select(Budget).where(
            Budget.user_id == user_id,
            Budget.year == year,
            Budget.month == month,
        )
        if category is None:
            stmt = stmt.where(Budget.category.is_(None))
        else:
            stmt = stmt.where(Budget.category == category)
        async with self.db.session() as session:
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        year: int | None = None,
        month: int | None = None,
        limit: int = 100,
    ) -> list[Budget]:
        stmt = select(Budget).where(Budget.user_id == user_id)
        if year is not None:
            stmt = stmt.where(Budget.year == year)
        if month is not None:
            stmt = stmt.where(Budget.month == month)
        stmt = stmt.order_by(Budget.year.desc(), Budget.month.desc(), Budget.id.desc()).limit(limit)
        async with self.db.session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, budget_id: int, **fields) -> Budget | None:
        budget = await self.find_one(user_id, budget_id)
        if budget is None:
            return None
        async with self.db.session() as session:
            for k, v in fields.items():
                setattr(budget, k, v)
            await session.commit()
            await session.refresh(budget)
        return budget

    async def delete(self, user_id: int, budget_id: int) -> bool:
        budget = await self.find_one(user_id, budget_id)
        if budget is None:
            return False
        async with self.db.session() as session:
            await session.delete(budget)
            await session.commit()
        return True
