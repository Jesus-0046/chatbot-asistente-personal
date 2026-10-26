"""Repositorio de pagos planificados (scoped por user_id).

La conversión a transacción real es ATÓMICA y anti-duplicación:
`convert` adquiere un bloqueo explícito (`SELECT ... FOR UPDATE`) sobre el
planificado, crea la transacción y marca el planificado como `converted` con
`converted_transaction_id` en LA MISMA transacción. Si algo falla, el
planificado NO queda convertido (se conserva `planned`).

El bloqueo `FOR UPDATE` previene la conversión concurrente del mismo
planificado: el segundo worker bloquea su SELECT hasta que el primero
confirma, y al releer descubre que el status ya no es `planned`.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from asistente.persistence.models import PlannedTransaction, Transaction
from asistente.persistence.repositories.base import Repository
from asistente.shared.errors import AlreadyConvertedError, NotFoundError


class PlannedTransactionRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        type: str,
        title: str | None = None,
        amount: Decimal,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str = "Otros",
        planned_at,
        note: str | None = None,
        recurring_transaction_id: int | None = None,
        status: str = "planned",
        converted_transaction_id: int | None = None,
    ) -> PlannedTransaction:
        async with self._session() as session:
            row = PlannedTransaction(
                user_id=user_id,
                type=type,
                title=title,
                amount=amount,
                currency=currency,
                account_id=account_id,
                merchant=merchant,
                category=category,
                planned_at=planned_at,
                note=note,
                recurring_transaction_id=recurring_transaction_id,
                status=status,
                converted_transaction_id=converted_transaction_id,
            )
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("Ya existe una ocurrencia planificada para esa fecha.") from exc
            await session.refresh(row)
            return row

    async def find_one(self, user_id: int, planned_id: int) -> PlannedTransaction | None:
        async with self._session() as session:
            result = await session.execute(
                select(PlannedTransaction).where(
                    PlannedTransaction.id == planned_id,
                    PlannedTransaction.user_id == user_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: int,
        *,
        status: str | None = None,
        transaction_type: str | None = None,
        start_utc=None,
        end_utc=None,
        limit: int = 100,
    ) -> list[PlannedTransaction]:
        stmt = select(PlannedTransaction).where(PlannedTransaction.user_id == user_id)
        if status is not None:
            stmt = stmt.where(PlannedTransaction.status == status)
        if transaction_type is not None:
            stmt = stmt.where(PlannedTransaction.type == transaction_type)
        if start_utc is not None:
            stmt = stmt.where(PlannedTransaction.planned_at >= start_utc)
        if end_utc is not None:
            stmt = stmt.where(PlannedTransaction.planned_at <= end_utc)
        stmt = stmt.order_by(PlannedTransaction.planned_at.asc(), PlannedTransaction.id.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def cancel(self, user_id: int, planned_id: int) -> PlannedTransaction | None:
        async with self._session() as session:
            row = await self._locked(session, user_id, planned_id)
            if row is None:
                return None
            if row.status != "planned":
                raise ValueError(
                    "El pago planificado ya no está pendiente (estado: "
                    f"{row.status}); solo se pueden cancelar pagos planificados."
                )
            row.status = "cancelled"
            await session.flush()
            await session.refresh(row)
            return row

    async def cancel_for_recurring(self, user_id: int, recurring_id: int, *, session=None) -> int:
        """Cancela los planificados futuros pendientes de una recurrencia."""
        async with self._session(session) as s:
            target_ids = list(
                (
                    await s.execute(
                        select(PlannedTransaction.id).where(
                            PlannedTransaction.user_id == user_id,
                            PlannedTransaction.recurring_transaction_id == recurring_id,
                            PlannedTransaction.status == "planned",
                        )
                    )
                ).scalars()
            )
            if not target_ids:
                return 0
            await s.execute(
                update(PlannedTransaction).where(PlannedTransaction.id.in_(target_ids)).values(status="cancelled")
            )
            if session is None:
                await s.commit()
            return len(target_ids)

    async def convert(
        self,
        planned: PlannedTransaction,
        *,
        occurred_at,
        merchant: str | None = None,
        note: str | None = None,
        source: str = "manual",
    ) -> tuple[PlannedTransaction, Transaction]:
        """Convierte el planificado en transacción real, de forma atómica.

        Usa `SELECT ... FOR UPDATE` para bloquear la fila durante toda la
        transacción. El segundo worker concurrente bloquea su SELECT hasta que
        el primero confirma (COMMIT); al releer descubre status != planned
        y levanta `AlreadyConvertedError`.

        El commit lo realiza ``Database.transaction()`` al salir del bloque
        de forma exitosa. NO se ejecuta ningún ``commit()`` manual dentro
        de este método.

        Devuelve (planificado convertido, transacción creada).
        Lanza ``AlreadyConvertedError`` si ya fue convertido;
        ``NotFoundError`` si la fila no existe.
        """
        async with self.db.transaction() as session:
            stmt = (
                select(PlannedTransaction)
                .where(
                    PlannedTransaction.id == planned.id,
                    PlannedTransaction.user_id == planned.user_id,
                )
                .with_for_update()
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                raise NotFoundError("Pago planificado no encontrado.")
            if row.status != "planned":
                raise AlreadyConvertedError(
                    f"El pago planificado ya fue convertido (estado: {row.status}); no se puede convertir de nuevo."
                )
            transaction = Transaction(
                user_id=row.user_id,
                type=row.type,
                amount=row.amount,
                currency=row.currency,
                account_id=row.account_id,
                merchant=merchant if merchant is not None else row.merchant,
                category=row.category,
                occurred_at=occurred_at,
                note=note if note is not None else row.note,
                source=source,
            )
            session.add(transaction)
            await session.flush()
            row.status = "converted"
            row.converted_transaction_id = transaction.id
        return row, transaction

    @staticmethod
    async def _locked(session, user_id: int, planned_id: int) -> PlannedTransaction | None:
        result = await session.execute(
            select(PlannedTransaction).where(
                PlannedTransaction.id == planned_id,
                PlannedTransaction.user_id == user_id,
            )
        )
        row = result.scalar_one_or_none()
        assert row is None or isinstance(row, PlannedTransaction)
        return row
