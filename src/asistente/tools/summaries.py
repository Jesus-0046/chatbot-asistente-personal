"""Herramientas de resúmenes de vida (Fase 6).

Vistas de SOLO LECTURA que reúnen agenda, tareas, pagos planificados y viajes:

- get_daily_summary:    lo de hoy (eventos, tareas de hoy, pagos, viajes que
  empiezan hoy y atrasos).
- get_weekly_summary:   los próximos 7 días.
- get_upcoming_summary: próximos N días (1..90) con eventos y tareas ordenados.
- get_pending_summary:  lo pendiente AHORA (tareas abiertas y pagos sin
  convertir, con importe agregado).

Ninguna tool escribe en la BD ni crea movimientos.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.summaries import SummaryService
from asistente.tools.agenda import _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class UpcomingArgs(BaseModel):
    days: int = Field(default=30, ge=1, le=90, description="Dias a mirar hacia adelante (1..90)")


def _services(ctx: ToolContext) -> SummaryService:
    return ctx.services.summaries


async def _get_daily_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    data = await _services(ctx).daily(ctx.user_id, timezone=await _user_timezone(ctx))
    return ToolResult(ok=True, data=data)


async def _get_weekly_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    data = await _services(ctx).weekly(ctx.user_id, timezone=await _user_timezone(ctx))
    return ToolResult(ok=True, data=data)


async def _get_upcoming_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpcomingArgs(**kwargs)
    data = await _services(ctx).upcoming(ctx.user_id, days=args.days, timezone=await _user_timezone(ctx))
    return ToolResult(ok=True, data=data)


async def _get_pending_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    data = await _services(ctx).pending(ctx.user_id, timezone=await _user_timezone(ctx))
    return ToolResult(ok=True, data=data)


SUMMARY_TOOLS = [
    ToolDefinition(
        name="get_daily_summary",
        description=(
            "Resumen de HOY: eventos, tareas con vencimiento hoy, pagos planificados de hoy, viajes que "
            "empiezan hoy y tareas atrasadas. 'Que tengo hoy?', 'dame el resumen del dia' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_daily_summary,
    ),
    ToolDefinition(
        name="get_weekly_summary",
        description=(
            "Resumen de los proximos 7 dias: eventos, tareas, pagos planificados y viajes. "
            "'Como tengo la semana?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_weekly_summary,
    ),
    ToolDefinition(
        name="get_upcoming_summary",
        description=(
            "Proximos N dias (por defecto 30) con eventos y tareas ordenados cronologicamente, mas los "
            "viajes en esa ventana. 'Que se me viene encima?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {"days": {"type": "integer", "default": 30, "description": "1..90"}},
        },
        handler=_get_upcoming_summary,
        args_model=UpcomingArgs,
    ),
    ToolDefinition(
        name="get_pending_summary",
        description=(
            "Lo pendiente AHORA: tareas abiertas (con las atrasadas destacadas) y pagos planificados sin "
            "convertir, con su importe total. 'Que me queda pendiente?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_pending_summary,
    ),
]
