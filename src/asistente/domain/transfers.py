"""Servicio de dominio de transferencias (Fase 3).

Una transferencia NO es gasto ni ingreso: mueve dinero entre dos cuentas del
mismo usuario. Afecta a los saldos DERIVADOS de ambas (una baja, la otra
sube) y no toca los totales de gastos/ingresos ni el patrimonio neto.

Reglas:
- `from_account_id != to_account_id`.
- Ambas cuentas pertenecen al MISMO usuario (nunca entre usuarios distintos).
- Ambas cuentas están activas.
- Importe > 0.

Pagar una tarjeta de crédito ("he pagado la Visa con 100 desde mi banco") es
una transferencia banco → tarjeta de crédito: la deuda de la tarjeta baja sin
duplicar el gasto original.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from asistente.domain.amounts import CENTS
from asistente.domain.currencies import SUPPORTED_CURRENCY, normalize_currency
from asistente.persistence.models import Transfer
from asistente.persistence.repositories import AccountRepository, TransferRepository
from asistente.shared.errors import AuthorizationError, NotFoundError
from asistente.shared.timeutil import utc_now


class TransferService:
    def __init__(self, transfer_repo: TransferRepository, account_repo: AccountRepository) -> None:
        self._transfers = transfer_repo
        self._accounts = account_repo

    async def create(
        self,
        *,
        user_id: int,
        from_account_id: int,
        to_account_id: int,
        amount: Decimal,
        currency: str = "EUR",
        occurred_at: datetime | None = None,
        note: str | None = None,
    ) -> Transfer:
        if from_account_id == to_account_id:
            raise ValueError("El traspaso debe ser entre dos cuentas distintas.")

        currency = normalize_currency(currency)

        source = await self._require_owned_active(user_id, from_account_id)
        target = await self._require_owned_active(user_id, to_account_id)
        if source.currency != currency or target.currency != currency:
            raise ValueError(
                f"Ambas cuentas de un traspaso deben estar en la misma moneda que el traspaso ({SUPPORTED_CURRENCY})."
            )

        amount = Decimal(str(amount)).quantize(CENTS)
        if amount <= 0:
            raise ValueError("El importe del traspaso debe ser mayor que cero.")

        return await self._transfers.create(
            user_id=user_id,
            from_account_id=source.id,
            to_account_id=target.id,
            amount=amount,
            occurred_at=occurred_at or utc_now(),
            currency=currency,
            note=note,
        )

    async def _require_owned_active(self, user_id: int, account_id: int):
        account = await self._accounts.find_one(user_id, account_id)
        if account is None:
            raise NotFoundError("La cuenta del traspaso no existe o no te pertenece.")
        if not account.active:
            raise AuthorizationError("La cuenta del traspaso está desactivada.")
        return account

    async def get(self, user_id: int, transfer_id: int) -> Transfer:
        transfer = await self._transfers.find_one(user_id, transfer_id)
        if transfer is None:
            raise NotFoundError("El traspaso no existe o no te pertenece.")
        return transfer

    async def list(
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
        return await self._transfers.list_for_user(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            account_id=account_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            limit=limit,
            offset=offset,
        )
