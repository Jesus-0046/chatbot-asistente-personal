"""Tests unitarios: dominio de inteligencia financiera (Fase 5).

Usa el fixture `services` de conftest (fakes en memoria, no aiosqlite) y
compone FinancialIntelligenceService directamente para pruebas de lógica
puramente determinista: fórmulas, detección de inusuales, proyecciones,
IDOR y cobertura de datos vacíos.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from asistente.domain.budgets import month_utc_range
from asistente.domain.intelligence import (
    FinancialIntelligenceService,
    _median,
    _normalize_text,
    _pct_change,
    previous_window,
)

TZ = "Europe/Madrid"


def _svc(services: dict) -> FinancialIntelligenceService:
    return FinancialIntelligenceService(
        finance=services["finance"],
        transactions=services["transactions"],
        budgets=services["budgets"],
        savings_goals=services["savings_goals"],
        recurring=services["recurring"],
        planned=services["planned"],
        debts=services["debts"],
        subscriptions=services["subscription_repo"],
    )


# --------------------------------------------------------------------------- #
# helpers puros                                                                #
# --------------------------------------------------------------------------- #
def test_previous_window_equal_length() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    end = datetime(2026, 10, 1, tzinfo=UTC)  # 30 days
    prev_start, prev_end = previous_window(start, end)
    assert prev_start == datetime(2026, 9, 1, tzinfo=UTC) - timedelta(days=30)
    assert prev_end == start


def test_previous_window_single_day() -> None:
    start = datetime(2026, 9, 17, tzinfo=UTC)
    end = datetime(2026, 9, 18, tzinfo=UTC)
    prev_start, prev_end = previous_window(start, end)
    assert (prev_end - prev_start).days == 1
    assert prev_end == start


def test_previous_window_empty_on_same() -> None:
    dt = datetime(2026, 9, 1, tzinfo=UTC)
    prev_start, prev_end = previous_window(dt, dt)
    assert prev_start == prev_end == dt


def test_median_empty() -> None:
    assert _median([]) is None


def test_median_single() -> None:
    assert _median([Decimal("10")]) == Decimal("10")


def test_median_odd() -> None:
    assert _median([Decimal("1"), Decimal("5"), Decimal("3")]) == Decimal("3")


def test_median_even() -> None:
    m = _median([Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")])
    assert m == Decimal("2.5")


def test_pct_change_none_on_zero_prev() -> None:
    assert _pct_change(Decimal("10"), Decimal("0")) is None


def test_pct_change_normal() -> None:
    assert _pct_change(Decimal("10"), Decimal("5")) == Decimal("100")


def test_normalize_text_accent() -> None:
    assert _normalize_text("Café y Churros") == "cafe y churros"


# --------------------------------------------------------------------------- #
# financial_overview                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_financial_overview_shape(services: dict) -> None:
    svc = _svc(services)
    out = await svc.financial_overview(1, timezone=TZ)
    assert "patrimonio" in out
    assert "cuentas" in out
    assert "gastos_mes" in out
    assert "ingresos_mes" in out
    assert "disponible_estimado" in out
    assert "total_me_deben" in out
    assert "objetivos_ahorro" in out
    assert "presupuestos_mes" in out


@pytest.mark.asyncio
async def test_financial_overview_isolation_between_users(services: dict) -> None:
    accs = services["accounts"]
    await accs.create(user_id=1, name="Banco1", type="bank", initial_balance=Decimal("500"))
    await accs.create(user_id=2, name="Banco2", type="bank", initial_balance=Decimal("300"))

    svc = _svc(services)
    out1 = await svc.financial_overview(1, timezone=TZ)
    out2 = await svc.financial_overview(2, timezone=TZ)
    assert out1["patrimonio"] != out2["patrimonio"]


@pytest.mark.asyncio
async def test_financial_overview_empty_db(services: dict) -> None:
    svc = _svc(services)
    out = await svc.financial_overview(999, timezone=TZ)
    assert out["patrimonio"] == Decimal("0.00")
    assert out["gastos_mes"] == Decimal("0.00")
    assert out["objetivos_ahorro"] == []


@pytest.mark.asyncio
async def test_financial_overview_with_subscription_in_recurring_summary(services: dict) -> None:
    """Una suscripción aparece como 'suscripcion' en recurring_spending_summary, no duplica."""
    accs = services["accounts"]
    acc = await accs.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    rec = services["recurring"]
    r = await rec.create(
        user_id=1,
        type="expense",
        title="Netflix",
        amount=Decimal("12"),
        frequency="monthly",
        interval=1,
        account_id=acc.id,
        merchant="Netflix",
        category="Suscripciones",
        day_of_month=1,
    )
    subs = services["subscription_repo"]
    await subs.create(
        user_id=1,
        name="Netflix",
        amount=Decimal("12"),
        billing_period="monthly",
        account_id=acc.id,
        recurring_transaction_id=r.id,
        active=True,
    )
    svc = _svc(services)
    out = await svc.recurring_spending_summary(1, timezone=TZ)
    assert len(out["recurrentes_activas"]) == 1
    assert out["recurrentes_activas"][0]["tipo"] == "suscripcion"


# --------------------------------------------------------------------------- #
# estimated_available                                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_estimated_available_basic(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    transactions = services["transactions"]
    now = datetime.now(UTC)
    await transactions.create(
        user_id=1,
        type="expense",
        amount=Decimal("200"),
        occurred_at=now,
        account_id=acc.id,
        category="Comida",
        merchant="Mercadona",
    )
    await transactions.create(
        user_id=1, type="income", amount=Decimal("1500"), occurred_at=now, account_id=acc.id, category="Nómina"
    )
    svc = _svc(services)
    start, end = month_utc_range(now.year, now.month, TZ)
    out = await svc.estimated_available(1, start_utc=start, end_utc=end, timezone=TZ)
    assert "disponible_estimado" in out
    assert "ahorro_objetivo" in out
    assert "deudas_que_vencen" in out


# --------------------------------------------------------------------------- #
# analyze_spending                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_analyze_spending_filters(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    tx = services["transactions"]
    now = datetime.now(UTC)
    await tx.create(
        user_id=1,
        type="expense",
        amount=Decimal("20"),
        occurred_at=now,
        account_id=acc.id,
        category="Alimentación",
        merchant="McDonalds",
    )
    await tx.create(
        user_id=1,
        type="expense",
        amount=Decimal("40"),
        occurred_at=now,
        account_id=acc.id,
        category="Transporte",
        merchant="Uber",
    )

    svc = _svc(services)
    start, end = month_utc_range(now.year, now.month, TZ)
    out = await svc.analyze_spending(1, start_utc=start, end_utc=end, category="Alimentación")
    assert out["numero_gastos"] == 1
    out2 = await svc.analyze_spending(1, start_utc=start, end_utc=end, merchant="Uber")
    assert out2["numero_gastos"] == 1


# --------------------------------------------------------------------------- #
# detect_unusual_expenses                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_detect_unusual_insufficient_history(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("5000"))
    tx = services["transactions"]
    now = datetime.now(UTC)
    await tx.create(
        user_id=1,
        type="expense",
        amount=Decimal("500"),
        occurred_at=now,
        account_id=acc.id,
        category="Restauración",
        merchant="Caro",
    )
    svc = _svc(services)
    start, end = month_utc_range(now.year, now.month, TZ)
    out = await svc.detect_unusual_expenses(1, start_utc=start, end_utc=end)
    assert len(out["marcados"]) == 0


@pytest.mark.asyncio
async def test_detect_unusual_per_transaction(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("10000"))
    tx_repo = services["transactions"]
    ref = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(5):
        await tx_repo.create(
            user_id=1,
            type="expense",
            amount=Decimal("10"),
            occurred_at=ref + timedelta(days=i),
            account_id=acc.id,
            category="Transporte",
            merchant="Taxi",
        )
    now = datetime(2026, 9, 10, tzinfo=UTC)
    await tx_repo.create(
        user_id=1,
        type="expense",
        amount=Decimal("100"),
        occurred_at=now,
        account_id=acc.id,
        category="Transporte",
        merchant="Taxi",
    )
    svc = _svc(services)
    start, end = month_utc_range(2026, 9, TZ)
    out = await svc.detect_unusual_expenses(1, start_utc=start, end_utc=end)
    assert len(out["marcados"]) >= 1
    assert any(r["tipo"] == "transaccion" for r in out["marcados"])
    assert "fraude" not in str(out).lower()


@pytest.mark.asyncio
async def test_detect_unusual_per_merchant_normalized(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("10000"))
    tx_repo = services["transactions"]
    ref = datetime(2026, 8, 1, tzinfo=UTC)
    for i in range(4):
        await tx_repo.create(
            user_id=1,
            type="expense",
            amount=Decimal("30"),
            occurred_at=ref + timedelta(days=i),
            account_id=acc.id,
            category="Comida",
            merchant="Café Central",
        )
    now = datetime(2026, 9, 10, tzinfo=UTC)
    await tx_repo.create(
        user_id=1,
        type="expense",
        amount=Decimal("120"),
        occurred_at=now,
        account_id=acc.id,
        category="Comida",
        merchant="Cafe central",
    )
    svc = _svc(services)
    start, end = month_utc_range(2026, 9, TZ)
    out = await svc.detect_unusual_expenses(1, start_utc=start, end_utc=end)
    assert any(r["tipo"] == "comercio" for r in out["marcados"])


# --------------------------------------------------------------------------- #
# largest_expenses                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_largest_expenses_sorted_and_limit(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("5000"))
    tx = services["transactions"]
    now = datetime.now(UTC)
    for amt in [10, 50, 30, 90, 20, 70]:
        await tx.create(
            user_id=1, type="expense", amount=Decimal(str(amt)), occurred_at=now, account_id=acc.id, category="Varios"
        )
    svc = _svc(services)
    start, end = month_utc_range(now.year, now.month, TZ)
    out = await svc.largest_expenses(1, start_utc=start, end_utc=end, limit=3)
    assert len(out["mayores_gastos"]) == 3
    amounts = [Decimal(r["amount"]) for r in out["mayores_gastos"]]
    assert amounts == sorted(amounts, reverse=True)


# --------------------------------------------------------------------------- #
# recurring_spending_summary                                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_recurring_spending_summary_shape(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    rec = services["recurring"]
    await rec.create(
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
    svc = _svc(services)
    out = await svc.recurring_spending_summary(1, timezone=TZ)
    assert "recurrentes_activas" in out
    assert "total_mensual_estimado" in out
    assert len(out["recurrentes_activas"]) == 1


@pytest.mark.asyncio
async def test_recurring_spending_summary_excludes_paused(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    rec = services["recurring"]
    await rec.create(
        user_id=1,
        type="expense",
        title="Netflix",
        amount=Decimal("12"),
        frequency="monthly",
        interval=1,
        account_id=acc.id,
        paused=True,
        day_of_month=1,
    )
    svc = _svc(services)
    out = await svc.recurring_spending_summary(1, timezone=TZ)
    assert len(out["recurrentes_activas"]) == 0


# --------------------------------------------------------------------------- #
# savings_overview                                                             #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_savings_overview_shape(services: dict) -> None:
    accs = services["accounts"]
    await accs.create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("2000"))
    goals = services["savings_goals"]
    await goals._goals.create(user_id=1, name="Coche", target_amount=Decimal("10000"))
    svc = _svc(services)
    out = await svc.savings_overview(1)
    assert "ahorro_real" in out
    assert "objetivos" in out
    assert Decimal(str(out["ahorro_real"])) == Decimal("2000")
    assert len(out["objetivos"]) == 1


# --------------------------------------------------------------------------- #
# financial_budget_overview                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_financial_budget_overview_shape(services: dict) -> None:
    budgets = services["budgets"]
    now = datetime.now(UTC)
    await budgets.create(
        user_id=1, name="Comida", amount=Decimal("300"), year=now.year, month=now.month, category="Comida"
    )
    svc = _svc(services)
    out = await svc.financial_budget_overview(1, year=now.year, month=now.month, timezone=TZ)
    assert len(out["presupuestos"]) == 1
    p = out["presupuestos"][0]
    assert "estado" in p
    assert "porcentaje" in p


# --------------------------------------------------------------------------- #
# month_end_projection                                                         #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_month_end_projection_solo_compromisos_when_insufficient_data(services: dict) -> None:
    svc = _svc(services)
    out = await svc.month_end_projection(1, timezone=TZ)
    assert "metodo" in out
    assert out["metodo"] == "solo_compromisos"


@pytest.mark.asyncio
async def test_month_end_projection_shape(services: dict) -> None:
    svc = _svc(services)
    out = await svc.month_end_projection(1, timezone=TZ)
    assert "realizado" in out
    assert "planificado_restante" in out
    assert "proyectado_mes" in out
    assert "fin_de_mes_liquido_estimado" in out
    assert "aviso" in out


# --------------------------------------------------------------------------- #
# compare_financial_periods                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_compare_financial_periods_shape(services: dict) -> None:
    svc = _svc(services)
    now = datetime.now(UTC)
    cur_start, cur_end = month_utc_range(now.year, now.month, TZ)
    prev_start, prev_end = previous_window(cur_start, cur_end)
    out = await svc.compare_financial_periods(
        1, current_start=cur_start, current_end=cur_end, previous_start=prev_start, previous_end=prev_end
    )
    assert "comparativa" in out
    for row in out["comparativa"]:
        assert "metrica" in row
        assert "variacion_absoluta" in row


# --------------------------------------------------------------------------- #
# IDOR / aislamiento                                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_idor_user_cannot_see_other_users_data(services: dict) -> None:
    accounts = services["accounts"]
    acc = await accounts.create(user_id=1, name="Banco1", type="bank", initial_balance=Decimal("500"))
    tx = services["transactions"]
    now = datetime.now(UTC)
    await tx.create(
        user_id=1,
        type="expense",
        amount=Decimal("100"),
        occurred_at=now,
        account_id=acc.id,
        category="Comida",
        merchant="Bar",
    )
    svc = _svc(services)
    start, end = month_utc_range(now.year, now.month, TZ)
    out1 = await svc.analyze_spending(1, start_utc=start, end_utc=end)
    out2 = await svc.analyze_spending(2, start_utc=start, end_utc=end)
    assert out1["total_gastos"] == Decimal("100")
    assert out2["total_gastos"] == Decimal("0.00")


# --------------------------------------------------------------------------- #
# _savings_commitment_for_period edge case                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_savings_commitment_ignores_past_deadline(services: dict) -> None:
    goals = services["savings_goals"]
    past = datetime.now(UTC) - timedelta(days=5)
    await goals._goals.create(user_id=1, name="Viejo", target_amount=Decimal("500"), deadline=past)
    svc = _svc(services)
    now = datetime.now(UTC)
    start, end = month_utc_range(now.year, now.month, TZ)
    commitment = await svc._savings_commitment_for_period(
        1, start_utc=start, end_utc=end, reference_utc=now, timezone=TZ
    )
    assert commitment == Decimal("0.00")
