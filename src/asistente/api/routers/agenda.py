"""Agenda (Fase 10.0): /api/v1/events, /api/v1/tasks y /api/v1/reminders."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import serialize, serialize_many

router = APIRouter(tags=["agenda"])

_MAX_LIST = 100


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_LIST))


# --------------------------------------------------------------------------- #
# Eventos                                                                      #
# --------------------------------------------------------------------------- #
class EventCreate(BaseModel):
    title: str
    start_at: datetime
    end_at: datetime | None = None
    timezone: str = "Europe/Madrid"
    description: str | None = None
    location: str | None = None
    kind: str = "event"
    all_day: bool = False
    priority: str = "medium"
    person: str | None = None
    category: str | None = None


class EventPatch(BaseModel):
    title: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    description: str | None = None
    location: str | None = None
    kind: str | None = None
    all_day: bool | None = None
    priority: str | None = None
    person: str | None = None
    category: str | None = None


@router.get("/events")
async def list_events(
    current: CurrentUserDeps,
    services: ServicesDeps,
    limit: int = Query(default=50, ge=1, le=_MAX_LIST),
) -> dict:
    events = await services.events.list_for_user(current.user_id, limit=limit)
    return {"items": serialize_many(events)}


@router.post("/events", status_code=status.HTTP_201_CREATED)
async def create_event(body: EventCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    event = await services.events.create(
        user_id=current.user_id,
        title=body.title,
        start_at_utc=body.start_at,
        end_at_utc=body.end_at,
        timezone=body.timezone,
        description=body.description,
        location=body.location,
        kind=body.kind,
        all_day=body.all_day,
        priority=body.priority,
        person=body.person,
        category=body.category,
    )
    return serialize(event)


@router.get("/events/{event_id}")
async def get_event(event_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.events.get(current.user_id, event_id))


@router.patch("/events/{event_id}")
async def update_event(event_id: int, body: EventPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.events.update(current.user_id, event_id, **fields))


@router.post("/events/{event_id}/cancel")
async def cancel_event(event_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.events.cancel(current.user_id, event_id))


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event(event_id: int, current: CurrentUserDeps, services: ServicesDeps) -> None:
    await services.events.delete(current.user_id, event_id)


# --------------------------------------------------------------------------- #
# Tareas                                                                       #
# --------------------------------------------------------------------------- #
class TaskCreate(BaseModel):
    title: str
    description: str | None = None
    due_at: datetime | None = None
    timezone: str = "Europe/Madrid"
    priority: str = "medium"


class TaskPatch(BaseModel):
    title: str | None = None
    description: str | None = None
    due_at: datetime | None = None
    timezone: str | None = None
    priority: str | None = None


@router.get("/tasks")
async def list_tasks(
    current: CurrentUserDeps,
    services: ServicesDeps,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=_MAX_LIST),
) -> dict:
    tasks = await services.tasks.list(current.user_id, status=status_filter, limit=limit)
    return {"items": serialize_many(tasks)}


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
async def create_task(body: TaskCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    task = await services.tasks.create(
        user_id=current.user_id,
        title=body.title,
        description=body.description,
        due_at_utc=body.due_at,
        timezone=body.timezone,
        priority=body.priority,
    )
    return serialize(task)


@router.get("/tasks/{task_id}")
async def get_task(task_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.tasks.get(current.user_id, task_id))


@router.patch("/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if "due_at" in fields:
        fields["due_at"] = fields.pop("due_at")
    return serialize(await services.tasks.update(current.user_id, task_id, **fields))


@router.post("/tasks/{task_id}/complete")
async def complete_task(task_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.tasks.complete(current.user_id, task_id))


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.tasks.cancel(current.user_id, task_id))


@router.delete("/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, current: CurrentUserDeps, services: ServicesDeps) -> None:
    await services.tasks.delete(current.user_id, task_id)


# --------------------------------------------------------------------------- #
# Recordatorios                                                                #
# --------------------------------------------------------------------------- #
class ReminderCreate(BaseModel):
    message: str | None = None
    remind_at: datetime | None = None
    event_id: int | None = None
    task_id: int | None = None
    offset_minutes: int | None = None


@router.get("/reminders")
async def list_reminders(
    current: CurrentUserDeps,
    services: ServicesDeps,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=200),
) -> dict:
    reminders = await services.reminders.list_all(current.user_id, status=status_filter, limit=limit)
    return {"items": serialize_many(reminders)}


@router.post("/reminders", status_code=status.HTTP_201_CREATED)
async def create_reminder(body: ReminderCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    reminder = await services.reminders.create(
        user_id=current.user_id,
        message=body.message,
        remind_at_utc=body.remind_at,
        event_id=body.event_id,
        task_id=body.task_id,
        offset_minutes=body.offset_minutes,
    )
    return serialize(reminder)


@router.get("/reminders/{reminder_id}")
async def get_reminder(reminder_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.reminders.get(current.user_id, reminder_id))


@router.post("/reminders/{reminder_id}/cancel")
async def cancel_reminder(reminder_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.reminders.cancel(current.user_id, reminder_id))


@router.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(reminder_id: int, current: CurrentUserDeps, services: ServicesDeps) -> None:
    await services.reminders.delete(current.user_id, reminder_id)
