"""Repositorio de suscripciones (scoped por user_id).

La suscripción NO es un motor independiente: apunta SIEMPRE a una recurrencia
(`recurring_transaction_id`) que es quien genera los pagos planificados.
`next_billing_date` es un espejo de `recurring_transactions.next_due_at`.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update

from asistente.persistence.models import Subscription
from asistente.persistence.repositories.base import Repository


class SubscriptionRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        amount: Decimal,
        currency: str = "EUR",
        billing_period: str,
        next_billing_date=None,
        account_id: int | None = None,
        category: str = "Suscripciones",
        recurring_transaction_id: int | None = None,
        active: bool = True,
        session=None,
    ) -> Subscription:
        async with self._session(session) as s:
            row = Subscription(
                user_id=user_id,
                name=name,
                amount=amount,
                currency=currency,
                billing_period=billing_period,
                next_billing_date=next_billing_date,
                account_id=account_id,
                category=category,
                recurring_transaction_id=recurring_transaction_id,
                active=active,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, subscription_id: int) -> Subscription | None:
        async with self._session() as session:
            result = await session.execute(
                select(Subscription).where(
                    Subscription.id == subscription_id,
                    Subscription.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        active_only: bool | None = None,
        limit: int = 100,
    ) -> list[Subscription]:
        stmt = select(Subscription).where(Subscription.user_id == user_id)
        if active_only is not None:
            stmt = stmt.where(Subscription.active.is_(active_only))
        stmt = stmt.order_by(Subscription.id.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, subscription_id: int, *, session=None, **fields) -> Subscription | None:
        async with self._session(session) as s:
            row = await s.get(Subscription, subscription_id)
            if row is None or row.user_id != user_id:
                return None
            for k, v in fields.items():
                setattr(row, k, v)
            await s.flush()
            await s.refresh(row)
            return row

    async def deactivate_for_recurring(self, user_id: int, recurring_id: int, *, session=None) -> int:
        """Desactiva las suscripciones activas vinculadas a una recurrencia."""
        async with self._session(session) as s:
            target_ids = list(
                (
                    await s.execute(
                        select(Subscription.id).where(
                            Subscription.user_id == user_id,
                            Subscription.recurring_transaction_id == recurring_id,
                            Subscription.active.is_(True),
                        )
                    )
                ).scalars()
            )
            if not target_ids:
                return 0
            await s.execute(update(Subscription).where(Subscription.id.in_(target_ids)).values(active=False))
            if session is None:
                await s.commit()
            return len(target_ids)

    async def deactivate(self, user_id: int, subscription_id: int, *, session=None) -> Subscription | None:
        async with self._session(session) as s:
            row = await s.get(Subscription, subscription_id)
            if row is None or row.user_id != user_id:
                return None
            row.active = False
            await s.flush()
            await s.refresh(row)
            return row
