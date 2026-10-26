"""Repositorio de transferencias (scoped por user_id).

Un traspaso tiene dos patas: reduce el saldo derivado de `from_account_id` y
aumenta el de `to_account_id`. Nunca aparece como gasto/ingreso. Todos los
listados filtran por `user_id` (nunca entre usuarios distintos).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import or_, select

from asistente.persistence.models import Transfer
from asistente.persistence.repositories.base import Repository


class TransferRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        from_account_id: int,
        to_account_id: int,
        amount: Decimal,
        occurred_at: datetime,
        currency: str = "EUR",
        note: str | None = None,
        status: str = "posted",
    ) -> Transfer:
        async with self.db.session() as session:
            row = Transfer(
                user_id=user_id,
                from_account_id=from_account_id,
                to_account_id=to_account_id,
                amount=amount,
                occurred_at=occurred_at,
                currency=currency,
                note=note,
                status=status,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, transfer_id: int) -> Transfer | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(Transfer).where(Transfer.id == transfer_id, Transfer.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        account_id: int | None = None,
        from_account_id: int | None = None,
        to_account_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transfer]:
        async with self.db.session() as session:
            stmt = select(Transfer).where(Transfer.user_id == user_id, Transfer.status == "posted")
            if start_utc is not None:
                stmt = stmt.where(Transfer.occurred_at >= start_utc)
            if end_utc is not None:
                stmt = stmt.where(Transfer.occurred_at < end_utc)
            # `account_id` filtra movimientos en los que la cuenta es destino o origen.
            if account_id is not None:
                stmt = stmt.where(or_(Transfer.from_account_id == account_id, Transfer.to_account_id == account_id))
            if from_account_id is not None:
                stmt = stmt.where(Transfer.from_account_id == from_account_id)
            if to_account_id is not None:
                stmt = stmt.where(Transfer.to_account_id == to_account_id)
            stmt = stmt.order_by(Transfer.occurred_at.desc(), Transfer.id.desc()).limit(limit).offset(offset)
            result = await session.execute(stmt)
            return list(result.scalars().all())
