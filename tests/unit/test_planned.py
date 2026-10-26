"""Tests del servicio de pagos planificados (Fase 3.3).

Cubren que un planificado es solo un PENDIENTE: NO toca saldos ni se registra
como transacción hasta convertirse; la conversión es atómica y anti-duplicación;
la cancelación solo aplica a planificados pendientes; y aislamiento.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from asistente.persistence.models import Base, Transaction
from asistente.persistence.repositories.planned import PlannedTransactionRepository
from asistente.shared.errors import AlreadyConvertedError, AuthorizationError, NotFoundError

_DT = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


async def _make_account(services, user_id: int = 1, name: str = "Banco") -> int:
    account = await services["accounts"].create(
        user_id=user_id, name=name, type="bank", initial_balance=Decimal("1000")
    )
    return account.id


async def _make_planned(services, user_id: int = 1, **overrides):
    if "account_id" not in overrides:
        accounts = await services["accounts"].list_for_user(user_id)
        if not accounts:
            await _make_account(services, user_id=user_id)
            accounts = await services["accounts"].list_for_user(user_id)
        overrides["account_id"] = accounts[0].id
    params = dict(
        user_id=user_id,
        type="expense",
        amount=Decimal("850.00"),
        title="Alquiler",
        merchant="Inmo",
        planned_at=_DT,
    )
    params.update(overrides)
    return await services["planned"].create(**params)


# --------------------------------------------------------------------------- #
# Crear                                                                        #
# --------------------------------------------------------------------------- #
async def test_crear_planificado(services) -> None:
    planned = await _make_planned(services)

    assert planned.id == 1
    assert planned.type == "expense"
    assert planned.amount == Decimal("850.00")
    assert planned.status == "planned"
    assert planned.planned_at == _DT
    assert planned.recurring_transaction_id is None
    assert planned.converted_transaction_id is None


async def test_crear_planificado_ingreso_con_moneda_normalizada(services) -> None:
    await _make_account(services)
    planned = await services["planned"].create(
        user_id=1,
        type="income",
        amount=Decimal("1500"),
        currency="eur",
        title="Nómina",
        planned_at=_DT,
    )

    assert planned.currency == "EUR"
    assert planned.type == "income"


async def test_crear_planificado_tipo_invalido_error(services) -> None:
    with pytest.raises(ValueError, match="Tipo no válido"):
        await _make_planned(services, type="transfer")


async def test_crear_planificado_importe_cero_error(services) -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        await _make_planned(services, amount=Decimal("0"))


async def test_crear_planificado_fecha_naive_error(services) -> None:
    with pytest.raises(ValueError, match="zona horaria"):
        await _make_planned(services, planned_at=datetime(2026, 10, 1, 12, 0))


async def test_crear_planificado_titulo_vacio_error(services) -> None:
    await _make_account(services)
    with pytest.raises(ValueError, match="título"):
        await _make_planned(services, title="  ")


async def test_crear_planificado_sin_cuentas_error(services) -> None:
    with pytest.raises(AuthorizationError, match="ninguna cuenta"):
        await services["planned"].create(
            user_id=1,
            type="expense",
            amount=Decimal("850"),
            title="Alquiler",
            planned_at=_DT,
        )


async def test_crear_planificado_cuenta_desactivada_error(services) -> None:
    account = await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services["accounts"].deactivate(1, account.id)

    with pytest.raises(AuthorizationError, match="desactivada"):
        await _make_planned(services, account_id=account.id)


async def test_crear_planificado_cuenta_ajena_error(services) -> None:
    await services["accounts"].create(user_id=2, name="Banco", type="bank", initial_balance=Decimal("1000"))
    other = await services["accounts"].list_for_user(2)

    with pytest.raises(NotFoundError, match="no existe o no te pertenece"):
        await _make_planned(services, account_id=other[0].id)


async def test_crear_planificado_duplicado_mismo_momento_error(services) -> None:
    await _make_account(services)
    await _make_planned(services)

    with pytest.raises(ValueError, match="Ya existe"):
        await _make_planned(services)


async def test_crear_planificado_duplicado_mismo_momento_importe_distinto_ok(services) -> None:
    await _make_account(services)
    await _make_planned(services)

    otro = await _make_planned(services, amount=Decimal("100"))
    assert otro.id != 1


async def test_crear_planificado_duplicado_rango_abierto_guarda_por_instante(services) -> None:
    # El guard anti-duplicación filtra por start==end==planned_at: un planificado
    # con la MISMA fecha exacta duplica; uno un minuto después no.
    await _make_account(services)
    await _make_planned(services)
    await _make_planned(services, planned_at=_DT + timedelta(minutes=1))
    await _make_planned(services, planned_at=_DT + timedelta(minutes=2))

    rows = await services["planned"].list_planned(1, status="planned")
    assert len(rows) == 3


# --------------------------------------------------------------------------- #
# Convertir (atómico y anti-duplicación)                                       #
# --------------------------------------------------------------------------- #
async def test_convertir_crea_transaccion_real_y_marca_convertido(services) -> None:
    planned = await _make_planned(services)

    converted = await services["planned"].convert(1, planned.id)

    assert converted.status == "converted"
    assert converted.converted_transaction_id is not None
    txns = await services["transactions"].list(1)
    assert len(txns) == 1
    assert txns[0].type == "expense"
    assert txns[0].amount == Decimal("850.00")
    assert txns[0].account_id == planned.account_id
    assert txns[0].merchant == "Inmo"
    assert txns[0].category == planned.category


async def test_convertir_dos_veces_error(services) -> None:
    planned = await _make_planned(services)
    await services["planned"].convert(1, planned.id)

    with pytest.raises(AlreadyConvertedError, match="ya fue convertido"):
        await services["planned"].convert(1, planned.id)

    assert len(await services["transactions"].list(1)) == 1  # no se duplica


async def test_convertir_dos_veces_concurrencia_gather(services) -> None:
    """Dos `convert()` concurrentes crean UNA sola transacción.

    Con los fakes (single-thread) el segundo siempre ve el estado del primero
    porque hay un `await` entre la lectura y la escritura. El resultado es
    determinista: una excepción AlreadyConvertedError. En PostgreSQL el real
    resultado se alcanza gracias al ``FOR UPDATE`` en la misma transacción
    (Fase 3.2/3.3.1), pero aquí validamos el contrato de dominio.
    """
    planned = await _make_planned(services)
    results = await asyncio.gather(
        services["planned"].convert(1, planned.id),
        services["planned"].convert(1, planned.id),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1
    assert isinstance(errors[0], AlreadyConvertedError)
    assert len(await services["transactions"].list(1)) == 1
    with pytest.raises(NotFoundError, match="no existe"):
        await services["planned"].convert(1, 999)


async def test_convertir_con_cuenta_desactivada_error(services) -> None:
    planned = await _make_planned(services)
    await services["accounts"].deactivate(1, planned.account_id)

    with pytest.raises(AuthorizationError, match="desactivada"):
        await services["planned"].convert(1, planned.id)

    assert len(await services["transactions"].list(1)) == 0


async def test_convertir_con_occurred_at_merchant_y_note(services) -> None:
    planned = await _make_planned(services)
    occurred = _DT + timedelta(days=3)

    converted = await services["planned"].convert(
        1, planned.id, occurred_at=occurred, merchant="Inmo SA", note="Recibo"
    )

    assert converted.status == "converted"
    txns = await services["transactions"].list(1)
    assert len(txns) == 1
    assert txns[0].occurred_at == occurred
    assert txns[0].merchant == "Inmo SA"
    assert txns[0].note == "Recibo"


async def test_convertir_planificado_manual_no_vincula_recurrencia(services) -> None:
    planned = await _make_planned(services)

    converted = await services["planned"].convert(1, planned.id)

    assert converted.recurring_transaction_id is None


# --------------------------------------------------------------------------- #
# Cancelar                                                                     #
# --------------------------------------------------------------------------- #
async def test_cancelar_planificado_pendiente(services) -> None:
    planned = await _make_planned(services)

    cancelled = await services["planned"].cancel(1, planned.id)

    assert cancelled.status == "cancelled"
    assert len(await services["transactions"].list(1)) == 0


async def test_cancelar_planificado_ya_convertido_error(services) -> None:
    planned = await _make_planned(services)
    await services["planned"].convert(1, planned.id)

    with pytest.raises(ValueError, match="ya no está pendiente"):
        await services["planned"].cancel(1, planned.id)


async def test_cancelar_planificado_inexistente_error(services) -> None:
    with pytest.raises(NotFoundError, match="no existe"):
        await services["planned"].cancel(1, 999)


# --------------------------------------------------------------------------- #
# Listar / upcoming                                                           #
# --------------------------------------------------------------------------- #
async def test_listar_planificados_con_filtros(services) -> None:
    await _make_account(services)
    exp1 = await services["planned"].create(user_id=1, type="expense", amount=Decimal("10"), planned_at=_DT)
    exp2 = await services["planned"].create(
        user_id=1, type="expense", amount=Decimal("20"), planned_at=_DT + timedelta(days=2)
    )
    inc = await services["planned"].create(
        user_id=1, type="income", amount=Decimal("30"), planned_at=_DT + timedelta(days=4)
    )
    await services["planned"].cancel(1, exp2.id)

    todos = await services["planned"].list_planned(1)
    gastos = await services["planned"].list_planned(1, transaction_type="expense", status="planned")
    ingresos = await services["planned"].list_planned(1, transaction_type="income")
    cancelados = await services["planned"].list_planned(1, status="cancelled")

    assert {p.id for p in todos} == {exp1.id, exp2.id, inc.id}
    assert {p.id for p in gastos} == {exp1.id}
    assert [p.id for p in ingresos] == [inc.id]
    assert [p.id for p in cancelados] == [exp2.id]


async def test_listar_planificados_por_rango_de_fechas(services) -> None:
    await _make_account(services)
    await services["planned"].create(user_id=1, type="expense", amount=Decimal("10"), planned_at=_DT)
    await services["planned"].create(
        user_id=1, type="expense", amount=Decimal("20"), planned_at=_DT + timedelta(days=10)
    )

    in_range = await services["planned"].list_planned(1, start_utc=_DT, end_utc=_DT + timedelta(days=1))

    assert len(in_range) == 1


async def test_upcoming_filtra_por_ventana_y_tipo(services) -> None:
    await _make_account(services)
    near = await services["planned"].create(user_id=1, type="expense", amount=Decimal("10"), planned_at=_DT)
    far = await services["planned"].create(
        user_id=1, type="expense", amount=Decimal("20"), planned_at=_DT + timedelta(days=400)
    )
    income = await services["planned"].create(user_id=1, type="income", amount=Decimal("30"), planned_at=_DT)

    gastos = await services["planned"].upcoming(1, days=30, transaction_type="expense", now=_DT)
    ingresos = await services["planned"].upcoming(1, days=30, transaction_type="income", now=_DT)

    assert {p.id for p in gastos} == {near.id}
    assert far.id not in {p.id for p in gastos}
    assert {p.id for p in ingresos} == {income.id}


# --------------------------------------------------------------------------- #
# Semántica: un planificado NO es una transacción                              #
# --------------------------------------------------------------------------- #
async def test_crear_planificado_no_genera_transaccion_ni_toca_saldos(services) -> None:
    await _make_account(services)

    await _make_planned(services)
    await services["planned"].create(
        user_id=1, type="income", amount=Decimal("500"), planned_at=_DT + timedelta(days=5)
    )

    assert len(await services["transactions"].list(1)) == 0
    balance = await services["finance"].balance_overview(1)
    assert Decimal(balance["patrimonio"]) == Decimal("1000.00")


async def test_convertir_si_afecta_a_las_transacciones_y_al_patrimonio(services) -> None:
    await _make_account(services)
    planned = await _make_planned(services)

    await services["planned"].convert(1, planned.id)

    assert len(await services["transactions"].list(1)) == 1
    balance = await services["finance"].balance_overview(1)
    assert Decimal(balance["patrimonio"]) == Decimal("150.00")  # 1000 - 850


# --------------------------------------------------------------------------- #
# Aislamiento                                                                  #
# --------------------------------------------------------------------------- #
async def test_planificados_aislados_entre_usuarios(services) -> None:
    await _make_account(services, user_id=1)
    account_b = await _make_account(services, user_id=2, name="Banco B")
    p1 = await _make_planned(services, user_id=1)
    p2 = await _make_planned(services, user_id=2, account_id=account_b)

    assert p1.user_id == 1
    assert p2.user_id == 2
    assert len(await services["planned"].list_planned(1)) == 1

    with pytest.raises(NotFoundError, match="no existe o no te pertenece"):
        await services["planned"].convert(1, p2.id)

    await services["planned"].convert(2, p2.id)
    assert len(await services["transactions"].list(1)) == 0
    assert len(await services["transactions"].list(2)) == 1


# --------------------------------------------------------------------------- #
# Rollback atómico con BD real (Fase 3.3.2)                                    #
# --------------------------------------------------------------------------- #
@pytest.fixture
async def _planned_db():
    """BD SQLite en memoria con SOLO las tablas de planificados y transacciones."""
    from asistente.persistence.db import Database

    database = Database(url="sqlite+aiosqlite:///:memory:")
    database.connect()
    async with database.engine.begin() as conn:
        needed = [Base.metadata.tables[name] for name in ["planned_transactions", "transactions"]]
        await conn.run_sync(Base.metadata.create_all, tables=needed)
    yield database
    await database.close()


async def test_convert_rollback_atomico_bd_real(_planned_db) -> None:
    """ROLLBACK total de `PlannedTransactionRepository.convert()`.

    Fuerza una excepción DESPUÉS de haber creado la Transaction (flush) pero
    ANTES de completar la transacción exterior (el primer/único commit, que es
    el de ``Database.transaction()``). El resultado debe ser rollback de TODO:

    - no queda ninguna Transaction creada;
    - el PlannedTransaction sigue en `planned`;
    - `converted_transaction_id` sigue siendo `NULL`.

    El listener de SQLAlchemy ``before_commit`` se usa SOLO para simular el
    fallo justo en el commit exterior: el único ``commit()`` del flujo correto.
    Si `convert` hiciera un ``commit()`` manual interno, este test no pasaría.
    """
    database = _planned_db
    repo = PlannedTransactionRepository(database)
    planned = await repo.create(user_id=1, type="expense", title="Alquiler", amount=Decimal("800.00"), planned_at=_DT)

    def _forzar_error(session) -> None:
        raise RuntimeError("fallo forzado antes del commit exterior")

    event.listen(Session, "before_commit", _forzar_error, once=True)
    try:
        with pytest.raises(RuntimeError, match="fallo forzado"):
            await repo.convert(planned, occurred_at=_DT)
    finally:
        # Seguridad: si no llegó a dispararse, se limpia igualmente.
        event.remove(Session, "before_commit", _forzar_error)

    refetched = await repo.find_one(1, planned.id)
    assert refetched is not None
    assert refetched.status == "planned"
    assert refetched.converted_transaction_id is None

    async with database.session() as session:
        total = (await session.execute(select(func.count()).select_from(Transaction))).scalar_one()
    assert total == 0
