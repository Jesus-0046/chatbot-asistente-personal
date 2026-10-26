"""Servicio de dominio de presupuestos (Fase 3.2).

Un presupuesto mensual puede ser general (category=None) o por categoría.
El gasto calculado se obtiene de transacciones posted (expense), nunca de
transfers ni incomes.

Estados:
- normal:     < 80% del presupuesto
- warning:    >= 80% y < 100%
- exceeded:   >= 100%

Fechas (Fase 3.2/3.3.1):
- El mes de un presupuesto es el MES LOCAL del usuario (límites de calendario
  en su zona horaria), convertidos a UTC para consultar `occurred_at`: el 1 a
  las 00:00 local del usuario marca el inicio; el 1 del mes siguiente marca el
  final. Se respetan días. No se usan duraciones fijas ni horarios supuestos.
  El service recibe el `timezone` del usuario (por defecto el del proyecto).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from asistente.domain.categories import normalize_category
from asistente.domain.currencies import normalize_currency
from asistente.persistence.models import Budget
from asistente.persistence.repositories import BudgetRepository, TransactionRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import resolve_timezone, to_utc

PROJECT_DEFAULT_TZ = "Europe/Madrid"


def month_utc_range(year: int, month: int, timezone: str = PROJECT_DEFAULT_TZ) -> tuple[datetime, datetime]:
    """Devuelve (start_utc, end_utc) del año/mes EN LA ZONA del usuario.

    `start_utc` = 1 del mes a las 00:00 local del usuario → UTC.
    `end_utc`   = 1 del mes siguiente a las 00:00 local del usuario → UTC
                  (exclusivo, el intervalo es [start, end)).
    """
    if not 1 <= month <= 12:
        raise ValueError(f"Mes no válido: {month}. Debe ser 1-12.")
    tz = resolve_timezone(timezone)
    if month == 12:
        next_start = date(year + 1, 1, 1)
    else:
        next_start = date(year, month + 1, 1)
    start_local = datetime(year, month, 1, tzinfo=tz)
    end_local = datetime(next_start.year, next_start.month, 1, tzinfo=tz)
    return to_utc(start_local), to_utc(end_local)


class BudgetService:
    def __init__(self, budget_repo: BudgetRepository, transaction_repo: TransactionRepository) -> None:
        self._budgets = budget_repo
        self._tx = transaction_repo

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        amount: Decimal,
        year: int,
        month: int,
        category: str | None = None,
    ) -> Budget:
        if amount <= 0:
            raise ValueError("El importe del presupuesto debe ser mayor que 0.")
        if not 1 <= month <= 12:
            raise ValueError(f"Mes no válido: {month}. Debe ser 1-12.")
        currency = normalize_currency(None)
        normalized_cat = normalize_category(category) if category else None
        existing = await self._budgets.find_by_period(user_id, year, month, normalized_cat)
        if existing is not None:
            label = normalized_cat or "general"
            raise ValueError(f"Ya existe un presupuesto para {label} en {year}/{month}.")
        return await self._budgets.create(
            user_id=user_id,
            name=name.strip()[:200],
            amount=amount,
            currency=currency,
            year=year,
            month=month,
            category=normalized_cat,
        )

    async def update(self, user_id: int, budget_id: int, **fields) -> Budget:
        budget = await self._budgets.find_one(user_id, budget_id)
        if budget is None:
            raise NotFoundError("El presupuesto no existe o no te pertenece.")
        if "amount" in fields:
            if fields["amount"] <= 0:
                raise ValueError("El importe del presupuesto debe ser mayor que 0.")
        if "month" in fields:
            if not 1 <= fields["month"] <= 12:
                raise ValueError(f"Mes no válido: {fields['month']}. Debe ser 1-12.")
        if "category" in fields:
            fields["category"] = normalize_category(fields["category"]) if fields["category"] else None
        updated = await self._budgets.update(user_id, budget_id, **fields)
        assert updated is not None
        return updated

    async def delete(self, user_id: int, budget_id: int) -> None:
        deleted = await self._budgets.delete(user_id, budget_id)
        if not deleted:
            raise NotFoundError("El presupuesto no existe o no te pertenece.")

    async def list_for_user(self, user_id: int, *, year: int | None = None, month: int | None = None) -> list[Budget]:
        return await self._budgets.list_for_user(user_id, year=year, month=month)

    async def get_status(self, user_id: int, budget_id: int, *, timezone: str = PROJECT_DEFAULT_TZ) -> dict:
        budget = await self._budgets.find_one(user_id, budget_id)
        if budget is None:
            raise NotFoundError("El presupuesto no existe o no te pertenece.")
        return await self._compute_status(user_id, budget, timezone=timezone)

    async def list_with_status(
        self, user_id: int, *, year: int | None = None, month: int | None = None, timezone: str = PROJECT_DEFAULT_TZ
    ) -> list[dict]:
        budgets = await self._budgets.list_for_user(user_id, year=year, month=month)
        results = []
        for b in budgets:
            results.append(await self._compute_status(user_id, b, timezone=timezone))
        return results

    async def _compute_status(self, user_id: int, budget, *, timezone: str = PROJECT_DEFAULT_TZ) -> dict:
        start_utc, end_utc = month_utc_range(budget.year, budget.month, timezone)
        expenses = await self._tx.list_for_user(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            transaction_type="expense",
            category=budget.category,
            limit=1_000_000,
        )
        spent = sum((t.amount for t in expenses), Decimal("0.00"))
        budget_amount = Decimal(str(budget.amount))
        remaining = max(budget_amount - spent, Decimal("0.00"))
        percent = (spent / budget_amount * 100) if budget_amount > 0 else Decimal("0.00")
        if percent >= 100:
            status = "exceeded"
        elif percent >= 80:
            status = "warning"
        else:
            status = "normal"
        return {
            "budget": budget,
            "spent": spent,
            "remaining": remaining,
            "percent_used": percent,
            "status": status,
        }
