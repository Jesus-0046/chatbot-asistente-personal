"""Tests unitarios: tools de Fase 6 (trips, summaries, proactivity)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest


class _Fake:
    """Wrapper que convierte atributos en un objeto con attrs para ToolContext."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def get(self, user_id: int):
        return _Fake(id=user_id, timezone="Europe/Madrid")

    async def timezone_for(self, user_id: int):
        return "Europe/Madrid"


class FakeNotes:
    pass


class FakeMemory:
    pass


class FakeConfirmations:
    pass


class FakeConversations:
    pass


class FakeRateLimiter:
    def __init__(self, allowed=True):
        self._allowed = allowed

    async def check(self, user_id: int):
        return self._allowed


class FakeSettings:
    def __init__(self):
        self.owner_telegram_ids: list[int] = []
        self.default_timezone = "Europe/Madrid"
        self.default_reminder_hour = 9
        self.default_reminder_offset_minutes = 60
        self.confirmation_ttl_minutes = 5
        self.rate_limit_enabled = False
        self.rate_limit_max_per_minute = 30
        self.llm_max_tool_iterations = 10


@pytest.fixture
def svc(services):
    return _Fake(
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=FakeSettings(),
        events=services["events"],
        tasks=services["tasks"],
        reminders=services["reminders"],
        agenda=services["agenda"],
        accounts=services["accounts"],
        transactions=services["transactions"],
        transfers=services["transfers"],
        finance=services["finance"],
        budgets=services["budgets"],
        savings_goals=services["savings_goals"],
        recurring=services["recurring"],
        planned=services["planned"],
        subscriptions=services["subscriptions"],
        people=services["people"],
        debts=services["debts"],
        shared_expenses=services["shared_expenses"],
        vehicles=services["vehicles"],
        homes=services["homes"],
        habits=services["habits"],
        intelligence=_Fake(),
        trips=services["trips"],
        summaries=services["summaries"],
        proactivity=services["proactivity"],
    )


async def test_create_trip_tool(svc) -> None:
    from asistente.tools.context import ToolContext
    from asistente.tools.registry import ToolRegistry
    from asistente.tools.trips import TRIP_TOOLS

    registry = ToolRegistry()
    for t in TRIP_TOOLS:
        registry.register(t)
    ctx = ToolContext(user_id=1, services=svc)

    result = await registry.execute(
        ctx,
        "create_trip",
        {
            "name": "Lisboa",
            "destination": "Lisboa",
            "start_at": "2026-08-03T10:00:00+00:00",
            "end_at": "2026-08-07T18:00:00+00:00",
        },
    )
    assert result.ok is True
    assert result.data["trip"]["name"] == "Lisboa"


async def test_list_trips_tool(svc) -> None:
    from asistente.tools.context import ToolContext
    from asistente.tools.registry import ToolRegistry
    from asistente.tools.trips import TRIP_TOOLS

    registry = ToolRegistry()
    for t in TRIP_TOOLS:
        registry.register(t)
    ctx = ToolContext(user_id=1, services=svc)

    await svc.trips.create(user_id=1, name="Cádiz", start_at=datetime(2026, 8, 5, tzinfo=UTC))
    result = await registry.execute(ctx, "list_trips", {})
    assert result.ok is True
    assert len(result.data["trips"]) == 1


async def test_cancel_trip_requires_confirmation(svc) -> None:
    from asistente.tools.context import ToolContext
    from asistente.tools.registry import ToolRegistry
    from asistente.tools.trips import TRIP_TOOLS

    registry = ToolRegistry()
    for t in TRIP_TOOLS:
        registry.register(t)
    ctx = ToolContext(user_id=1, services=svc)

    trip = await svc.trips.create(user_id=1, name="Lisboa")
    result = await registry.execute(ctx, "cancel_trip", {"trip_id": trip.id})
    assert result.needs_confirmation is True


async def test_daily_summary_tool(svc) -> None:
    from asistente.tools.context import ToolContext
    from asistente.tools.registry import ToolRegistry
    from asistente.tools.summaries import SUMMARY_TOOLS

    registry = ToolRegistry()
    for t in SUMMARY_TOOLS:
        registry.register(t)
    ctx = ToolContext(user_id=1, services=svc)

    result = await registry.execute(ctx, "get_daily_summary", {})
    assert result.ok is True
    assert "date" in result.data


async def test_get_proactivity_settings_tool(svc) -> None:
    from asistente.tools.context import ToolContext
    from asistente.tools.proactivity import PROACTIVITY_TOOLS
    from asistente.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for t in PROACTIVITY_TOOLS:
        registry.register(t)
    ctx = ToolContext(user_id=1, services=svc)

    result = await registry.execute(ctx, "get_proactivity_settings", {})
    assert result.ok is True
    assert result.data["enabled"] is False
