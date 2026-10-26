"""Servicio de dominio de pagos planificados (Fase 3.3).

Un planificado (`planned_transactions`) es una ocurrencia futura PENDIENTE:
NO mueve dinero, no toca saldos ni presupuestos. Al confirmarlo se convierte
en una transacción REAL (posted) de forma atómica y anti-duplicación.

Ciclo de vida: planned → converted / cancelled.
- Convertir crea la transacción y marca el planificado como `converted` con
  `converted_transaction_id` (trazabilidad, nunca coexistirán ambos).
- Solo se puede convertir/cancelar un planificado en estado `planned`.
- La cuenta se RE-resuelve al convertir: si se desactivó entre tanto, se
  rechaza la conversión (no se puede confirmar un pago sobre una cuenta inactiva
  o no-EUR).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from asistente.domain.amounts import CENTS
from asistente.domain.categories import classify_category
from asistente.domain.currencies import normalize_currency
from asistente.domain.transactions import TRANSACTION_TYPES, TransactionService
from asistente.persistence.models import PlannedTransaction
from asistente.persistence.repositories import PlannedTransactionRepository
from asistente.shared.errors import AlreadyConvertedError, NotFoundError
from asistente.shared.timeutil import utc_now


class PlannedTransactionService:
    def __init__(self, planned_repo: PlannedTransactionRepository, transaction_service: TransactionService) -> None:
        self._planned = planned_repo
        self._tx = transaction_service

    async def create(
        self,
        *,
        user_id: int,
        type: str,
        amount: Decimal,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str | None = None,
        planned_at: datetime,
        title: str | None = None,
        note: str | None = None,
    ) -> PlannedTransaction:
        if type not in TRANSACTION_TYPES:
            raise ValueError(f"Tipo no válido: {type}. Válidos: {', '.join(TRANSACTION_TYPES)}.")
        currency = normalize_currency(currency)
        amount = Decimal(str(amount)).quantize(CENTS)
        if amount <= 0:
            raise ValueError("El importe del pago planificado debe ser mayor que cero.")
        if planned_at.tzinfo is None:
            raise ValueError("La fecha planificada debe incluir zona horaria (aware).")
        if title is not None and not (title or "").strip():
            raise ValueError("El título del pago planificado no puede estar vacío.")

        account = await self._tx.resolve_account(user_id, account_id)
        account_id = account.id
        resolved_category = classify_category(merchant, category)

        # Anti-duplicación manual: mismo usuario/cuenta/tipo/importe/momento.
        same_moment = await self._planned.list_for_user(
            user_id, status="planned", start_utc=planned_at, end_utc=planned_at, limit=100
        )
        dup = next(
            (p for p in same_moment if p.type == type and p.account_id == account_id and p.amount == amount),
            None,
        )
        if dup is not None:
            raise ValueError("Ya existe un pago planificado idéntico para esa fecha y cuenta.")

        return await self._planned.create(
            user_id=user_id,
            type=type,
            title=(title or "").strip()[:300] or None,
            amount=amount,
            currency=currency,
            account_id=account_id,
            merchant=(merchant or "").strip()[:300] or None,
            category=resolved_category,
            planned_at=planned_at,
            note=note,
        )

    async def convert(
        self,
        user_id: int,
        planned_id: int,
        *,
        occurred_at: datetime | None = None,
        merchant: str | None = None,
        note: str | None = None,
    ) -> PlannedTransaction:
        planned = await self._planned.find_one(user_id, planned_id)
        if planned is None:
            raise NotFoundError("El pago planificado no existe o no te pertenece.")
        if planned.status != "planned":
            if planned.status == "converted":
                raise AlreadyConvertedError("El pago planificado ya fue convertido; no se puede convertir de nuevo.")
            raise ValueError("El pago planificado ya no está pendiente; no se puede convertir.")

        # Re-resolución de la cuenta: si se desactivó, la conversión se rechaza.
        await self._tx.resolve_account(user_id, planned.account_id)

        occurred = occurred_at or utc_now()
        source = "recurring" if planned.recurring_transaction_id is not None else "manual"
        converted, _ = await self._planned.convert(
            planned,
            occurred_at=occurred,
            merchant=merchant,
            note=note,
            source=source,
        )
        return converted

    async def cancel(self, user_id: int, planned_id: int) -> PlannedTransaction:
        planned = await self._planned.find_one(user_id, planned_id)
        if planned is None:
            raise NotFoundError("El pago planificado no existe o no te pertenece.")
        if planned.status != "planned":
            raise ValueError("El pago planificado ya no está pendiente; solo se cancelan planificados.")
        cancelled = await self._planned.cancel(user_id, planned_id)
        assert cancelled is not None
        return cancelled

    async def list_planned(
        self,
        user_id: int,
        *,
        status: str | None = None,
        transaction_type: str | None = None,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        limit: int = 100,
    ) -> list[PlannedTransaction]:
        return await self._planned.list_for_user(
            user_id,
            status=status,
            transaction_type=transaction_type,
            start_utc=start_utc,
            end_utc=end_utc,
            limit=limit,
        )

    async def upcoming(
        self, user_id: int, *, days: int = 30, transaction_type: str | None = None
    ) -> list[PlannedTransaction]:
        now = utc_now()
        end = now + timedelta(days=days)
        return await self._planned.list_for_user(
            user_id,
            status="planned",
            transaction_type=transaction_type,
            start_utc=now.replace(tzinfo=now.tzinfo),
            end_utc=end,
            limit=200,
        )
