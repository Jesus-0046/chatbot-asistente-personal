"""Repositorio de reglas de memoria (scoped por user_id)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from asistente.persistence.models import MemoryRule
from asistente.persistence.repositories.base import Repository


class MemoryRuleRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        kind: str,
        key: str,
        value: dict[str, Any],
        scope: str | None,
        source: str,
        active: bool = False,
    ) -> MemoryRule:
        async with self.db.session() as session:
            row = MemoryRule(
                user_id=user_id,
                kind=kind,
                key=key,
                value=value,
                scope=scope,
                source=source,
                active=active,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def active_matched(self, user_id: int, key: str, kind: str) -> MemoryRule | None:
        """Devuelve una regla activa concreta (kind+key) del usuario, si existe."""
        async with self.db.session() as session:
            result = await session.execute(
                select(MemoryRule).where(
                    MemoryRule.user_id == user_id,
                    MemoryRule.kind == kind,
                    MemoryRule.key == key,
                    MemoryRule.active.is_(True),
                )
            )
            return result.scalar_one_or_none()

    async def set_active(
        self,
        user_id: int,
        rule_id: int,
        active: bool,
        confirmed_at: datetime | None = None,
    ) -> MemoryRule | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(MemoryRule).where(MemoryRule.id == rule_id, MemoryRule.user_id == user_id)
            )
            rule = result.scalar_one_or_none()
            if rule is None:
                return None
            rule.active = active
            rule.confirmed_at = confirmed_at
            await session.commit()
            await session.refresh(rule)
            return rule

    async def list_for_user(self, user_id: int, *, active_only: bool = False) -> list[MemoryRule]:
        async with self.db.session() as session:
            stmt = select(MemoryRule).where(MemoryRule.user_id == user_id)
            if active_only:
                stmt = stmt.where(MemoryRule.active.is_(True))
            result = await session.execute(stmt.order_by(MemoryRule.id))
            return list(result.scalars().all())

    async def delete(self, user_id: int, rule_id: int) -> bool:
        async with self.db.session() as session:
            from sqlalchemy import delete as sa_delete

            result = await session.execute(
                sa_delete(MemoryRule)
                .where(MemoryRule.id == rule_id, MemoryRule.user_id == user_id)
                .returning(MemoryRule.id)
            )
            found = result.scalar_one_or_none() is not None
            await session.commit()
            return found
