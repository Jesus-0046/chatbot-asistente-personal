"""Tests del servicio de presupuestos (Fase 3.2).

Cubren creación, unicidad, estados (normal/warning/exceeded), qué cuenta como
gasto y qué no (transfers/income/contribuciones), tarjeta de crédito que
cuenta una sola vez, y aislamiento por usuario.

Los movimientos se crean directamente en el repo de transacciones (vía el
servicio solo cuando se necesita la resolución de cuenta) para controlar
exactamente la categoría, la fecha y el estado sin depender de cuentas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.domain.budgets import month_utc_range
from asistente.shared.errors import NotFoundError

_DT = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)


async def _expense(services, user_id: int, amount: str, *, category: str | None = None, when=None) -> None:
    await services["transactions"]._tx.create(
        user_id=user_id,
        type="expense",
        amount=Decimal(amount),
        occurred_at=when or _DT,
        account_id=None,
        category=category or "Otros",
    )


async def _add_user_account(services, user_id: int, name: str, acc_type: str, balance: str):
    return await services["accounts"].create(
        user_id=user_id, name=name, type=acc_type, initial_balance=Decimal(balance)
    )


# --------------------------------------------------------------------------- #
# Crear                                                                        #
# --------------------------------------------------------------------------- #
async def test_crear_presupuesto_general(services) -> None:
    budget = await services["budgets"].create(
        user_id=1, name="Presupuesto mensual", amount=Decimal("1200.00"), year=2027, month=1
    )

    assert budget.id == 1
    assert budget.category is None
    assert budget.amount == Decimal("1200.00")
    assert budget.currency == "EUR"


async def test_crear_presupuesto_por_categoria(services) -> None:
    budget = await services["budgets"].create(
        user_id=1, name="Alimentación", amount=Decimal("350.00"), year=2027, month=1, category="Alimentación"
    )

    assert budget.category == "Alimentación"


async def test_crear_presupuesto_categoria_desconocida_se_normaliza(services) -> None:
    budget = await services["budgets"].create(
        user_id=1, name="Comer", amount=Decimal("100.00"), year=2027, month=2, category="Compras extra"
    )

    assert budget.category == "Otros"


async def test_crear_presupuesto_importe_cero_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["budgets"].create(user_id=1, name="X", amount=Decimal("0.00"), year=2027, month=1)


async def test_crear_presupuesto_importe_negativo_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["budgets"].create(user_id=1, name="X", amount=Decimal("-50.00"), year=2027, month=1)


async def test_crear_presupuesto_mes_invalido_error(services) -> None:
    for month in (0, 13):
        with pytest.raises(ValueError, match="Mes no válido"):
            await services["budgets"].create(user_id=1, name="X", amount=Decimal("100"), year=2027, month=month)


async def test_crear_presupuesto_duplicado_mismo_usuario_mes_categoria_error(services) -> None:
    await services["budgets"].create(
        user_id=1, name="A", amount=Decimal("100"), year=2027, month=1, category="Alimentación"
    )

    with pytest.raises(ValueError, match="Ya existe"):
        await services["budgets"].create(
            user_id=1, name="B", amount=Decimal("200"), year=2027, month=1, category="Alimentación"
        )


async def test_crear_presupuesto_general_duplicado_error(services) -> None:
    await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)

    with pytest.raises(ValueError, match="Ya existe"):
        await services["budgets"].create(user_id=1, name="B", amount=Decimal("200"), year=2027, month=1)


async def test_crear_presupuesto_categorias_distintas_conviven(services) -> None:
    await services["budgets"].create(
        user_id=1, name="A", amount=Decimal("100"), year=2027, month=1, category="Alimentación"
    )
    budget2 = await services["budgets"].create(
        user_id=1, name="B", amount=Decimal("200"), year=2027, month=1, category="Transporte"
    )

    assert budget2.id == 2


async def test_crear_presupuesto_mismo_mes_otro_usuario_ok(services) -> None:
    await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)
    budget2 = await services["budgets"].create(user_id=2, name="B", amount=Decimal("200"), year=2027, month=1)

    assert budget2.user_id == 2


# --------------------------------------------------------------------------- #
# Estado                                                                       #
# --------------------------------------------------------------------------- #
async def test_estado_sin_gastos_cero_por_ciento(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("0.00")
    assert status["remaining"] == Decimal("500.00")
    assert status["percent_used"] == Decimal("0.00")
    assert status["status"] == "normal"


async def test_estado_100_de_500_veinte_por_ciento(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "100")

    status = await services["budgets"].get_status(1, b.id)

    assert status["percent_used"] == Decimal("20.00")
    assert status["status"] == "normal"


async def test_estado_400_de_500_warning(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "400")

    status = await services["budgets"].get_status(1, b.id)

    assert status["percent_used"] == Decimal("80.00")
    assert status["status"] == "warning"


async def test_estado_499_99_warning(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "499.99")

    status = await services["budgets"].get_status(1, b.id)

    assert status["status"] == "warning"
    assert status["percent_used"] >= Decimal("99.99")


async def test_estado_500_de_500_exceeded(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "500")

    status = await services["budgets"].get_status(1, b.id)

    assert status["status"] == "exceeded"
    assert status["remaining"] == Decimal("0.00")


async def test_estado_600_de_500_exceeded_y_restante_cero(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "600")

    status = await services["budgets"].get_status(1, b.id)

    assert status["status"] == "exceeded"
    assert status["remaining"] == Decimal("0.00")
    assert status["percent_used"] == Decimal("120.00")


# --------------------------------------------------------------------------- #
# Categorías                                                                   #
# --------------------------------------------------------------------------- #
async def test_presupuesto_categoria_solo_suma_su_categoria(services) -> None:
    b = await services["budgets"].create(
        user_id=1, name="Alim", amount=Decimal("350"), year=2027, month=1, category="Alimentación"
    )
    await _expense(services, 1, "100", category="Alimentación")
    await _expense(services, 1, "50", category="Alimentación")
    await _expense(services, 1, "40", category="Ocio")

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("150.00")
    assert status["remaining"] == Decimal("200.00")


# --------------------------------------------------------------------------- #
# General                                                                      #
# --------------------------------------------------------------------------- #
async def test_presupuesto_general_suma_todos_los_expenses(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("1200"), year=2027, month=1)
    await _expense(services, 1, "100", category="Alimentación")
    await _expense(services, 1, "50", category="Ocio")

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("150.00")


async def test_presupuesto_general_no_suma_incomes(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("1200"), year=2027, month=1)
    await _expense(services, 1, "100", category="Alimentación")
    await services["transactions"]._tx.create(
        user_id=1, type="income", amount=Decimal("2000"), occurred_at=_DT, account_id=None
    )

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("100.00")


async def test_presupuesto_general_no_suma_transfers(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("1200"), year=2027, month=1)
    await _expense(services, 1, "100", category="Alimentación")
    bank = await _add_user_account(services, 1, "Banco", "bank", "1000")
    savings = await _add_user_account(services, 1, "Ahorro", "savings", "0")
    await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
        note="a ahorro",
    )

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("100.00")


async def test_presupuesto_general_no_suma_movimientos_pending(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("1200"), year=2027, month=1)
    await _expense(services, 1, "100", category="Alimentación")
    await services["transactions"]._tx.create(
        user_id=1, type="expense", amount=Decimal("200"), occurred_at=_DT, account_id=None, status="pending"
    )

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("100.00")


# --------------------------------------------------------------------------- #
# Tarjeta de crédito                                                           #
# --------------------------------------------------------------------------- #
async def test_gasto_con_tarjeta_credito_cuenta_una_vez(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    card = await _add_user_account(services, 1, "Visa", "credit_card", "-500")
    await services["transactions"].create(
        user_id=1,
        type="expense",
        amount=Decimal("50"),
        occurred_at=_DT,
        category="Ocio",
        account_id=card.id,
        merchant="Restaurante",
    )

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("50.00")


async def test_pago_de_tarjeta_no_vuelve_a_contar(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    account = await _add_user_account(services, 1, "Banco", "bank", "1000")
    card = await _add_user_account(services, 1, "Visa", "credit_card", "-500")
    # Gasto con la tarjeta (cuenta una vez).
    await services["transactions"].create(
        user_id=1, type="expense", amount=Decimal("50"), occurred_at=_DT, account_id=card.id, merchant="Restaurante"
    )
    # Pago de la tarjeta desde el banco: transferencia, no gasto.
    await services["transfers"].create(
        user_id=1,
        from_account_id=account.id,
        to_account_id=card.id,
        amount=Decimal("50"),
        occurred_at=_DT,
        note="pago visa",
    )

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("50.00")


# --------------------------------------------------------------------------- #
# Fechas                                                                       #
# --------------------------------------------------------------------------- #
async def test_no_mezcla_meses(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    await _expense(services, 1, "100", when=datetime(2027, 1, 10, tzinfo=UTC))
    await _expense(services, 1, "300", when=datetime(2027, 2, 10, tzinfo=UTC))

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("100.00")


async def test_enero_diciembre_no_se_mezclan(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    # 31 dic 22:30 UTC = 31 dic 23:30 CET (sigue en diciembre en Madrid)
    await _expense(services, 1, "100", when=datetime(2026, 12, 31, 22, 30, tzinfo=UTC))
    await _expense(services, 1, "50", when=datetime(2027, 1, 1, 8, 0, tzinfo=UTC))

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("50.00")


async def test_mes_sigue_al_usuario_en_las_americas(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    # En America/New_York (UTC-5 invernal):
    # - 1 ene 03:00 UTC = 31 dic 22:00 EST -> diciembre 2026 -> NO cuenta.
    await _expense(services, 1, "100", when=datetime(2027, 1, 1, 3, 0, tzinfo=UTC))
    # - 1 ene 08:00 UTC = 1 ene 03:00 EST -> enero 2027 -> cuenta.
    await _expense(services, 1, "50", when=datetime(2027, 1, 1, 8, 0, tzinfo=UTC))

    status = await services["budgets"].get_status(1, b.id, timezone="America/New_York")

    assert status["spent"] == Decimal("50.00")


async def test_mes_enero_frontera_cet(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2027, month=1)
    # 31 dic 23:00 UTC = 1 ene 00:00 CET -> enero 2027 en Madrid.
    await _expense(services, 1, "20", when=datetime(2026, 12, 31, 23, 0, tzinfo=UTC))
    # 31 dic 22:59 UTC = 31 dic 23:59 CET -> diciembre 2026.
    await _expense(services, 1, "80", when=datetime(2026, 12, 31, 22, 59, tzinfo=UTC))

    status = await services["budgets"].get_status(1, b.id, timezone="Europe/Madrid")

    assert status["spent"] == Decimal("20.00")


async def test_month_utc_range_madrid_marzo_dst() -> None:
    # Marzo 2027 en Europe/Madrid: el día 28 hay DST (+1h). Un "mes" dura
    # (30 días y 23 horas) en UTC.
    start, end = month_utc_range(2027, 3, timezone="Europe/Madrid")
    assert start == datetime(2027, 2, 28, 23, 0, tzinfo=UTC)  # 1 mar 00:00 CET
    assert end == datetime(2027, 3, 31, 22, 0, tzinfo=UTC)  # 1 abr 00:00 CEST
    assert (end - start).total_seconds() == 31 * 24 * 3600 - 3600


async def test_month_utc_range_sin_dst() -> None:
    # Febrero 2027 en Europe/Madrid: sin cambio de hora -> mes completo en UTC.
    start, end = month_utc_range(2027, 2, timezone="Europe/Madrid")
    assert start == datetime(2027, 1, 31, 23, 0, tzinfo=UTC)
    assert end == datetime(2027, 2, 28, 23, 0, tzinfo=UTC)
    assert (end - start).total_seconds() == 28 * 24 * 3600


async def test_año_correcto(services) -> None:
    b = await services["budgets"].create(user_id=1, name="G", amount=Decimal("500"), year=2026, month=1)
    await _expense(services, 1, "100", when=datetime(2026, 1, 10, tzinfo=UTC))
    await _expense(services, 1, "200", when=datetime(2027, 1, 10, tzinfo=UTC))

    status = await services["budgets"].get_status(1, b.id)

    assert status["spent"] == Decimal("100.00")


# --------------------------------------------------------------------------- #
# Listado / actualización / borrado / aislamiento                              #
# --------------------------------------------------------------------------- #
async def test_listar_presupuestos_solo_del_usuario(services) -> None:
    await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)
    await services["budgets"].create(user_id=2, name="B", amount=Decimal("999"), year=2027, month=1)

    budgets = await services["budgets"].list_for_user(1, year=2027, month=1)

    assert len(budgets) == 1
    assert budgets[0].user_id == 1


async def test_estado_presupuesto_ajeno_rechazado(services) -> None:
    await services["budgets"].create(user_id=2, name="B", amount=Decimal("999"), year=2027, month=1)

    with pytest.raises(NotFoundError):
        await services["budgets"].get_status(1, 1)


async def test_actualizar_presupuesto(services) -> None:
    b = await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)

    updated = await services["budgets"].update(1, b.id, amount=Decimal("200"), category="Ocio")

    assert updated.amount == Decimal("200.00")
    assert updated.category == "Ocio"


async def test_actualizar_presupuesto_importe_negativo_error(services) -> None:
    b = await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)

    with pytest.raises(ValueError, match="mayor que 0"):
        await services["budgets"].update(1, b.id, amount=Decimal("-5"))


async def test_actualizar_presupuesto_ajeno_rechazado(services) -> None:
    await services["budgets"].create(user_id=2, name="B", amount=Decimal("100"), year=2027, month=1)

    with pytest.raises(NotFoundError):
        await services["budgets"].update(1, 1, amount=Decimal("200"))


async def test_borrar_presupuesto(services) -> None:
    b = await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)

    await services["budgets"].delete(1, b.id)

    assert await services["budgets"].list_for_user(1) == []


async def test_borrar_presupuesto_movimientos_intactos(services) -> None:
    b = await services["budgets"].create(user_id=1, name="A", amount=Decimal("100"), year=2027, month=1)
    await _expense(services, 1, "50")

    await services["budgets"].delete(1, b.id)

    remaining = await services["transactions"].list(1)
    assert len(remaining) == 1
