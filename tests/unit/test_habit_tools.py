"""Tests de las tools de salud y hábitos (Fase 4) vía el registro.

Verifican: CRUD, registro con valor, progreso por periodo, confirmación para
desactivar, mención a que las citas médicas NO van aquí (agenda) y aislamiento.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.unit.fakes import FakeHabitLogRepo, FakeHabitRepo
from tests.unit.helpers import make_settings

from asistente.domain.habits import HabitService
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry

_UID = 7


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    habits = HabitService(FakeHabitRepo(), FakeHabitLogRepo())
    return _Fake(users=FakeUsers(), settings=settings, habits=habits)


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=_UID, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict, *, confirmed: bool = True):
    return await registry.execute(ctx, name, args, confirmed=confirmed)


async def _add_habit(services, user_id: int = _UID, *, name: str = "Agua", period: str = "day") -> int:
    habit = await services.habits.create(user_id=user_id, name=name, unit="litros", target_per_period=Decimal("8"))
    return habit.id


async def test_create_habit_happy_path(registry, ctx, services) -> None:
    res = await _run(
        registry,
        ctx,
        "create_habit",
        {"name": "Beber agua", "unit": "litros", "target_per_period": "2", "period": "day"},
    )

    assert res.ok
    assert res.data["name"] == "Beber agua"
    assert res.data["target_per_period"] == "2.00"


async def test_create_habit_periodo_invalido(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_habit", {"name": "Agua", "period": "mes"})

    assert not res.ok
    assert "Periodo" in res.error


async def test_list_habits_con_progreso(registry, ctx, services) -> None:
    hid = await _add_habit(services)
    habit = await services.habits.get(_UID, hid)
    await services.habits.log(user_id=_UID, habit=habit)

    res = await _run(registry, ctx, "list_habits", {})

    assert res.ok
    assert len(res.data["habits"]) == 1
    assert res.data["habits"][0]["count_current_period"] == 1


async def test_update_habit(registry, ctx, services) -> None:
    hid = await _add_habit(services)
    res = await _run(registry, ctx, "update_habit", {"habit_id": hid, "target_per_period": "10"})

    assert res.ok
    assert res.data["name"] == "Agua"


async def test_deactivate_habit_requiere_confirmacion(registry, ctx, services) -> None:
    hid = await _add_habit(services)

    res = await _run(registry, ctx, "deactivate_habit", {"habit_id": hid}, confirmed=False)
    assert not res.ok
    assert res.needs_confirmation is True

    res = await _run(registry, ctx, "deactivate_habit", {"habit_id": hid}, confirmed=True)
    assert res.ok
    assert res.data["active"] is False


async def test_log_habit(registry, ctx, services) -> None:
    hid = await _add_habit(services)
    res = await _run(registry, ctx, "log_habit", {"habit": hid, "value": "2"})

    assert res.ok
    assert res.data["value"] == 2.0
    assert res.data["current_period_counts"] == 1


async def test_log_habit_por_nombre(registry, ctx, services) -> None:
    await _add_habit(services)
    res = await _run(registry, ctx, "log_habit", {"habit": "Agua"})

    assert res.ok


async def test_log_habit_importe_invalido(registry, ctx, services) -> None:
    hid = await _add_habit(services)
    res = await _run(registry, ctx, "log_habit", {"habit": hid, "value": "-3"})

    assert not res.ok


async def test_get_habit_status(registry, ctx, services) -> None:
    hid = await _add_habit(services)
    habit = await services.habits.get(_UID, hid)
    await services.habits.log(user_id=_UID, habit=habit, value=Decimal("2"))

    res = await _run(registry, ctx, "get_habit_status", {"habit": hid})

    assert res.ok
    assert res.data["habit"]["name"] == "Agua"
    assert res.data["current_period"]["count"] >= 1
    assert res.data["current_period"]["achieved"] is False  # objetivo 8


async def test_aislamiento_entre_usuarios(registry, ctx, services) -> None:
    await _add_habit(services)
    await _add_habit(services, user_id=999, name="Ajeno")

    res = await _run(registry, ctx, "list_habits", {})

    assert res.ok
    assert len(res.data["habits"]) == 1
