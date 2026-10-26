"""Viajes (Fase 10.0): /api/v1/trips (CRUD, detalle, finanzas, checklist)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize, serialize_many

router = APIRouter(tags=["trips"])


class TripCreate(BaseModel):
    name: str
    destination: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    notes: str | None = None
    status: str = "planned"
    trip_type: str = "other"
    timezone: str | None = None
    budget: Decimal | None = None


class TripPatch(BaseModel):
    name: str | None = None
    destination: str | None = None
    start_at: datetime | None = None
    end_at: datetime | None = None
    notes: str | None = None
    trip_type: str | None = None
    timezone: str | None = None
    budget: Decimal | None = None


class ChecklistItemCreate(BaseModel):
    text: str


@router.get("/trips")
async def list_trips(
    current: CurrentUserDeps,
    services: ServicesDeps,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = 100,
) -> dict:
    items = await services.trips.list_trips(current.user_id, status=status_filter, limit=min(limit, 200))
    return {"items": serialize_many(items)}


@router.post("/trips", status_code=status.HTTP_201_CREATED)
async def create_trip(body: TripCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    trip = await services.trips.create(
        user_id=current.user_id,
        name=body.name,
        destination=body.destination,
        start_at=body.start_at,
        end_at=body.end_at,
        notes=body.notes,
        status=body.status,
        trip_type=body.trip_type,
        timezone=body.timezone,
        budget=body.budget,
    )
    return serialize(trip)


@router.get("/trips/{trip_id}")
async def get_trip(trip_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.trips.get(current.user_id, trip_id))


@router.get("/trips/{trip_id}/detail")
async def get_trip_detail(trip_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.trips.get_trip_detail(current.user_id, trip_id)))


@router.get("/trips/{trip_id}/finance")
async def trip_financial_summary(trip_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.trips.financial_summary(current.user_id, trip_id)))


@router.get("/trips/{trip_id}/transactions")
async def trip_transactions(trip_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    items = await services.trips.get_trip_transactions(current.user_id, trip_id)
    return {"items": serialize_many(items)}


@router.get("/trips/{trip_id}/checklist")
async def trip_checklist(trip_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    items = await services.trips.list_checklist(current.user_id, trip_id)
    return {"items": serialize_many(items)}


@router.post("/trips/{trip_id}/checklist", status_code=status.HTTP_201_CREATED)
async def add_checklist_item(
    trip_id: int, body: ChecklistItemCreate, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    return serialize(await services.trips.add_checklist_item(current.user_id, trip_id, body.text))


@router.delete("/trips/checklist-items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_checklist_item(item_id: int, current: CurrentUserDeps, services: ServicesDeps) -> None:
    await services.trips.delete_checklist_item(current.user_id, item_id)


@router.patch("/trips/{trip_id}")
async def update_trip(trip_id: int, body: TripPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.trips.update(current.user_id, trip_id, **fields))
