"""Herramientas de la vivienda (Fase 4).

Tools (7): add_home / list_homes / update_home / deactivate_home /
record_home_maintenance / get_home_status / get_home_upcoming.

Semántica:
- Los mantenimientos sin fecha de ejecución ni coste quedan `pending` (en
  "próximas"); con `occurred_at` o `cost` pasan a `done`.
- Un mantenimiento con `cost` crea SU única transacción de gasto (categoría
  'Vivienda') de forma atómica. Sin `cost` no se toca ninguna cuenta.
- Los ids se obtienen de list_homes; el LLM nunca inventa ids.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.shared.timeutil import to_local
from asistente.tools.agenda import _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money, _resolve_occurred
from asistente.tools.registry import ToolDefinition, ToolResult

HomeRef = int | str


class HomeCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Nombre: 'mi piso', 'casa de la playa'...")
    home_type: str | None = Field(default=None, description="piso, casa, garaje, local, otro")
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class HomeRefArgs(BaseModel):
    home_id: int = Field(gt=0)


class HomeUpdateArgs(BaseModel):
    home_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    home_type: str | None = Field(default=None)
    address: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=1000)


class HomeMaintenanceRecordArgs(BaseModel):
    home: HomeRef = Field(description="home_id (entero) o nombre ('mi piso')")
    title: str = Field(
        min_length=1, max_length=300, description="Qué se hizo/planifica, p. ej. 'revisión de la caldera'"
    )
    maintenance_type: str = Field(default="revision", description="revision, reparacion, otro")
    occurred_at: str | None = Field(default=None, max_length=200, description="Cuándo se hizo (por defecto hoy)")
    cost: str | None = Field(default=None, max_length=40, description="Coste (opcional; si existe crea el gasto)")
    next_due_at: str | None = Field(default=None, max_length=200, description="Próxima vez: 'en 6 meses' o ISO")
    note: str | None = Field(default=None, max_length=1000)
    account_id: int | None = Field(default=None, gt=0, description="Cuenta del gasto (si se conoce)")


class HomeStatusArgs(BaseModel):
    home: HomeRef = Field(description="home_id (entero) o nombre")


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _add_home(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HomeCreateArgs(**kwargs)
    try:
        home = await ctx.services.homes.create(
            user_id=ctx.user_id,
            name=args.name,
            home_type=args.home_type,
            address=args.address,
            notes=args.notes,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "home_id": home.id,
            "name": home.name,
            "home_type": home.home_type,
            "description": f"Vivienda '{home.name}' dada de alta (ID {home.id}).",
        },
    )


async def _list_homes(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    homes = await ctx.services.homes.list(ctx.user_id)
    return ToolResult(
        ok=True,
        data={
            "homes": [
                {
                    "id": h.id,
                    "name": h.name,
                    "home_type": h.home_type,
                    "address": h.address,
                    "active": h.active,
                }
                for h in homes
            ]
        },
    )


async def _update_home(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HomeUpdateArgs(**kwargs)
    try:
        home = await ctx.services.homes.update(
            ctx.user_id,
            args.home_id,
            name=args.name,
            home_type=args.home_type,
            address=args.address,
            notes=args.notes,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "home_id": home.id,
            "name": home.name,
            "description": f"Vivienda '{home.name}' actualizada.",
        },
    )


async def _deactivate_home(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HomeRefArgs(**kwargs)
    try:
        home = await ctx.services.homes.deactivate(ctx.user_id, args.home_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "home_id": home.id,
            "name": home.name,
            "active": False,
            "description": f"Vivienda '{home.name}' desactivada (su histórico se conserva).",
        },
    )


async def _record_home_maintenance(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HomeMaintenanceRecordArgs(**kwargs)
    try:
        home = await ctx.services.homes.resolve(ctx.user_id, args.home)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    next_due = _resolve_occurred(ctx, args.next_due_at, user_tz)
    if isinstance(next_due, ToolResult):
        return next_due
    cost = None
    if args.cost:
        cost = _parse_money(args.cost)
    if isinstance(cost, ToolResult):
        return cost
    try:
        result = await ctx.services.homes.record_maintenance(
            user_id=ctx.user_id,
            home=home,
            title=args.title,
            maintenance_type=args.maintenance_type,
            occurred_at=occurred,
            cost=cost,
            next_due_at=next_due,
            note=args.note,
            account_id=args.account_id,
        )
    except Exception as exc:
        return _ko(exc)
    status_text = "hecho" if result["status"] == "done" else "pendiente"
    return ToolResult(
        ok=True,
        data={
            "home_id": result["home_id"],
            "maintenance_id": result["maintenance_id"],
            "title": result["title"],
            "maintenance_type": result["maintenance_type"],
            "status": result["status"],
            "cost": fmt_amount(result["cost"]) if result["cost"] is not None else None,
            "transaction_id": result["transaction_id"],
            "occurred_at": _fmt_dt(result["occurred_at"]),
            "next_due_at": _fmt_dt(result["next_due_at"]),
            "description_text": (
                f"Mantenimiento '{result['title']}' de '{home.name}' registrado como {status_text}."
                + (
                    f" Gasto creado: {fmt_amount(result['cost'])} € (movimiento {result['transaction_id']})."
                    if result["cost"] is not None
                    else ""
                )
            ),
        },
    )


async def _get_home_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HomeStatusArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        home = await ctx.services.homes.resolve(ctx.user_id, args.home)
        status = await ctx.services.homes.get_status(ctx.user_id, home.id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "home": {
                "id": status["home"].id,
                "name": status["home"].name,
                "home_type": status["home"].home_type,
                "address": status["home"].address,
            },
            "pending": [
                {
                    "id": m.id,
                    "title": m.title,
                    "maintenance_type": m.maintenance_type,
                    "next_due_at": to_local(m.next_due_at, user_tz).isoformat() if m.next_due_at else None,
                    "status": m.status,
                }
                for m in status["pending"]
            ],
            "recent": [
                {
                    "id": m.id,
                    "title": m.title,
                    "maintenance_type": m.maintenance_type,
                    "occurred_at": to_local(m.occurred_at, user_tz).isoformat() if m.occurred_at else None,
                    "cost": fmt_amount(m.cost) if m.cost is not None else None,
                    "status": m.status,
                }
                for m in status["recent"]
            ],
            "upcoming": [
                {
                    "id": m.id,
                    "title": m.title,
                    "next_due_at": to_local(m.next_due_at, user_tz).isoformat() if m.next_due_at else None,
                }
                for m in status["upcoming"]
            ],
            "spend_last_30_days": {
                "total": fmt_amount(status["spend_last_30_days"]["total"]),
                "currency": status["spend_last_30_days"]["currency"],
            },
        },
    )


async def _get_home_upcoming(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    user_tz = await _user_timezone(ctx)
    try:
        data = await ctx.services.homes.get_upcoming(ctx.user_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "upcoming": [
                {
                    "maintenance_id": m.id,
                    "home_id": m.home_id,
                    "title": m.title,
                    "maintenance_type": m.maintenance_type,
                    "next_due_at": to_local(m.next_due_at, user_tz).isoformat() if m.next_due_at else None,
                }
                for m in data["upcoming"]
            ]
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
HOME_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="add_home",
        description="Da de alta una vivienda del usuario ('mi piso', 'casa de la playa').",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre: 'mi piso', 'casa de la playa'..."},
                "home_type": {"type": "string", "default": None, "enum": ["piso", "casa", "garaje", "local", "otro"]},
                "address": {"type": "string", "default": None},
                "notes": {"type": "string", "default": None},
            },
            "required": ["name"],
        },
        handler=_add_home,
        args_model=HomeCreateArgs,
    ),
    ToolDefinition(
        name="list_homes",
        description="Lista las viviendas dados de alta (ids, nombre, tipo, dirección). Úsala para obtener los home_id; "
        "nunca inventes ids.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_homes,
    ),
    ToolDefinition(
        name="update_home",
        description="Actualiza una vivienda existente (nombre, tipo, dirección...).",
        input_schema={
            "type": "object",
            "properties": {
                "home_id": {"type": "integer"},
                "name": {"type": "string", "default": None},
                "home_type": {"type": "string", "default": None},
                "address": {"type": "string", "default": None},
                "notes": {"type": "string", "default": None},
            },
            "required": ["home_id"],
        },
        handler=_update_home,
        args_model=HomeUpdateArgs,
    ),
    ToolDefinition(
        name="deactivate_home",
        description="Desactiva una vivienda (ya no se listará). Su histórico NO se borra.",
        input_schema={
            "type": "object",
            "properties": {"home_id": {"type": "integer"}},
            "required": ["home_id"],
        },
        handler=_deactivate_home,
        args_model=HomeRefArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="record_home_maintenance",
        description="Registra un mantenimiento de una vivienda (revisión de caldera, reparación...). Si tiene coste, "
        "crea un gasto de categoría 'Vivienda' en la cuenta correspondiente de forma automática (si no, solo se anota). "
        "Sin fecha de ejecución queda pendiente y aparecerá en próximos mantenimientos. 'Hay que revisar la caldera "
        "en octubre' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "home": {
                    "type": ["integer", "string"],
                    "description": "home_id (de list_homes) o nombre ('mi piso')",
                },
                "title": {"type": "string", "description": "Qué se hizo/planifica, p. ej. 'revisión de la caldera'"},
                "maintenance_type": {
                    "type": "string",
                    "default": "revision",
                    "enum": ["revision", "reparacion", "otro"],
                },
                "occurred_at": {"type": "string", "default": None, "description": "Cuándo se hizo (por defecto hoy)"},
                "cost": {"type": "string", "default": None, "description": "Coste (opcional; si existe crea el gasto)"},
                "next_due_at": {"type": "string", "default": None, "description": "Próxima vez: 'en 6 meses' o ISO"},
                "note": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None, "description": "Cuenta del gasto (si se conoce)"},
            },
            "required": ["home", "title"],
        },
        handler=_record_home_maintenance,
        args_model=HomeMaintenanceRecordArgs,
    ),
    ToolDefinition(
        name="get_home_status",
        description="Estado de UNA vivienda: pendientes, últimas revisiones, próximas y gasto de los últimos 30 días. "
        "'¿Qué mantenimientos tengo pendientes en casa?' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "home": {
                    "type": ["integer", "string"],
                    "description": "home_id (de list_homes) o nombre",
                },
            },
            "required": ["home"],
        },
        handler=_get_home_status,
        args_model=HomeStatusArgs,
    ),
    ToolDefinition(
        name="get_home_upcoming",
        description="Próximos mantenimientos de TODAS las viviendas (pendientes con fecha prevista), ordenados por "
        "fecha. '¿Qué mantenimientos del hogar se acercan?' → esta tool.",
        input_schema={"type": "object", "properties": {}},
        handler=_get_home_upcoming,
    ),
]
