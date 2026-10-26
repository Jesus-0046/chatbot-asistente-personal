"""Tests de AccountService (Fase 3): creación, default y desactivación.

Reglas verificadas:
- La primera cuenta de un usuario pasa a ser la principal (default) sola.
- Máximo UNA cuenta default por usuario (set_default desmarca las demás).
- Desactivar la cuenta default la desmarca sin promover otra.
- Las cuentas desactivadas no pueden marcarse como principal.
- Los tipos de cuenta son exactamente bank|savings|cash|credit_card|other.
- Aislamiento por usuario: ningún método cruza a otro usuario.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.domain.accounts import ACCOUNT_TYPES, AccountService
from asistente.shared.errors import NotFoundError


@pytest.fixture
def svc(account_repo, transaction_repo, transfer_repo) -> AccountService:
    return AccountService(account_repo, transaction_repo, transfer_repo)


async def test_primera_cuenta_es_default(svc) -> None:
    account = await svc.create(user_id=1, name="Mi Cuenta", type="bank", initial_balance=Decimal("1000.00"))

    assert account.is_default is True
    assert account.active is True
    assert account.name == "Mi Cuenta"


async def test_cuenta_con_saldo_inicial_cero(svc) -> None:
    account = await svc.create(user_id=1, name="Vacaciones", type="savings", initial_balance=Decimal("0.00"))

    assert account.initial_balance == Decimal("0.00")
    assert account.is_default is True


async def test_cuenta_con_saldo_inicial_positivo(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000.00"))

    assert account.initial_balance == Decimal("1000.00")


async def test_saldo_inicial_negativo_rechazado(svc) -> None:
    with pytest.raises(ValueError, match="negativo"):
        await svc.create(user_id=1, name="Rara", type="bank", initial_balance=Decimal("-100"))


async def test_update_saldo_inicial_negativo_rechazado(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("100"))

    with pytest.raises(ValueError, match="negativo"):
        await svc.update(1, account.id, initial_balance=Decimal("-5"))


async def test_segunda_cuenta_no_es_default(svc) -> None:
    await svc.create(user_id=1, name="Mi Cuenta", type="bank")
    second = await svc.create(user_id=1, name="Ahorro", type="savings")

    assert second.is_default is False


async def test_set_default_desmarca_anteriores(svc) -> None:
    first = await svc.create(user_id=1, name="Banco", type="bank")
    second = await svc.create(user_id=1, name="Ahorro", type="savings")

    promoted = await svc.set_default(1, second.id)

    assert promoted.is_default is True
    assert (await svc.get(1, first.id)).is_default is False
    assert len([a for a in (await svc.list_for_user(1)) if a.is_default]) == 1


async def test_set_default_cuenta_inactiva_rechazada(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank")
    other = await svc.create(user_id=1, name="Ahorro", type="savings")
    await svc.deactivate(1, other.id)

    with pytest.raises(ValueError, match="desactivada"):
        await svc.set_default(1, other.id)

    assert (await svc.get(1, account.id)).is_default is True


async def test_desactivar_default_la_desmarca_sin_promover(svc) -> None:
    first = await svc.create(user_id=1, name="Banco", type="bank")
    second = await svc.create(user_id=1, name="Ahorro", type="savings")

    deactivated = await svc.deactivate(1, first.id)

    assert deactivated.active is False
    assert deactivated.is_default is False
    assert (await svc.get(1, second.id)).is_default is False


async def test_desactivar_no_default_conserva_la_principal(svc) -> None:
    first = await svc.create(user_id=1, name="Banco", type="bank")
    second = await svc.create(user_id=1, name="Ahorro", type="savings")

    await svc.deactivate(1, second.id)

    assert (await svc.get(1, first.id)).is_default is True


async def test_tipo_invalido_rechazado(svc) -> None:
    with pytest.raises(ValueError, match="Tipo de cuenta"):
        await svc.create(user_id=1, name="Rara", type="crypto")


async def test_nombre_vacio_rechazado(svc) -> None:
    with pytest.raises(ValueError, match="nombre"):
        await svc.create(user_id=1, name="   ", type="bank")


async def test_update_valida_tipo_y_cuantiza_saldo(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("10.399"))

    updated = await svc.update(1, account.id, type="savings", initial_balance=Decimal("1.005"))

    assert updated.type == "savings"
    assert updated.initial_balance == Decimal("1.01")


async def test_update_cuenta_ajena_no_existe(svc) -> None:
    mine = await svc.create(user_id=1, name="Mía", type="bank")
    await svc.create(user_id=2, name="Otra", type="bank")

    with pytest.raises(NotFoundError):
        await svc.get(2, mine.id)


async def test_get_cuenta_ajena_rechazada(svc) -> None:
    await svc.create(user_id=1, name="Mía", type="bank")
    other = await svc.create(user_id=2, name="Otra", type="bank")

    with pytest.raises(NotFoundError):
        await svc.get(1, other.id)


async def test_list_for_user_aisla_por_usuario(svc) -> None:
    await svc.create(user_id=1, name="Mía", type="bank")
    await svc.create(user_id=1, name="Ahorro", type="savings")
    await svc.create(user_id=2, name="Otra", type="cash")

    mine = await svc.list_for_user(1)

    assert [a.name for a in mine] == ["Mía", "Ahorro"]


async def test_list_for_user_only_active(svc) -> None:
    first = await svc.create(user_id=1, name="Banco", type="bank")
    await svc.create(user_id=1, name="Ahorro", type="savings")
    await svc.deactivate(1, first.id)

    active = await svc.list_for_user(1, only_active=True)

    assert len(active) == 1
    assert active[0].name == "Ahorro"


async def test_tipos_exactos() -> None:
    assert ACCOUNT_TYPES == ("bank", "savings", "cash", "credit_card", "other")


# --------------------------------------------------------------------------- #
# Moneda única (Fase 3.1.1)                                                   #
# --------------------------------------------------------------------------- #
async def test_create_cuenta_eur_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", currency="EUR")

    assert account.currency == "EUR"


async def test_create_cuenta_eur_en_minusculas_normaliza(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", currency=" eur ")

    assert account.currency == "EUR"


async def test_create_cuenta_usd_rechazada(svc) -> None:
    with pytest.raises(ValueError, match="EUR"):
        await svc.create(user_id=1, name="Banco", type="bank", currency="USD")


async def test_create_cuenta_gbp_rechazada(svc) -> None:
    with pytest.raises(ValueError, match="EUR"):
        await svc.create(user_id=1, name="Rara", type="other", currency="GBP")


async def test_update_currency_usd_rechazado(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank")

    with pytest.raises(ValueError, match="EUR"):
        await svc.update(1, account.id, currency="USD")


# --------------------------------------------------------------------------- #
# Saldo inicial por tipo de cuenta (Fase 3.1.1)                                #
# --------------------------------------------------------------------------- #
async def test_tarjeta_credito_saldo_cero_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("0"))

    assert account.initial_balance == Decimal("0.00")


async def test_tarjeta_credito_saldo_negativo_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("-350"))

    assert account.initial_balance == Decimal("-350.00")


async def test_tarjeta_credito_saldo_positivo_rechazado(svc) -> None:
    with pytest.raises(ValueError, match="deuda"):
        await svc.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("100"))


async def test_cash_saldo_cero_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Cartera", type="cash", initial_balance=Decimal("0"))

    assert account.initial_balance == Decimal("0.00")


async def test_ahorro_saldo_positivo_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Ahorro", type="savings", initial_balance=Decimal("500"))

    assert account.initial_balance == Decimal("500.00")


async def test_update_tarjeta_saldo_negativo_sin_movimientos_ok(svc) -> None:
    account = await svc.create(user_id=1, name="Visa", type="credit_card")

    updated = await svc.update(1, account.id, initial_balance=Decimal("-120.50"))

    assert updated.initial_balance == Decimal("-120.50")


async def test_update_tarjeta_saldo_positivo_rechazado(svc) -> None:
    account = await svc.create(user_id=1, name="Visa", type="credit_card")

    with pytest.raises(ValueError, match="deuda"):
        await svc.update(1, account.id, initial_balance=Decimal("100"))


# --------------------------------------------------------------------------- #
# Protección del saldo inicial (Fase 3.1.1)                                    #
# --------------------------------------------------------------------------- #
async def test_update_saldo_inicial_sin_movimientos_permitido(svc) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))

    updated = await svc.update(1, account.id, initial_balance=Decimal("800"))

    assert updated.initial_balance == Decimal("800.00")


async def test_update_saldo_inicial_con_movimiento_rechazado(svc, transaction_repo) -> None:
    account = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await transaction_repo.create(
        user_id=1,
        type="expense",
        amount=Decimal("200"),
        account_id=account.id,
        occurred_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="saldo inicial"):
        await svc.update(1, account.id, initial_balance=Decimal("500"))


async def test_update_saldo_inicial_con_traspaso_saliente_rechazado(svc, transfer_repo) -> None:
    banco = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    ahorro = await svc.create(user_id=1, name="Ahorro", type="savings")
    await transfer_repo.create(
        user_id=1,
        from_account_id=banco.id,
        to_account_id=ahorro.id,
        amount=Decimal("100"),
        occurred_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="saldo inicial"):
        await svc.update(1, banco.id, initial_balance=Decimal("500"))


async def test_update_saldo_inicial_con_traspaso_entrante_rechazado(svc, transfer_repo) -> None:
    banco = await svc.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    ahorro = await svc.create(user_id=1, name="Ahorro", type="savings")
    await transfer_repo.create(
        user_id=1,
        from_account_id=banco.id,
        to_account_id=ahorro.id,
        amount=Decimal("100"),
        occurred_at=datetime.now(UTC),
    )

    with pytest.raises(ValueError, match="saldo inicial"):
        await svc.update(1, ahorro.id, initial_balance=Decimal("50"))


async def test_cambio_de_tipo_a_credito_con_saldo_positivo_rechazado(svc) -> None:
    account = await svc.create(user_id=1, name="Trampa", type="bank", initial_balance=Decimal("500"))

    with pytest.raises(ValueError, match="deuda"):
        await svc.update(1, account.id, type="credit_card")


async def test_cambio_de_tipo_a_banco_con_saldo_negativo_rechazado(svc) -> None:
    account = await svc.create(user_id=1, name="Visa", type="credit_card", initial_balance=Decimal("-350"))

    with pytest.raises(ValueError, match="negativo"):
        await svc.update(1, account.id, type="bank")
