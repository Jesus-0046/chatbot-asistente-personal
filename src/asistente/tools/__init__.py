"""Factory: construye el registro con todas las tools (Fase 1 + 2 + 3 + 3.2 + 3.3 + 3.4 + 4 + 6)."""

from __future__ import annotations

from asistente.tools.agenda import AGENDA_TOOLS
from asistente.tools.budgets import BUDGET_TOOLS
from asistente.tools.definitions import TOOLS
from asistente.tools.finance import FINANCE_TOOLS
from asistente.tools.habits import HABIT_TOOLS
from asistente.tools.home import HOME_TOOLS
from asistente.tools.images import IMAGE_TOOLS
from asistente.tools.intelligence import INTELLIGENCE_TOOLS
from asistente.tools.link import LINK_TOOLS
from asistente.tools.personas import PERSONA_TOOLS
from asistente.tools.planned import PLANNED_TOOLS
from asistente.tools.proactivity import PROACTIVITY_TOOLS
from asistente.tools.recurring import RECURRING_TOOLS
from asistente.tools.registry import ToolRegistry
from asistente.tools.savings import SAVINGS_TOOLS
from asistente.tools.subscriptions import SUBSCRIPTION_TOOLS
from asistente.tools.summaries import SUMMARY_TOOLS
from asistente.tools.trips import TRIP_TOOLS
from asistente.tools.vehicle import VEHICLE_TOOLS


def create_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in [
        *TOOLS,
        *AGENDA_TOOLS,
        *FINANCE_TOOLS,
        *BUDGET_TOOLS,
        *SAVINGS_TOOLS,
        *RECURRING_TOOLS,
        *PLANNED_TOOLS,
        *SUBSCRIPTION_TOOLS,
        *PERSONA_TOOLS,
        *VEHICLE_TOOLS,
        *HOME_TOOLS,
        *HABIT_TOOLS,
        *IMAGE_TOOLS,
        *INTELLIGENCE_TOOLS,
        *TRIP_TOOLS,
        *SUMMARY_TOOLS,
        *PROACTIVITY_TOOLS,
        *LINK_TOOLS,
    ]:
        registry.register(tool)
    return registry
