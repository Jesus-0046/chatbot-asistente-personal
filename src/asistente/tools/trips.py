"""Herramientas de viajes (Fase 6).

CRUD de viajes, checklist y vínculo financiero/agenda:

- create_trip / list_trips / get_trip / update_trip / cancel_trip / complete_trip
- add_trip_checklist_item / list_trip_checklist / toggle_trip_checklist_item /
  delete_trip_checklist_item
- link_transactions_to_trip / unlink_transactions_from_trip /
  get_trip_financial_summary

Los importes del resumen financiero son SIEMPRE de movimientos reales del
usuario; estas tools nunca crean transacciones. Las fechas se resuelven en la
zona del usuario (lenguaje natural o ISO).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.trips import TripService
from asistente.shared.timeutil import to_local
from asistente.tools.agenda import _resolve_datetime, _ResolvedNode, _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class CreateTripArgs(BaseModel):
    name: str = Field(min_length=1, max_length=300, description="Nombre del viaje")
    destination: str | None = Field(default=None, max_length=300)
    start: str | None = Field(default=None, max_length=200, description='Inicio: ISO o "el 12 de julio"')
    end: str | None = Field(default=None, max_length=200, description="Fin (opcional)")
    notes: str | None = Field(default=None, max_length=4000)
    trip_type: str = Field(default="other", description="leisure|business|family|other")
    timezone: str | None = Field(default=None, max_length=64, description="IANA timezone, p.ej. Europe/Madrid")
    budget: str | None = Field(default=None, description="Presupuesto del viaje (Decimal, puede ser NULL)")


class TripIdArgs(BaseModel):
    trip_id: int = Field(gt=0)


class ListTripsArgs(BaseModel):
    status: str | None = Field(default=None, description="planned|active|completed|cancelled")


class UpdateTripArgs(TripIdArgs):
    name: str | None = Field(default=None, max_length=300)
    destination: str | None = Field(default=None, max_length=300)
    start: str | None = Field(default=None, max_length=200)
    end: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    trip_type: str | None = Field(default=None, description="leisure|business|family|other")
    timezone: str | None = Field(default=None, max_length=64)
    budget: str | None = Field(default=None, description="Presupuesto (Decimal o vacío para NULL)")


class AddChecklistArgs(TripIdArgs):
    text: str = Field(min_length=1, max_length=500)


class ToggleChecklistArgs(BaseModel):
    item_id: int = Field(gt=0)
    is_done: bool


class ChecklistItemArgs(BaseModel):
    item_id: int = Field(gt=0)


class LinkTransactionsArgs(TripIdArgs):
    transaction_ids: list[int] = Field(min_length=1, description="IDs de movimientos del usuario")


class LinkItemArgs(BaseModel):
    trip_id: int = Field(gt=0)
    item_id: int = Field(gt=0)


def _services(ctx: ToolContext) -> TripService:
    return ctx.services.trips


def _trip_dict(trip: Any, user_tz: str) -> dict:
    return {
        "id": trip.id,
        "name": trip.name,
        "destination": trip.destination,
        "status": trip.status,
        "trip_type": trip.trip_type,
        "timezone": trip.timezone,
        "budget": str(trip.budget) if trip.budget is not None else None,
        "start_at": to_local(trip.start_at, user_tz).isoformat() if trip.start_at else None,
        "end_at": to_local(trip.end_at, user_tz).isoformat() if trip.end_at else None,
        "notes": trip.notes,
    }


def _resolve_optional(ctx: ToolContext, expression: str | None, user_tz: str) -> Any:
    if not expression:
        return None
    resolved = _resolve_datetime(ctx, expression, user_tz)
    if isinstance(resolved, ToolResult):
        return resolved
    node = resolved
    assert isinstance(node, _ResolvedNode)
    if node.all_day:
        return node.value.replace(hour=9, minute=0)
    return node.value


async def _create_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = CreateTripArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    start = _resolve_optional(ctx, args.start, user_tz)
    if isinstance(start, ToolResult):
        return start
    end = _resolve_optional(ctx, args.end, user_tz)
    if isinstance(end, ToolResult):
        return end
    budget = None
    if args.budget:
        try:
            from decimal import Decimal

            budget = Decimal(args.budget)
        except Exception:  # noqa: BLE001
            return ToolResult(ok=False, error="El presupuesto debe ser un número decimal válido.")
    try:
        trip = await _services(ctx).create(
            user_id=ctx.user_id,
            name=args.name,
            destination=args.destination,
            start_at=start,
            end_at=end,
            notes=args.notes,
            trip_type=args.trip_type,
            timezone=args.timezone,
            budget=budget,
        )
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"trip": _trip_dict(trip, user_tz)})


async def _list_trips(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ListTripsArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        trips = await _services(ctx).list_trips(ctx.user_id, status=args.status)
    except ValueError as exc:
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"trips": [_trip_dict(t, user_tz) for t in trips], "count": len(trips)})


async def _get_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TripIdArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    service = _services(ctx)
    try:
        detail = await service.get_trip_detail(ctx.user_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    trip = detail["trip"]
    data = _trip_dict(trip, user_tz)
    data["checklist"] = [{"id": i.id, "text": i.text, "is_done": i.is_done} for i in detail["checklist"]]
    data["pending_checklist"] = sum(1 for i in detail["checklist"] if not i.is_done)
    data["linked_tasks"] = [{"id": t.id, "title": t.title, "status": t.status} for t in detail["tasks"]]
    data["linked_events"] = [{"id": e.id, "title": e.title, "status": e.status} for e in detail["events"]]
    data["linked_reminders"] = [{"id": r.id, "message": r.message, "status": r.status} for r in detail["reminders"]]
    data["financial_summary"] = detail["financial_summary"]
    return ToolResult(ok=True, data=data)


async def _update_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = UpdateTripArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    start = _resolve_optional(ctx, args.start, user_tz)
    if isinstance(start, ToolResult):
        return start
    end = _resolve_optional(ctx, args.end, user_tz)
    if isinstance(end, ToolResult):
        return end
    budget = None
    if args.budget is not None:
        if args.budget == "":
            budget = None  # clear budget
        else:
            try:
                from decimal import Decimal

                budget = Decimal(args.budget)
            except Exception:  # noqa: BLE001
                return ToolResult(ok=False, error="El presupuesto debe ser un número decimal válido.")
    try:
        trip = await _services(ctx).update(
            ctx.user_id,
            args.trip_id,
            name=args.name,
            destination=args.destination,
            start_at=start,
            end_at=end,
            notes=args.notes,
            trip_type=args.trip_type,
            timezone=args.timezone,
            budget=budget,
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"trip": _trip_dict(trip, user_tz)})


async def _cancel_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TripIdArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        trip = await _services(ctx).cancel(ctx.user_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"trip": _trip_dict(trip, user_tz)})


async def _complete_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TripIdArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        trip = await _services(ctx).complete(ctx.user_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"trip": _trip_dict(trip, user_tz)})


async def _add_trip_checklist_item(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = AddChecklistArgs(**kwargs)
    try:
        item = await _services(ctx).add_checklist_item(ctx.user_id, args.trip_id, args.text)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"item": {"id": item.id, "text": item.text, "is_done": item.is_done}})


async def _list_trip_checklist(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TripIdArgs(**kwargs)
    try:
        items = await _services(ctx).list_checklist(ctx.user_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(
        ok=True,
        data={
            "items": [{"id": i.id, "text": i.text, "is_done": i.is_done} for i in items],
            "pending": sum(1 for i in items if not i.is_done),
        },
    )


async def _toggle_trip_checklist_item(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ToggleChecklistArgs(**kwargs)
    try:
        item = await _services(ctx).toggle_checklist_item(ctx.user_id, args.item_id, args.is_done)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"item": {"id": item.id, "text": item.text, "is_done": item.is_done}})


async def _delete_trip_checklist_item(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ChecklistItemArgs(**kwargs)
    try:
        await _services(ctx).delete_checklist_item(ctx.user_id, args.item_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"deleted": args.item_id})


async def _link_transactions_to_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkTransactionsArgs(**kwargs)
    try:
        count = await _services(ctx).link_transactions(ctx.user_id, args.trip_id, args.transaction_ids)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"linked": count})


async def _unlink_transactions_from_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkTransactionsArgs(**kwargs)
    try:
        count = await _services(ctx).unlink_transactions(ctx.user_id, args.trip_id, args.transaction_ids)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"unlinked": count})


async def _get_trip_financial_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = TripIdArgs(**kwargs)
    try:
        data = await _services(ctx).financial_summary(ctx.user_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data=data)


async def _link_task_to_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkItemArgs(**kwargs)
    try:
        task = await _services(ctx).link_task(ctx.user_id, args.item_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"task_id": task.id, "trip_id": task.trip_id})


async def _unlink_task_from_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ChecklistItemArgs(**kwargs)
    try:
        task = await _services(ctx).unlink_task(ctx.user_id, args.item_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"task_id": task.id, "trip_id": None})


async def _link_event_to_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkItemArgs(**kwargs)
    try:
        event = await _services(ctx).link_event(ctx.user_id, args.item_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"event_id": event.id, "trip_id": event.trip_id})


async def _unlink_event_from_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ChecklistItemArgs(**kwargs)
    try:
        event = await _services(ctx).unlink_event(ctx.user_id, args.item_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"event_id": event.id, "trip_id": None})


async def _link_reminder_to_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LinkItemArgs(**kwargs)
    try:
        reminder = await _services(ctx).link_reminder(ctx.user_id, args.item_id, args.trip_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"reminder_id": reminder.id, "trip_id": reminder.trip_id})


async def _unlink_reminder_from_trip(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ChecklistItemArgs(**kwargs)
    try:
        reminder = await _services(ctx).unlink_reminder(ctx.user_id, args.item_id)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=str(exc))
    return ToolResult(ok=True, data={"reminder_id": reminder.id, "trip_id": None})


TRIP_TOOLS = [
    ToolDefinition(
        name="create_trip",
        description=(
            "Crea un viaje con nombre y, si se indican, destino, fechas y notas. "
            "'Apunta un viaje a Lisboa del 3 al 10 de agosto' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "destination": {"type": "string"},
                "start": {"type": "string", "description": "Inicio: ISO o lenguaje natural"},
                "end": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name"],
        },
        handler=_create_trip,
        args_model=CreateTripArgs,
    ),
    ToolDefinition(
        name="list_trips",
        description="Lista los viajes del usuario, opcionalmente filtrando por estado. 'Que viajes tengo?' -> esta tool.",
        input_schema={
            "type": "object",
            "properties": {"status": {"type": "string", "description": "planned|active|completed|cancelled"}},
        },
        handler=_list_trips,
        args_model=ListTripsArgs,
    ),
    ToolDefinition(
        name="get_trip",
        description=(
            "Detalle de un viaje: datos, checklist y resumen financiero (gasto/ingreso/net). "
            "'Como va el viaje a Lisboa?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {"trip_id": {"type": "integer"}}, "required": ["trip_id"]},
        handler=_get_trip,
        args_model=TripIdArgs,
    ),
    ToolDefinition(
        name="update_trip",
        description="Actualiza nombre, destino, fechas o notas de un viaje existente.",
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "name": {"type": "string"},
                "destination": {"type": "string"},
                "start": {"type": "string"},
                "end": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["trip_id"],
        },
        handler=_update_trip,
        args_model=UpdateTripArgs,
    ),
    ToolDefinition(
        name="cancel_trip",
        description="Cancela un viaje (soft delete: conserva historico y vinculos).",
        input_schema={"type": "object", "properties": {"trip_id": {"type": "integer"}}, "required": ["trip_id"]},
        handler=_cancel_trip,
        args_model=TripIdArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="complete_trip",
        description="Marca un viaje como completado (realizado).",
        input_schema={"type": "object", "properties": {"trip_id": {"type": "integer"}}, "required": ["trip_id"]},
        handler=_complete_trip,
        args_model=TripIdArgs,
    ),
    ToolDefinition(
        name="add_trip_checklist_item",
        description="Anade un item a la checklist de un viaje. 'Anade pasaporte al viaje' -> esta tool.",
        input_schema={
            "type": "object",
            "properties": {"trip_id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["trip_id", "text"],
        },
        handler=_add_trip_checklist_item,
        args_model=AddChecklistArgs,
    ),
    ToolDefinition(
        name="list_trip_checklist",
        description="Lista la checklist de un viaje con pendientes y completados.",
        input_schema={"type": "object", "properties": {"trip_id": {"type": "integer"}}, "required": ["trip_id"]},
        handler=_list_trip_checklist,
        args_model=TripIdArgs,
    ),
    ToolDefinition(
        name="toggle_trip_checklist_item",
        description="Marca como hecho/pendiente un item de checklist de viaje.",
        input_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer"}, "is_done": {"type": "boolean"}},
            "required": ["item_id", "is_done"],
        },
        handler=_toggle_trip_checklist_item,
        args_model=ToggleChecklistArgs,
    ),
    ToolDefinition(
        name="delete_trip_checklist_item",
        description="Elimina un item de checklist de viaje.",
        input_schema={"type": "object", "properties": {"item_id": {"type": "integer"}}, "required": ["item_id"]},
        handler=_delete_trip_checklist_item,
        args_model=ChecklistItemArgs,
    ),
    ToolDefinition(
        name="link_transactions_to_trip",
        description=(
            "Vincula movimientos reales del usuario a un viaje (para su gasto). No crea movimientos. "
            "'Asocia estos gastos al viaje a Lisboa' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "transaction_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["trip_id", "transaction_ids"],
        },
        handler=_link_transactions_to_trip,
        args_model=LinkTransactionsArgs,
    ),
    ToolDefinition(
        name="unlink_transactions_from_trip",
        description="Desvincula movimientos de un viaje (no los borra).",
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "transaction_ids": {"type": "array", "items": {"type": "integer"}},
            },
            "required": ["trip_id", "transaction_ids"],
        },
        handler=_unlink_transactions_from_trip,
        args_model=LinkTransactionsArgs,
    ),
    ToolDefinition(
        name="get_trip_financial_summary",
        description=(
            "Resumen financiero de un viaje: total gastado, ingresado, neto y gasto por categoria, "
            "calculado solo con movimientos vinculados. 'Cuanto llevo gastado en el viaje?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {"trip_id": {"type": "integer"}}, "required": ["trip_id"]},
        handler=_get_trip_financial_summary,
        args_model=TripIdArgs,
    ),
    ToolDefinition(
        name="link_task_to_trip",
        description="Vincula una tarea existente a un viaje.",
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "item_id": {"type": "integer", "description": "ID de la tarea"},
            },
            "required": ["trip_id", "item_id"],
        },
        handler=_link_task_to_trip,
        args_model=LinkItemArgs,
    ),
    ToolDefinition(
        name="unlink_task_from_trip",
        description="Desvincula una tarea de su viaje.",
        input_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer", "description": "ID de la tarea"}},
            "required": ["item_id"],
        },
        handler=_unlink_task_from_trip,
        args_model=ChecklistItemArgs,
    ),
    ToolDefinition(
        name="link_event_to_trip",
        description="Vincula un evento existente a un viaje.",
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "item_id": {"type": "integer", "description": "ID del evento"},
            },
            "required": ["trip_id", "item_id"],
        },
        handler=_link_event_to_trip,
        args_model=LinkItemArgs,
    ),
    ToolDefinition(
        name="unlink_event_from_trip",
        description="Desvincula un evento de su viaje.",
        input_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer", "description": "ID del evento"}},
            "required": ["item_id"],
        },
        handler=_unlink_event_from_trip,
        args_model=ChecklistItemArgs,
    ),
    ToolDefinition(
        name="link_reminder_to_trip",
        description="Vincula un recordatorio existente a un viaje.",
        input_schema={
            "type": "object",
            "properties": {
                "trip_id": {"type": "integer"},
                "item_id": {"type": "integer", "description": "ID del recordatorio"},
            },
            "required": ["trip_id", "item_id"],
        },
        handler=_link_reminder_to_trip,
        args_model=LinkItemArgs,
    ),
    ToolDefinition(
        name="unlink_reminder_from_trip",
        description="Desvincula un recordatorio de su viaje.",
        input_schema={
            "type": "object",
            "properties": {"item_id": {"type": "integer", "description": "ID del recordatorio"}},
            "required": ["item_id"],
        },
        handler=_unlink_reminder_from_trip,
        args_model=ChecklistItemArgs,
    ),
]
