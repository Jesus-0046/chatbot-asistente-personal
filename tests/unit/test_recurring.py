"""Tests del servicio y motor de fechas de recurrencias financieras (Fase 3.3).

Cubren el motor de fechas ancla-preservante (fin de mes, bisiestos, cadencias
mensuales/anuales), validaciones de creación, pausa/reanudación, cancelación
(conservando histórico y cancelando planificados futuros), generación perezosa
e idempotente de planificados y aislamiento entre usuarios.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from asistente.domain.recurring import (
    first_occurrence_after,
    month_step_anchored,
    next_occurrence_dt,
)
from asistente.shared.errors import AuthorizationError, NotFoundError

_DT = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def _make_recurring(services, user_id: int = 1, **overrides):
    """Crea la cuenta base y una recurrencia mensual determinista."""
    await services["accounts"].create(user_id=user_id, name="Banco", type="bank", initial_balance=Decimal("1000"))
    params = dict(
        user_id=user_id,
        type="expense",
        title="Alquiler",
        amount=Decimal("850.00"),
        frequency="monthly",
        interval=1,
        day_of_month=1,
        next_due_at=_DT,
        timezone="Europe/Madrid",
    )
    params.update(overrides)
    return await services["recurring"].create(**params)


# --------------------------------------------------------------------------- #
# Motor de fechas (funciones puras)                                            #
# --------------------------------------------------------------------------- #
def test_saneo_fin_de_mes_31_ene_a_feb_y_marzo() -> None:
    assert month_step_anchored(date(2026, 1, 31), 1, anchor_day=31) == date(2026, 2, 28)
    assert month_step_anchored(date(2026, 1, 31), 2, anchor_day=31) == date(2026, 3, 31)
    assert month_step_anchored(date(2026, 1, 31), 3, anchor_day=31) == date(2026, 4, 30)
    assert month_step_anchored(date(2026, 1, 31), 4, anchor_day=31) == date(2026, 5, 31)


def test_saneo_fin_de_mes_bisiesto() -> None:
    # Febrero de 2028 SÍ es bisiesto → 29.
    assert month_step_anchored(date(2028, 1, 31), 1, anchor_day=31) == date(2028, 2, 29)
    assert month_step_anchored(date(2028, 1, 31), 12, anchor_day=31) == date(2029, 1, 31)


def test_ancla_se_aplica_siempre_desde_el_dia_ancla() -> None:
    # Aunque la ocurrencia previa se saneó al 28, el ancla sigue siendo el 31.
    feb = month_step_anchored(date(2026, 1, 31), 1, anchor_day=31)
    assert month_step_anchored(feb, 1, anchor_day=31) == date(2026, 3, 31)


def test_next_occurrence_mensual_fin_de_mes() -> None:
    nxt = next_occurrence_dt(
        datetime(2026, 1, 31, 12, 0, tzinfo=UTC),
        frequency="monthly",
        interval=1,
        day_of_month=31,
        month_of_year=None,
        start_date=date(2026, 1, 31),
        end_date=None,
        timezone="Europe/Madrid",
    )
    assert nxt is not None
    assert nxt.astimezone(UTC) == datetime(2026, 2, 28, 11, 0, tzinfo=UTC)  # 31 feb → 28 a las 12:00 CET


def test_next_occurrence_cada_dos_semanas_mismo_dia() -> None:
    base = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)  # lunes
    nxt = next_occurrence_dt(
        base,
        frequency="weekly",
        interval=2,
        day_of_month=None,
        month_of_year=None,
        start_date=None,
        end_date=None,
        timezone="Europe/Madrid",
    )
    assert nxt is not None
    assert nxt.astimezone(UTC).date() == date(2026, 9, 21)  # lunes +14


def test_next_occurrence_respeta_fecha_fin() -> None:
    nxt = next_occurrence_dt(
        datetime(2026, 12, 1, 12, 0, tzinfo=UTC),
        frequency="monthly",
        interval=1,
        day_of_month=1,
        month_of_year=None,
        start_date=None,
        end_date=date(2026, 10, 31),
        timezone="Europe/Madrid",
    )
    assert nxt is None


def test_first_occurrence_after_anual_con_mes_ancla() -> None:
    occ = first_occurrence_after(
        datetime(2026, 12, 1, 12, 0, tzinfo=UTC).astimezone(),
        frequency="yearly",
        interval=1,
        day_of_month=15,
        month_of_year=6,
        start_date=None,
        end_date=None,
        timezone="Europe/Madrid",
    )
    assert occ is not None
    assert occ.astimezone(UTC).date() == date(2027, 6, 15)


# --------------------------------------------------------------------------- #
# Crear recurrencia                                                            #
# --------------------------------------------------------------------------- #
async def test_crear_recurrencia_mensual_con_ancla(services) -> None:
    rec = await _make_recurring(services)

    assert rec.id == 1
    assert rec.title == "Alquiler"
    assert rec.amount == Decimal("850.00")
    assert rec.currency == "EUR"
    assert rec.type == "expense"
    assert rec.frequency == "monthly"
    assert rec.day_of_month == 1
    assert rec.account_id is not None
    assert rec.active
    assert not rec.paused
    assert rec.next_due_at == _DT


async def test_crear_recurrencia_sin_cuenta_directa_usa_la_principal(services) -> None:
    default = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services["accounts"].create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("0"))

    rec = await services["recurring"].create(
        user_id=1,
        type="expense",
        title="Estrella",
        amount=Decimal("10"),
        frequency="weekly",
        next_due_at=_DT,
    )

    assert rec.account_id == default.id


async def test_crear_recurrencia_sin_cuentas_error(services) -> None:
    with pytest.raises(AuthorizationError, match="ninguna cuenta"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="weekly",
            next_due_at=_DT,
        )


async def test_crear_recurrencia_cuenta_desactivada_error(services) -> None:
    account = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services["accounts"].deactivate(1, account.id)

    with pytest.raises(AuthorizationError, match="desactivada"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="weekly",
            account_id=account.id,
            next_due_at=_DT,
        )


async def test_crear_recurrencia_cuenta_ajena_error(services) -> None:
    await services["accounts"].create(user_id=2, name="Banco", type="bank", initial_balance=Decimal("1000"))
    account = await services["accounts"].list_for_user(2)
    with pytest.raises(NotFoundError, match="no existe o no te pertenece"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="weekly",
            account_id=account[0].id,
            next_due_at=_DT,
        )


async def test_crear_recurrencia_titulo_vacio_error(services) -> None:
    with pytest.raises(ValueError, match="título"):
        await _make_recurring(services, title="   ")


async def test_crear_recurrencia_tipo_invalido_error(services) -> None:
    with pytest.raises(ValueError, match="Tipo no válido"):
        await _make_recurring(services, type="transfer")


async def test_crear_recurrencia_importe_cero_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        await _make_recurring(services, amount=Decimal("0"))


async def test_crear_recurrencia_frecuencia_invalida_error(services) -> None:
    with pytest.raises(ValueError, match="Frecuencia no válida"):
        await _make_recurring(services, frequency="bimonthly")


async def test_crear_recurrencia_mensual_sin_ancla_error(services) -> None:
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    with pytest.raises(ValueError, match="día ancla|fecha de inicio"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="monthly",
            next_due_at=_DT,
        )


async def test_crear_recurrencia_anual_sin_mes_error(services) -> None:
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    with pytest.raises(ValueError, match="mes"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="yearly",
            day_of_month=15,
            next_due_at=_DT,
        )


async def test_crear_recurrencia_dia_fuera_de_rango_error(services) -> None:
    with pytest.raises(ValueError, match="entre 1 y 31"):
        await _make_recurring(services, day_of_month=32)


async def test_crear_recurrencia_fecha_fin_anterior_a_primera_ocurrencia_error(services) -> None:
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    with pytest.raises(ValueError, match="fecha fin"):
        await services["recurring"].create(
            user_id=1,
            type="expense",
            title="X",
            amount=Decimal("10"),
            frequency="monthly",
            day_of_month=1,
            next_due_at=_DT,
            end_date=date(2026, 8, 1),
        )


async def test_crear_recurrencia_aislamiento(services) -> None:
    await _make_recurring(services, user_id=1)
    await _make_recurring(services, user_id=2)

    assert [r.user_id for r in await services["recurring"].list(1)] == [1]
    assert [r.user_id for r in await services["recurring"].list(2)] == [2]


# --------------------------------------------------------------------------- #
# Actualizar                                                                   #
# --------------------------------------------------------------------------- #
async def test_actualizar_importe_y_titulo(services) -> None:
    rec = await _make_recurring(services)

    updated = await services["recurring"].update(
        1, rec.id, amount=Decimal("900.00"), title="Alquiler nuevo", merchant="Inmo"
    )

    assert updated.amount == Decimal("900.00")
    assert updated.title == "Alquiler nuevo"
    assert updated.merchant == "Inmo"


async def test_actualizar_importe_no_positivo_error(services) -> None:
    rec = await _make_recurring(services)
    with pytest.raises(ValueError, match="mayor que cero"):
        await services["recurring"].update(1, rec.id, amount=Decimal("-5"))


async def test_actualizar_no_existente_error(services) -> None:
    with pytest.raises(NotFoundError, match="no existe"):
        await services["recurring"].update(1, 999, title="Nada")


async def test_actualizar_frecuencia_mensual_revalida_ancla(services) -> None:
    rec = await _make_recurring(services, frequency="daily")

    with pytest.raises(ValueError, match="día ancla"):
        await services["recurring"].update(1, rec.id, frequency="monthly", day_of_month=None)


# --------------------------------------------------------------------------- #
# Pausar / reanudar                                                            #
# --------------------------------------------------------------------------- #
async def test_pausar_recurrencia(services) -> None:
    rec = await _make_recurring(services)

    paused = await services["recurring"].pause(1, rec.id)

    assert paused.paused


async def test_pausar_dos_veces_error(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].pause(1, rec.id)
    with pytest.raises(ValueError, match="ya está pausada"):
        await services["recurring"].pause(1, rec.id)


async def test_pausar_cancelada_error(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].cancel(1, rec.id)
    with pytest.raises(ValueError, match="cancelada"):
        await services["recurring"].pause(1, rec.id)


async def test_reanudar_recurrencia_pausada(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].pause(1, rec.id)

    resumed = await services["recurring"].resume(1, rec.id)

    assert not resumed.paused
    assert resumed.next_due_at == _DT


async def test_reanudar_sin_estar_pausada_error(services) -> None:
    rec = await _make_recurring(services)
    with pytest.raises(ValueError, match="no está pausada"):
        await services["recurring"].resume(1, rec.id)


# --------------------------------------------------------------------------- #
# Cancelar (conserva histórico, cancela planificados futuros)                  #
# --------------------------------------------------------------------------- #
async def test_cancelar_recurrencia_inactiva_y_cancela_planificados(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=120)

    cancelled_rec, cancelled_planned = await services["recurring"].cancel(1, rec.id)

    assert not cancelled_rec.active
    assert cancelled_rec.next_due_at is None
    assert cancelled_planned >= 1


async def test_cancelar_recurrencia_conserva_historico(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=120)
    planned = services["planned_store"][0]
    await services["planned"].convert(1, planned.id)

    await services["recurring"].cancel(1, rec.id)

    # La transacción convertida NO se toca.
    txns = await services["transactions"].list(1)
    assert len(txns) == 1
    assert txns[0].amount == rec.amount


async def test_cancelar_recurrencia_desactiva_suscripciones(services) -> None:
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    sub = await services["subscriptions"].create(
        user_id=1,
        name="Netflix",
        amount=Decimal("15.99"),
        billing_period="monthly",
        next_billing_date=_DT,
    )

    await services["recurring"].cancel(1, sub.recurring_transaction_id)

    subs = await services["subscription_repo"].list_for_user(1)
    assert len(subs) == 1
    assert not subs[0].active


# --------------------------------------------------------------------------- #
# Listar                                                                       #
# --------------------------------------------------------------------------- #
async def test_listar_filtros_active_y_paused(services) -> None:
    rec_a = await _make_recurring(services, title="A")
    rec_b = await _make_recurring(services, title="B")
    await services["recurring"].cancel(1, rec_b.id)

    all_rows = await services["recurring"].list(1)
    active_rows = await services["recurring"].list(1, active_only=True)

    assert {r.id for r in all_rows} == {rec_a.id, rec_b.id}
    assert [r.id for r in active_rows] == [rec_a.id]


async def test_listar_sin_pausadas(services) -> None:
    rec = await _make_recurring(services)
    await services["recurring"].pause(1, rec.id)

    con_pausadas = await services["recurring"].list(1)
    sin_pausadas = await services["recurring"].list(1, include_paused=False)

    assert len(con_pausadas) == 1
    assert sin_pausadas == []


# --------------------------------------------------------------------------- #
# Generación perezosa e idempotente                                            #
# --------------------------------------------------------------------------- #
async def test_generar_planificados_desde_vencida(services) -> None:
    due = datetime.now(UTC) - timedelta(days=20)
    await _make_recurring(services, frequency="daily", next_due_at=due)

    created = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)

    planned = services["planned_store"]
    assert created >= 20
    assert len(planned) == created
    # Ordenados por fecha y única ocurrencia por fecha/serie.
    dates = [p.planned_at for p in planned]
    assert dates == sorted(dates)
    assert len({p.planned_at for p in planned}) == len(planned)


async def test_generar_es_idempotente(services) -> None:
    due = datetime.now(UTC) - timedelta(days=20)
    await _make_recurring(services, frequency="daily", next_due_at=due)

    first = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)
    second = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)

    assert first == len(services["planned_store"])
    assert second == 0
    assert len(services["planned_store"]) == first


async def test_generar_no_crea_para_recurrencia_pausada(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    rec = await _make_recurring(services, frequency="daily", next_due_at=due)
    await services["recurring"].pause(1, rec.id)

    created = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)

    assert created == 0


async def test_generar_no_crea_para_recurrencia_cancelada(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    rec = await _make_recurring(services, frequency="daily", next_due_at=due)
    await services["recurring"].cancel(1, rec.id)

    created = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)

    assert created == 0


async def test_generar_respeta_fecha_fin(services) -> None:
    due = datetime.now(UTC) - timedelta(days=40)
    end_date = (datetime.now(UTC) + timedelta(days=3)).date()
    await _make_recurring(services, frequency="daily", next_due_at=due, end_date=end_date)

    created = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=30)

    planned = services["planned_store"]
    assert len(planned) == created
    # Ningún planificado supera la fecha fin (a las 12:00 CET).
    assert all(p.planned_at.date() <= end_date for p in planned)


async def test_generar_no_pisa_ocurrencias_existentes(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    rec = await _make_recurring(services, frequency="daily", next_due_at=due)
    # Planificado manual (sin serie) el mismo día de una ocurrencia generable.
    await services["planned"].create(
        user_id=1,
        type="expense",
        title="Manual",
        amount=Decimal("10"),
        planned_at=due + timedelta(days=2),
    )

    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=90)

    manual = [p for p in services["planned_store"] if p.recurring_transaction_id is None]
    assert len(manual) == 1
    assert manual[0].title == "Manual"
    # Las ocurrencias generadas por la serie siguen siendo únicas por fecha.
    gen = [p for p in services["planned_store"] if p.recurring_transaction_id == rec.id]
    assert len(gen) == len({p.planned_at for p in gen})
    assert gen[0].planned_at == due


async def test_generar_mensual_respeta_ancla_preservante(services) -> None:
    # Serie el día 31: 31, 28, 31, 30... (2026, no bisiesto).
    await _make_recurring(
        services,
        frequency="monthly",
        day_of_month=31,
        next_due_at=datetime(2026, 1, 31, 11, 0, tzinfo=UTC),
    )

    await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=120)

    dates = sorted(p.planned_at.date() for p in services["planned_store"])
    assert set(dates[:4]) == {date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30)}


async def test_generar_aislamiento_entre_usuarios(services) -> None:
    due = datetime.now(UTC) - timedelta(days=5)
    await _make_recurring(services, user_id=1, frequency="daily", next_due_at=due)

    created_1 = await services["recurring"].generate_upcoming_planned_transactions(1, horizon_days=10)

    assert created_1 > 0
    assert all(p.user_id == 1 for p in services["planned_store"])
