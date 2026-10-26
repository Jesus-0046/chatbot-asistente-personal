"""Tests del servicio de dominio de gastos compartidos (Fase 3.4).

Cubre: reparto igualitario EXACTO al céntimo, reparto manual (suma == total),
pagador usuario/persona/multiple (incluido pagador NO participante),
balances derivados, validaciones y aislamiento.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.domain.shared_expenses import split_equal
from asistente.shared.errors import NotFoundError

_DT = datetime(2026, 9, 15, 20, 0, tzinfo=UTC)


async def _make_person(services, user_id: int = 1, name: str = "Juan") -> int:
    person = await services["people_svc"].create(user_id=user_id, name=name)
    return person.id


def test_split_equal_suma_exacta_100_entre_3() -> None:
    amounts = split_equal(Decimal("100.00"), 3)
    assert amounts == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]
    assert sum(amounts, Decimal("0")) == Decimal("100.00")


def test_split_equal_divide_exacto() -> None:
    amounts = split_equal(Decimal("84.00"), 3)
    assert amounts == [Decimal("28.00"), Decimal("28.00"), Decimal("28.00")]


async def test_crear_gasto_igualitario_usuario_pago(services) -> None:
    await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    created = await services["shared_expenses"].create(
        user_id=1,
        title="Cena con Juan y María",
        total_amount=Decimal("84.00"),
        participants=[
            {"is_user": True},
            {"name": "Juan"},
            {"person_id": maria},
        ],
        paid_by="user",
        occurred_at=_DT,
    )

    assert created["expense"].total_amount == Decimal("84.00")
    shares = {p["name"]: p["share_amount"] for p in created["participants"]}
    assert shares == {"user": Decimal("28.00"), "Juan": Decimal("28.00"), "María": Decimal("28.00")}

    status = await services["shared_expenses"].get_status(user_id=1)
    assert len(status["expenses"]) == 1
    expense = status["expenses"][0]
    # El usuario pagó todo: le deben 56 (28 de Juan + 28 de María).
    assert expense["user_balance"] == Decimal("-56.00")
    by_name = {p["name"]: p["balance"] for p in expense["participants"]}
    assert by_name["Juan"] == Decimal("28.00")  # debe
    assert by_name["María"] == Decimal("28.00")  # debe


async def test_crear_gasto_paga_una_persona_no_participante(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["shared_expenses"].create(
        user_id=1,
        title="Pizza de Juan y María",
        total_amount=Decimal("60.00"),
        participants=[{"person_id": juan}, {"person_id": maria}],
        paid_by="user",
        occurred_at=_DT,
    )

    status = await services["shared_expenses"].get_status(user_id=1)
    expense = status["expenses"][0]
    # Usuario pagó 60 pero no participa: le deben 60 en total.
    assert expense["user_balance"] == Decimal("-60.00")
    assert [p["balance"] for p in expense["participants"]] == [Decimal("30.00"), Decimal("30.00")]


async def test_crear_gasto_con_varios_pagadores(services) -> None:
    juan = await _make_person(services, name="Juan")
    created = await services["shared_expenses"].create(
        user_id=1,
        title="Cena repartida",
        total_amount=Decimal("90.00"),
        participants=[{"is_user": True}, {"person_id": juan}],
        paid_by=None,
        payments=[
            {"who": "user", "amount": "60"},
            {"who": juan, "amount": "30"},
        ],
        occurred_at=_DT,
    )
    assert created["expense"].total_amount == Decimal("90.00")

    status = await services["shared_expenses"].get_status(user_id=1)
    expense = status["expenses"][0]
    # Shares iguales (45/45). Usuario puso 60 → le deben 15; Juan puso 30 → debe 15.
    assert expense["user_balance"] == Decimal("-15.00")
    assert expense["participants"][1]["balance"] == Decimal("15.00")


async def test_reparto_manual_sumas_no_coinciden_error(services) -> None:
    juan = await _make_person(services, name="Juan")
    with pytest.raises(ValueError, match="suman"):
        await services["shared_expenses"].create(
            user_id=1,
            title="Cena",
            total_amount=Decimal("84.00"),
            participants=[
                {"is_user": True, "share": "40"},
                {"person_id": juan, "share": "40"},
            ],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_reparto_manual_exacto(services) -> None:
    juan = await _make_person(services, name="Juan")
    created = await services["shared_expenses"].create(
        user_id=1,
        title="Cena",
        total_amount=Decimal("84.00"),
        participants=[
            {"is_user": True, "share": "44"},
            {"person_id": juan, "share": "40"},
        ],
        paid_by="user",
        occurred_at=_DT,
    )
    shares = {p["name"]: p["share_amount"] for p in created["participants"]}
    assert shares == {"user": Decimal("44.00"), "Juan": Decimal("40.00")}


async def test_mezclar_reparto_manual_e_igualitario_error(services) -> None:
    juan = await _make_person(services, name="Juan")
    with pytest.raises(ValueError, match="TODOS"):
        await services["shared_expenses"].create(
            user_id=1,
            title="Cena",
            total_amount=Decimal("84.00"),
            participants=[
                {"is_user": True, "share": "44"},
                {"person_id": juan},
            ],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_participante_duplicado_error(services) -> None:
    juan = await _make_person(services, name="Juan")
    with pytest.raises(ValueError, match="más de una vez"):
        await services["shared_expenses"].create(
            user_id=1,
            title="Cena",
            total_amount=Decimal("60.00"),
            participants=[{"person_id": juan}, {"person_name": "juan"}],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_usuario_participante_duplicado_error(services) -> None:
    with pytest.raises(ValueError, match="UNA vez"):
        await services["shared_expenses"].create(
            user_id=1,
            title="Cena",
            total_amount=Decimal("60.00"),
            participants=[{"is_user": True}, {"is_user": True}],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_persona_de_otro_usuario_not_found(services) -> None:
    juan = await _make_person(services, user_id=1, name="Juan")
    with pytest.raises(NotFoundError):
        await services["shared_expenses"].create(
            user_id=2,
            title="Cena",
            total_amount=Decimal("60.00"),
            participants=[{"person_id": juan}],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_importe_total_negativo_rechazado(services) -> None:
    with pytest.raises(ValueError, match="mayor que 0"):
        await services["shared_expenses"].create(
            user_id=1,
            title="Cena",
            total_amount=Decimal("-5"),
            participants=[{"is_user": True}],
            paid_by="user",
            occurred_at=_DT,
        )


async def test_get_status_filtra_por_persona(services) -> None:
    juan = await _make_person(services, name="Juan")
    maria = await _make_person(services, name="María")
    await services["shared_expenses"].create(
        user_id=1,
        title="Con Juan",
        total_amount=Decimal("20.00"),
        participants=[{"is_user": True}, {"person_id": juan}],
        paid_by="user",
        occurred_at=_DT,
    )
    await services["shared_expenses"].create(
        user_id=1,
        title="Con María",
        total_amount=Decimal("30.00"),
        participants=[{"is_user": True}, {"person_id": maria}],
        paid_by="user",
        occurred_at=_DT,
    )

    status = await services["shared_expenses"].get_status(user_id=1, person_id=juan)
    assert [e["title"] for e in status["expenses"]] == ["Con Juan"]


async def test_cancelar_gasto_y_no_aparece_en_estado(services) -> None:
    juan = await _make_person(services, name="Juan")
    created = await services["shared_expenses"].create(
        user_id=1,
        title="Cena",
        total_amount=Decimal("60.00"),
        participants=[{"is_user": True}, {"person_id": juan}],
        paid_by="user",
        occurred_at=_DT,
    )
    cancelled = await services["shared_expenses"].cancel(user_id=1, expense_id=created["expense"].id)
    assert cancelled.status == "cancelled"

    status = await services["shared_expenses"].get_status(user_id=1)
    assert status["expenses"] == []

    with pytest.raises(ValueError, match="cancelado"):
        await services["shared_expenses"].cancel(user_id=1, expense_id=created["expense"].id)
