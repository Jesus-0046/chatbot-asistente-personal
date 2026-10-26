"""Personas, deudas y gastos compartidos (Fase 10.0):
/api/v1/people, /debts y /shared-expenses."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize, serialize_many

router = APIRouter(tags=["people"])


class PersonCreate(BaseModel):
    name: str


class PersonPatch(BaseModel):
    name: str


class DebtCreate(BaseModel):
    person_id: int
    direction: str  # owed_to_user (me deben) | user_owes (yo debo)
    amount: Decimal
    reason: str | None = None
    due_at: datetime | None = None


class SharedExpenseParticipant(BaseModel):
    is_user: bool = False
    person_id: int | None = None
    name: str | None = None
    share: Decimal | None = None


class SharedExpensePayment(BaseModel):
    who: str | int
    amount: Decimal


class SharedExpenseCreate(BaseModel):
    title: str
    total_amount: Decimal
    participants: list[SharedExpenseParticipant]
    paid_by: str | int | None = None
    payments: list[SharedExpensePayment] | None = None
    occurred_at: datetime | None = None
    note: str | None = None


# --------------------------------------------------------------------------- #
# Personas                                                                     #
# --------------------------------------------------------------------------- #
@router.get("/people")
async def list_people(
    current: CurrentUserDeps,
    services: ServicesDeps,
    only_active: bool = True,
) -> dict:
    items = await services.people.list(current.user_id, only_active=only_active)
    return {"items": serialize_many(items)}


@router.post("/people", status_code=status.HTTP_201_CREATED)
async def create_person(body: PersonCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.people.create(user_id=current.user_id, name=body.name))


@router.patch("/people/{person_id}")
async def update_person(person_id: int, body: PersonPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.people.update(current.user_id, person_id, name=body.name))


@router.post("/people/{person_id}/deactivate")
async def deactivate_person(person_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.people.deactivate(current.user_id, person_id))


# --------------------------------------------------------------------------- #
# Deudas                                                                       #
# --------------------------------------------------------------------------- #
@router.get("/debts")
async def list_debts(
    current: CurrentUserDeps,
    services: ServicesDeps,
    person_id: int | None = None,
) -> dict:
    data = await services.debts.get_status(current.user_id, person_id=person_id)
    return dict(deep_json(data))


@router.get("/people/{person_id}/debts")
async def list_person_debts(
    current: CurrentUserDeps,
    services: ServicesDeps,
    person_id: int,
    status_filter: str | None = None,
) -> dict:
    items = await services.debts.list_for_person(current.user_id, person_id, status=status_filter)
    return {"items": serialize_many(items)}


@router.post("/debts", status_code=status.HTTP_201_CREATED)
async def create_debt(body: DebtCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    debt = await services.debts.create(
        user_id=current.user_id,
        person_id=body.person_id,
        direction=body.direction,
        amount=body.amount,
        reason=body.reason,
        due_at=body.due_at,
    )
    return serialize(debt)


@router.post("/debts/{debt_id}/settle")
async def settle_debt(debt_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.debts.settle(current.user_id, debt_id))


# --------------------------------------------------------------------------- #
# Gastos compartidos                                                           #
# --------------------------------------------------------------------------- #
@router.get("/shared-expenses")
async def list_shared_expenses(
    current: CurrentUserDeps,
    services: ServicesDeps,
    person_id: int | None = None,
    occurred_from: datetime | None = None,
    occurred_until: datetime | None = None,
) -> dict:
    data = await services.shared_expenses.get_status(
        current.user_id,
        person_id=person_id,
        occurred_from=occurred_from,
        occurred_until=occurred_until,
    )
    return dict(deep_json(data))


@router.post("/shared-expenses", status_code=status.HTTP_201_CREATED)
async def create_shared_expense(body: SharedExpenseCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    participants = [p.model_dump(exclude_none=True) for p in body.participants]
    payments = [{"who": p.who, "amount": p.amount} for p in body.payments] if body.payments else None
    result = await services.shared_expenses.create(
        user_id=current.user_id,
        title=body.title,
        total_amount=body.total_amount,
        participants=participants,
        paid_by=body.paid_by,
        payments=payments,
        occurred_at=body.occurred_at,
        note=body.note,
    )
    return dict(deep_json(result))
