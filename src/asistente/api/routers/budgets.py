"""Presupuestos y objetivos de ahorro (Fase 10.0):
/api/v1/budgets y /api/v1/savings-goals."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, status
from pydantic import BaseModel

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize, serialize_many

router = APIRouter(tags=["budgets"])


class BudgetCreate(BaseModel):
    name: str
    amount: Decimal
    year: int
    month: int
    category: str | None = None


class BudgetPatch(BaseModel):
    name: str | None = None
    amount: Decimal | None = None
    category: str | None = None


class SavingsGoalCreate(BaseModel):
    name: str
    target_amount: Decimal
    deadline: datetime | None = None


class ContributionCreate(BaseModel):
    amount: Decimal
    contributed_at: datetime | None = None
    note: str | None = None


@router.get("/budgets")
async def list_budgets(
    current: CurrentUserDeps,
    services: ServicesDeps,
    year: int | None = None,
    month: int | None = None,
    with_status: bool = False,
) -> dict:
    if with_status:
        items = await services.budgets.list_with_status(current.user_id, year=year, month=month)
        return {"items": [dict(deep_json(item)) for item in items]}
    return {"items": serialize_many(await services.budgets.list_for_user(current.user_id, year=year, month=month))}


@router.post("/budgets", status_code=status.HTTP_201_CREATED)
async def create_budget(body: BudgetCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    budget = await services.budgets.create(
        user_id=current.user_id,
        name=body.name,
        amount=body.amount,
        year=body.year,
        month=body.month,
        category=body.category,
    )
    return serialize(budget)


@router.get("/budgets/{budget_id}")
async def get_budget(budget_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.budgets.get_status(current.user_id, budget_id)))


@router.patch("/budgets/{budget_id}")
async def update_budget(budget_id: int, body: BudgetPatch, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    fields = body.model_dump(exclude_unset=True)
    return serialize(await services.budgets.update(current.user_id, budget_id, **fields))


@router.delete("/budgets/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_budget(budget_id: int, current: CurrentUserDeps, services: ServicesDeps) -> None:
    await services.budgets.delete(current.user_id, budget_id)


@router.get("/budgets/{budget_id}/status")
async def budget_status(budget_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    data = await services.budgets.get_status(current.user_id, budget_id)
    return dict(deep_json(data))


# --------------------------------------------------------------------------- #
# Objetivos de ahorro                                                          #
# --------------------------------------------------------------------------- #
@router.get("/savings-goals")
async def list_savings_goals(
    current: CurrentUserDeps,
    services: ServicesDeps,
    status_filter: str | None = None,
) -> dict:
    items = await services.savings_goals.list_goals(current.user_id, status=status_filter)
    return {"items": serialize_many(items)}


@router.post("/savings-goals", status_code=status.HTTP_201_CREATED)
async def create_savings_goal(body: SavingsGoalCreate, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    goal = await services.savings_goals.create_goal(
        user_id=current.user_id,
        name=body.name,
        target_amount=body.target_amount,
        deadline=body.deadline,
    )
    return serialize(goal)


@router.get("/savings-goals/{goal_id}")
async def get_savings_goal(goal_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.savings_goals.get_goal_status(current.user_id, goal_id)))


@router.get("/savings-goals/{goal_id}/status")
async def savings_goal_status(goal_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return dict(deep_json(await services.savings_goals.get_goal_status(current.user_id, goal_id)))


@router.get("/savings-goals/{goal_id}/contributions")
async def list_contributions(goal_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    items = await services.savings_goals.list_contributions(current.user_id, goal_id)
    return {"items": serialize_many(items)}


@router.post("/savings-goals/{goal_id}/contributions", status_code=status.HTTP_201_CREATED)
async def create_contribution(
    goal_id: int, body: ContributionCreate, current: CurrentUserDeps, services: ServicesDeps
) -> dict:
    item = await services.savings_goals.record_contribution(
        user_id=current.user_id,
        goal_id=goal_id,
        amount=body.amount,
        contributed_at=body.contributed_at,
        note=body.note,
    )
    return serialize(item)


@router.post("/savings-goals/{goal_id}/cancel")
async def cancel_savings_goal(goal_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    return serialize(await services.savings_goals.cancel_goal(current.user_id, goal_id))
