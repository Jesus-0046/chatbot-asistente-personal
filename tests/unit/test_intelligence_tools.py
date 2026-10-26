"""Tests de las tools de inteligencia financiera (Fase 5) ejecutadas vía el registro.

Usan fakes en memoria (no SQLite) y verifican:
- Registro: las 12 tools existen, ninguna requiere confirmación.
- Cada tool produce ok=True con amounts como strings y periodo_consultado.
- Filtrado, limit, weekend y errores de período.
- Aislamiento por usuario.

Patrón coherente con test_finance_tools.py / test_budget_tools.py: services como _Fake.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakeBudgetRepo,
    FakeDebtRepo,
    FakePersonRepo,
    FakePlannedTransactionRepo,
    FakeRecurringTransactionRepo,
    FakeSavingsContributionRepo,
    FakeSavingsGoalRepo,
    FakeSubscriptionRepo,
    FakeTransactionRepo,
    FakeTransferRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.accounts import AccountService
from asistente.domain.budgets import BudgetService
from asistente.domain.debts import DebtService
from asistente.domain.finance import FinanceService
from asistente.domain.intelligence import FinancialIntelligenceService
from asistente.domain.people import PersonService
from asistente.domain.planned import PlannedTransactionService
from asistente.domain.recurring import RecurringTransactionService
from asistente.domain.savings import SavingsGoalService
from asistente.domain.transactions import TransactionService
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry

TZ = "Europe/Madrid"


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None


def _build_intelligence_svc() -> tuple[FinancialIntelligenceService, dict]:
    """Build a real FinancialIntelligenceService wired to fakes. Returns (svc, deps)."""
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    transfers = FakeTransferRepo()
    budgets = FakeBudgetRepo()
    goals = FakeSavingsGoalRepo()
    contributions = FakeSavingsContributionRepo()
    recurring_repo = FakeRecurringTransactionRepo(recurring_store=[], planned_store=[])
    planned_repo = FakePlannedTransactionRepo(planned_store=[], tx_store=transactions._store)
    subs = FakeSubscriptionRepo()
    people = FakePersonRepo()
    debts = FakeDebtRepo()

    accounts_svc = AccountService(accounts, transactions, transfers)
    tx_svc = TransactionService(transactions, accounts)
    recurring_svc = RecurringTransactionService(recurring_repo, planned_repo, accounts, subs)
    planned_svc = PlannedTransactionService(planned_repo, tx_svc)
    finance_svc = FinanceService(accounts, transactions, transfers)

    svc = FinancialIntelligenceService(
        finance=finance_svc,
        transactions=tx_svc,
        budgets=BudgetService(budgets, transactions),
        savings_goals=SavingsGoalService(goals, contributions, transfers, accounts),
        recurring=recurring_svc,
        planned=planned_svc,
        debts=DebtService(debts, PersonService(people)),
        subscriptions=subs,
    )
    deps = {
        "accounts": accounts_svc,
        "transactions": tx_svc,
        "recurring": recurring_svc,
        "budgets": BudgetService(budgets, transactions),
        "savings_goals": SavingsGoalService(goals, contributions, transfers, accounts),
        "subscription_repo": subs,
    }
    return svc, deps


@pytest.fixture
def bundle():
    svc, deps = _build_intelligence_svc()
    settings = make_settings(default_timezone=TZ)
    return _Fake(
        users=_FakeUsers(),
        settings=settings,
        intelligence=svc,
        deps=deps,
        accounts=deps["accounts"],
        transactions=deps["transactions"],
        savings_goals=deps["savings_goals"],
    )


@pytest.fixture
def services(bundle):
    return bundle


@pytest.fixture
def deps(bundle):
    return bundle.deps


@pytest.fixture
def ctx(bundle) -> ToolContext:
    return ToolContext(user_id=1, services=bundle)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(reg: ToolRegistry, ctx: ToolContext, name: str, args: dict):
    return await reg.execute(ctx, name, args)


# --------------------------------------------------------------------------- #
# Registry sanity                                                              #
# --------------------------------------------------------------------------- #
async def test_registry_contains_12_intelligence_tools(registry) -> None:
    names = {t.name for t in registry._tools.values()}
    expected = {
        "get_financial_overview",
        "get_estimated_available",
        "get_spending_capacity",
        "analyze_spending",
        "get_spending_changes",
        "detect_unusual_expenses",
        "get_largest_expenses",
        "get_recurring_spending_summary",
        "get_savings_overview",
        "get_financial_budget_overview",
        "get_month_end_projection",
        "compare_financial_periods",
    }
    assert expected <= names


async def test_intelligence_tools_no_confirmation_required(registry) -> None:
    expected = {
        "get_financial_overview",
        "get_estimated_available",
        "get_spending_capacity",
        "analyze_spending",
        "get_spending_changes",
        "detect_unusual_expenses",
        "get_largest_expenses",
        "get_recurring_spending_summary",
        "get_savings_overview",
        "get_financial_budget_overview",
        "get_month_end_projection",
        "compare_financial_periods",
    }
    for name in expected:
        tool = registry.get(name)
        assert tool is not None, name
        assert not tool.requires_confirmation, name


# --------------------------------------------------------------------------- #
# get_financial_overview                                                       #
# --------------------------------------------------------------------------- #
async def test_financial_overview_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_financial_overview", {})
    assert res.ok, res.error
    assert res.data["patrimonio"] == "0.00"
    assert res.data["cuentas"] == []
    assert "disponible_estimado" in res.data


async def test_financial_overview_with_account(registry, ctx, deps) -> None:
    await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("2500"))
    res = await _run(registry, ctx, "get_financial_overview", {})
    assert res.ok, res.error
    assert res.data["patrimonio"] == "2500.00"
    assert len(res.data["cuentas"]) == 1


async def test_financial_overview_isolation(registry, services) -> None:
    ctx_a = ToolContext(user_id=1, services=services)
    ctx_b = ToolContext(user_id=2, services=services)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("100"))
    res_a = await _run(registry, ctx_a, "get_financial_overview", {})
    res_b = await _run(registry, ctx_b, "get_financial_overview", {})
    assert res_a.ok
    assert res_a.data["patrimonio"] != res_b.data["patrimonio"]


# --------------------------------------------------------------------------- #
# get_estimated_available                                                      #
# --------------------------------------------------------------------------- #
async def test_estimated_available_with_data(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    now = datetime.now(UTC)
    await deps["transactions"].create(
        user_id=1, type="expense", amount=Decimal("200"), occurred_at=now, account_id=acc.id, merchant="Mercadona"
    )
    await deps["transactions"].create(
        user_id=1, type="income", amount=Decimal("1500"), occurred_at=now, account_id=acc.id, merchant="Nómina"
    )
    res = await _run(registry, ctx, "get_estimated_available", {"period": "este mes"})
    assert res.ok, res.error
    assert "disponible_estimado" in res.data
    assert isinstance(res.data["disponible_estimado"], str)
    assert res.data["periodo_consultado"] == "este mes"
    assert "ahorro_objetivo" in res.data
    assert "deudas_que_vencen" in res.data


# --------------------------------------------------------------------------- #
# get_spending_capacity                                                        #
# --------------------------------------------------------------------------- #
async def test_spending_capacity_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_spending_capacity", {"period": "este mes"})
    assert res.ok, res.error
    assert "capacidad_gasto" in res.data
    assert res.data["periodo_consultado"] == "este mes"


# --------------------------------------------------------------------------- #
# analyze_spending                                                             #
# --------------------------------------------------------------------------- #
async def test_analyze_spending_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "analyze_spending", {"period": "este mes"})
    assert res.ok, res.error
    assert res.data["numero_gastos"] == 0
    assert res.data["total_gastos"] == "0.00"


async def test_analyze_spending_with_data(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    now = datetime.now(UTC)
    await deps["transactions"].create(
        user_id=1,
        type="expense",
        amount=Decimal("15"),
        occurred_at=now,
        account_id=acc.id,
        category="Alimentación",
        merchant="Mercadona",
    )
    await deps["transactions"].create(
        user_id=1,
        type="expense",
        amount=Decimal("45"),
        occurred_at=now,
        account_id=acc.id,
        category="Transporte",
        merchant="Uber",
    )
    res = await _run(registry, ctx, "analyze_spending", {"period": "este mes"})
    assert res.ok, res.error
    assert res.data["numero_gastos"] == 2
    assert isinstance(res.data["total_gastos"], str)


async def test_analyze_spending_filter_category(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    now = datetime.now(UTC)
    await deps["transactions"].create(
        user_id=1,
        type="expense",
        amount=Decimal("15"),
        occurred_at=now,
        account_id=acc.id,
        category="Alimentación",
        merchant="Mercadona",
    )
    await deps["transactions"].create(
        user_id=1,
        type="expense",
        amount=Decimal("45"),
        occurred_at=now,
        account_id=acc.id,
        category="Transporte",
        merchant="Uber",
    )
    res = await _run(registry, ctx, "analyze_spending", {"period": "este mes", "category": "Transporte"})
    assert res.ok, res.error
    assert res.data["numero_gastos"] == 1


async def test_analyze_spending_filter_merchant(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    now = datetime.now(UTC)
    await deps["transactions"].create(
        user_id=1, type="expense", amount=Decimal("10"), occurred_at=now, account_id=acc.id, merchant="McDonalds"
    )
    await deps["transactions"].create(
        user_id=1, type="expense", amount=Decimal("20"), occurred_at=now, account_id=acc.id, merchant="Uber"
    )
    res = await _run(registry, ctx, "analyze_spending", {"period": "este mes", "merchant": "McDonalds"})
    assert res.ok, res.error
    assert res.data["numero_gastos"] == 1


# --------------------------------------------------------------------------- #
# get_spending_changes                                                         #
# --------------------------------------------------------------------------- #
async def test_spending_changes_shape(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_spending_changes", {"period": "este mes"})
    assert res.ok, res.error
    assert "cambios_por_categoria" in res.data
    assert res.data["periodo_consultado"] == "este mes"


# --------------------------------------------------------------------------- #
# detect_unusual_expenses                                                      #
# --------------------------------------------------------------------------- #
async def test_detect_unusual_empty_history(registry, ctx) -> None:
    res = await _run(registry, ctx, "detect_unusual_expenses", {"period": "este mes"})
    assert res.ok, res.error
    assert res.data["marcados"] == []
    assert "fraude" not in str(res.data).lower()
    assert "reglas_aplicadas" in res.data


# --------------------------------------------------------------------------- #
# get_largest_expenses                                                         #
# --------------------------------------------------------------------------- #
async def test_largest_expenses_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_largest_expenses", {"period": "este mes"})
    assert res.ok, res.error
    assert res.data["mayores_gastos"] == []


async def test_largest_expenses_sorted_and_limited(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    now = datetime.now(UTC)
    for amt in [10, 50, 30, 90, 20]:
        await deps["transactions"].create(
            user_id=1, type="expense", amount=Decimal(str(amt)), occurred_at=now, account_id=acc.id
        )
    res = await _run(registry, ctx, "get_largest_expenses", {"period": "este mes", "limit": 3})
    assert res.ok, res.error
    assert len(res.data["mayores_gastos"]) == 3
    amounts = [g["amount"] for g in res.data["mayores_gastos"]]
    assert amounts == sorted(amounts, reverse=True)
    assert isinstance(amounts[0], str)


# --------------------------------------------------------------------------- #
# get_recurring_spending_summary                                               #
# --------------------------------------------------------------------------- #
async def test_recurring_spending_summary_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_recurring_spending_summary", {})
    assert res.ok, res.error
    assert res.data["recurrentes_activas"] == []
    assert res.data["total_mensual_estimado"]["gastos"] == "0.00"


async def test_recurring_spending_summary_with_recurrence(registry, ctx, deps) -> None:
    acc = await deps["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await deps["recurring"].create(
        user_id=1,
        type="expense",
        title="Netflix",
        amount=Decimal("12"),
        frequency="monthly",
        interval=1,
        account_id=acc.id,
        category="Suscripciones",
        day_of_month=1,
    )
    res = await _run(registry, ctx, "get_recurring_spending_summary", {})
    assert res.ok, res.error
    assert len(res.data["recurrentes_activas"]) >= 1
    assert res.data["total_mensual_estimado"]["gastos"] == "12.00"


# --------------------------------------------------------------------------- #
# get_savings_overview                                                         #
# --------------------------------------------------------------------------- #
async def test_savings_overview_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_savings_overview", {})
    assert res.ok, res.error
    assert res.data["ahorro_real"] == "0.00"
    assert res.data["objetivos"] == []


async def test_savings_overview_with_savings_account(registry, ctx, deps) -> None:
    await deps["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("500"))
    res = await _run(registry, ctx, "get_savings_overview", {})
    assert res.ok, res.error
    assert res.data["ahorro_real"] == "500.00"


# --------------------------------------------------------------------------- #
# get_financial_budget_overview                                                #
# --------------------------------------------------------------------------- #
async def test_financial_budget_overview_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_financial_budget_overview", {})
    assert res.ok, res.error
    assert res.data["presupuestos"] == []


async def test_financial_budget_overview_with_budget(registry, ctx, deps) -> None:
    now = datetime.now(UTC)
    await deps["budgets"].create(user_id=1, name="Comida", amount=Decimal("300"), year=now.year, month=now.month)
    res = await _run(registry, ctx, "get_financial_budget_overview", {})
    assert res.ok, res.error
    assert len(res.data["presupuestos"]) == 1
    p = res.data["presupuestos"][0]
    assert "estado" in p
    assert "porcentaje" in p


# --------------------------------------------------------------------------- #
# get_month_end_projection                                                     #
# --------------------------------------------------------------------------- #
async def test_month_end_projection_empty(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_month_end_projection", {})
    assert res.ok, res.error
    assert res.data["metodo"] == "solo_compromisos"
    assert "fin_de_mes_liquido_estimado" in res.data
    assert isinstance(res.data["fin_de_mes_liquido_estimado"], str)


# --------------------------------------------------------------------------- #
# compare_financial_periods                                                    #
# --------------------------------------------------------------------------- #
async def test_compare_financial_periods_shape(registry, ctx) -> None:
    res = await _run(registry, ctx, "compare_financial_periods", {"period": "este mes"})
    assert res.ok, res.error
    assert "comparativa" in res.data
    for row in res.data["comparativa"]:
        assert "metrica" in row
        assert "variacion_absoluta" in row
    assert res.data["periodo_consultado"] == "este mes"


# --------------------------------------------------------------------------- #
# _resolve_finance_period weekend support                                     #
# --------------------------------------------------------------------------- #
async def test_resolve_finance_period_weekend(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_estimated_available", {"period": "este finde"})
    assert res.ok, res.error
    assert res.data["periodo_consultado"] == "este finde"


# --------------------------------------------------------------------------- #
# Invalid period error                                                         #
# --------------------------------------------------------------------------- #
async def test_invalid_period_returns_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_estimated_available", {"period": "la semana próxima"})
    assert not res.ok
    assert res.error is not None
