"""Repositorio de reglas de recurrencia (scoped por user_id)."""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update

from asistente.persistence.models import RecurrenceRule
from asistente.persistence.repositories.base import Repository

_RULE_DEFAULT_FIELDS = ("freq", "interval", "days_of_week", "start_at", "timezone", "end_at")


class RecurrenceRuleRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        freq: str,
        interval: int,
        days_of_week: list[int] | None,
        start_at,
        timezone: str,
        end_at=None,
    ) -> RecurrenceRule:
        async with self.db.session() as session:
            row = RecurrenceRule(
                user_id=user_id,
                freq=freq,
                interval=interval,
                days_of_week={"days": days_of_week} if days_of_week else None,
                start_at=start_at,
                timezone=timezone,
                end_at=end_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, rule_id: int) -> RecurrenceRule | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(RecurrenceRule).where(RecurrenceRule.id == rule_id, RecurrenceRule.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def update(self, user_id: int, rule_id: int, **fields) -> RecurrenceRule | None:
        allowed = {k: v for k, v in fields.items() if k in _RULE_DEFAULT_FIELDS}
        if not allowed:
            return await self.find_one(user_id, rule_id)
        async with self.db.session() as session:
            result = await session.execute(
                update(RecurrenceRule)
                .where(RecurrenceRule.id == rule_id, RecurrenceRule.user_id == user_id)
                .values(**allowed)
                .returning(RecurrenceRule.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, rule_id)

    async def delete(self, user_id: int, rule_id: int) -> bool:
        async with self.db.session() as session:
            result = await session.execute(
                sa_delete(RecurrenceRule)
                .where(RecurrenceRule.id == rule_id, RecurrenceRule.user_id == user_id)
                .returning(RecurrenceRule.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found
