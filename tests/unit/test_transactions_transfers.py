"""Tests de TransactionService y TransferService (Fase 3).

Cubre: asignación automática de cuenta, validaciones, categorías por palabras
clave, aislamiento por usuario y las diferencias semánticas de traspasos (no
gastos/ingresos).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from asistente.domain.accounts import AccountService
from asistente.domain.transactions import TransactionService
from asistente.domain.transfers import TransferService
from asistente.shared.errors import AuthorizationError, NotFoundError
from asistente.shared.timeutil import to_utc

TZ_MAD = ZoneInfo("Europe/Madrid")


@pytest.fixture
def account_service(account_repo, transaction_repo, transfer_repo) -> AccountService:
    return AccountService(account_repo, transaction_repo, transfer_repo)


@pytest.fixture
def tx_svc(transaction_repo, account_repo) -> TransactionService:
    return TransactionService(transaction_repo, account_repo)


@pytest.fixture
def tr_svc(transfer_repo, account_repo) -> TransferService:
    return TransferService(transfer_repo, account_repo)


async def _make_accounts(account_service: AccountService, user_id: int = 1) -> dict[str, int]:
    bank = await account_service.create(user_id=user_id, name="Banco", type="bank")
    savings = await account_service.create(user_id=user_id, name="Ahorro", type="savings")
    return {"bank": bank.id, "savings": savings.id}


# --------------------------------------------------------------------------- #
# Transactions                                                                #
# --------------------------------------------------------------------------- #
async def test_expense_sin_cuenta_usa_la_default(tx_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    bank_id = ids["bank"]

    txn = await tx_svc.create(user_id=1, type="expense", amount=Decimal("28.80"), merchant="Mercadona")

    assert txn.account_id == bank_id
    assert txn.type == "expense"
    assert txn.amount == Decimal("28.80")


async def test_expense_categoria_automatica_por_merchant(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    txn = await tx_svc.create(user_id=1, type="expense", amount=Decimal("40"), merchant="gasolina Repsol")

    assert txn.category == "Transporte"


async def test_expense_categoria_explicita_gana_a_keywords(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    txn = await tx_svc.create(user_id=1, type="expense", amount=Decimal("40"), merchant="gasolina", category="Ocio")

    assert txn.category == "Ocio"


async def test_amazon_prime_es_suscripcion_y_amazon_compras(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    prime = await tx_svc.create(user_id=1, type="expense", amount=Decimal("4.99"), merchant="Amazon Prime")
    compra = await tx_svc.create(user_id=1, type="expense", amount=Decimal("25"), merchant="Amazon")

    assert prime.category == "Suscripciones"
    assert compra.category == "Compras"


async def test_expense_con_cuenta_explicita(tx_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    txn = await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), account_id=ids["savings"])

    assert txn.account_id == ids["savings"]


async def test_income_cantidad_positiva(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    txn = await tx_svc.create(user_id=1, type="income", amount=Decimal("1500"), merchant="Nómina")

    assert txn.type == "income"
    assert txn.amount == Decimal("1500")
    assert txn.merchant == "Nómina"


async def test_tipo_invalido_rechazado(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    with pytest.raises(ValueError, match="Tipo de movimiento"):
        await tx_svc.create(user_id=1, type="refund", amount=Decimal("5"))


async def test_importe_no_positivo_rechazado(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    with pytest.raises(ValueError, match="mayor que cero"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("0"))


async def test_sin_cuentas_activas_error(tx_svc) -> None:
    with pytest.raises(AuthorizationError, match="cuenta activa"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("5"))


async def test_varias_activas_sin_default_pregunta(tx_svc, account_repo) -> None:
    await account_repo.create(user_id=1, name="Banco", type="bank", is_default=False)
    await account_repo.create(user_id=1, name="Ahorro", type="savings", is_default=False)

    with pytest.raises(AuthorizationError, match="ninguna está marcada como principal"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("5"))


async def test_cuenta_desactivada_no_recibe_movimientos(tx_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    # Desactivamos la cuenta de ahorro; la cuenta bancaria sigue siendo la default.
    await tx_svc._accounts.update(1, ids["savings"], active=False)

    # Caso 1: sin indicar cuenta, la default (banco) se usa.
    ok = await tx_svc.create(user_id=1, type="expense", amount=Decimal("3"))
    assert ok.account_id == ids["bank"]

    # Caso 2: indicando la cuenta desactivada → error.
    with pytest.raises(AuthorizationError, match="desactivada"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("5"), account_id=ids["savings"])


async def test_cuenta_ajena_rechazada(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)
    other = await account_repo.create(user_id=2, name="Su Banco", type="bank")

    with pytest.raises(NotFoundError):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("5"), account_id=other.id)


async def test_list_con_filtros_y_aislamiento(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)
    await account_service.create(user_id=2, name="Su Banco", type="bank")
    await tx_svc.create(user_id=1, type="expense", amount=Decimal("20"), merchant="Zara")
    await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), merchant="Mercadona")
    await tx_svc.create(user_id=1, type="income", amount=Decimal("100"), merchant="Nómina")
    await tx_svc.create(user_id=2, type="expense", amount=Decimal("999"), merchant="Mercadona")

    solo_mercadona = await tx_svc.list(1, merchant="merca")
    assert len(solo_mercadona) == 1
    assert solo_mercadona[0].amount == Decimal("10")

    solo_gastos = await tx_svc.list(1, transaction_type="expense")
    assert len(solo_gastos) == 2

    solo_otros = await tx_svc.list(2)
    assert len(solo_otros) == 1


async def test_list_con_rango_de_fechas(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)
    now = datetime(2026, 10, 5, 12, 0, tzinfo=TZ_MAD)
    await tx_svc.create(user_id=1, type="expense", amount=Decimal("1"), occurred_at=to_utc(now))
    await tx_svc.create(user_id=1, type="expense", amount=Decimal("2"), occurred_at=to_utc(now + timedelta(days=3)))

    rows = await tx_svc.list(
        1,
        start_utc=to_utc(now - timedelta(hours=1)),
        end_utc=to_utc(now + timedelta(days=2)),
    )
    assert len(rows) == 1
    assert rows[0].amount == Decimal("1")


# --------------------------------------------------------------------------- #
# Transfers                                                                   #
# --------------------------------------------------------------------------- #
async def test_transferencia_normal(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    transfer = await tr_svc.create(
        user_id=1, from_account_id=ids["bank"], to_account_id=ids["savings"], amount=Decimal("300")
    )

    assert transfer.from_account_id == ids["bank"]
    assert transfer.to_account_id == ids["savings"]
    assert transfer.amount == Decimal("300")


async def test_transferencia_misma_cuenta_rechazada(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    with pytest.raises(ValueError, match="distintas"):
        await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=ids["bank"], amount=Decimal("10"))


async def test_transferencia_cuenta_inactiva_rechazada(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    await tr_svc._accounts.update(1, ids["savings"], active=False)

    with pytest.raises(AuthorizationError, match="desactivada"):
        await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=ids["savings"], amount=Decimal("10"))


async def test_transferencia_cuenta_ajena_rechazada(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    other = await account_repo.create(user_id=2, name="Su Banco", type="bank")

    with pytest.raises(NotFoundError):
        await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=other.id, amount=Decimal("10"))


async def test_traspasos_aislados_por_usuario(tr_svc, account_repo, account_service) -> None:
    ids_a = await _make_accounts(account_service, user_id=1)
    ids_b = await _make_accounts(account_service, user_id=2)
    await tr_svc.create(user_id=1, from_account_id=ids_a["bank"], to_account_id=ids_a["savings"], amount=Decimal("100"))

    lista_a = await tr_svc.list(1)
    lista_b = await tr_svc.list(2)

    assert len(lista_a) == 1
    assert lista_b == []
    assert await tr_svc.list(2, account_id=ids_b["bank"]) == []


async def test_transferencia_importe_cero_rechazado(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    with pytest.raises(ValueError, match="mayor que cero"):
        await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=ids["savings"], amount=Decimal("0"))


async def test_transferencia_no_es_gasto_ni_ingreso(tr_svc, tx_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=ids["savings"], amount=Decimal("300"))

    movimientos = await tx_svc.list(1)
    traspasos = await tr_svc.list(1)

    assert movimientos == []
    assert len(traspasos) == 1


async def test_historial_traspasos_con_filtros(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    await tr_svc.create(user_id=1, from_account_id=ids["bank"], to_account_id=ids["savings"], amount=Decimal("100"))
    await tr_svc.create(user_id=1, from_account_id=ids["savings"], to_account_id=ids["bank"], amount=Decimal("20"))

    por_cuenta = await tr_svc.list(1, account_id=ids["bank"])
    assert len(por_cuenta) == 2

    por_origen = await tr_svc.list(1, from_account_id=ids["bank"])
    assert len(por_origen) == 1
    assert por_origen[0].amount == Decimal("100")


# --------------------------------------------------------------------------- #
# Coherencia de moneda (Fase 3.1.1: solo EUR)                                  #
# --------------------------------------------------------------------------- #
async def test_gasto_eur_ok(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    txn = await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), currency="EUR")

    assert txn.currency == "EUR"


async def test_gasto_usd_rechazado(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    with pytest.raises(ValueError, match="EUR"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), currency="USD")


async def test_ingreso_usd_rechazado(tx_svc, account_repo, account_service) -> None:
    await _make_accounts(account_service)

    with pytest.raises(ValueError, match="EUR"):
        await tx_svc.create(user_id=1, type="income", amount=Decimal("100"), currency="USD")


async def test_gasto_en_cuenta_usd_rechazado(tx_svc, account_repo, account_service) -> None:
    """La cuenta es de usuario, activa, pero en USD → incoherencia de moneda."""
    usd_account = await account_repo.create(user_id=1, name="Cuenta USD", type="bank", currency="USD")

    with pytest.raises(ValueError, match="EUR|USD"):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), account_id=usd_account.id)


async def test_transferencia_eur_ok(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    transfer = await tr_svc.create(
        user_id=1,
        from_account_id=ids["bank"],
        to_account_id=ids["savings"],
        amount=Decimal("300"),
        currency="EUR",
    )

    assert transfer.currency == "EUR"


async def test_transferencia_usd_rechazada(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)

    with pytest.raises(ValueError, match="EUR"):
        await tr_svc.create(
            user_id=1,
            from_account_id=ids["bank"],
            to_account_id=ids["savings"],
            amount=Decimal("300"),
            currency="USD",
        )


async def test_transferencia_con_cuenta_usd_rechazada(tr_svc, account_repo, account_service) -> None:
    ids = await _make_accounts(account_service)
    usd_account = await account_repo.create(user_id=1, name="Cuenta USD", type="savings", currency="USD")

    with pytest.raises(ValueError, match="EUR|USD"):
        await tr_svc.create(
            user_id=1,
            from_account_id=ids["bank"],
            to_account_id=usd_account.id,
            amount=Decimal("300"),
        )


async def test_cuenta_usd_de_otro_usuario_sigue_siendo_aislada(tx_svc, account_repo, account_service) -> None:
    """El aislamiento manda: la cuenta ajena no existe, aunque sea USD."""
    await _make_accounts(account_service)
    other_usd = await account_repo.create(user_id=2, name="Banco USD ajeno", type="bank", currency="USD")

    with pytest.raises(NotFoundError):
        await tx_svc.create(user_id=1, type="expense", amount=Decimal("10"), account_id=other_usd.id)
