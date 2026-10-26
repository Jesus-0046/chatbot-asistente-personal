"""Vida cotidiana (Fase 10.0): /api/v1/vehicles, /homes y /habits."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize, serialize_many

router = APIRouter(tags=["life"])


# --------------------------------------------------------------------------- #
# Vehículos                                                                    #
# --------------------------------------------------------------------------- #
class VehicleCreate(BaseModel):
    name: str
    brand: str | None = None
    model: str | None = None
    plate: str | None = None
    year: int | None = None
    color: str | None = None
    fuel_type: str | None = None
    current_odometer: Decimal | None = None
    notes: str | None = None


class VehiclePatch(BaseModel):
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    plate: str | None = None
    year: int | None = None
    color: str | None = None
    fuel_type: str | None = None
    current_odometer: Decimal | None = None
    notes: str | None = None


class VehicleMaintenanceCreate(BaseModel):
    maintenance_type: str = "revision"
    description: str | None = None
    occurred_at: datetime | None = None
    odometer: Decimal | None = None
    cost: Decimal | None = None
    next_due_at: datetime | None = None
    next_due_odometer: Decimal | None = None
    note: str | None = None
    account_id: int | None = None


class FuelCreate(BaseModel):
    amount: Decimal
    occurred_at: datetime | None = None
    liters: Decimal | None = None
    odometer: Decimal | None = None
    fuel_type: str | None = None
    full_tank: bool | None = None
    note: str | None = None
    account_id: int | None = None


class InsuranceCreate(BaseModel):
    company: str | None = None
    policy_number: str | None = None
    insurance_type: str | None = None
    coverage: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    amount: Decimal | None = None
    billing_period: str | None = None
    notes: str | None = None


@router.get("/vehicles/expirations")
async def vehicle_expirations(
    current: CurrentUserDeps,
    services: ServicesDeps,
) -> dict:
    return dict(deep_json(await services.vehicles.get_expirations(current.user_id)))


@router.get("/vehicles")
async def list_vehicles(
    current: CurrentUserDeps,
    services: ServicesDeps,
    only_active: bool = True,
) -> dict:
    items = await services.vehicles.list(current.user_id, only_active=only_active)
    return {"items": serialize_many(items)}


@router.post("/vehicles", status_code=status.HTTP_201_CREATED)
async def create_vehicle(body: VehicleCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    vehicle = await services.vehicles.create(
        user_id=current.user_id,
        name=body.name,
        brand=body.brand,
        model=body.model,
        plate=body.plate,
        year=body.year,
        color=body.color,
        fuel_type=body.fuel_type,
        current_odometer=body.current_odometer,
        notes=body.notes,
    )
    return serialize(vehicle)


@router.get("/vehicles/{vehicle_id}")
async def get_vehicle(vehicle_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.vehicles.get(current.user_id, vehicle_id))


@router.patch("/vehicles/{vehicle_id}")
async def update_vehicle(vehicle_id: int, body: VehiclePatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.vehicles.update(current.user_id, vehicle_id, **fields))


@router.post("/vehicles/{vehicle_id}/deactivate")
async def deactivate_vehicle(vehicle_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.vehicles.deactivate(current.user_id, vehicle_id))


@router.get("/vehicles/{vehicle_id}/status")
async def vehicle_status(vehicle_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.vehicles.get_status(current.user_id, vehicle_id)))


@router.get("/vehicles/{vehicle_id}/maintenance")
async def list_vehicle_maintenance(vehicle_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    vehicle = await services.vehicles.get(current.user_id, vehicle_id)
    items = await services.vehicles.list_maintenance(current.user_id, vehicle)
    return {"items": serialize_many(items)}


@router.post("/vehicles/{vehicle_id}/maintenance", status_code=status.HTTP_201_CREATED)
async def record_vehicle_maintenance(
    vehicle_id: int, body: VehicleMaintenanceCreate, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    vehicle = await services.vehicles.get(current.user_id, vehicle_id)
    result = await services.vehicles.record_maintenance(
        user_id=current.user_id,
        vehicle=vehicle,
        maintenance_type=body.maintenance_type,
        description=body.description,
        occurred_at=body.occurred_at,
        odometer=body.odometer,
        cost=body.cost,
        next_due_at=body.next_due_at,
        next_due_odometer=body.next_due_odometer,
        note=body.note,
        account_id=body.account_id,
    )
    return dict(deep_json(result))


@router.post("/vehicles/{vehicle_id}/fuel", status_code=status.HTTP_201_CREATED)
async def record_fuel(vehicle_id: int, body: FuelCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    vehicle = await services.vehicles.get(current.user_id, vehicle_id)
    result = await services.vehicles.record_fuel(
        user_id=current.user_id,
        vehicle=vehicle,
        amount=body.amount,
        occurred_at=body.occurred_at,
        liters=body.liters,
        odometer=body.odometer,
        fuel_type=body.fuel_type,
        full_tank=body.full_tank,
        note=body.note,
        account_id=body.account_id,
    )
    return dict(deep_json(result))


@router.post("/vehicles/{vehicle_id}/insurance", status_code=status.HTTP_201_CREATED)
async def add_insurance(
    vehicle_id: int, body: InsuranceCreate, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    vehicle = await services.vehicles.get(current.user_id, vehicle_id)
    insurance = await services.vehicles.add_insurance(
        user_id=current.user_id,
        vehicle=vehicle,
        company=body.company,
        policy_number=body.policy_number,
        insurance_type=body.insurance_type,
        coverage=body.coverage,
        start_date=body.start_date,
        end_date=body.end_date,
        amount=body.amount,
        billing_period=body.billing_period,
        notes=body.notes,
    )
    return serialize(insurance)


# --------------------------------------------------------------------------- #
# Viviendas                                                                    #
# --------------------------------------------------------------------------- #
class HomeCreate(BaseModel):
    name: str
    home_type: str | None = None
    address: str | None = None
    notes: str | None = None


class HomePatch(BaseModel):
    name: str | None = None
    home_type: str | None = None
    address: str | None = None
    notes: str | None = None


class HomeMaintenanceCreate(BaseModel):
    title: str
    maintenance_type: str = "revision"
    occurred_at: datetime | None = None
    cost: Decimal | None = None
    next_due_at: datetime | None = None
    note: str | None = None
    account_id: int | None = None


@router.get("/homes/upcoming")
async def home_upcoming(current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.homes.get_upcoming(current.user_id)))


@router.get("/homes")
async def list_homes(
    current: CurrentUserDeps,
    services: ServicesDeps,
    only_active: bool = True,
) -> dict:
    items = await services.homes.list(current.user_id, only_active=only_active)
    return {"items": serialize_many(items)}


@router.post("/homes", status_code=status.HTTP_201_CREATED)
async def create_home(body: HomeCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    home = await services.homes.create(
        user_id=current.user_id,
        name=body.name,
        home_type=body.home_type,
        address=body.address,
        notes=body.notes,
    )
    return serialize(home)


@router.get("/homes/{home_id}")
async def get_home(home_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.homes.get(current.user_id, home_id))


@router.patch("/homes/{home_id}")
async def update_home(home_id: int, body: HomePatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(
        await services.homes.update(
            current.user_id,
            home_id,
            name=body.name,
            home_type=body.home_type,
            address=body.address,
            notes=body.notes,
        )
    )


@router.post("/homes/{home_id}/deactivate")
async def deactivate_home(home_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.homes.deactivate(current.user_id, home_id))


@router.get("/homes/{home_id}/status")
async def home_status(home_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.homes.get_status(current.user_id, home_id)))


@router.get("/homes/{home_id}/maintenance")
async def list_home_maintenance(home_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    home = await services.homes.get(current.user_id, home_id)
    items = await services.homes.list_maintenance(current.user_id, home)
    return {"items": serialize_many(items)}


@router.post("/homes/{home_id}/maintenance", status_code=status.HTTP_201_CREATED)
async def record_home_maintenance(
    home_id: int, body: HomeMaintenanceCreate, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    home = await services.homes.get(current.user_id, home_id)
    result = await services.homes.record_maintenance(
        user_id=current.user_id,
        home=home,
        title=body.title,
        maintenance_type=body.maintenance_type,
        occurred_at=body.occurred_at,
        cost=body.cost,
        next_due_at=body.next_due_at,
        note=body.note,
        account_id=body.account_id,
    )
    return dict(deep_json(result))


# --------------------------------------------------------------------------- #
# Hábitos                                                                      #
# --------------------------------------------------------------------------- #
class HabitCreate(BaseModel):
    name: str
    description: str | None = None
    unit: str | None = None
    target_per_period: Decimal | None = None
    period: str = "week"


class HabitPatch(BaseModel):
    name: str | None = None
    description: str | None = None
    unit: str | None = None
    target_per_period: Decimal | None = None
    period: str | None = None


class HabitLogCreate(BaseModel):
    value: Decimal | None = None
    logged_at: datetime | None = None
    note: str | None = None
    timezone: str | None = None


@router.get("/habits")
async def list_habits(
    current: CurrentUserDeps,
    services: ServicesDeps,
    only_active: bool = True,
) -> dict:
    items = await services.habits.list(current.user_id, only_active=only_active)
    return {"items": serialize_many(items)}


@router.post("/habits", status_code=status.HTTP_201_CREATED)
async def create_habit(body: HabitCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    habit = await services.habits.create(
        user_id=current.user_id,
        name=body.name,
        description=body.description,
        unit=body.unit,
        target_per_period=body.target_per_period,
        period=body.period,
    )
    return serialize(habit)


@router.get("/habits/{habit_id}")
async def get_habit(habit_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.habits.get(current.user_id, habit_id))


@router.patch("/habits/{habit_id}")
async def update_habit(habit_id: int, body: HabitPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.habits.update(current.user_id, habit_id, **fields))


@router.post("/habits/{habit_id}/deactivate")
async def deactivate_habit(habit_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.habits.deactivate(current.user_id, habit_id))


@router.get("/habits/{habit_id}/status")
async def habit_status(habit_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.habits.get_status(current.user_id, habit_id)))


@router.post("/habits/{habit_id}/log", status_code=status.HTTP_201_CREATED)
async def log_habit(habit_id: int, body: HabitLogCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    habit = await services.habits.get(current.user_id, habit_id)
    result = await services.habits.log(
        user_id=current.user_id,
        habit=habit,
        value=body.value,
        logged_at=body.logged_at,
        note=body.note,
        timezone=body.timezone,
    )
    return dict(deep_json(result))
