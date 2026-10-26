"""Tests del servicio de objetivos de ahorro y contribuciones (Fase 3.2).

Cubren creación de objetivos, contribuciones, cálculo de estado, completado,
exceso permitido, cancelación, vinculación de transferencias y aislamiento.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from asistente.shared.errors import NotFoundError

_DT = datetime(2027, 1, 15, 12, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Crear objetivo                                                               #
# --------------------------------------------------------------------------- #
async def test_crear_objetivo(services) -> None:
    goal = await services["savings_goals"].create_goal(
        user_id=1, name="coche", target_amount=Decimal("3000.00"), deadline=date(2027, 8, 31)
    )

    assert goal.id == 1
    assert goal.name == "coche"
    assert goal.target_amount == Decimal("3000.00")
    assert goal.currency == "EUR"
    assert goal.deadline == date(2027, 8, 31)
    assert goal.status == "active"


async def test_crear_objetivo_deadline_opcional(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="vacaciones", target_amount=Decimal("1000.00"))

    assert goal.deadline is None


async def test_crear_objetivo_target_cero_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["savings_goals"].create_goal(user_id=1, name="x", target_amount=Decimal("0.00"))


async def test_crear_objetivo_target_negativo_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["savings_goals"].create_goal(user_id=1, name="x", target_amount=Decimal("-100"))


async def test_crear_objetivo_aislamiento(services) -> None:
    goal_a = await services["savings_goals"].create_goal(user_id=1, name="A", target_amount=Decimal("100"))
    await services["savings_goals"].create_goal(user_id=2, name="B", target_amount=Decimal("200"))

    assert goal_a.user_id == 1
    assert len(await services["savings_goals"].list_goals(1)) == 1


# --------------------------------------------------------------------------- #
# Contribuciones                                                               #
# --------------------------------------------------------------------------- #
async def test_contribucion_valida(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    contrib = await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("300.00"), contributed_at=_DT, note="enero"
    )

    assert contrib.goal_id == goal.id
    assert contrib.amount == Decimal("300.00")
    assert contrib.note == "enero"
    assert contrib.transfer_id is None


async def test_contribucion_cero_error(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))

    with pytest.raises(ValueError, match="mayor que 0"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("0.00"), contributed_at=_DT
        )


async def test_contribucion_negativa_error(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))

    with pytest.raises(ValueError, match="mayor que 0"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("-50"), contributed_at=_DT
        )


async def test_contribucion_a_objetivo_ajeno_error(services) -> None:
    await services["savings_goals"].create_goal(user_id=2, name="A", target_amount=Decimal("1000"))

    with pytest.raises(NotFoundError):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=1, amount=Decimal("100"), contributed_at=_DT
        )


async def test_contribucion_a_objetivo_cancelado_error(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    await services["savings_goals"].cancel_goal(1, goal.id)

    with pytest.raises(Exception, match="cancelado"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("100"), contributed_at=_DT
        )


# --------------------------------------------------------------------------- #
# Estado                                                                       #
# --------------------------------------------------------------------------- #
async def test_estado_objetivo(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("3000"))
    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("500"), contributed_at=_DT
    )
    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("250"), contributed_at=_DT
    )

    status = await services["savings_goals"].get_goal_status(1, goal.id)

    assert status["contributed"] == Decimal("750.00")
    assert status["remaining"] == Decimal("2250.00")
    assert status["progress"] == Decimal("25.00")
    assert status["goal"].status == "active"


async def test_estado_sin_contribuciones(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("3000"))

    status = await services["savings_goals"].get_goal_status(1, goal.id)

    assert status["contributed"] == Decimal("0.00")
    assert status["remaining"] == Decimal("3000.00")
    assert status["progress"] == Decimal("0.00")


async def test_objetivo_completado(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("1000"), contributed_at=_DT
    )

    status = await services["savings_goals"].get_goal_status(1, goal.id)

    assert status["contributed"] == Decimal("1000.00")
    assert status["remaining"] == Decimal("0.00")
    assert status["progress"] == Decimal("100.00")
    assert status["status"] == "completed"
    # get_goal_status es de solo lectura (Fase 3.2/3.3.1): no persiste nada.
    assert status["goal"].status == "active"


async def test_objetivo_con_exceso_no_rompe(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    contrib = await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("1200"), contributed_at=_DT
    )

    assert contrib.amount == Decimal("1200.00")

    status = await services["savings_goals"].get_goal_status(1, goal.id)

    assert status["contributed"] == Decimal("1200.00")
    assert status["remaining"] == Decimal("0.00")
    assert status["progress"] == Decimal("120.00")
    assert status["status"] == "completed"
    assert status["goal"].status == "active"


# --------------------------------------------------------------------------- #
# Transferencia vinculada                                                       #
# --------------------------------------------------------------------------- #
async def test_contribucion_vinculada_a_transferencia(services) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    savings = await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
    )

    contrib = await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
    )

    assert contrib.transfer_id == transfer.id


async def test_contribucion_importe_no_coincide_con_transferencia_error(services) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    savings = await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
    )

    with pytest.raises(ValueError, match="no coincide"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("250"), contributed_at=_DT, transfer_id=transfer.id
        )


async def test_contribucion_vincular_transferencia_ajena_error(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    await services["accounts"].create(user_id=2, name="Banco", type="bank", initial_balance=Decimal("0"))
    await services["accounts"].create(user_id=2, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    transfer_ajena = await services["transfers"].create(
        user_id=2,
        from_account_id=1,
        to_account_id=2,
        amount=Decimal("300"),
        occurred_at=_DT,
    )

    with pytest.raises(Exception, match="no existe"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer_ajena.id
        )


async def test_contribucion_transferencia_destino_no_ahorro_error(services) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    cash = await services["accounts"].create(user_id=1, name="Efectivo", type="cash", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=cash.id,
        amount=Decimal("300"),
        occurred_at=_DT,
    )

    with pytest.raises(ValueError, match="tipo ahorro"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
        )


async def test_contribucion_transferencia_no_publicada_error(services, transfer_repo) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    savings = await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await transfer_repo.create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
        status="cancelled",
    )

    with pytest.raises(ValueError, match="state 'posted'|estado 'posted'"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
        )


async def test_contribucion_transferencia_misma_cuenta_error(services, transfer_repo) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await transfer_repo.create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=bank.id,
        amount=Decimal("300"),
        occurred_at=_DT,
        status="posted",
    )

    with pytest.raises(ValueError, match="cuentas distintas"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
        )


async def test_contribucion_transferencia_ya_vinculada_error(services) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    savings = await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
    )
    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
    )

    with pytest.raises(ValueError, match="ya está vinculada"):
        await services["savings_goals"].record_contribution(
            user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
        )


async def test_contribucion_no_es_gasto_ni_cambia_saldos(services) -> None:
    bank = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    savings = await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    transfer = await services["transfers"].create(
        user_id=1,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=Decimal("300"),
        occurred_at=_DT,
    )
    await services["transactions"].create(
        user_id=1, type="expense", amount=Decimal("100"), occurred_at=_DT, account_id=bank.id, merchant="restaurante"
    )

    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("300"), contributed_at=_DT, transfer_id=transfer.id
    )

    overview = await services["finance"].balance_overview(1)
    balances = {a["name"]: a["balance"] for a in overview["accounts"]}
    assert balances["Banco"] == Decimal("600.00")  # 1000 - 100 gasto - 300 traspaso
    assert balances["Ahorro"] == Decimal("300.00")
    assert overview["patrimonio"] == Decimal("900.00")
    summary = await services["finance"].spending_summary(
        1, start_utc=datetime(2027, 1, 1, tzinfo=UTC), end_utc=datetime(2027, 2, 1, tzinfo=UTC)
    )
    # La contribución NO aparece como gasto; solo el gasto real de 100.
    assert summary["total_gastos"] == Decimal("100.00")


# --------------------------------------------------------------------------- #
# Cancelar y listar                                                            #
# --------------------------------------------------------------------------- #
async def test_cancelar_objetivo_no_borra_nada(services) -> None:
    goal = await services["savings_goals"].create_goal(user_id=1, name="coche", target_amount=Decimal("1000"))
    await services["savings_goals"].record_contribution(
        user_id=1, goal_id=goal.id, amount=Decimal("100"), contributed_at=_DT
    )

    cancelled = await services["savings_goals"].cancel_goal(1, goal.id)

    assert cancelled.status == "cancelled"
    # El objetivo sigue consultable y sus contribuciones persisten.
    status = await services["savings_goals"].get_goal_status(1, goal.id)
    assert status["contributed"] == Decimal("100.00")
    assert len(await services["savings_goals"].list_contributions(1, goal.id)) == 1


async def test_cancelar_objetivo_ajeno_error(services) -> None:
    await services["savings_goals"].create_goal(user_id=2, name="A", target_amount=Decimal("1000"))

    with pytest.raises(NotFoundError):
        await services["savings_goals"].cancel_goal(1, 1)


async def test_listar_objetivos_activos_por_defecto(services) -> None:
    g1 = await services["savings_goals"].create_goal(user_id=1, name="A", target_amount=Decimal("100"))
    g2 = await services["savings_goals"].create_goal(user_id=1, name="B", target_amount=Decimal("200"))
    await services["savings_goals"].cancel_goal(1, g2.id)

    goals = await services["savings_goals"].list_goals(1)
    assert [g.id for g in goals] == [g1.id]

    cancelled = await services["savings_goals"].list_goals(1, status="cancelled")
    assert [g.id for g in cancelled] == [g2.id]
