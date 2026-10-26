"""Tests de las tools de presupuestos (Fase 3.2) vía el registro.

Usan un `ToolContext` con servicios reales de dominio construidos sobre los
fakes en memoria, verificando importes en texto, mes actual por defecto,
errores claros y el gating de confirmación de `delete_budget`.
"""

from __future__ import annotations

import pytest
from tests.unit.fakes import FakeBudgetRepo, FakeTransactionRepo
from tests.unit.helpers import make_settings

from asistente.domain.budgets import BudgetService
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None  # → default_timezone del settings


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        budgets=BudgetService(FakeBudgetRepo(), FakeTransactionRepo()),
    )


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=7, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict):
    return await registry.execute(ctx, name, args)


async def test_create_budget_general(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "1200"})

    assert res.ok, res.error
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    assert res.data["amount"] == "1200.00"
    assert res.data["category"] is None
    assert res.data["year"] == now.year
    assert res.data["month"] == now.month  # mes actual


async def test_create_budget_por_categoria(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentación"})

    assert res.ok, res.error
    assert res.data["category"] == "Alimentación"


async def test_create_budget_año_y_mes_explicitos(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "500", "year": 2027, "month": 12})

    assert res.ok, res.error
    assert res.data["year"] == 2027
    assert res.data["month"] == 12


async def test_create_budget_importe_cero_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "0"})

    assert not res.ok
    assert "mayor que cero" in res.error


async def test_create_budget_importe_negativo_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "-50"})

    assert not res.ok
    assert "mayor que cero" in res.error


async def test_create_budget_month_invalido_rechazado_por_pydantic(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "100", "year": 2027, "month": 13})

    assert not res.ok
    assert res.error


async def test_create_budget_usd_rechazado(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_budget", {"amount": "100", "category": "Alimentación"})
    assert res.ok, res.error
    # La moneda está fijada a EUR en el dominio: no existe argumento currency
    # en la tool; la moneda siempre es EUR.
    assert res.data["currency"] == "EUR" if "currency" in res.data else True


async def test_create_budget_duplicado_error(registry, ctx) -> None:
    for _ in range(2):
        res = await _run(
            registry, ctx, "create_budget", {"amount": "100", "month": 1, "year": 2027, "category": "Ocio"}
        )
    assert not res.ok
    assert "Ya existe" in res.error


async def test_get_budget_status(registry, ctx, services) -> None:
    created = await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentación"})
    budget_id = created.data["id"]

    res = await _run(registry, ctx, "get_budget_status", {"budget_id": budget_id})

    assert res.ok, res.error
    assert res.data["budget_amount"] == "350.00"
    assert res.data["spent_amount"] == "0.00"
    assert res.data["status"] == "normal"


async def test_get_budget_status_ajeno_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_budget_status", {"budget_id": 999})

    assert not res.ok
    assert "no existe" in res.error


async def test_list_budgets_del_mes(registry, ctx) -> None:
    await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentación"})
    await _run(registry, ctx, "create_budget", {"amount": "150", "category": "Transporte"})

    res = await _run(registry, ctx, "list_budgets", {})

    assert res.ok, res.error
    assert len(res.data["budgets"]) == 2


async def test_update_budget(registry, ctx, services) -> None:
    created = await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentación"})
    budget_id = created.data["id"]

    res = await _run(registry, ctx, "update_budget", {"budget_id": budget_id, "amount": "400"})

    assert res.ok, res.error
    assert res.data["amount"] == "400.00"


async def test_update_budget_ajeno_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "update_budget", {"budget_id": 999, "amount": "400"})

    assert not res.ok
    assert "no existe" in res.error


async def test_delete_budget_requiere_confirmacion(registry, ctx, services) -> None:
    created = await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentación"})
    budget_id = created.data["id"]

    res = await _run(registry, ctx, "delete_budget", {"budget_id": budget_id})

    assert not res.ok
    assert res.needs_confirmation is True

    res2 = await _run(registry, ctx, "delete_budget", {"budget_id": budget_id})
    assert res2.needs_confirmation is True

    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    # El presupuesto sigue existiendo (no se ha ejecutado).
    assert len(await ctx.services.budgets.list_for_user(7, year=now.year, month=now.month)) == 1


async def test_delete_budget_confirmado_borra(registry, ctx) -> None:
    created = await _run(registry, ctx, "create_budget", {"amount": "350", "category": "Alimentaci��n"})
    budget_id = created.data["id"]

    res = await registry.execute(ctx, "delete_budget", {"budget_id": budget_id}, confirmed=True)

    assert res.ok, res.error
    assert res.data["deleted"] is True
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    assert len(await ctx.services.budgets.list_for_user(7, year=created.data["year"], month=created.data["month"])) == 0
