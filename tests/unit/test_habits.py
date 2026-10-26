"""Tests del servicio de dominio de salud y hábitos (Fase 4).

Cubren: CRUD, validaciones, ventanas de periodo (deterministas con fechas
fijas), cumplimiento por objetivo, progreso y aislamiento entre usuarios.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from asistente.shared.errors import NotFoundError


async def _make_habit(
    services,
    user_id: int = 1,
    *,
    name: str = "Agua",
    unit: str | None = "litros",
    target: str | None = "8",
    period: str = "day",
) -> int:
    habit = await services["habits"].create(
        user_id=user_id, name=name, unit=unit, target_per_period=target, period=period
    )
    return habit.id


async def test_crear_habito_feliz(services) -> None:
    habit = await services["habits"].create(
        user_id=1, name="Beber agua", unit="litros", target_per_period=Decimal("2"), period="day"
    )

    assert habit.user_id == 1
    assert habit.name == "Beber agua"
    assert habit.unit == "litros"
    assert habit.target_per_period == Decimal("2")
    assert habit.period == "day"
    assert habit.active is True


async def test_nombre_vacio_rechazado(services) -> None:
    with pytest.raises(ValueError, match="vacío"):
        await services["habits"].create(user_id=1, name="  ")


async def test_periodo_invalido_rechazado(services) -> None:
    with pytest.raises(ValueError, match="Periodo"):
        await services["habits"].create(user_id=1, name="Agua", period="mes")


async def test_objetivo_no_positivo_rechazado(services) -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        await services["habits"].create(user_id=1, name="Agua", target_per_period=Decimal("0"))


async def test_resolve_por_nombre_y_por_id(services) -> None:
    hid = await _make_habit(services)

    assert (await services["habits"].resolve(1, hid)).id == hid
    assert (await services["habits"].resolve(1, "Agua")).id == hid


async def test_resolve_ambiguo_pide_id(services) -> None:
    await _make_habit(services, name="Correr")
    await _make_habit(services, name="Correr")

    with pytest.raises(ValueError, match="ID"):
        await services["habits"].resolve(1, "Correr")


async def test_log_suma_registros_del_periodo(services) -> None:
    hid = await _make_habit(services)
    habit = await services["habits"].get(1, hid)
    first = await services["habits"].log(
        user_id=1, habit=habit, value=Decimal("1.5"), logged_at=datetime(2026, 9, 16, 10, 0, tzinfo=UTC), timezone="UTC"
    )
    second = await services["habits"].log(
        user_id=1, habit=habit, value=Decimal("0.5"), logged_at=datetime(2026, 9, 16, 12, 0, tzinfo=UTC), timezone="UTC"
    )

    assert first["count"] == 1
    assert second["count"] == 2


async def test_log_valor_no_positivo_rechazado(services) -> None:
    hid = await _make_habit(services)
    habit = await services["habits"].get(1, hid)
    with pytest.raises(ValueError, match="mayor que cero"):
        await services["habits"].log(user_id=1, habit=habit, value=Decimal("0"))


async def test_get_status_cumplimiento_por_objetivo(services) -> None:
    hid = await _make_habit(services, period="week", target="2")
    habit = await services["habits"].get(1, hid)

    await services["habits"].log(user_id=1, habit=habit)
    status = await services["habits"].get_status(1, hid, timezone="Europe/Madrid")

    assert status["count"] == 1
    assert status["target"] == Decimal("2")
    assert status["achieved"] is False

    await services["habits"].log(user_id=1, habit=habit)
    status = await services["habits"].get_status(1, hid, timezone="Europe/Madrid")

    assert status["count"] == 2
    assert status["achieved"] is True


async def test_list_with_progress_incluye_activos(services) -> None:
    await _make_habit(services, name="Agua")
    hid = await _make_habit(services, name="Gimnasio")
    await services["habits"].deactivate(1, hid)

    progress = await services["habits"].list_with_progress(1, timezone="Europe/Madrid")

    assert [p["habit"].name for p in progress] == ["Agua"]


async def test_update_habit(services) -> None:
    hid = await _make_habit(services)
    updated = await services["habits"].update(1, hid, target_per_period=Decimal("10"), period="week")

    assert updated.target_per_period == Decimal("10")
    assert updated.period == "week"


async def test_deactivate_habit_doble_rechazado(services) -> None:
    hid = await _make_habit(services)
    await services["habits"].deactivate(1, hid)
    with pytest.raises(ValueError, match="desactivado"):
        await services["habits"].deactivate(1, hid)


async def test_habito_ajeno_no_accesible(services) -> None:
    hid = await _make_habit(services, user_id=1)

    with pytest.raises(NotFoundError):
        await services["habits"].get(2, hid)

    assert await services["habits"].list(2) == []


async def test_ventana_semanal_ignora_registros_de_otra_semana(services) -> None:
    hid = await _make_habit(services, period="week", target=None)
    habit = await services["habits"].get(1, hid)

    monday = await services["habits"].log(
        user_id=1, habit=habit, logged_at=datetime(2026, 9, 14, 8, 0, tzinfo=UTC), timezone="UTC"
    )
    next_monday = await services["habits"].log(
        user_id=1, habit=habit, logged_at=datetime(2026, 9, 21, 10, 0, tzinfo=UTC), timezone="UTC"
    )

    assert monday["count"] == 1
    assert next_monday["count"] == 1  # 2026-09-21 ya abre la ventana siguiente


async def test_ventana_mensual_rebasa_fin_de_mes(services) -> None:
    hid = await _make_habit(services, period="month", target=None)
    habit = await services["habits"].get(1, hid)

    last_day = await services["habits"].log(user_id=1, habit=habit, logged_at=datetime(2026, 9, 30, 12, 0, tzinfo=UTC))
    next_month = await services["habits"].log(user_id=1, habit=habit, logged_at=datetime(2026, 10, 1, 6, 0, tzinfo=UTC))

    assert last_day["count"] == 1
    assert next_month["count"] == 1  # ya está en la ventana de octubre
