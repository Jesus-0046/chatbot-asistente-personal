"""Herramientas de salud y hábitos (Fase 4).

Tools (6): create_habit / list_habits / update_habit / deactivate_habit /
log_habit / get_habit_status.

Semántica:
- Un hábito tiene un objetivo POR PERIODO (`day`/`week`/`month`/`year`),
  opcional: sin objetivo solo se cuentan registros.
- `log_habit` registra "una vez cumplido" o un valor opcional (litros, minutos).
- Cumplimiento = registros del periodo EN CURSO (en la zona horaria del usuario).
- Citas médicas NUNCA van aquí: se registran como eventos con category 'salud'
  (create_event) para que aparezcan en la agenda y con sus recordatorios.
- Nada aquí diagnostica ni recomienda tratamientos: es organización.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.shared.timeutil import to_local
from asistente.tools.agenda import _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money
from asistente.tools.registry import ToolDefinition, ToolResult

HabitRef = int | str


class HabitCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Nombre: 'agua', 'correr', 'meditar'...")
    description: str | None = Field(default=None, max_length=1000)
    unit: str | None = Field(default=None, max_length=32, description="litros, minutos, km...")
    target_per_period: str | None = Field(
        default=None, max_length=40, description="Objetivo por periodo, p. ej. '8' o '150'"
    )
    period: str = Field(default="week", description="day, week, month, year")


class HabitRefArgs(BaseModel):
    habit_id: int = Field(gt=0)


class HabitUpdateArgs(BaseModel):
    habit_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    unit: str | None = Field(default=None, max_length=32)
    target_per_period: str | None = Field(default=None, max_length=40)
    period: str | None = Field(default=None)


class HabitLogArgs(BaseModel):
    habit: HabitRef = Field(description="habit_id (entero) o nombre ('agua')")
    value: str | None = Field(default=None, max_length=40, description="Valor opcional, p. ej. '2' litros")
    when: str | None = Field(default=None, max_length=200, description="Cuándo (por defecto ahora)")
    note: str | None = Field(default=None, max_length=1000)


class HabitStatusArgs(BaseModel):
    habit: HabitRef = Field(description="habit_id (entero) o nombre")


def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


async def _create_habit(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HabitCreateArgs(**kwargs)
    target = None
    if args.target_per_period:
        target = _parse_money(args.target_per_period)
        if isinstance(target, ToolResult):
            return target
    try:
        habit = await ctx.services.habits.create(
            user_id=ctx.user_id,
            name=args.name,
            description=args.description,
            unit=args.unit,
            target_per_period=target,
            period=args.period,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habit_id": habit.id,
            "name": habit.name,
            "unit": habit.unit,
            "target_per_period": fmt_amount(habit.target_per_period) if habit.target_per_period is not None else None,
            "period": habit.period,
            "description_text": (
                f"Hábito '{habit.name}' creado (ID {habit.id}): "
                + (
                    f"objetivo {fmt_amount(habit.target_per_period)} {habit.unit or 'unidades'}/{habit.period}."
                    if habit.target_per_period is not None
                    else "sin objetivo."
                )
            ),
        },
    )


async def _list_habits(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    user_tz = await _user_timezone(ctx)
    try:
        habits = await ctx.services.habits.list_with_progress(ctx.user_id, timezone=user_tz)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habits": [
                {
                    "id": item["habit"].id,
                    "name": item["habit"].name,
                    "unit": item["habit"].unit,
                    "target_per_period": float(item["target"]) if item["target"] is not None else None,
                    "period": item["habit"].period,
                    "count_current_period": item["count"],
                    "achieved": item["achieved"],
                    "active": item["habit"].active,
                }
                for item in habits
            ]
        },
    )


async def _update_habit(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HabitUpdateArgs(**kwargs)
    target = None
    if args.target_per_period is not None:
        target = _parse_money(args.target_per_period)
        if isinstance(target, ToolResult):
            return target
    try:
        habit = await ctx.services.habits.update(
            ctx.user_id,
            args.habit_id,
            name=args.name,
            description=args.description,
            unit=args.unit,
            target_per_period=target,
            period=args.period,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habit_id": habit.id,
            "name": habit.name,
            "description_text": f"Hábito '{habit.name}' actualizado.",
        },
    )


async def _deactivate_habit(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HabitRefArgs(**kwargs)
    try:
        habit = await ctx.services.habits.deactivate(ctx.user_id, args.habit_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habit_id": habit.id,
            "name": habit.name,
            "active": False,
            "description_text": f"Hábito '{habit.name}' desactivado (su histórico se conserva).",
        },
    )


async def _log_habit(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HabitLogArgs(**kwargs)
    try:
        habit = await ctx.services.habits.resolve(ctx.user_id, args.habit)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    value = None
    if args.value:
        value = _parse_money(args.value)
        if isinstance(value, ToolResult):
            return value
    when = None
    if args.when:
        from asistente.tools.finance import _resolve_occurred

        when = _resolve_occurred(ctx, args.when, user_tz)
    if isinstance(when, ToolResult):
        return when
    try:
        result = await ctx.services.habits.log(
            user_id=ctx.user_id,
            habit=habit,
            value=value,
            logged_at=when,
            note=args.note,
            timezone=user_tz,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habit_id": result["habit_id"],
            "log_id": result["log_id"],
            "value": float(result["value"]) if result["value"] is not None else None,
            "logged_at": result["logged_at"].isoformat(),
            "current_period_counts": result["count"],
            "description_text": (
                f"Registrado: '{habit.name}' ({result['count']} en el periodo {result['period']} en curso)."
            ),
        },
    )


async def _get_habit_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = HabitStatusArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        habit = await ctx.services.habits.resolve(ctx.user_id, args.habit)
        status = await ctx.services.habits.get_status(ctx.user_id, habit.id, timezone=user_tz)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "habit": {
                "id": status["habit"].id,
                "name": status["habit"].name,
                "description": status["habit"].description,
                "unit": status["habit"].unit,
                "target_per_period": float(status["target"]) if status["target"] is not None else None,
                "period": status["habit"].period,
            },
            "current_period": {
                "label": status["window"]["label"],
                "start_utc": status["window"]["start_utc"].isoformat(),
                "end_utc": status["window"]["end_utc"].isoformat(),
                "count": status["count"],
                "total": str(status["total"]),
                "target": float(status["target"]) if status["target"] is not None else None,
                "achieved": status["achieved"],
            },
            "last_logs": [
                {
                    "id": log.id,
                    "value": float(log.value) if log.value is not None else None,
                    "logged_at": to_local(log.logged_at, user_tz).isoformat(),
                    "note": log.note,
                }
                for log in status["last_logs"]
            ],
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
HABIT_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_habit",
        description="Crea un hábito con objetivo por periodo (day/week/month/year), p. ej. 'beber 2 litros de agua al "
        "día', 'correr 3 días a la semana'. Si no hay objetivo, solo se cuentan registros.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre: 'agua', 'correr', 'meditar'..."},
                "description": {"type": "string", "default": None},
                "unit": {"type": "string", "default": None, "description": "litros, minutos, km..."},
                "target_per_period": {"type": "string", "default": None, "description": "p. ej. '8' o '150'"},
                "period": {
                    "type": "string",
                    "default": "week",
                    "enum": ["day", "week", "month", "year"],
                },
            },
            "required": ["name"],
        },
        handler=_create_habit,
        args_model=HabitCreateArgs,
    ),
    ToolDefinition(
        name="list_habits",
        description="Lista los hábitos con su objetivo, los registros del periodo en curso y si se ha cumplido. "
        "Úsala para obtener los habit_id; nunca inventes ids.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_habits,
    ),
    ToolDefinition(
        name="update_habit",
        description="Actualiza un hábito existente (nombre, objetivo, periodo...).",
        input_schema={
            "type": "object",
            "properties": {
                "habit_id": {"type": "integer"},
                "name": {"type": "string", "default": None},
                "description": {"type": "string", "default": None},
                "unit": {"type": "string", "default": None},
                "target_per_period": {"type": "string", "default": None},
                "period": {"type": "string", "default": None},
            },
            "required": ["habit_id"],
        },
        handler=_update_habit,
        args_model=HabitUpdateArgs,
    ),
    ToolDefinition(
        name="deactivate_habit",
        description="Desactiva un hábito (deja de contarse). Su histórico NO se borra.",
        input_schema={
            "type": "object",
            "properties": {"habit_id": {"type": "integer"}},
            "required": ["habit_id"],
        },
        handler=_deactivate_habit,
        args_model=HabitRefArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="log_habit",
        description="Registra una vez cumplido el hábito (opcionalmente con un valor, p. ej. 2 litros o 30 minutos). "
        "'Hoy he bebido 2 litros de agua' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "habit": {
                    "type": ["integer", "string"],
                    "description": "habit_id (de list_habits) o nombre ('agua')",
                },
                "value": {"type": "string", "default": None, "description": "Valor opcional, p. ej. '2'"},
                "when": {"type": "string", "default": None, "description": "Cuándo (por defecto ahora)"},
                "note": {"type": "string", "default": None},
            },
            "required": ["habit"],
        },
        handler=_log_habit,
        args_model=HabitLogArgs,
    ),
    ToolDefinition(
        name="get_habit_status",
        description="Progreso de UN hábito en el periodo en curso: registros, objetivo y cumplimiento. "
        "'¿Llevo bien lo del agua esta semana?' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "habit": {
                    "type": ["integer", "string"],
                    "description": "habit_id (de list_habits) o nombre",
                },
            },
            "required": ["habit"],
        },
        handler=_get_habit_status,
        args_model=HabitStatusArgs,
    ),
]
