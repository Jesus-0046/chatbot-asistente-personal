"""Tests del servicio de dominio de deudas/préstamos personales (Fase 3.4).

Cubre: favor/contra, validaciones, liquidación total (sin pago parcial),
doble liquidación, aislamiento y consolidación de estado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.shared.errors import NotFoundError


async def _make_person(services, user_id: int = 1, name: str = "Juan") -> int:
    person = await services["people_svc"].create(user_id=user_id, name=name)
    return person.id


async def test_registrar_me_debe(services) -> None:
    person_id = await _make_person(services)
    debt = await services["debts"].create(
        user_id=1,
        person_id=person_id,
        direction="owed_to_user",
        amount=Decimal("50.00"),
        reason="parte del alquiler",
    )

    assert debt.direction == "owed_to_user"
    assert debt.amount == Decimal("50.00")
    assert debt.remaining_amount == Decimal("50.00")
    assert debt.status == "open"
    assert debt.currency == "EUR"
    assert debt.reason == "parte del alquiler"


async def test_registrar_yo_debo(services) -> None:
    person_id = await _make_person(services, name="María")
    debt = await services["debts"].create(
        user_id=1, person_id=person_id, direction="user_owes", amount=Decimal("30.00")
    )
    assert debt.direction == "user_owes"


async def test_direction_invalida_rechazada(services) -> None:
    person_id = await _make_person(services)
    with pytest.raises(ValueError, match="owed_to_user"):
        await services["debts"].create(user_id=1, person_id=person_id, direction="ambos", amount=Decimal("10"))


async def test_importe_no_positivo_rechazado(services) -> None:
    person_id = await _make_person(services)
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["debts"].create(user_id=1, person_id=person_id, direction="owed_to_user", amount=Decimal("0"))


async def test_persona_inexistente_not_found(services) -> None:
    with pytest.raises(NotFoundError):
        await services["debts"].create(user_id=1, person_id=999, direction="owed_to_user", amount=Decimal("10"))


async def test_reliquidar_deuda_ya_liquidada_error(services) -> None:
    person_id = await _make_person(services)
    debt = await services["debts"].create(
        user_id=1, person_id=person_id, direction="owed_to_user", amount=Decimal("50")
    )
    settled = await services["debts"].settle(user_id=1, debt_id=debt.id)
    assert settled.status == "settled"
    assert settled.remaining_amount == Decimal("0.00")

    with pytest.raises(ValueError, match="liquidada"):
        await services["debts"].settle(user_id=1, debt_id=debt.id)


async def test_liquidar_deuda_ajena_not_found(services) -> None:
    person_id = await _make_person(services)
    debt = await services["debts"].create(
        user_id=1, person_id=person_id, direction="owed_to_user", amount=Decimal("50")
    )
    with pytest.raises(NotFoundError):
        await services["debts"].settle(user_id=2, debt_id=debt.id)


async def test_get_status_consolida_totales(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["debts"].create(
        user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("100"), reason="cenas"
    )
    await services["debts"].create(
        user_id=1,
        person_id=maria,
        direction="user_owes",
        amount=Decimal("40"),
        due_at=datetime(2026, 12, 1, tzinfo=UTC),
    )
    await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("10"))

    status = await services["debts"].get_status(user_id=1)

    assert status["total_owed_to_me"] == Decimal("110.00")
    assert status["total_i_owe"] == Decimal("40.00")
    rows = status["debts"]
    assert len(rows) == 3
    by_person = sorted(set(r["person"] for r in rows))
    assert by_person == ["Juan", "María"]


async def test_pago_parcial_no_soportado_al_crear_solo(services) -> None:
    """El servicio solo permite deudas completas; el estado `remaining` baja a 0
    únicamente con la liquidación total (settle). No hay mecanismo de parciales."""
    person_id = await _make_person(services)
    debt = await services["debts"].create(
        user_id=1, person_id=person_id, direction="owed_to_user", amount=Decimal("50")
    )
    assert debt.remaining_amount == debt.amount


async def test_isolacion_listas_por_usuario(services) -> None:
    person1 = await _make_person(services, user_id=1, name="Juan")
    person2 = await _make_person(services, user_id=2, name="Ana")
    await services["debts"].create(user_id=1, person_id=person1, direction="owed_to_user", amount=Decimal("10"))
    await services["debts"].create(user_id=2, person_id=person2, direction="owed_to_user", amount=Decimal("20"))

    status1 = await services["debts"].get_status(user_id=1)
    status2 = await services["debts"].get_status(user_id=2)
    assert status1["total_owed_to_me"] == Decimal("10.00")
    assert status2["total_owed_to_me"] == Decimal("20.00")


# --------------------------------------------------------------------------- #
# Fase 3.4.1: filtro por persona en el estado de deudas                         #
# --------------------------------------------------------------------------- #
async def test_get_status_sin_persona_devuelve_todas_las_abiertas(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("10"))
    await services["debts"].create(user_id=1, person_id=maria, direction="owed_to_user", amount=Decimal("20"))

    status = await services["debts"].get_status(user_id=1)
    assert len(status["debts"]) == 2
    assert sorted(set(r["person"] for r in status["debts"])) == ["Juan", "María"]


async def test_get_status_filtra_por_persona_id(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("10"))
    await services["debts"].create(user_id=1, person_id=maria, direction="user_owes", amount=Decimal("20"))

    status = await services["debts"].get_status(user_id=1, person_id=juan)

    assert [r["person"] for r in status["debts"]] == ["Juan"]
    assert status["total_owed_to_me"] == Decimal("10.00")
    assert status["total_i_owe"] == Decimal("0.00")


async def test_get_status_filtra_y_excluye_a_los_demas(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("10"))
    await services["debts"].create(user_id=1, person_id=maria, direction="owed_to_user", amount=Decimal("20"))

    status = await services["debts"].get_status(user_id=1, person_id=juan)

    assert len(status["debts"]) == 1
    assert status["debts"][0]["person_id"] == juan
    assert all(r["person_id"] != maria for r in status["debts"])


async def test_get_status_persona_de_otro_usuario_not_found(services) -> None:
    juan_ajeno = await _make_person(services, user_id=2, name="Juan")
    with pytest.raises(NotFoundError):
        await services["debts"].get_status(user_id=1, person_id=juan_ajeno)


async def test_get_status_persona_inexistente_not_found(services) -> None:
    with pytest.raises(NotFoundError):
        await services["debts"].get_status(user_id=1, person_id=999)


async def test_get_status_solo_suma_abiertas_no_liquidadas(services) -> None:
    juan = await _make_person(services, name="Juan")
    debt = await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("10"))
    await services["debts"].create(user_id=1, person_id=juan, direction="owed_to_user", amount=Decimal("20"))
    await services["debts"].settle(user_id=1, debt_id=debt.id)

    status = await services["debts"].get_status(user_id=1)
    assert len(status["debts"]) == 1
    assert status["total_owed_to_me"] == Decimal("20.00")

    filtered = await services["debts"].get_status(user_id=1, person_id=juan)
    assert len(filtered["debts"]) == 1
    assert filtered["total_owed_to_me"] == Decimal("20.00")
