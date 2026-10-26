"""Herramientas de proactividad (Fase 6).

La proactividad es OPT-IN: se activa explícitamente con
`update_proactivity_settings(enabled=true)`. Estas tools gestionan las
preferencias (activación, horas de silencio, límite diario y zona horaria) y
nunca envían mensajes por sí mismas; el envío lo hace el `ProactivePoller`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.proactivity import ProactivityService
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class UpdateProactivityArgs(BaseModel):
    enabled: bool | None = Field(default=None, description="Activa/desactiva la proactividad")
    quiet_hours_start: str | None = Field(default=None, max_length=5, description="HH:MM")
    quiet_hours_end: str | None = Field(default=None, max_length=5, description="HH:MM")
    daily_limit: int | None = Field(default=None, ge=0, le=50, description="Maximo de avisos al dia")
    timezone: str | None = Field(default=None, max_length=64, description="Zona IANA, p.ej. Europe/Madrid")


def _services(ctx: ToolContext) -> ProactivityService:
    return ctx.services.proactivity


async def _get_proactivity_settings(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    data = await _services(ctx).get_settings(ctx.user_id)
    return ToolResult(ok=True, data=data)


async def _update_proactivity_settings(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpdateProactivityArgs(**kwargs)
    try:
        data = await _services(ctx).update_settings(
            ctx.user_id,
            enabled=args.enabled,
            quiet_hours_start=args.quiet_hours_start,
            quiet_hours_end=args.quiet_hours_end,
            daily_limit=args.daily_limit,
            timezone=args.timezone,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data=data)


PROACTIVITY_TOOLS = [
    ToolDefinition(
        name="get_proactivity_settings",
        description=(
            "Consulta si la proactividad esta activada y sus preferencias (horas de silencio, limite "
            "diario, zona horaria). 'Tienes los avisos activados?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_proactivity_settings,
    ),
    ToolDefinition(
        name="update_proactivity_settings",
        description=(
            "Activa o desactiva los avisos proactivos y ajusta horas de silencio (HH:MM), limite diario "
            "(0..50) o zona horaria. Es OPT-IN: por defecto estan desactivados. "
            "'Activa los avisos pero no me molestes de noche' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean"},
                "quiet_hours_start": {"type": "string", "description": "HH:MM"},
                "quiet_hours_end": {"type": "string", "description": "HH:MM"},
                "daily_limit": {"type": "integer", "description": "0..50"},
                "timezone": {"type": "string"},
            },
        },
        handler=_update_proactivity_settings,
        args_model=UpdateProactivityArgs,
    ),
]
