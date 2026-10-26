"""Tests de FinanceService (Fase 3): saldos derivados y resúmenes.

Cubre la fórmula exacta de saldos (initial_balance ± ingresos/gastos +-
traspasos), el patrimonio, dinero líquido, deuda de tarjetas, el escenario de
tarjeta de crédito (gasto → deuda; pagar → traspaso sin duplicar el gasto) y
los resúmenes de gastos con desgloses.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from asistente.domain.finance import FinanceService
from asistente.shared.timeutil import to_utc

TZ_MAD = ZoneInfo("Europe/Madrid")


async def _balance_of(overview: dict, account_id: int) -> Decimal:
    return next(a["balance"] for a in overview["accounts"] if a["id"] == account_id)


async def test_patrimonio_liquido_deuda_con_gastos_ingresos(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]

    banco = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    ahorro = await accounts.create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("500"))
    visa = await accounts.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("0"))

    tx = services["transactions"]
    await tx.create(user_id=1, type="expense", amount=Decimal("100"), account_id=visa.id)
    await tx.create(user_id=1, type="income", amount=Decimal("50"), account_id=banco.id)

    tr = services["transfers"]
    await tr.create(user_id=1, from_account_id=banco.id, to_account_id=ahorro.id, amount=Decimal("200"))

    overview = await finance.balance_overview(1)

    assert await _balance_of(overview, banco.id) == Decimal("850.00")  # 1000 + 50 - 200
    assert await _balance_of(overview, ahorro.id) == Decimal("700.00")  # 500 + 200
    assert await _balance_of(overview, visa.id) == Decimal("-100.00")  # gasto con tarjeta
    assert overview["patrimonio"] == Decimal("1450.00")  # 850 + 700 - 100
    assert overview["liquido"] == Decimal("850.00")  # bank + cash + other (sin ahorro ni crédito)
    assert overview["deuda_tarjetas"] == Decimal("100.00")


async def test_pagar_tarjeta_es_traspaso_y_no_duplica_el_gasto(services) -> None:
    """Escenario clave: compra con Visa y pago posterior desde el banco.

    - Compra 100 con Visa → gasto 100, saldo Visa -100.
    - Pagar 100 (banco → Visa) es un TRASPASO, no un gasto.
    - El patrimonio neto no cambia al pagar; el "¿en qué he gastado?" sigue en 100.
    """
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]
    tr = services["transfers"]

    banco = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    visa = await accounts.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("0"))

    await tx.create(user_id=1, type="expense", amount=Decimal("100"), account_id=visa.id)
    overview_antes = await finance.balance_overview(1)
    assert await _balance_of(overview_antes, banco.id) == Decimal("1000.00")
    assert await _balance_of(overview_antes, visa.id) == Decimal("-100.00")
    assert overview_antes["deuda_tarjetas"] == Decimal("100.00")
    assert overview_antes["patrimonio"] == Decimal("900.00")
    summary_antes = await finance.spending_summary(1)
    assert summary_antes["total_gastos"] == Decimal("100.00")

    await tr.create(user_id=1, from_account_id=banco.id, to_account_id=visa.id, amount=Decimal("100"))

    overview_despues = await finance.balance_overview(1)
    assert await _balance_of(overview_despues, banco.id) == Decimal("900.00")
    assert await _balance_of(overview_despues, visa.id) == Decimal("0.00")
    assert overview_despues["deuda_tarjetas"] == Decimal("0.00")
    # El pago NO altera el patrimonio neto (traspaso interno).
    assert overview_despues["patrimonio"] == Decimal("900.00")

    # El pago NO crea otro gasto ni ingreso; el gasto total sigue siendo 100 (no 200).
    movimientos = await tx.list(1)
    assert len(movimientos) == 1
    assert movimientos[0].type == "expense"
    summary = await finance.spending_summary(1)
    assert summary["total_gastos"] == Decimal("100.00")
    assert summary["total_ingresos"] == Decimal("0.00")


async def test_multiples_traspasos_y_cuentas_sin_movimientos(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tr = services["transfers"]

    banco = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    ahorro = await accounts.create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    cash = await accounts.create(user_id=1, name="Cartera", type="cash")
    otra = await accounts.create(user_id=1, name="Otra", type="other", initial_balance=Decimal("10"))

    # Traspasos: varias cuentas (incluida banco → cash) y varias patas encadenadas.
    await tr.create(user_id=1, from_account_id=banco.id, to_account_id=ahorro.id, amount=Decimal("100"))
    await tr.create(user_id=1, from_account_id=ahorro.id, to_account_id=cash.id, amount=Decimal("50"))
    await tr.create(user_id=1, from_account_id=banco.id, to_account_id=cash.id, amount=Decimal("25"))

    overview = await finance.balance_overview(1)

    assert await _balance_of(overview, banco.id) == Decimal("875.00")  # 1000 - 100 - 25
    assert await _balance_of(overview, ahorro.id) == Decimal("50.00")  # 0 + 100 - 50
    assert await _balance_of(overview, cash.id) == Decimal("75.00")  # 0 + 50 + 25
    assert await _balance_of(overview, otra.id) == Decimal("10.00")  # sin movimientos
    assert overview["patrimonio"] == Decimal("1010.00")  # los traspasos no cambian el patrimonio
    assert overview["liquido"] == Decimal("960.00")  # bank + cash + other


async def test_balance_overview_aislado_por_usuario(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]
    tr = services["transfers"]

    banco_a = await accounts.create(user_id=1, name="Banco A", type="bank", initial_balance=Decimal("1000"))
    ahorro_a = await accounts.create(user_id=1, name="Ahorro A", type="savings")
    await accounts.create(user_id=2, name="Banco B", type="bank", initial_balance=Decimal("0"))

    # Usuario A: gasto y traspaso hacia sus propias cuentas.
    await tx.create(user_id=1, type="expense", amount=Decimal("100"), account_id=banco_a.id)
    await tr.create(user_id=1, from_account_id=banco_a.id, to_account_id=ahorro_a.id, amount=Decimal("200"))

    overview_a = await finance.balance_overview(1)
    overview_b = await finance.balance_overview(2)

    assert await _balance_of(overview_a, banco_a.id) == Decimal("700.00")
    assert await _balance_of(overview_a, ahorro_a.id) == Decimal("200.00")
    assert overview_a["patrimonio"] == Decimal("900.00")

    # B no ve absolutamente nada de A.
    assert len(overview_b["accounts"]) == 1
    assert overview_b["accounts"][0]["balance"] == Decimal("0.00")
    assert overview_b["patrimonio"] == Decimal("0.00")
    assert overview_b["liquido"] == Decimal("0.00")
    assert overview_b["deuda_tarjetas"] == Decimal("0.00")


async def test_spending_summary_desglose_por_categoria_y_comercio(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]

    await accounts.create(user_id=1, name="Banco", type="bank")
    now = to_utc(datetime(2026, 10, 5, 12, 0, tzinfo=TZ_MAD))
    await tx.create(
        user_id=1, type="expense", amount=Decimal("10"), merchant="Mercadona", category="Alimentación", occurred_at=now
    )
    await tx.create(
        user_id=1, type="expense", amount=Decimal("5"), merchant="Netflix", category="Suscripciones", occurred_at=now
    )
    await tx.create(
        user_id=1, type="expense", amount=Decimal("40"), merchant="gasolina", category="Transporte", occurred_at=now
    )
    await tx.create(user_id=1, type="income", amount=Decimal("1500"), merchant="Nómina", occurred_at=now)

    summary = await finance.spending_summary(1)

    assert summary["total_gastos"] == Decimal("55.00")
    assert summary["total_ingresos"] == Decimal("1500.00")
    assert summary["mayor_categoria"] == "Transporte"
    assert summary["mayor_comercio"] == "gasolina"
    assert {i["categoria"] for i in summary["por_categoria"]} == {"Alimentación", "Suscripciones", "Transporte"}
    assert len(summary["por_comercio"]) == 3


async def test_spending_summary_filtra_por_categoria(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]

    await accounts.create(user_id=1, name="Banco", type="bank")
    now = to_utc(datetime(2026, 10, 5, 12, 0, tzinfo=TZ_MAD))
    await tx.create(
        user_id=1, type="expense", amount=Decimal("10"), merchant="Mercadona", category="Alimentación", occurred_at=now
    )
    await tx.create(
        user_id=1, type="expense", amount=Decimal("40"), merchant="gasolina", category="Transporte", occurred_at=now
    )

    summary = await finance.spending_summary(1, category="Alimentación")

    assert summary["total_gastos"] == Decimal("10.00")
    assert len(summary["por_comercio"]) == 1


async def test_spending_summary_respeta_rango_de_fechas(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]

    await accounts.create(user_id=1, name="Banco", type="bank")
    en_rango = to_utc(datetime(2026, 10, 5, 12, 0, tzinfo=TZ_MAD))
    fuera = to_utc(datetime(2026, 11, 5, 12, 0, tzinfo=TZ_MAD))
    await tx.create(
        user_id=1,
        type="expense",
        amount=Decimal("10"),
        merchant="Mercadona",
        category="Alimentación",
        occurred_at=en_rango,
    )
    await tx.create(
        user_id=1, type="expense", amount=Decimal("90"), merchant="Otra tienda", category="Compras", occurred_at=fuera
    )

    summary = await finance.spending_summary(
        1, start_utc=en_rango, end_utc=to_utc(datetime(2026, 11, 1, 0, 0, tzinfo=TZ_MAD))
    )

    assert summary["total_gastos"] == Decimal("10.00")


async def test_traspasos_no_cuentan_en_resumen_de_gastos(services) -> None:
    accounts = services["accounts"]
    finance: FinanceService = services["finance"]
    tx = services["transactions"]
    tr = services["transfers"]

    banco = await accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    ahorro = await accounts.create(user_id=1, name="Ahorro", type="savings")
    await tr.create(user_id=1, from_account_id=banco.id, to_account_id=ahorro.id, amount=Decimal("300"))
    await tx.create(user_id=1, type="expense", amount=Decimal("20"), merchant="tienda")

    summary = await finance.spending_summary(1)

    assert summary["total_gastos"] == Decimal("20.00")  # el traspaso NO es gasto
