"""Herramientas de agenda, tareas y recordatorios (Fase 2).

Tools expuestas al LLM (todas aíslan por `user_id` del contexto autenticado;
ninguna toca la BD directamente):

- create_event / get_event / list_events / update_event / delete_event
- create_task / list_tasks / update_task / complete_task / cancel_task
- create_reminder / list_reminders / cancel_reminder
- get_agenda

Reglas clave:
- Las fechas del usuario se interpretan SIEMPRE en su zona horaria; el
  servicio de dominio persiste en UTC. El LLM puede pasar lenguaje natural
  ("mañana a las 10", "el viernes") o ISO 8601.
- Prioridades: se clasifican con `parse_priority` ("urgente"→high, "normal"→
  medium, "poca importancia"→low).
- Recurrencias: naturales ("todos los martes", "cada 2 semanas") → regla
  única + una fila base. Sin explosion de filas.
- Ambigüedad real de fecha → se responde con las opciones para que el LLM
  pregunte al usuario (nunca se inventa una fecha).
- Acciones destructivas (delete_event, cancel_task, cancel_reminder) llevan
  `requires_confirmation` y pasan por `ConfirmationService` (Fase 1).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, Field

from asistente.shared.dateparse import DateParseError, NaturalDateParser
from asistente.shared.language import RecurrencePhrase, parse_priority, parse_recurrence
from asistente.shared.recurrence import RecurrenceSpec
from asistente.shared.timeutil import resolve_timezone, to_utc
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #
class EventId(BaseModel):
    event_id: int = Field(gt=0)


class TaskId(BaseModel):
    task_id: int = Field(gt=0)


class ReminderId(BaseModel):
    reminder_id: int = Field(gt=0)


class CreateEventArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    when: str = Field(min_length=1, max_length=200, description="Fecha y hora (lenguaje natural o ISO 8601)")
    end_when: str | None = Field(
        default=None, max_length=200, description="Fecha/hora de fin de la ocurrencia (opcional)"
    )
    all_day: bool | None = Field(default=None, description="Fijar como día completo (sin hora)")
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=300)
    priority: str = Field(default="medium", max_length=50, description="urgente/importante/normal/baja, etc.")
    kind: Literal["event", "birthday", "important_date"] = "event"
    person: str | None = Field(default=None, max_length=200, description="Persona (para cumpleaños/fechas importantes)")
    category: str | None = Field(
        default=None,
        max_length=32,
        description="Etiqueta libre del evento. Pasos 'salud': usa 'salud' para citas médicas.",
    )
    recurrence: str | None = Field(default=None, max_length=200, description="todos los martes, cada día, cada año...")
    recurrence_until: str | None = Field(
        default=None,
        max_length=200,
        description="Fin explícito de la SERIE recurrente (natural o ISO): hasta cuándo se repite. Sin él la serie es indefinida.",
    )
    timezone: str | None = Field(default=None, max_length=64)


class UpdateEventArgs(BaseModel):
    event_id: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=300)
    when: str | None = Field(default=None, max_length=200)
    end_when: str | None = Field(default=None, max_length=200)
    all_day: bool | None = None
    description: str | None = Field(default=None, max_length=5000)
    location: str | None = Field(default=None, max_length=300)
    priority: str | None = Field(default=None, max_length=50)
    kind: Literal["event", "birthday", "important_date"] | None = None
    person: str | None = Field(default=None, max_length=200)
    category: str | None = Field(default=None, max_length=32)


class EventListArgs(BaseModel):
    period: str = Field(
        default="hoy",
        max_length=200,
        description="hoy, mañana, el viernes, esta semana, este mes, los próximos 7 días...",
    )


class CreateTaskArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_when: str | None = Field(default=None, max_length=200, description="Cuándo vence (opcional)")
    description: str | None = Field(default=None, max_length=5000)
    priority: str = Field(default="medium", max_length=50)
    recurrence: str | None = Field(default=None, max_length=200)


class UpdateTaskArgs(BaseModel):
    task_id: int = Field(gt=0)
    title: str | None = Field(default=None, max_length=300)
    due_when: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=5000)
    priority: str | None = Field(default=None, max_length=50)


class TaskListArgs(BaseModel):
    status: Literal["pending", "completed", "cancelled"] | None = None
    limit: int = Field(default=50, ge=1, le=100)


class CreateReminderArgs(BaseModel):
    message: str | None = Field(default=None, max_length=1000, description="Texto del recordatorio (opcional)")
    event_id: int | None = Field(default=None, gt=0)
    task_id: int | None = Field(default=None, gt=0)
    when: str | None = Field(default=None, max_length=200, description="Cuándo avisar (solo recordatorio suelto)")
    offset_minutes: int | None = Field(
        default=None, ge=0, description="Minutos antes del evento/tarea (por defecto 60)"
    )


class ReminderListArgs(BaseModel):
    status: Literal["pending", "sent", "cancelled"] | None = None
    limit: int = Field(default=30, ge=1, le=100)


class AgendaPeriod(BaseModel):
    period: str = Field(default="hoy", max_length=200)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _parse_iso(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt
    except ValueError:
        return None


class _ResolvedNode:
    def __init__(self, value: datetime, all_day: bool, tz: str, candidates: list[datetime] | None = None):
        self.value = value
        self.all_day = all_day
        self.timezone = tz
        self.candidates = candidates or []


async def _user_timezone(ctx: ToolContext) -> str:
    stored = await ctx.services.users.timezone_for(ctx.user_id)
    return stored or ctx.services.settings.default_timezone


def _now_local(user_tz: str) -> datetime:
    from asistente.shared.timeutil import now_in_timezone

    return now_in_timezone(user_tz)


def _parser(ctx: ToolContext) -> NaturalDateParser:
    return NaturalDateParser(default_hour=ctx.services.settings.default_reminder_hour)


def _resolve_datetime(ctx: ToolContext, expression: str, user_tz: str) -> ToolResult | _ResolvedNode:
    """Resuelve una expresión (ISO o lenguaje natural) en la zona del usuario.

    Devuelve un `_ResolvedNode` o un `ToolResult(ok=False)` si es ambiguo.
    También acepta una fecha ISO sin hora ("2026-10-30") → las 00:00 locales
    y `all_day=True` (útil para fines de serie `recurrence_until`).
    """
    expr = (expression or "").strip()
    if not expr:
        return ToolResult(ok=False, error="Falta la fecha/hora.")
    if (dt := _parse_iso(expr)) is not None:
        if dt.tzinfo is None:
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0:
                local = dt.replace(tzinfo=resolve_timezone(user_tz))
                return _ResolvedNode(local, all_day=True, tz=user_tz)
            dt = dt.replace(tzinfo=resolve_timezone(user_tz))
        local = dt.astimezone(_tz(user_tz))
        return _ResolvedNode(local, all_day=False, tz=user_tz)
    try:
        parsed = _parser(ctx).parse(expr, now_local=_now_local(user_tz), timezone=user_tz)
    except DateParseError as exc:
        return ToolResult(ok=False, error=str(exc))
    if parsed.candidates:
        formatted = [c.strftime("%d/%m/%Y %H:%M") for c in parsed.candidates]
        return ToolResult(
            ok=False,
            error=(
                f"La fecha '{expr}' es ambigua: puede ser hoy/alcanzada o {', '.join(formatted)}. "
                "Pregunta al usuario cuál quiere."
            ),
        )
    return _ResolvedNode(parsed.value, parsed.all_day, user_tz)


def _tz(name: str):
    from zoneinfo import ZoneInfo

    return ZoneInfo(name)


def _base_for_recurrence(phrase: RecurrencePhrase, now_local: datetime) -> datetime:
    """Línea base cuando el usuario pide recurrencia sin fecha inicial."""
    if phrase.freq == "weekly" and phrase.days_of_week:
        today = now_local.weekday()
        for d in sorted(phrase.days_of_week):
            ahead = (d - today) % 7
            if ahead != 0:
                return now_local + timedelta(days=ahead)
        return now_local + timedelta(days=7)
    return now_local


def _recurrence_spec(
    ctx: ToolContext,
    phrase: RecurrencePhrase,
    base: datetime,
    user_tz: str,
    *,
    until_utc: datetime | None = None,
) -> ToolResult | RecurrenceSpec:
    if phrase.freq == "weekly" and phrase.days_of_week and base.weekday() not in phrase.days_of_week:
        days_name = ", ".join(str(d) for d in sorted(phrase.days_of_week))
        return ToolResult(
            ok=False,
            error=f"La fecha elegida ({base:%d/%m/%Y}, {('lun,mar,mié,jue,vie,sáb,dom')[base.weekday()]}) "
            f"no cae en los días de la recurrencia semanal indicada (días {days_name}). Pide al usuario confirmar la fecha.",
        )
    return RecurrenceSpec(
        id=None,
        freq=phrase.freq,
        interval=phrase.interval,
        days_of_week=phrase.days_of_week,
        start_at_utc=to_utc(base),
        end_at_utc=until_utc,
        timezone=user_tz,
    )


def _event_to_dict(event, user_tz: str) -> dict:
    return {
        "id": event.id,
        "title": event.title,
        "kind": event.kind,
        "start_at": event.start_at.astimezone(_tz(user_tz)).isoformat(),
        "end_at": event.end_at.astimezone(_tz(user_tz)).isoformat() if event.end_at else None,
        "all_day": event.all_day,
        "location": event.location,
        "priority": event.priority,
        "person": event.person,
        "category": event.category,
        "recurring": event.recurrence_id is not None,
    }


# --------------------------------------------------------------------------- #
# Eventos                                                                     #
# --------------------------------------------------------------------------- #
async def _create_event(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = CreateEventArgs(**kwargs)
    user_tz = args.timezone or await _user_timezone(ctx)
    resolved = _resolve_datetime(ctx, args.when, user_tz)
    if not isinstance(resolved, _ResolvedNode):
        return resolved
    start_local = resolved.value

    end_spec = None
    if args.end_when:
        resolved_end = _resolve_datetime(ctx, args.end_when, user_tz)
        if not isinstance(resolved_end, _ResolvedNode):
            return resolved_end
        if resolved_end.value <= start_local:
            return ToolResult(ok=False, error="La fecha de fin debe ser posterior al inicio.")
        end_spec = to_utc(resolved_end.value)

    all_day = args.all_day if args.all_day is not None else resolved.all_day
    if args.kind in ("birthday", "important_date"):
        all_day = True

    rule_spec = None
    if args.recurrence:
        phrase = parse_recurrence(args.recurrence)
        if phrase is None:
            return ToolResult(ok=False, error=f"No reconozco la recurrencia: '{args.recurrence}'.")

        until_utc: datetime | None = None
        if args.recurrence_until:
            until_resolved = _resolve_datetime(ctx, args.recurrence_until, user_tz)
            if not isinstance(until_resolved, _ResolvedNode):
                return until_resolved
            if until_resolved.value <= start_local:
                return ToolResult(
                    ok=False, error="La fecha de fin de la serie recurrente debe ser posterior al inicio del evento."
                )
            until_utc = to_utc(until_resolved.value)

        rule_or_err = _recurrence_spec(ctx, phrase, start_local, user_tz, until_utc=until_utc)
        if not isinstance(rule_or_err, RecurrenceSpec):
            return rule_or_err
        rule_spec = rule_or_err

    event = await ctx.services.events.create(
        user_id=ctx.user_id,
        title=args.title,
        start_at_utc=to_utc(start_local),
        end_at_utc=end_spec,
        timezone=user_tz,
        description=args.description,
        location=args.location,
        kind=args.kind,
        all_day=all_day,
        priority=parse_priority(args.priority),
        person=args.person,
        category=((args.category or "").strip()[:32] or None),
        rule=rule_spec,
    )
    return ToolResult(ok=True, data=_event_to_dict(event, user_tz))


async def _get_event(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = EventId(**kwargs)
    event = await ctx.services.events.get(ctx.user_id, args.event_id)
    user_tz = await _user_timezone(ctx)
    return ToolResult(ok=True, data=_event_to_dict(event, user_tz))


async def _list_events(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = EventListArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    data = await ctx.services.agenda.during_period(
        ctx.user_id, start_utc, end_utc, timezone=user_tz, include_cancelled=True
    )
    return ToolResult(ok=True, data={"period": args.period, "events": data["events"]})


async def _update_event(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpdateEventArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    fields: dict = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.description is not None:
        fields["description"] = args.description
    if args.location is not None:
        fields["location"] = args.location
    if args.priority is not None:
        fields["priority"] = parse_priority(args.priority)
    if args.kind is not None:
        fields["kind"] = args.kind
    if args.person is not None:
        fields["person"] = args.person
    if args.category is not None:
        fields["category"] = (args.category or "").strip()[:32] or None
    if args.all_day is not None:
        fields["all_day"] = args.all_day
    if args.when:
        resolved = _resolve_datetime(ctx, args.when, user_tz)
        if not isinstance(resolved, _ResolvedNode):
            return resolved
        fields["start_at"] = to_utc(resolved.value)
        fields["all_day"] = resolved.all_day
    if args.end_when:
        resolved_end = _resolve_datetime(ctx, args.end_when, user_tz)
        if not isinstance(resolved_end, _ResolvedNode):
            return resolved_end
        fields["end_at"] = to_utc(resolved_end.value)
        if "start_at" in fields and fields["end_at"] <= fields["start_at"]:
            return ToolResult(ok=False, error="La fecha de fin debe ser posterior al inicio.")
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    event = await ctx.services.events.update(ctx.user_id, args.event_id, **fields)
    return ToolResult(ok=True, data=_event_to_dict(event, user_tz))


async def _delete_event(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = EventId(**kwargs)
    await ctx.services.events.delete(ctx.user_id, args.event_id)
    return ToolResult(ok=True, data={"deleted": True, "event_id": args.event_id})


# --------------------------------------------------------------------------- #
# Tareas                                                                      #
# --------------------------------------------------------------------------- #
async def _create_task(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = CreateTaskArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    due_utc: datetime | None = None
    rule_spec = None
    if args.due_when:
        resolved = _resolve_datetime(ctx, args.due_when, user_tz)
        if not isinstance(resolved, _ResolvedNode):
            return resolved
        due_utc = to_utc(resolved.value)
    if args.recurrence:
        phrase = parse_recurrence(args.recurrence)
        if phrase is None:
            return ToolResult(ok=False, error=f"No reconozco la recurrencia: '{args.recurrence}'.")
        base = due_utc if due_utc is not None else _base_for_recurrence(phrase, _now_local(user_tz))
        rule_or_err = _recurrence_spec(ctx, phrase, base, user_tz)
        if not isinstance(rule_or_err, RecurrenceSpec):
            return rule_or_err
        rule_spec = rule_or_err
        due_utc = to_utc(base)
    task = await ctx.services.tasks.create(
        user_id=ctx.user_id,
        title=args.title,
        description=args.description,
        due_at_utc=due_utc,
        timezone=user_tz,
        priority=parse_priority(args.priority),
        rule=rule_spec,
    )
    return ToolResult(
        ok=True,
        data={
            "id": task.id,
            "title": task.title,
            "due_at": task.due_at.astimezone(_tz(user_tz)).isoformat() if task.due_at else None,
            "priority": task.priority,
            "recurring": task.recurrence_id is not None,
        },
    )


async def _list_tasks(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TaskListArgs(**kwargs)
    tasks = await ctx.services.tasks.list(ctx.user_id, status=args.status, limit=args.limit)
    user_tz = await _user_timezone(ctx)
    return ToolResult(
        ok=True,
        data={
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "due_at": t.due_at.astimezone(_tz(user_tz)).isoformat() if t.due_at else None,
                    "priority": t.priority,
                    "status": t.status,
                }
                for t in tasks
            ]
        },
    )


async def _update_task(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpdateTaskArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    fields: dict = {}
    if args.title is not None:
        fields["title"] = args.title
    if args.description is not None:
        fields["description"] = args.description
    if args.priority is not None:
        fields["priority"] = parse_priority(args.priority)
    if args.due_when:
        resolved = _resolve_datetime(ctx, args.due_when, user_tz)
        if not isinstance(resolved, _ResolvedNode):
            return resolved
        fields["due_at"] = to_utc(resolved.value)
    if not fields:
        return ToolResult(ok=False, error="No hay nada que actualizar.")
    task = await ctx.services.tasks.update(ctx.user_id, args.task_id, **fields)
    return ToolResult(ok=True, data={"id": task.id, "title": task.title, "status": task.status})


async def _complete_task(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TaskId(**kwargs)
    task = await ctx.services.tasks.complete(ctx.user_id, args.task_id)
    return ToolResult(ok=True, data={"id": task.id, "status": task.status, "recurring": task.recurrence_id is not None})


async def _cancel_task(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TaskId(**kwargs)
    task = await ctx.services.tasks.cancel(ctx.user_id, args.task_id)
    return ToolResult(ok=True, data={"id": task.id, "status": task.status})


# --------------------------------------------------------------------------- #
# Recordatorios                                                               #
# --------------------------------------------------------------------------- #
async def _create_reminder(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = CreateReminderArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    if args.event_id and args.task_id:
        return ToolResult(ok=False, error="No puede vincular a un evento y una tarea a la vez.")
    offset = (
        args.offset_minutes
        if args.offset_minutes is not None
        else ctx.services.settings.default_reminder_offset_minutes
    )
    if args.event_id or args.task_id:
        reminder = await ctx.services.reminders.create(
            user_id=ctx.user_id,
            message=args.message,
            event_id=args.event_id,
            task_id=args.task_id,
            offset_minutes=offset,
        )
    else:
        if not args.when:
            return ToolResult(ok=False, error="Indica a qué hora debe sonar el recordatorio (cuándo).")
        resolved = _resolve_datetime(ctx, args.when, user_tz)
        if not isinstance(resolved, _ResolvedNode):
            return resolved
        reminder = await ctx.services.reminders.create(
            user_id=ctx.user_id,
            message=args.message,
            remind_at_utc=to_utc(resolved.value),
        )
    return ToolResult(
        ok=True,
        data={
            "id": reminder.id,
            "message": reminder.message,
            "remind_at": reminder.remind_at.astimezone(_tz(user_tz)).isoformat(),
            "linked_event_id": reminder.event_id,
            "linked_task_id": reminder.task_id,
        },
    )


async def _list_reminders(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ReminderListArgs(**kwargs)
    reminders = await ctx.services.reminders.list_all(ctx.user_id, status=args.status, limit=args.limit)
    user_tz = await _user_timezone(ctx)
    return ToolResult(
        ok=True,
        data={
            "reminders": [
                {
                    "id": r.id,
                    "message": r.message,
                    "remind_at": r.remind_at.astimezone(_tz(user_tz)).isoformat(),
                    "status": r.status,
                }
                for r in reminders
            ]
        },
    )


async def _cancel_reminder(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ReminderId(**kwargs)
    reminder = await ctx.services.reminders.cancel(ctx.user_id, args.reminder_id)
    return ToolResult(ok=True, data={"id": reminder.id, "status": reminder.status})


# --------------------------------------------------------------------------- #
# Agenda                                                                      #
# --------------------------------------------------------------------------- #
def _resolve_period(ctx: ToolContext, period_text: str, user_tz: str) -> ToolResult | tuple[datetime, datetime]:
    """Devuelve (start_utc, end_utc) para un periodo natural o fecha concreta."""
    period = (period_text or "").strip().lower()
    now_local = _now_local(user_tz)

    def day(dt: datetime) -> tuple[datetime, datetime]:
        start = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        return to_utc(start), to_utc(start + timedelta(days=1))

    if period in ("", "hoy", "este día"):
        return day(now_local)
    if period == "mañana":
        return day(now_local + timedelta(days=1))
    if period == "pasado mañana":
        return day(now_local + timedelta(days=2))
    if period in ("esta semana", "semana", "la semana"):
        monday = now_local - timedelta(days=now_local.weekday())
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return to_utc(start), to_utc(start + timedelta(days=7))
    if period in ("la semana que viene", "semana que viene"):
        monday = now_local - timedelta(days=now_local.weekday()) + timedelta(days=7)
        start = monday.replace(hour=0, minute=0, second=0, microsecond=0)
        return to_utc(start), to_utc(start + timedelta(days=7))
    if period in ("este mes", "mes"):
        start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        return to_utc(start), to_utc(nxt)
    if period == "el próximo mes" or period == "el mes que viene":
        start = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if start.month == 12:
            start = start.replace(year=start.year + 1, month=1)
        else:
            start = start.replace(month=start.month + 1)
        if start.month == 12:
            nxt = start.replace(year=start.year + 1, month=1)
        else:
            nxt = start.replace(month=start.month + 1)
        return to_utc(start), to_utc(nxt)
    import re as _re

    if m := _re.match(r"los\s+próximos?\s+(\d+)\s+(\días?|dias?|semanas?|horas?)\s*$", period):
        amount = int(m.group(1))
        unit = m.group(2)
        if unit.startswith("día"):
            end = now_local + timedelta(days=amount)
        elif unit.startswith("semana"):
            end = now_local + timedelta(weeks=amount)
        else:
            end = now_local + timedelta(hours=amount)
        return to_utc(now_local), to_utc(end)

    # Fecha concreta: día completo, o hora concreta (una hora tras inicio).
    resolved = _resolve_datetime(ctx, period_text, user_tz)
    if not isinstance(resolved, _ResolvedNode):
        return resolved
    if resolved.all_day:
        return day(resolved.value)
    return to_utc(resolved.value), to_utc(resolved.value + timedelta(hours=1))


async def _get_agenda(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AgendaPeriod(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    data = await ctx.services.agenda.during_period(ctx.user_id, start_utc, end_utc, timezone=user_tz)
    data["period"] = args.period
    return ToolResult(ok=True, data=data)


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
AGENDA_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="create_event",
        description=(
            "Crea una cita/reunión/cumpleaños/fecha importante. "
            "Recibe la fecha en lenguaje natural ('mañana a las 10', 'el viernes', 'el 2 de octubre') y una recurrencia opcional. "
            "Las citas MÉDICAS ('ir al dentista el 3 de marzo') se crean con category='salud' y, si es un evento repetido "
            "(dieta, medicación), se usa 'recurrence'. Tareas de casa o del coche NO son eventos: usa list_homes únicamente "
            "para mantenimientos domésticos."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título del evento"},
                "when": {"type": "string", "description": "Cuándo (natural o ISO)"},
                "end_when": {"type": "string", "description": "Fin (natural o ISO)", "default": None},
                "all_day": {"type": "boolean", "description": "Día completo", "default": None},
                "description": {"type": "string", "default": None},
                "location": {"type": "string", "default": None},
                "priority": {"type": "string", "default": "medium", "description": "urgente/importante/normal/baja"},
                "kind": {"type": "string", "enum": ["event", "birthday", "important_date"], "default": "event"},
                "person": {"type": "string", "description": "Persona (cumpleaños)", "default": None},
                "category": {
                    "type": "string",
                    "description": "Etiqueta libre. Las citas médicas usan 'salud'.",
                    "default": None,
                },
                "recurrence": {
                    "type": "string",
                    "description": "todos los martes, cada día, cada año",
                    "default": None,
                },
                "recurrence_until": {
                    "type": "string",
                    "description": "Fin de la serie recurrente (fecha u hora natural/ISO), ej. '2026-10-30'",
                    "default": None,
                },
                "timezone": {"type": "string", "default": None},
            },
            "required": ["title", "when"],
        },
        handler=_create_event,
        args_model=CreateEventArgs,
    ),
    ToolDefinition(
        name="get_event",
        description="Obtiene el detalle de un evento por su id.",
        input_schema={"type": "object", "properties": {"event_id": {"type": "integer"}}, "required": ["event_id"]},
        handler=_get_event,
        args_model=EventId,
    ),
    ToolDefinition(
        name="list_events",
        description="Lista los eventos de un periodo (hoy, esta semana, este mes...). Incluye ocurrencias de recurrentes.",
        input_schema={"type": "object", "properties": {"period": {"type": "string", "default": "hoy"}}},
        handler=_list_events,
        args_model=EventListArgs,
    ),
    ToolDefinition(
        name="update_event",
        description="Actualiza campos de un evento existente (título, cuándo, fin, hora, ubicación, prioridad, etc.).",
        input_schema={
            "type": "object",
            "properties": {
                "event_id": {"type": "integer"},
                "title": {"type": "string", "default": None},
                "when": {"type": "string", "default": None},
                "end_when": {"type": "string", "default": None},
                "all_day": {"type": "boolean", "default": None},
                "description": {"type": "string", "default": None},
                "location": {"type": "string", "default": None},
                "priority": {"type": "string", "default": None},
                "kind": {"type": "string", "default": None},
                "person": {"type": "string", "default": None},
                "category": {
                    "type": "string",
                    "description": "Etiqueta libre. Las citas médicas usan 'salud'.",
                    "default": None,
                },
            },
            "required": ["event_id"],
        },
        handler=_update_event,
        args_model=UpdateEventArgs,
    ),
    ToolDefinition(
        name="delete_event",
        description="Elimina un evento de forma permanente (requiere confirmación).",
        input_schema={"type": "object", "properties": {"event_id": {"type": "integer"}}, "required": ["event_id"]},
        handler=_delete_event,
        requires_confirmation=True,
        args_model=EventId,
    ),
    ToolDefinition(
        name="create_task",
        description="Crea una tarea con vencimiento opcional (natural) y prioridad.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "due_when": {"type": "string", "description": "Cuándo vence (natural o ISO)", "default": None},
                "description": {"type": "string", "default": None},
                "priority": {"type": "string", "default": "medium"},
                "recurrence": {"type": "string", "default": None},
            },
            "required": ["title"],
        },
        handler=_create_task,
        args_model=CreateTaskArgs,
    ),
    ToolDefinition(
        name="list_tasks",
        description="Lista las tareas del usuario (pendientes/completadas/canceladas).",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "completed", "cancelled"], "default": None},
                "limit": {"type": "integer", "default": 50},
            },
        },
        handler=_list_tasks,
        args_model=TaskListArgs,
    ),
    ToolDefinition(
        name="update_task",
        description="Actualiza una tarea (título, vencimiento, descripción, prioridad).",
        input_schema={
            "type": "object",
            "properties": {
                "task_id": {"type": "integer"},
                "title": {"type": "string", "default": None},
                "due_when": {"type": "string", "default": None},
                "description": {"type": "string", "default": None},
                "priority": {"type": "string", "default": None},
            },
            "required": ["task_id"],
        },
        handler=_update_task,
        args_model=UpdateTaskArgs,
    ),
    ToolDefinition(
        name="complete_task",
        description="Marca una tarea como completada (las recurrentes pasan a la siguiente ocurrencia).",
        input_schema={"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
        handler=_complete_task,
        args_model=TaskId,
    ),
    ToolDefinition(
        name="cancel_task",
        description="Cancela una tarea (requiere confirmación).",
        input_schema={"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
        handler=_cancel_task,
        requires_confirmation=True,
        args_model=TaskId,
    ),
    ToolDefinition(
        name="create_reminder",
        description=(
            "Crea un recordatorio: vinculado a un evento (event_id) o tarea (task_id) con offset_minutes antes, "
            "o suelto con 'when'. Los recordatorios a recurrentes se repiten en cada ocurrencia."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string", "default": None},
                "event_id": {"type": "integer", "default": None},
                "task_id": {"type": "integer", "default": None},
                "when": {"type": "string", "default": None, "description": "Cuándo avisar (sueltos)"},
                "offset_minutes": {"type": "integer", "default": None, "description": "Minutos antes"},
            },
        },
        handler=_create_reminder,
        args_model=CreateReminderArgs,
    ),
    ToolDefinition(
        name="list_reminders",
        description="Lista recordatorios del usuario.",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["pending", "sent", "cancelled"], "default": None},
                "limit": {"type": "integer", "default": 30},
            },
        },
        handler=_list_reminders,
        args_model=ReminderListArgs,
    ),
    ToolDefinition(
        name="cancel_reminder",
        description="Cancela un recordatorio pendiente (requiere confirmación).",
        input_schema={
            "type": "object",
            "properties": {"reminder_id": {"type": "integer"}},
            "required": ["reminder_id"],
        },
        handler=_cancel_reminder,
        requires_confirmation=True,
        args_model=ReminderId,
    ),
    ToolDefinition(
        name="get_agenda",
        description=(
            "Vista de agenda de un periodo (eventos + tareas pendientes + recordatorios). "
            "Usa fechas naturales: hoy, mañana, el viernes, esta semana, este mes, los próximos 7 días."
        ),
        input_schema={"type": "object", "properties": {"period": {"type": "string", "default": "hoy"}}},
        handler=_get_agenda,
        args_model=AgendaPeriod,
    ),
]
