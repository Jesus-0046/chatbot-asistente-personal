"""Tests del servicio de suscripciones (Fase 3.3).

Una suscripción SIEMPRE se apoya en una recurrencia: crearla crea la recurrencia
subyacente; `next_billing_date` es un espejo de `next_due_at`; cancelarla
desactiva la recurrencia y los planificados futuros, conservando el histórico.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from asistente.shared.errors import AuthorizationError, NotFoundError

_DT = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


async def _make_account(services, user_id: int = 1, name: str = "Banco") -> int:
    account = await services["accounts"].create(
        user_id=user_id, name=name, type="bank", initial_balance=Decimal("1000")
    )
    return account.id


async def _make_sub(services, user_id: int = 1, **overrides):
    if "account_id" not in overrides:
        overrides["account_id"] = await _make_account(services, user_id=user_id)
    params = dict(
        user_id=user_id,
        name="Netflix",
        amount=Decimal("15.99"),
        billing_period="monthly",
        next_billing_date=_DT,
    )
    params.update(overrides)
    return await services["subscriptions"].create(**params)


# --------------------------------------------------------------------------- #
# Crear                                                                        #
# --------------------------------------------------------------------------- #
async def test_crear_suscripcion_mensual_crea_recurrencia_espejo(services) -> None:
    sub = await _make_sub(services)

    assert sub.id == 1
    assert sub.name == "Netflix"
    assert sub.amount == Decimal("15.99")
    assert sub.currency == "EUR"
    assert sub.billing_period == "monthly"
    assert sub.category == "Suscripciones"
    assert sub.active
    assert sub.recurring_transaction_id is not None

    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.title == "Netflix"
    assert rec.merchant == "Netflix"
    assert rec.type == "expense"
    assert rec.frequency == "monthly"
    assert rec.day_of_month == 5
    assert rec.next_due_at == _DT
    assert sub.next_billing_date == rec.next_due_at


async def test_crear_suscripcion_anual_usa_mes_ancla(services) -> None:
    sub = await _make_sub(services, billing_period="yearly")

    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.frequency == "yearly"
    assert rec.month_of_year == 10
    assert rec.day_of_month == 5


async def test_crear_suscripcion_mensual_sin_fecha_usa_hoy_como_ancla(services) -> None:
    sub = await _make_sub(services, next_billing_date=None)

    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.day_of_month is not None
    assert rec.next_due_at is not None


async def test_crear_suscripcion_nombre_vacio_error(services) -> None:
    with pytest.raises(ValueError, match="nombre"):
        await _make_sub(services, name="   ")


async def test_crear_suscripcion_periodo_invalido_error(services) -> None:
    with pytest.raises(ValueError, match="Periodo"):
        await _make_sub(services, billing_period="quarterly")


async def test_crear_suscripcion_fecha_naive_error(services) -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        await _make_sub(services, next_billing_date=datetime(2026, 10, 5, 12, 0))


async def test_crear_suscripcion_cuenta_ajena_error(services) -> None:
    await _make_account(services, user_id=2, name="Banco B")
    other = await services["accounts"].list_for_user(2)

    with pytest.raises(NotFoundError, match="no existe o no te pertenece"):
        await _make_sub(services, account_id=other[0].id)


async def test_crear_suscripcion_sin_cuentas_error(services) -> None:
    with pytest.raises(AuthorizationError, match="ninguna cuenta"):
        await services["subscriptions"].create(
            user_id=1,
            name="Netflix",
            amount=Decimal("15.99"),
            billing_period="monthly",
        )


# --------------------------------------------------------------------------- #
# Actualizar                                                                   #
# --------------------------------------------------------------------------- #
async def test_actualizar_nombre_sincroniza_recurrencia(services) -> None:
    sub = await _make_sub(services)

    updated = await services["subscriptions"].update(1, sub.id, name="Netflix Pro")

    assert updated.name == "Netflix Pro"
    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.title == "Netflix Pro"
    assert rec.merchant == "Netflix Pro"


async def test_actualizar_importe_sincroniza_recurrencia(services) -> None:
    sub = await _make_sub(services)

    updated = await services["subscriptions"].update(1, sub.id, amount=Decimal("19.99"))

    assert updated.amount == Decimal("19.99")
    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.amount == Decimal("19.99")


async def test_actualizar_categoria_sincroniza_recurrencia(services) -> None:
    sub = await _make_sub(services)

    updated = await services["subscriptions"].update(1, sub.id, category="Ocio")

    assert updated.category == "Ocio"
    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.category == "Ocio"


async def test_actualizar_periodo_sin_fecha_recalcula_next_due(services) -> None:
    sub = await _make_sub(services)
    original_due = sub.next_billing_date

    updated = await services["subscriptions"].update(1, sub.id, billing_period="yearly")

    assert updated.billing_period == "yearly"
    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.frequency == "yearly"
    assert rec.next_due_at != original_due
    assert updated.next_billing_date == rec.next_due_at


async def test_actualizar_next_billing_date_explicito_ajusta_anclas(services) -> None:
    sub = await _make_sub(services)
    nueva = datetime(2027, 3, 15, 12, 0, tzinfo=UTC)

    updated = await services["subscriptions"].update(1, sub.id, billing_period="yearly", next_billing_date=nueva)

    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert rec.next_due_at == nueva
    assert rec.day_of_month == 15
    assert rec.month_of_year == 3
    assert updated.next_billing_date == nueva


async def test_actualizar_suscripcion_inexistente_error(services) -> None:
    with pytest.raises(NotFoundError, match="no existe"):
        await services["subscriptions"].update(1, 999, name="Otro")


# --------------------------------------------------------------------------- #
# Cancelar                                                                     #
# --------------------------------------------------------------------------- #
async def test_cancelar_desactiva_sub_y_recurrencia_y_cancela_planificados(services) -> None:
    sub = await _make_sub(services)
    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=120)

    cancelled = await services["subscriptions"].cancel(1, sub.id)

    assert not cancelled.active
    rec = await services["recurring"].get(1, sub.recurring_transaction_id)
    assert not rec.active
    remaining = [p for p in services["planned_store"] if p.recurring_transaction_id == rec.id and p.status == "planned"]
    assert remaining == []


async def test_cancelar_conserva_historico(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    sub = await _make_sub(services, billing_period="daily", next_billing_date=due)
    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=120)
    planned = services["planned_store"][0]
    await services["planned"].convert(1, planned.id)

    await services["subscriptions"].cancel(1, sub.id)

    txns = await services["transactions"].list(1)
    assert len(txns) == 1  # la cuota ya pagada sigue en el histórico


async def test_cancelar_suscripcion_inexistente_error(services) -> None:
    with pytest.raises(NotFoundError, match="no existe"):
        await services["subscriptions"].cancel(1, 999)


# --------------------------------------------------------------------------- #
# Listar (espejo y generación perezosa)                                        #
# --------------------------------------------------------------------------- #
async def test_listar_sincroniza_next_billing_date_con_recurrencia(services) -> None:
    sub = await _make_sub(services)
    # Desincronizar a propósito desde la recurrencia (simula una serie avanzada).
    await services["recurring"].update(1, sub.recurring_transaction_id, next_due_at=_DT + timedelta(days=30))

    subs = await services["subscriptions"].list(1)

    assert len(subs) == 1
    assert subs[0].next_billing_date == _DT + timedelta(days=30)


async def test_listar_genera_planificados_y_respeta_active_only(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    sub = await _make_sub(services, billing_period="daily", next_billing_date=due)
    # Generación perezosa: listar materializa las cuotas pendientes.
    await services["subscriptions"].list(1)

    assert len(services["planned_store"]) >= 1

    solo_activas = await services["subscriptions"].list(1, active_only=True)
    inactivas = await services["subscriptions"].list(1, active_only=False)
    assert [s.id for s in solo_activas] == [sub.id]
    assert inactivas == []


async def test_listar_aislamiento(services) -> None:
    await _make_sub(services, user_id=1)
    await _make_sub(services, user_id=2)

    assert [s.user_id for s in await services["subscriptions"].list(1)] == [1]
    assert [s.user_id for s in await services["subscriptions"].list(2)] == [2]
