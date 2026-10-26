"""Repositorio de cuentas (scoped por user_id).

Reglas:
- Máximo UNA cuenta `is_default=True` por usuario (índice único parcial en
  PostgreSQL); el servicio y este repo la mantienen desactivando el resto.
- `active=False` impide que las tools asignen nuevos movimientos.
- Las cuentas con histórico no se borran físicamente; se desactivan.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update

from asistente.persistence.models import Account
from asistente.persistence.repositories.base import Repository

_ACCOUNT_FIELDS = ("name", "type", "currency", "initial_balance", "is_default", "active", "sort_order")


class AccountRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        type: str,
        currency: str = "EUR",
        initial_balance: Decimal = Decimal("0.00"),
        is_default: bool = False,
        active: bool = True,
        sort_order: int = 0,
    ) -> Account:
        async with self.db.session() as session:
            row = Account(
                user_id=user_id,
                name=name,
                type=type,
                currency=currency,
                initial_balance=initial_balance,
                is_default=is_default,
                active=active,
                sort_order=sort_order,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, account_id: int) -> Account | None:
        async with self.db.session() as session:
            result = await session.execute(select(Account).where(Account.id == account_id, Account.user_id == user_id))
            return result.scalar_one_or_none()

    async def list_for_user(self, user_id: int, *, only_active: bool = False) -> list[Account]:
        async with self.db.session() as session:
            stmt = select(Account).where(Account.user_id == user_id)
            if only_active:
                stmt = stmt.where(Account.active.is_(True))
            stmt = stmt.order_by(Account.sort_order.asc(), Account.id.asc())
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, account_id: int, **fields) -> Account | None:
        allowed = {k: v for k, v in fields.items() if k in _ACCOUNT_FIELDS}
        if not allowed:
            return await self.find_one(user_id, account_id)
        async with self.db.session() as session:
            result = await session.execute(
                update(Account)
                .where(Account.id == account_id, Account.user_id == user_id)
                .values(**allowed)
                .returning(Account.id)
            )
            found_id = result.scalar_one_or_none()
            await session.commit()
            if found_id is None:
                return None
            return await self.find_one(user_id, account_id)

    async def set_default(self, user_id: int, account_id: int) -> Account | None:
        """Deja `account_id` como única cuenta por defecto del usuario."""
        async with self.db.session() as session:
            target = (
                await session.execute(select(Account).where(Account.id == account_id, Account.user_id == user_id))
            ).scalar_one_or_none()
            if target is None:
                return None
            await session.execute(update(Account).where(Account.user_id == user_id).values(is_default=False))
            target.is_default = True
            await session.commit()
            await session.refresh(target)
            return target

    async def clear_default(self, user_id: int) -> None:
        """Quita la marca de default (p. ej. al desactivar la cuenta por defecto)."""
        async with self.db.session() as session:
            await session.execute(update(Account).where(Account.user_id == user_id).values(is_default=False))
            await session.commit()
