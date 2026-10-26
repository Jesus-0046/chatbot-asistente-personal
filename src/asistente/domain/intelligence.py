"""Servicio de dominio: inteligencia financiera, previsión y patrones (Fase 5).

Capa de LECTURA pura sobre el resto del dominio financiero. NO escribe nada:
no crea transacciones, planificados, presupuestos, objetivos, deudas ni
suscripciones. Reutiliza los servicios existentes (no duplica su lógica):

- `FinanceService`  → saldos derivados, patrimonio/liquido/deuda, resúmenes.
- `TransactionService`  → lecturas de movimientos (topes, extraños, periodos).
- `BudgetService`  → estados de presupuesto (normal/warning/exceeded).
- `SavingsGoalService` → progreso de objetivos (aportado/objetivo).
- `RecurringTransactionService` → recurrencias activas (sin escribir).
- `PlannedTransactionService` → pagos planificados pendientes (`planned`).
- `DebtService`  → deudas abiertas (me deben / debo).
- `SubscriptionRepository` → suscripciones activas (lectura pura del repo).

Principios (spec Fase 5):
- Determinista: mismas entradas → mismas salidas. Sin ML, sin probabilsm.
- Conservador: solo se afirma lo que hay datos para afirmar; los campos
  `metodo`/`aviso` explican QUÉ se calculó y CÓMO.
- Un gasto con tarjeta de crédito NO se resta dos veces del patrimonio: sus
  saldo ya resta; `deuda_tarjetas` solo informa del saldo negativo.
- Los traspasos NO son ingresos ni gastos; los planificados NO son reales.
- El ahorro objetivo se estima con la cuota documentada en `estimated_available`;
  el ahorro REAL son los saldos de cuentas `savings` (nunca se mezclan).
- Decimal en todo momento; los porcentajes admiten denominador 0 → None.

Fórmulas documentadas:
- Patrimonio/liquido/deuda_tarjetas/ahorro_real → `FinanceService.balance_overview`.
- Disponible estimado (periodo):
    disponible = liquido
               + ingresos_propuestos (posted + planned del periodo)
               - gastos_propuestos  (posted + planned del periodo)
               - ahorro_objetivo (cuota del periodo)
               - deudas que vencen en el periodo (user_owes con due_at)
    donde ahorro_objetivo = Σ (objetivo - aportado) × días_periodo / días_hasta_deadline
    (si el objetivo tiene deadline futura; si no, 0).
- Capacidad de gasto = mismo cálculo del disponible estimado (misma base).
- Equivalencia mensual de recurrencias:
    diaria × 30,4375 (365,25/12) · semanal × 52/12 · mensual × intervalo · anual /12.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from asistente.domain.budgets import BudgetService, month_utc_range
from asistente.domain.debts import DebtService
from asistente.domain.finance import FinanceService
from asistente.domain.planned import PlannedTransactionService
from asistente.domain.recurring import RecurringTransactionService
from asistente.domain.savings import SavingsGoalService
from asistente.domain.transactions import TransactionService
from asistente.persistence.repositories import SubscriptionRepository
from asistente.shared.recurrence import FREQ_DAILY, FREQ_MONTHLY, FREQ_WEEKLY, FREQ_YEARLY
from asistente.shared.timeutil import to_local, to_utc, utc_now

# Umbrales deterministas de gastos inusuales (configurables aquí).
HISTORICAL_LOOKBACK_DAYS = 90
MIN_OBSERVATIONS_CATEGORY = 5
CATEGORY_MULTIPLIER = Decimal("2.5")
MIN_AMOUNT_CATEGORY = Decimal("20.00")
MIN_OBSERVATIONS_MERCHANT = 3
MERCHANT_MULTIPLIER = Decimal("3.0")
MIN_AMOUNT_MERCHANT = Decimal("20.00")
MIN_EQUIVALENT_WINDOWS = 2
CATEGORY_WINDOW_MULTIPLIER = Decimal("2.0")
MIN_AMOUNT_CATEGORY_WINDOW = Decimal("20.00")

# Previsión de fin de mes.
PROJECTION_MIN_ELAPSED_DAYS = 10
PROJECTION_MIN_VARIABLE_TXS = 3

# Conversión a equivalente mensual (constantes documentadas).
MONTH_DAYS = Decimal("30.4375")
WEEKS_PER_YEAR = Decimal("52")
MONTHS_PER_YEAR = Decimal("12")

_BIG = 1000
_LIQUID_TYPES = ("bank", "cash", "other")


def previous_window(start_utc: datetime, end_utc: datetime) -> tuple[datetime, datetime]:
    """Ventana equivalente inmediatamente anterior a [start, end)."""
    length = end_utc - start_utc
    return start_utc - length, start_utc


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    if n % 2 == 1:
        return ordered[n // 2]
    return (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def _normalize_text(value: str | None) -> str:
    """Clave de comercio: minúsculas y sin acentos (comparación estable)."""
    folded = unicodedata.normalize("NFD", (value or "").strip().casefold())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


def _pct_change(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == 0:
        return None
    return (current - previous) / previous * 100


class FinancialIntelligenceService:
    def __init__(
        self,
        *,
        finance: FinanceService,
        transactions: TransactionService,
        budgets: BudgetService,
        savings_goals: SavingsGoalService,
        recurring: RecurringTransactionService,
        planned: PlannedTransactionService,
        debts: DebtService,
        subscriptions: SubscriptionRepository,
    ) -> None:
        self._finance = finance
        self._transactions = transactions
        self._budgets = budgets
        self._savings_goals = savings_goals
        self._recurring = recurring
        self._planned = planned
        self._debts = debts
        self._subscriptions = subscriptions

    # ------------------------------------------------------------------ #
    # Base compartida                                                     #
    # ------------------------------------------------------------------ #
    async def _balances(self, user_id: int) -> dict:
        overview = await self._finance.balance_overview(user_id)
        by_type: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
        savings_accounts: list[dict] = []
        for acc in overview["accounts"]:
            balance = Decimal(str(acc["balance"]))
            by_type[acc["type"]] += balance
            if acc["type"] == "savings":
                savings_accounts.append({"id": acc["id"], "name": acc["name"], "balance": balance})
        return {
            "overview": overview,
            "patrimonio": overview["patrimonio"],
            "liquido": overview["liquido"],
            "deuda_tarjetas": overview["deuda_tarjetas"],
            "ahorro_real": by_type["savings"],
            "savings_accounts": savings_accounts,
        }

    async def _planned_summary(
        self, user_id: int, *, start_utc: datetime, end_utc: datetime
    ) -> tuple[Decimal, Decimal]:
        end = end_utc - timedelta(microseconds=1) if end_utc is not None else None
        expenses = await self._planned.list_planned(
            user_id, status="planned", transaction_type="expense", start_utc=start_utc, end_utc=end, limit=500
        )
        incomes = await self._planned.list_planned(
            user_id, status="planned", transaction_type="income", start_utc=start_utc, end_utc=end, limit=500
        )
        total_expense = sum((Decimal(str(p.amount)) for p in expenses), Decimal("0.00"))
        total_income = sum((Decimal(str(p.amount)) for p in incomes), Decimal("0.00"))
        return total_expense, total_income

    async def _savings_commitment_for_period(
        self, user_id: int, *, start_utc: datetime, end_utc: datetime, reference_utc: datetime, timezone: str
    ) -> Decimal:
        """Cuota de ahorro objetivo prorrateada al periodo (fórmula documentada)."""
        if end_utc <= start_utc:
            return Decimal("0.00")
        period_days = (end_utc - start_utc).days
        if period_days <= 0:
            return Decimal("0.00")
        local_today = to_local(reference_utc, timezone).date()
        goals = await self._savings_goals.list_goals(user_id)
        total = Decimal("0.00")
        for goal in goals:
            deadline = goal.deadline
            if deadline is None:
                continue
            deadline_date = to_local(deadline, timezone).date()
            days_until = (deadline_date - local_today).days
            if days_until <= 0:
                continue
            status = await self._savings_goals.get_goal_status(user_id, goal.id)
            contributed = status["contributed"]
            target = Decimal(str(goal.target_amount))
            remaining = target - contributed
            if remaining <= 0:
                continue
            total += remaining * Decimal(period_days) / Decimal(days_until)
        return total

    async def _debts_due_in(self, user_id: int, *, start_utc: datetime, end_utc: datetime) -> Decimal:
        status = await self._debts.get_status(user_id)
        total = Decimal("0.00")
        for debt in status["debts"]:
            if debt["direction"] != "user_owes" or debt["due_at"] is None:
                continue
            due_utc = to_utc(debt["due_at"])
            if start_utc <= due_utc < end_utc:
                total += Decimal(str(debt["remaining"]))
        return total

    # ------------------------------------------------------------------ #
    # 3. get_financial_overview                                           #
    # ------------------------------------------------------------------ #
    async def financial_overview(self, user_id: int, *, timezone: str, reference_utc: datetime | None = None) -> dict:
        now = reference_utc or utc_now()
        local_now = to_local(now, timezone)
        start_utc, end_utc = month_utc_range(local_now.year, local_now.month, timezone)
        balances = await self._balances(user_id)
        summary = await self._finance.spending_summary(user_id, start_utc=start_utc, end_utc=end_utc)
        planned_expense, _ = await self._planned_summary(user_id, start_utc=start_utc, end_utc=end_utc)
        debts = await self._debts.get_status(user_id)
        budgets = await self._budgets.list_with_status(
            user_id, year=local_now.year, month=local_now.month, timezone=timezone
        )
        goals = await self._goals_overview(user_id)
        available = await self.estimated_available(
            user_id, start_utc=start_utc, end_utc=end_utc, timezone=timezone, reference_utc=now
        )
        return {
            "periodo": {
                "year": local_now.year,
                "month": local_now.month,
                "start_utc": start_utc,
                "end_utc": end_utc,
            },
            "patrimonio": balances["patrimonio"],
            "liquido": balances["liquido"],
            "deuda_tarjetas": balances["deuda_tarjetas"],
            "ahorro_real": balances["ahorro_real"],
            "cuentas": [
                {"id": a["id"], "name": a["name"], "type": a["type"], "balance": a["balance"]}
                for a in balances["overview"]["accounts"]
            ],
            "ingresos_mes": summary["total_ingresos"],
            "gastos_mes": summary["total_gastos"],
            "gastos_pendientes_mes": planned_expense,
            "total_me_deben": debts["total_owed_to_me"],
            "total_debo": debts["total_i_owe"],
            "objetivos_ahorro": goals,
            "presupuestos_mes": budgets,
            "disponible_estimado": available,
        }

    async def _goals_overview(self, user_id: int) -> list[dict]:
        goals = await self._savings_goals.list_goals(user_id)
        result = []
        for goal in goals:
            status = await self._savings_goals.get_goal_status(user_id, goal.id)
            result.append(
                {
                    "id": goal.id,
                    "name": goal.name,
                    "target": Decimal(str(goal.target_amount)),
                    "contributed": status["contributed"],
                    "remaining": status["remaining"],
                    "progress": status["progress"],
                    "status": status["status"],
                    "deadline": goal.deadline,
                }
            )
        return result

    # ------------------------------------------------------------------ #
    # 4. get_estimated_available                                          #
    # ------------------------------------------------------------------ #
    async def estimated_available(
        self,
        user_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
        timezone: str,
        reference_utc: datetime | None = None,
    ) -> dict:
        now = reference_utc or utc_now()
        balances = await self._balances(user_id)
        income_posted = await self._finance.spending_summary(user_id, start_utc=start_utc, end_utc=end_utc)
        planned_expense, planned_income = await self._planned_summary(user_id, start_utc=start_utc, end_utc=end_utc)
        savings_commitment = await self._savings_commitment_for_period(
            user_id, start_utc=start_utc, end_utc=end_utc, reference_utc=now, timezone=timezone
        )
        debts_due = await self._debts_due_in(user_id, start_utc=start_utc, end_utc=end_utc)

        ingresos_previstos = income_posted["total_ingresos"] + planned_income
        gastos_previstos = income_posted["total_gastos"] + planned_expense
        resultado = balances["liquido"] + ingresos_previstos - gastos_previstos - savings_commitment - debts_due
        return {
            "periodo": {"start_utc": start_utc, "end_utc": end_utc},
            "liquido_actual": balances["liquido"],
            "ingresos_previstos": ingresos_previstos,
            "gastos_previstos": gastos_previstos,
            "ahorro_objetivo": savings_commitment,
            "deudas_que_vencen": debts_due,
            "disponible_estimado": resultado,
            "metodo": (
                "disponible = liquido + ingresos(realizados y planificados) "
                "- gastos(realizados y planificados) - ahorro objetivo - deudas que vencen"
            ),
            "limitacion": "los planificados cubren los pr\u00f3ximos 90 d\u00edas generados por recurrencias",
        }

    # ------------------------------------------------------------------ #
    # 5. get_spending_capacity                                            #
    # ------------------------------------------------------------------ #
    async def spending_capacity(
        self,
        user_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
        timezone: str,
        reference_utc: datetime | None = None,
    ) -> dict:
        data = await self.estimated_available(
            user_id, start_utc=start_utc, end_utc=end_utc, timezone=timezone, reference_utc=reference_utc
        )
        balances = await self._balances(user_id)
        data["capacidad_gasto"] = data["disponible_estimado"]
        data["ahorro_excluido"] = balances["ahorro_real"]
        data["metodo"] = "mismo c\u00e1lculo que el disponible estimado; el ahorro no se toca para gastar"
        return data

    # ------------------------------------------------------------------ #
    # 6. analyze_spending                                                 #
    # ------------------------------------------------------------------ #
    async def analyze_spending(
        self,
        user_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
        category: str | None = None,
        merchant: str | None = None,
        account_id: int | None = None,
    ) -> dict:
        summary = await self._finance.spending_summary(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            category=category,
            merchant=merchant,
            account_id=account_id,
        )
        prev_start, prev_end = previous_window(start_utc, end_utc)
        prev_summary = await self._finance.spending_summary(
            user_id,
            start_utc=prev_start,
            end_utc=prev_end,
            category=category,
            merchant=merchant,
            account_id=account_id,
        )
        rows = await self._transactions.list(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            transaction_type="expense",
            category=category,
            merchant=merchant,
            account_id=account_id,
            limit=_BIG,
        )
        total_gastos = summary["total_gastos"]
        count = len(rows)
        total_ingresos = summary["total_ingresos"]
        gasto_medio = (total_gastos / count) if count > 0 else Decimal("0.00")
        por_categoria = []
        for item in summary["por_categoria"]:
            porcentaje = (item["total"] / total_gastos * 100) if total_gastos > 0 else Decimal("0.00")
            por_categoria.append({"categoria": item["categoria"], "total": item["total"], "porcentaje": porcentaje})
        largest = sorted(rows, key=lambda t: (t.amount, t.occurred_at), reverse=True)[:5]
        delta = total_gastos - prev_summary["total_gastos"]
        return {
            "periodo": {"start_utc": start_utc, "end_utc": end_utc},
            "periodo_anterior_equivalente": {"start_utc": prev_start, "end_utc": prev_end},
            "numero_gastos": count,
            "total_gastos": total_gastos,
            "total_ingresos": total_ingresos,
            "gasto_medio": gasto_medio,
            "por_categoria": por_categoria,
            "por_comercio": summary["por_comercio"],
            "mayores_gastos": [
                {
                    "id": t.id,
                    "amount": t.amount,
                    "merchant": t.merchant,
                    "category": t.category,
                    "account_id": t.account_id,
                    "occurred_at": t.occurred_at,
                }
                for t in largest
            ],
            "comparacion_periodo_anterior": {
                "gastos_anterior": prev_summary["total_gastos"],
                "ingresos_anterior": prev_summary["total_ingresos"],
                "variacion_absoluta": delta,
                "variacion_porcentual": _pct_change(total_gastos, prev_summary["total_gastos"]),
            },
        }

    # ------------------------------------------------------------------ #
    # 7. get_spending_changes                                             #
    # ------------------------------------------------------------------ #
    async def spending_changes(
        self,
        user_id: int,
        *,
        current_start: datetime,
        current_end: datetime,
        previous_start: datetime,
        previous_end: datetime,
    ) -> dict:
        current = await self._finance.spending_summary(user_id, start_utc=current_start, end_utc=current_end)
        previous = await self._finance.spending_summary(user_id, start_utc=previous_start, end_utc=previous_end)
        by_category: dict[str, dict] = {}
        for item in current["por_categoria"]:
            by_category.setdefault(item["categoria"], {"current": Decimal("0.00"), "previous": Decimal("0.00")})[
                "current"
            ] = item["total"]
        for item in previous["por_categoria"]:
            by_category.setdefault(item["categoria"], {"current": Decimal("0.00"), "previous": Decimal("0.00")})[
                "previous"
            ] = item["total"]
        changes = []
        for categoria, amounts in sorted(
            by_category.items(), key=lambda kv: -max(kv[1]["current"] - kv[1]["previous"], Decimal("0.00"))
        ):
            delta = amounts["current"] - amounts["previous"]
            changes.append(
                {
                    "categoria": categoria,
                    "gastos_actuales": amounts["current"],
                    "gastos_anterior": amounts["previous"],
                    "variacion_absoluta": delta,
                    "variacion_porcentual": _pct_change(amounts["current"], amounts["previous"]),
                    "nota": "sin porcentaje comparable (no hab\u00eda gasto previo en esa categor\u00eda)"
                    if amounts["previous"] == 0
                    else None,
                }
            )
        return {
            "periodo_actual": {"start_utc": current_start, "end_utc": current_end},
            "periodo_anterior": {"start_utc": previous_start, "end_utc": previous_end},
            "cambios_por_categoria": changes,
        }

    # ------------------------------------------------------------------ #
    # 8. detect_unusual_expenses                                          #
    # ------------------------------------------------------------------ #
    async def detect_unusual_expenses(self, user_id: int, *, start_utc: datetime, end_utc: datetime) -> dict:
        current_rows = await self._transactions.list(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            transaction_type="expense",
            limit=_BIG,
        )
        hist_start = start_utc - timedelta(days=HISTORICAL_LOOKBACK_DAYS)
        history = await self._transactions.list(
            user_id,
            start_utc=hist_start,
            end_utc=start_utc,
            transaction_type="expense",
            limit=_BIG,
        )
        hist_by_category: dict[str, list[Decimal]] = defaultdict(list)
        hist_by_merchant: dict[str, list[Decimal]] = defaultdict(list)
        for h in history:
            hist_by_category[h.category].append(Decimal(str(h.amount)))
            if h.merchant:
                hist_by_merchant[_normalize_text(h.merchant)].append(Decimal(str(h.amount)))

        marcados: list[dict] = []
        for t in current_rows:
            amount = Decimal(str(t.amount))
            amounts = hist_by_category.get(t.category, [])
            if len(amounts) >= MIN_OBSERVATIONS_CATEGORY and amount >= MIN_AMOUNT_CATEGORY:
                median_value = _median(amounts)
                if median_value and median_value > 0 and amount > median_value * CATEGORY_MULTIPLIER:
                    marcados.append(
                        {
                            "tipo": "transaccion",
                            "transaction_id": t.id,
                            "amount": amount,
                            "merchant": t.merchant,
                            "category": t.category,
                            "account_id": t.account_id,
                            "occurred_at": t.occurred_at,
                            "referencia_historica": {
                                "mediana": median_value,
                                "observaciones": len(amounts),
                                "umbral": (
                                    f"gasto en {t.category} por encima de x{CATEGORY_MULTIPLIER} "
                                    "de su mediana historica"
                                ),
                            },
                            "razon": (
                                f"gasto de {amount} en {t.category}, muy por encima de tu historico "
                                f"(mediana {median_value} con {len(amounts)} registros previos)"
                            ),
                        }
                    )
                    continue
            if t.merchant:
                amounts_merchant = hist_by_merchant.get(_normalize_text(t.merchant), [])
                if len(amounts_merchant) >= MIN_OBSERVATIONS_MERCHANT and amount >= MIN_AMOUNT_MERCHANT:
                    median_merchant = _median(amounts_merchant)
                    if median_merchant and median_merchant > 0 and amount > median_merchant * MERCHANT_MULTIPLIER:
                        marcados.append(
                            {
                                "tipo": "comercio",
                                "transaction_id": t.id,
                                "amount": amount,
                                "merchant": t.merchant,
                                "category": t.category,
                                "account_id": t.account_id,
                                "occurred_at": t.occurred_at,
                                "referencia_historica": {
                                    "mediana": median_merchant,
                                    "observaciones": len(amounts_merchant),
                                    "umbral": (
                                        f"importe en {t.merchant} por encima de x{MERCHANT_MULTIPLIER} "
                                        "de su mediana historica"
                                    ),
                                },
                                "razon": (
                                    f"compra de {amount} en {t.merchant}, por encima de lo habitual "
                                    f"(mediana {median_merchant} con {len(amounts_merchant)} registros previos)"
                                ),
                            }
                        )

        # Categoría: total del periodo frente a ventanas equivalentes anteriores.
        current_summary = await self._finance.spending_summary(user_id, start_utc=start_utc, end_utc=end_utc)
        current_totals = {item["categoria"]: item["total"] for item in current_summary["por_categoria"]}
        window_length = end_utc - start_utc
        windows = []
        if window_length.total_seconds() > 0:
            cursor = hist_start
            while cursor + window_length <= start_utc:
                windows.append((cursor, cursor + window_length))
                cursor = cursor + window_length
        window_totals: dict[str, list[Decimal]] = defaultdict(list)
        if len(windows) >= MIN_EQUIVALENT_WINDOWS:
            for wstart, wend in windows:
                wsummary = await self._finance.spending_summary(user_id, start_utc=wstart, end_utc=wend)
                for item in wsummary["por_categoria"]:
                    window_totals[item["categoria"]].append(item["total"])
            for categoria, total in current_totals.items():
                equivalent = window_totals.get(categoria, [])
                if len(equivalent) < MIN_EQUIVALENT_WINDOWS or total < MIN_AMOUNT_CATEGORY_WINDOW:
                    continue
                median_cat = _median(equivalent)
                if median_cat and median_cat > 0 and total > median_cat * CATEGORY_WINDOW_MULTIPLIER:
                    marcados.append(
                        {
                            "tipo": "categoria",
                            "transaction_id": None,
                            "amount": total,
                            "merchant": None,
                            "category": categoria,
                            "account_id": None,
                            "occurred_at": None,
                            "referencia_historica": {
                                "mediana": median_cat,
                                "observaciones": len(equivalent),
                                "umbral": (
                                    f"total del periodo en {categoria} por encima de x"
                                    f"{CATEGORY_WINDOW_MULTIPLIER} de su mediana historica"
                                ),
                            },
                            "razon": (
                                f"el gasto total en {categoria} ({total}) es muy superior al de "
                                f"periodos equivalentes previos (mediana {median_cat})"
                            ),
                        }
                    )

        return {
            "periodo": {"start_utc": start_utc, "end_utc": end_utc},
            "historial_analizado": {
                "ventana_dias": HISTORICAL_LOOKBACK_DAYS,
                "observaciones": len(history),
            },
            "marcados": marcados,
            "reglas_aplicadas": {
                "por_transaccion": {
                    "min_observaciones": MIN_OBSERVATIONS_CATEGORY,
                    "multiplicador_mediana": CATEGORY_MULTIPLIER,
                    "importe_minimo": MIN_AMOUNT_CATEGORY,
                },
                "por_comercio": {
                    "min_observaciones": MIN_OBSERVATIONS_MERCHANT,
                    "multiplicador_mediana": MERCHANT_MULTIPLIER,
                    "importe_minimo": MIN_AMOUNT_MERCHANT,
                },
                "por_categoria_periodo": {
                    "min_ventanas_equivalentes": MIN_EQUIVALENT_WINDOWS,
                    "multiplicador_mediana": CATEGORY_WINDOW_MULTIPLIER,
                    "importe_minimo": MIN_AMOUNT_CATEGORY_WINDOW,
                },
            },
        }

    # ------------------------------------------------------------------ #
    # 9. get_largest_expenses                                             #
    # ------------------------------------------------------------------ #
    async def largest_expenses(
        self,
        user_id: int,
        *,
        start_utc: datetime,
        end_utc: datetime,
        category: str | None = None,
        merchant: str | None = None,
        account_id: int | None = None,
        limit: int = 5,
    ) -> dict:
        rows = await self._transactions.list(
            user_id,
            start_utc=start_utc,
            end_utc=end_utc,
            transaction_type="expense",
            category=category,
            merchant=merchant,
            account_id=account_id,
            limit=_BIG,
        )
        largest = sorted(rows, key=lambda t: (t.amount, t.occurred_at), reverse=True)[:limit]
        return {
            "periodo": {"start_utc": start_utc, "end_utc": end_utc},
            "numero_gastos": len(rows),
            "mayores_gastos": [
                {
                    "posicion": index + 1,
                    "id": t.id,
                    "amount": t.amount,
                    "merchant": t.merchant,
                    "category": t.category,
                    "account_id": t.account_id,
                    "occurred_at": t.occurred_at,
                }
                for index, t in enumerate(largest)
            ],
        }

    # ------------------------------------------------------------------ #
    # 10. get_recurring_spending_summary                                  #
    # ------------------------------------------------------------------ #
    async def recurring_spending_summary(
        self, user_id: int, *, timezone: str, reference_utc: datetime | None = None
    ) -> dict:
        now = reference_utc or utc_now()
        recurrences = await self._recurring.list(user_id, active_only=True, include_paused=False, limit=200)
        subscriptions = await self._subscriptions.list_for_user(user_id, active_only=True, limit=200)
        subscription_recurring_ids = {
            s.recurring_transaction_id for s in subscriptions if s.recurring_transaction_id is not None
        }
        entries: list[dict[str, Any]] = []
        monthly_expense = Decimal("0.00")
        monthly_income = Decimal("0.00")
        for r in recurrences:
            amount = Decimal(str(r.amount))
            monthly = self._monthly_equivalent(amount, r.frequency, r.interval)
            if r.type == "expense":
                monthly_expense += monthly
            else:
                monthly_income += monthly
            entries.append(
                {
                    "id": r.id,
                    "tipo": "suscripcion" if r.id in subscription_recurring_ids else "recurrencia",
                    "title": r.title,
                    "type": r.type,
                    "amount": amount,
                    "frequency": r.frequency,
                    "interval": r.interval,
                    "category": r.category,
                    "next_due_at": r.next_due_at,
                    "equivalente_mensual": monthly,
                }
            )
        entries.sort(key=lambda e: (-e["equivalente_mensual"], e["title"]))
        upcoming_expenses = await self._planned.list_planned(
            user_id,
            status="planned",
            transaction_type="expense",
            start_utc=now,
            end_utc=now + timedelta(days=30) - timedelta(microseconds=1),
            limit=100,
        )
        return {
            "recurrentes_activas": entries,
            "total_mensual_estimado": {"gastos": monthly_expense, "ingresos": monthly_income},
            "total_anual_estimado": {
                "gastos": monthly_expense * MONTHS_PER_YEAR,
                "ingresos": monthly_income * MONTHS_PER_YEAR,
            },
            "proximos_cobros": [
                {
                    "id": p.id,
                    "title": p.title or p.merchant,
                    "amount": p.amount,
                    "category": p.category,
                    "planned_at": p.planned_at,
                }
                for p in upcoming_expenses
            ],
            "metodo": (
                "mensual: diaria x30,4375, semanal x52/12, mensual xintervalo, anual /12; "
                "las suscripciones no se suman aparte porque ya son recurrencias"
            ),
        }

    @staticmethod
    def _monthly_equivalent(amount: Decimal, frequency: str, interval: int) -> Decimal:
        if interval <= 0:
            interval = 1
        if frequency == FREQ_DAILY:
            return amount * MONTH_DAYS
        if frequency == FREQ_WEEKLY:
            return amount * WEEKS_PER_YEAR / MONTHS_PER_YEAR
        if frequency == FREQ_MONTHLY:
            return amount * Decimal(interval)
        if frequency == FREQ_YEARLY:
            return amount * Decimal(interval) / MONTHS_PER_YEAR
        return amount

    # ------------------------------------------------------------------ #
    # 11. get_savings_overview                                            #
    # ------------------------------------------------------------------ #
    async def savings_overview(self, user_id: int) -> dict:
        balances = await self._balances(user_id)
        goals = await self._goals_overview(user_id)
        return {
            "ahorro_real": balances["ahorro_real"],
            "cuentas_ahorro": balances["savings_accounts"],
            "objetivos": goals,
            "nota": (
                "el ahorro real (saldos de cuentas savings) y los objetivos de ahorro "
                "(aportaciones) se reportan por separado; progreso = aportado / objetivo"
            ),
        }

    # ------------------------------------------------------------------ #
    # 12. get_financial_budget_overview                                   #
    # ------------------------------------------------------------------ #
    async def financial_budget_overview(self, user_id: int, *, year: int, month: int, timezone: str) -> dict:
        budgets = await self._budgets.list_with_status(user_id, year=year, month=month, timezone=timezone)
        rows = []
        total_limit = Decimal("0.00")
        total_spent = Decimal("0.00")
        for status in budgets:
            budget = status["budget"]
            limit = Decimal(str(budget.amount))
            spent = status["spent"]
            total_limit += limit
            total_spent += spent
            rows.append(
                {
                    "id": budget.id,
                    "name": budget.name,
                    "category": budget.category,
                    "limite": limit,
                    "gastado": spent,
                    "restante": status["remaining"],
                    "porcentaje": status["percent_used"],
                    "estado": status["status"],
                }
            )
        return {
            "year": year,
            "month": month,
            "presupuestos": rows,
            "resumen": {
                "total_limite": total_limit,
                "total_gastado": total_spent,
                "alerta": any(row["estado"] in ("warning", "exceeded") for row in rows),
            },
            "estados": "normal <80% · warning 80-99,99% · exceeded >=100%",
        }

    # ------------------------------------------------------------------ #
    # 13. get_month_end_projection                                        #
    # ------------------------------------------------------------------ #
    async def month_end_projection(self, user_id: int, *, timezone: str, reference_utc: datetime | None = None) -> dict:
        now = reference_utc or utc_now()
        local_now = to_local(now, timezone)
        start_utc, end_utc = month_utc_range(local_now.year, local_now.month, timezone)
        realized_expenses = await self._transactions.list(
            user_id, start_utc=start_utc, end_utc=now, transaction_type="expense", limit=_BIG
        )
        realized_income = await self._transactions.list(
            user_id, start_utc=start_utc, end_utc=now, transaction_type="income", limit=_BIG
        )
        realized_expense_total = sum((Decimal(str(t.amount)) for t in realized_expenses), Decimal("0.00"))
        realized_income_total = sum((Decimal(str(t.amount)) for t in realized_income), Decimal("0.00"))
        # Restante planificado: [now, fin de mes).
        end_inclusive = end_utc - timedelta(microseconds=1)
        planned_expense, planned_income = await self._planned_summary(user_id, start_utc=now, end_utc=end_inclusive)
        days_elapsed = (local_now.date() - to_local(start_utc, timezone).date()).days
        remaining_days = (to_local(end_utc, timezone).date() - local_now.date()).days
        variable_daily = Decimal("0.00")
        if days_elapsed > 0 and realized_expense_total > 0:
            variable_daily = realized_expense_total / Decimal(days_elapsed)
        sufficient = (
            days_elapsed >= PROJECTION_MIN_ELAPSED_DAYS
            and len(realized_expenses) >= PROJECTION_MIN_VARIABLE_TXS
            and remaining_days > 0
        )
        projected_variable = variable_daily * Decimal(remaining_days) if sufficient else Decimal("0.00")
        method = "extrapolacion_diaria" if sufficient else "solo_compromisos"

        balances = await self._balances(user_id)
        projected_spend_remaining = planned_expense + projected_variable
        projected_income_remaining = planned_income
        end_liquid = balances["liquido"] + projected_income_remaining - projected_spend_remaining
        aviso = []
        if method == "extrapolacion_diaria":
            aviso.append(
                f"gastos variables estimados a {variable_daily} por dia durante {remaining_days} dias restantes"
            )
        elif remaining_days <= 0:
            aviso.append("el mes ya ha terminado: fecha de referencia fuera del mes")
        else:
            aviso.append(
                "solo se incluyen compromisos conocidos (datos diarios insuficientes para extrapolar variables)"
            )
        return {
            "periodo": {"year": local_now.year, "month": local_now.month},
            "metodo": method,
            "dias_transcurridos": days_elapsed,
            "dias_restantes": remaining_days,
            "realizado": {"gastos": realized_expense_total, "ingresos": realized_income_total},
            "planificado_restante": {
                "gastos": planned_expense,
                "ingresos": planned_income,
                "gastos_variables_extrapolados": projected_variable,
            },
            "proyectado_mes": {
                "gastos": realized_expense_total + projected_spend_remaining,
                "ingresos": realized_income_total + projected_income_remaining,
            },
            "fin_de_mes_liquido_estimado": end_liquid,
            "aviso": aviso,
        }

    # ------------------------------------------------------------------ #
    # 14. compare_financial_periods                                       #
    # ------------------------------------------------------------------ #
    async def compare_financial_periods(
        self,
        user_id: int,
        *,
        current_start: datetime,
        current_end: datetime,
        previous_start: datetime,
        previous_end: datetime,
    ) -> dict:
        current = await self._finance.spending_summary(user_id, start_utc=current_start, end_utc=current_end)
        previous = await self._finance.spending_summary(user_id, start_utc=previous_start, end_utc=previous_end)

        def _row(metric: str, current_value: Decimal, previous_value: Decimal) -> dict:
            return {
                "metrica": metric,
                "actual": current_value,
                "anterior": previous_value,
                "variacion_absoluta": current_value - previous_value,
                "variacion_porcentual": _pct_change(current_value, previous_value),
            }

        ahorro_current = current["total_ingresos"] - current["total_gastos"]
        ahorro_previous = previous["total_ingresos"] - previous["total_gastos"]
        return {
            "periodo_actual": {"start_utc": current_start, "end_utc": current_end},
            "periodo_anterior": {"start_utc": previous_start, "end_utc": previous_end},
            "comparativa": [
                _row("ingresos", current["total_ingresos"], previous["total_ingresos"]),
                _row("gastos", current["total_gastos"], previous["total_gastos"]),
                _row("ahorro", ahorro_current, ahorro_previous),
            ],
            "nota": "el ahorro del periodo es ingresos - gastos; los traspasos internos no cuentan",
        }
