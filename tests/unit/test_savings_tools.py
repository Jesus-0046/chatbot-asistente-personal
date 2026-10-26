"""Tests de las tools de objetivos de ahorro (Fase 3.2) vía el registro.

Usan un `ToolContext` con servicios reales de dominio sobre los fakes en
memoria: creación de objetivos, contribuciones (simples y vinculadas a
transferencia), estado, cancelación con confirmación, y aislamiento.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakeSavingsContributionRepo,
    FakeSavingsGoalRepo,
    FakeTransferRepo,
)
from tests.unit.helpers import make_settings

from asistente.domain.accounts import AccountService
from asistente.domain.savings import SavingsGoalService
from asistente.domain.transfers import TransferService
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transfers = FakeTransferRepo()
    goals = FakeSavingsGoalRepo()
    contributions = FakeSavingsContributionRepo()
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        accounts=AccountService(accounts, None, transfers),
        transfers=TransferService(transfers, accounts),
        savings_goals=SavingsGoalService(goals, contributions, transfers, accounts),
    )


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=7, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict):
    return await registry.execute(ctx, name, args)


async def test_create_savings_goal(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})

    assert res.ok, res.error
    assert res.data["name"] == "coche"
    assert res.data["target_amount"] == "3000.00"
    assert res.data["currency"] == "EUR"
    assert res.data["deadline"] is None
    assert res.data["status"] == "active"


async def test_create_savings_goal_con_deadline(registry, ctx) -> None:
    res = await _run(
        registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000", "deadline": "2027-08-31"}
    )

    assert res.ok, res.error
    assert res.data["deadline"] == "2027-08-31"


async def test_create_savings_goal_target_cero_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "0"})

    assert not res.ok
    assert "mayor que cero" in res.error


async def test_create_savings_goal_target_negativo_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "-100"})

    assert not res.ok
    assert "mayor que cero" in res.error


async def test_record_savings_contribution(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    goal_id = created.data["id"]

    res = await _run(registry, ctx, "record_savings_contribution", {"goal_id": goal_id, "amount": "200"})

    assert res.ok, res.error
    assert res.data["amount"] == "200.00"
    assert res.data["transfer_id"] is None


async def test_record_savings_contribution_con_nota(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    goal_id = created.data["id"]

    res = await _run(
        registry, ctx, "record_savings_contribution", {"goal_id": goal_id, "amount": "200", "note": "nómina"}
    )

    assert res.ok, res.error
    assert res.data["amount"] == "200.00"


async def test_record_savings_contribution_ajeno_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "record_savings_contribution", {"goal_id": 999, "amount": "100"})

    assert not res.ok
    assert "no existe" in res.error


async def test_get_savings_goal_status(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    goal_id = created.data["id"]
    await _run(registry, ctx, "record_savings_contribution", {"goal_id": goal_id, "amount": "500"})
    await _run(registry, ctx, "record_savings_contribution", {"goal_id": goal_id, "amount": "250"})

    res = await _run(registry, ctx, "get_savings_goal_status", {"goal_id": goal_id})

    assert res.ok, res.error
    assert res.data["contributed_amount"] == "750.00"
    assert res.data["remaining_amount"] == "2250.00"
    assert res.data["progress_percent"] == "25.00"
    assert res.data["status"] == "active"


async def test_list_savings_goals(registry, ctx) -> None:
    await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    await _run(registry, ctx, "create_savings_goal", {"name": "viaje", "target_amount": "1000"})

    res = await _run(registry, ctx, "list_savings_goals", {})

    assert res.ok, res.error
    assert len(res.data["goals"]) == 2


async def test_cancel_savings_goal_requiere_confirmacion(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    goal_id = created.data["id"]

    res = await _run(registry, ctx, "cancel_savings_goal", {"goal_id": goal_id})

    assert not res.ok
    assert res.needs_confirmation is True


async def test_cancel_savings_goal_confirmado(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "3000"})
    goal_id = created.data["id"]

    res = await registry.execute(ctx, "cancel_savings_goal", {"goal_id": goal_id}, confirmed=True)

    assert res.ok, res.error
    assert res.data["status"] == "cancelled"


async def test_contribucion_vinculada_a_transferencia_via_tool(registry, ctx) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    await _run(registry, ctx, "create_account", {"name": "Ahorro", "type": "savings", "initial_balance": "0"})
    accounts = await ctx.services.accounts.list_for_user(7)
    bank, savings = accounts[0], accounts[1]

    transfer_res = await _run(
        registry,
        ctx,
        "create_transfer",
        {"from_account_id": bank.id, "to_account_id": savings.id, "amount": "300"},
    )
    assert transfer_res.ok, transfer_res.error
    transfer_id = transfer_res.data["id"]

    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "1000"})
    goal_id = created.data["id"]

    res = await _run(
        registry,
        ctx,
        "record_savings_contribution",
        {"goal_id": goal_id, "amount": "300", "transfer_id": transfer_id},
    )

    assert res.ok, res.error
    assert res.data["transfer_id"] == transfer_id
    assert res.data["amount"] == "300.00"


async def test_contribucion_importe_no_coincide_con_transferencia_error(registry, ctx) -> None:
    await ctx.services.accounts.create(user_id=7, name="Banco", type="bank", initial_balance=0)
    await ctx.services.accounts.create(user_id=7, name="Ahorro", type="savings", initial_balance=0)
    accounts = await ctx.services.accounts.list_for_user(7)
    bank, savings = accounts[0], accounts[1]
    transfer = await ctx.services.transfers.create(
        user_id=7,
        from_account_id=bank.id,
        to_account_id=savings.id,
        amount=300,
        occurred_at=datetime.now(UTC),
    )
    created = await _run(registry, ctx, "create_savings_goal", {"name": "coche", "target_amount": "1000"})
    goal_id = created.data["id"]

    res = await _run(
        registry,
        ctx,
        "record_savings_contribution",
        {"goal_id": goal_id, "amount": "250", "transfer_id": transfer.id},
    )

    assert not res.ok
    assert "no coincide" in res.error
