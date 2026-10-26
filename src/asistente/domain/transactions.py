"""Servicio de dominio de movimientos (Fase 3).

Un movimiento es un gasto (`expense`) o un ingreso (`income`) con importe
siempre positivo. La cuenta se asigna así, en orden:

1. Si llega `account_id` explícito: debe existir, pertenecer al usuario y
   estar activa (una cuenta inactiva no recibe movimientos).
2. Si no llega: la cuenta por defecto activa; si no hay default pero solo hay
   UNA cuenta activa, se usa esa automáticamente.
3. Si hay varias cuentas activas y ninguna default → error claro para que la
   tool pregunte al usuario.

Semántica tarjeta de crédito: comprar con `credit_card` es un GASTO sobre esa
cuenta (su saldo derivado baja hacia negativo). NO se convierte en traspaso.
Pagar la tarjeta desde otra cuenta es una TRANSFERENCIA (ver TransferService):
así el gasto no se duplica.

Aislamiento: toda operación recibe `user_id` autenticado y filtra por él.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from asistente.domain.amounts import CENTS
from asistente.domain.categories import classify_category
from asistente.domain.currencies import SUPPORTED_CURRENCY, normalize_currency
from asistente.persistence.models import Account, Transaction
from asistente.persistence.repositories import AccountRepository, TransactionRepository
from asistente.shared.errors import AuthorizationError, NotFoundError
from asistente.shared.timeutil import utc_now

TRANSACTION_TYPES = ("expense", "income")


class TransactionService:
    def __init__(self, transaction_repo: TransactionRepository, account_repo: AccountRepository) -> None:
        self._tx = transaction_repo
        self._accounts = account_repo

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
        occurred_at: datetime | None = None,
        note: str | None = None,
        source: str = "manual",
    ) -> Transaction:
        return await self.create_in_session(
            user_id=user_id,
            type=type,
            amount=amount,
            currency=currency,
            account_id=account_id,
            merchant=merchant,
            category=category,
            occurred_at=occurred_at,
            note=note,
            source=source,
        )

    async def create_in_session(
        self,
        *,
        user_id: int,
        type: str,
        amount: Decimal,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str | None = None,
        occurred_at: datetime | None = None,
        note: str | None = None,
        source: str = "manual",
        session=None,
        account: Account | None = None,
    ) -> Transaction:
        """Calcula validando/creando un movimiento dentro de la `session` dada.

        Permite que otras partes del dominio (un repostaje, un mantenimiento)
        creen SU única transacción y la inserten en la MISMA transacción
        atómica que el detalle correspondiente, para no dejar movimientos
        huérfanos. Con `session=None` se comporta igual que `create()`.

        `account` (opcional, ya resuelto) evita re-leer la cuenta dentro de una
        transacción abierta: los flujos atómicos resuelven la cuenta ANTES de
        abrir la transacción (patrón del SharedExpenseService).
        """
        if type not in TRANSACTION_TYPES:
            raise ValueError(f"Tipo de movimiento no válido: {type}. Válidos: {', '.join(TRANSACTION_TYPES)}.")
        currency = normalize_currency(currency)
        amount = Decimal(str(amount)).quantize(CENTS)
        if amount <= 0:
            raise ValueError("El importe del movimiento debe ser mayor que cero.")

        resolved = account or await self.resolve_account(user_id, account_id)
        resolved_id: int = resolved.id
        if resolved.currency != currency:
            raise ValueError(
                f"El movimiento está en '{currency}' pero su cuenta está en '{resolved.currency}': "
                f"solo se soporta {SUPPORTED_CURRENCY}."
            )
        resolved_category = classify_category(merchant, category)
        occurred = occurred_at or utc_now()

        return await self._tx.create(
            user_id=user_id,
            type=type,
            amount=amount,
            occurred_at=occurred,
            currency=currency,
            account_id=resolved_id,
            merchant=(merchant or "").strip()[:300] or None,
            category=resolved_category,
            note=note,
            source=source,
            session=session,
        )

    async def resolve_account(self, user_id: int, account_id: int | None) -> Account:
        """Resuelve la cuenta destino de un movimiento (regla 1-2-3 del módulo).

        Si llega `account_id` explícito debe existir, pertenecer al usuario y
        estar activa; si no, devuelve la cuenta principal o la única activa.
        """
        if account_id is not None:
            account = await self._accounts.find_one(user_id, account_id)
            if account is None:
                raise NotFoundError("La cuenta del movimiento no existe o no te pertenece.")
            if not account.active:
                raise AuthorizationError("La cuenta está desactivada y no puede recibir nuevos movimientos.")
            return account

        accounts = await self._accounts.list_for_user(user_id, only_active=True)
        if not accounts:
            raise AuthorizationError("No tienes ninguna cuenta activa. Crea una cuenta primero.")
        default = next((a for a in accounts if a.is_default), None)
        if default is not None:
            return default
        if len(accounts) == 1:
            return accounts[0]
        raise AuthorizationError(
            "Tienes varias cuentas activas y ninguna está marcada como principal. "
            "Indica en qué cuenta registrar el movimiento o marca una por defecto."
        )

    async def get(self, user_id: int, transaction_id: int) -> Transaction:
        txn = await self._tx.find_one(user_id, transaction_id)
        if txn is None:
            raise NotFoundError("El movimiento no existe o no te pertenece.")
        return txn

    async def list(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        account_id: int | None = None,
        transaction_type: str | None = None,
        category: str | None = None,
        merchant: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Transaction]:
        return await self._tx.list_for_user(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            account_id=account_id,
            transaction_type=transaction_type,
            category=category,
            merchant=merchant,
            limit=limit,
            offset=offset,
        )
