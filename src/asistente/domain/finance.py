"""Servicio de dominio financiero: saldos derivados y resúmenes (Fase 3).

Saldos
------
El saldo de una cuenta es DERIVADO, nunca almacenado:

    saldo(cuenta) = initial_balance
                  + Σ ingresos (posted) de la cuenta
                  - Σ gastos  (posted) de la cuenta
                  + Σ traspasos entrantes (posted) a la cuenta
                  - Σ traspasos salientes (posted) de la cuenta

Una tarjeta de crédito (`credit_card`) representa DEUDA: un gasto con ella
baja su saldo hacia negativo. Los traspasos de pago de tarjeta la suben de
vuelta sin crear un segundo gasto.

Agregados (fórmulas exactas):
- `patrimonio` = Σ saldo de TODAS las cuentas del usuario (a las cuentas de
  crédito, su saldo negativo resta patrimonio).
- `liquido`    = Σ saldo de las cuentas tipo `bank` + `cash` + `other`
  (dinero realmente disponible). Excluye `savings` (ahorro/reserva) y
  `credit_card` (deuda, no es dinero disponible).
- `deuda`      = Σ max(0, -saldo) de las cuentas tipo `credit_card`
  (lo que se debe pendiente; no se cuenta dos veces porque el gasto original
  ya está dentro de la disminución de patrimonio).

Resumen de gastos
-----------------
`spending_summary` devuelve datos estructurados (sin predicción): total de
gastos/ingresos en el periodo, desglose por categoría y por comercio, y el
primero de cada desglose.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from asistente.persistence.models import Account
from asistente.persistence.repositories import AccountRepository, TransactionRepository, TransferRepository

_LIQUID_TYPES = ("bank", "cash", "other")
_BIG_LIMIT = 1_000_000


class FinanceService:
    def __init__(
        self,
        account_repo: AccountRepository,
        transaction_repo: TransactionRepository,
        transfer_repo: TransferRepository,
    ) -> None:
        self._accounts = account_repo
        self._tx = transaction_repo
        self._transfers = transfer_repo

    async def balance_overview(self, user_id: int) -> dict:
        accounts = await self._accounts.list_for_user(user_id)
        transactions = await self._tx.list_for_user(user_id, limit=_BIG_LIMIT)
        transfers = await self._transfers.list_for_user(user_id, limit=_BIG_LIMIT)

        balances = self._balances(accounts, transactions, transfers)

        patrimonio = sum(balances.values(), Decimal("0.00"))
        liquido = sum(
            (bal for acc, bal in balances.items() if acc.type in _LIQUID_TYPES),
            Decimal("0.00"),
        )
        deuda = sum(
            (max(Decimal("0.00"), -bal) for acc, bal in balances.items() if acc.type == "credit_card"),
            Decimal("0.00"),
        )

        return {
            "accounts": [
                {
                    "id": acc.id,
                    "name": acc.name,
                    "type": acc.type,
                    "currency": acc.currency,
                    "balance": balances[acc],
                    "is_default": acc.is_default,
                    "active": acc.active,
                    "sort_order": acc.sort_order,
                }
                for acc in accounts
            ],
            "patrimonio": patrimonio,
            "liquido": liquido,
            "deuda_tarjetas": deuda,
        }

    async def spending_summary(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        category: str | None = None,
        merchant: str | None = None,
        account_id: int | None = None,
    ) -> dict:
        transactions = await self._tx.list_for_user(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            account_id=account_id,
            transaction_type="expense",
            category=category,
            merchant=merchant,
            limit=_BIG_LIMIT,
            offset=0,
        )
        expenses = transactions

        total_expense = sum((t.amount for t in expenses), Decimal("0.00"))

        by_category: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        by_merchant: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        for t in expenses:
            by_category[t.category] += t.amount
            if t.merchant:
                by_merchant[t.merchant] += t.amount

        def _top(items: dict[str, Decimal], limit: int = 5) -> list[dict]:
            ranked = sorted(items.items(), key=lambda kv: (-kv[1], kv[0]))
            return [{"name": name, "total": total} for name, total in ranked[:limit]]

        top_category = _top(by_category, 1)
        top_merchant = _top(by_merchant, 1)

        incomes = await self._tx.list_for_user(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            account_id=account_id,
            transaction_type="income",
            limit=_BIG_LIMIT,
            offset=0,
        )
        total_income = sum((t.amount for t in incomes), Decimal("0.00"))

        return {
            "total_gastos": total_expense,
            "total_ingresos": total_income,
            "por_categoria": [
                {"categoria": name, "total": total}
                for name, total in sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "por_comercio": [
                {"comercio": name, "total": total}
                for name, total in sorted(by_merchant.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "mayor_categoria": top_category[0]["name"] if top_category else None,
            "mayor_comercio": top_merchant[0]["name"] if top_merchant else None,
        }

    @staticmethod
    def _balances(
        accounts: list[Account],
        transactions,
        transfers,
    ) -> dict[Account, Decimal]:
        balances: dict[int, Decimal] = {acc.id: acc.initial_balance for acc in accounts}
        for t in transactions:
            if t.status != "posted" or t.account_id is None:
                continue
            if t.type == "income":
                balances[t.account_id] += t.amount
            elif t.type == "expense":
                balances[t.account_id] -= t.amount
        for tr in transfers:
            if tr.status != "posted":
                continue
            if tr.to_account_id in balances:
                balances[tr.to_account_id] += tr.amount
            if tr.from_account_id in balances:
                balances[tr.from_account_id] -= tr.amount
        return {acc: balances[acc.id] for acc in accounts}
