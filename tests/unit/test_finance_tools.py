"""Tests de las tools financieras (Fase 3) ejecutadas vía el registro.

Usan un `ToolContext` con servicios falsos en memoria y verifican los happy
paths, los importes en texto ("28,80" → Decimal) y los errores claros para
que el LLM pregunte (sin cuentas, cuentas desactivadas, importe inválido).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.unit.helpers import make_settings

from asistente.domain.accounts import validate_initial_balance
from asistente.domain.currencies import normalize_currency
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeUsers:
    async def timezone_for(self, user_id: int) -> str | None:
        return None  # → default_timezone del settings ("Europe/Madrid")


class FakeAccounts:
    def __init__(self) -> None:
        self.store: list[_Fake] = []
        self._next = 1

    async def create(
        self, *, user_id, name, type, currency="EUR", initial_balance=Decimal("0"), sort_order=0, **kwargs
    ):
        currency = normalize_currency(currency)
        validate_initial_balance(type, Decimal(str(initial_balance)))
        first = not any(a.user_id == user_id for a in self.store)
        account = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            type=type,
            currency=currency,
            initial_balance=initial_balance,
            is_default=first,
            active=True,
            sort_order=sort_order,
        )
        self.store.append(account)
        self._next += 1
        return account

    async def list_for_user(self, user_id: int, **kwargs):
        return [a for a in self.store if a.user_id == user_id]


class FakeTransactions:
    def __init__(self) -> None:
        self.store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        type,
        amount,
        account_id=None,
        merchant=None,
        category=None,
        occurred_at=None,
        note=None,
        currency="EUR",
        **kwargs,
    ):
        txn = _Fake(
            id=self._next,
            user_id=user_id,
            type=type,
            amount=amount,
            account_id=account_id,
            merchant=merchant,
            category=category or "Otros",
            occurred_at=occurred_at or datetime.now(UTC),
            note=note,
            currency=currency,
        )
        self.store.append(txn)
        self._next += 1
        return txn

    async def list(self, user_id: int, **kwargs):
        return [t for t in self.store if t.user_id == user_id]


class FakeTransfers:
    def __init__(self) -> None:
        self.store: list[_Fake] = []
        self._next = 1

    async def create(
        self, *, user_id, from_account_id, to_account_id, amount, occurred_at=None, note=None, currency="EUR", **kwargs
    ):
        transfer = _Fake(
            id=self._next,
            user_id=user_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=amount,
            occurred_at=occurred_at or datetime.now(UTC),
            note=note,
            currency=currency,
        )
        self.store.append(transfer)
        self._next += 1
        return transfer

    async def list(self, user_id: int, **kwargs):
        return [t for t in self.store if t.user_id == user_id]


class FakeFinance:
    async def balance_overview(self, user_id: int):
        return {
            "accounts": [],
            "patrimonio": Decimal("0.00"),
            "liquido": Decimal("0.00"),
            "deuda_tarjetas": Decimal("0.00"),
        }

    async def spending_summary(self, user_id: int, **kwargs):
        return {
            "total_gastos": Decimal("0.00"),
            "total_ingresos": Decimal("0.00"),
            "por_categoria": [],
            "por_comercio": [],
            "mayor_categoria": None,
            "mayor_comercio": None,
        }


@pytest.fixture
def services():
    settings = make_settings(default_timezone="Europe/Madrid")
    return _Fake(
        users=FakeUsers(),
        settings=settings,
        accounts=FakeAccounts(),
        transactions=FakeTransactions(),
        transfers=FakeTransfers(),
        finance=FakeFinance(),
    )


@pytest.fixture
def ctx(services) -> ToolContext:
    return ToolContext(user_id=7, services=services)


@pytest.fixture
def registry() -> ToolRegistry:
    return create_tool_registry()


async def _run(registry: ToolRegistry, ctx: ToolContext, name: str, args: dict):
    return await registry.execute(ctx, name, args)


async def test_create_account_primera_es_default(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_account", {"name": "Mi Cuenta", "type": "bank", "initial_balance": "1000"})

    assert res.ok, res.error
    assert res.data["is_default"] is True
    assert res.data["initial_balance"] == "1000.00"
    assert services.accounts.store[0].user_id == 7


async def test_create_account_saldo_inicial_cero_por_defecto(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_account", {"name": "Vacaciones", "type": "savings"})

    assert res.ok, res.error
    assert res.data["initial_balance"] == "0.00"
    assert services.accounts.store[0].initial_balance == Decimal("0.00")


async def test_create_account_saldo_inicial_positivo(registry, ctx, services) -> None:
    res = await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "500"})

    assert res.ok, res.error
    assert res.data["initial_balance"] == "500.00"


async def test_create_account_saldo_inicial_negativo_rechazado(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_account", {"name": "Rara", "type": "bank", "initial_balance": "-100"})

    assert not res.ok
    assert "negativo" in res.error


async def test_create_account_tarjeta_credito_saldo_negativo(registry, ctx, services) -> None:
    res = await _run(
        registry, ctx, "create_account", {"name": "Visa", "type": "credit_card", "initial_balance": "-350"}
    )

    assert res.ok, res.error
    assert res.data["initial_balance"] == "-350.00"
    assert services.accounts.store[0].initial_balance == Decimal("-350.00")


async def test_create_account_tarjeta_credito_saldo_positivo_rechazado(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_account", {"name": "Visa", "type": "credit_card", "initial_balance": "350"})

    assert not res.ok
    assert "deuda" in res.error


async def test_create_account_usd_rechazado(registry, ctx) -> None:
    res = await _run(
        registry, ctx, "create_account", {"name": "Banco", "type": "bank", "currency": "USD", "initial_balance": "100"}
    )

    assert not res.ok
    assert "EUR" in res.error
    assert "USD" in res.error


async def test_update_account_usd_rechazado(registry, ctx, services) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    account_id = services.accounts.store[0].id

    res = await _run(registry, ctx, "update_account", {"account_id": account_id, "currency": "USD"})

    assert not res.ok
    assert "EUR" in res.error


async def test_create_expense_importe_en_texto_y_cuenta_auto(registry, ctx, services) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    res = await _run(registry, ctx, "create_expense", {"amount": "28,80", "merchant": "Mercadona"})

    assert res.ok, res.error
    assert res.data["amount"] == "28.80"
    assert res.data["merchant"] == "Mercadona"
    txns = services.transactions.store
    assert Decimal(txns[-1].amount) == Decimal("28.80")
    assert txns[-1].type == "expense"


async def test_create_expense_importe_invalido_error_claro(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_expense", {"amount": "abc"})

    assert not res.ok
    assert "importe" in res.error


async def test_create_expense_importe_no_positivo_error(registry, ctx) -> None:
    res = await _run(registry, ctx, "create_expense", {"amount": "0"})

    assert not res.ok
    assert "mayor que cero" in res.error


async def test_create_income(registry, ctx, services) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    res = await _run(registry, ctx, "create_income", {"amount": "1500", "merchant": "Nómina"})

    assert res.ok, res.error
    assert res.data["type"] == "income"
    assert services.transactions.store[-1].amount == Decimal("1500")


async def test_create_transfer(registry, ctx, services) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    await _run(registry, ctx, "create_account", {"name": "Ahorro", "type": "savings", "initial_balance": "500"})
    bank_id = services.accounts.store[0].id
    savings_id = services.accounts.store[1].id

    res = await _run(
        registry,
        ctx,
        "create_transfer",
        {"from_account_id": bank_id, "to_account_id": savings_id, "amount": "300"},
    )

    assert res.ok, res.error
    assert services.transfers.store[-1].amount == Decimal("300")


async def test_list_transactions_devuelve_movimientos(registry, ctx, services) -> None:
    await _run(registry, ctx, "create_account", {"name": "Banco", "type": "bank", "initial_balance": "1000"})
    await _run(registry, ctx, "create_expense", {"amount": "10", "merchant": "tienda"})
    await _run(registry, ctx, "create_income", {"amount": "100", "merchant": "Nómina"})

    res = await _run(registry, ctx, "list_transactions", {"period": "este mes"})

    assert res.ok, res.error
    assert len(res.data["transactions"]) == 2
    assert res.data["period"] == "este mes"


async def test_get_balance_overview_formatea_importes(registry, ctx) -> None:
    res = await _run(registry, ctx, "get_balance_overview", {})

    assert res.ok, res.error
    assert res.data["patrimonio"] == "0.00"
    assert res.data["liquido"] == "0.00"
    assert res.data["deuda_tarjetas"] == "0.00"


async def test_spending_summary_se_ejecuta(registry, ctx) -> None:
    res = await _run(registry, ctx, "spending_summary", {"period": "este mes"})

    assert res.ok, res.error
    assert res.data["total_gastos"] == "0.00"


async def test_tools_destructivas_no_estan_en_finanzas(registry) -> None:
    names = {t.name for t in registry._tools.values()}
    for forbidden in ("delete_account", "delete_transaction", "delete_transfer", "cancel_transaction"):
        assert forbidden not in names, forbidden


async def test_aislamiento_de_usuarios_en_las_tools(registry, services) -> None:
    ctx_a = ToolContext(user_id=1, services=services)
    ctx_b = ToolContext(user_id=2, services=services)
    await _run(registry, ctx_a, "create_account", {"name": "Banco A", "type": "bank"})

    res_b = await _run(registry, ctx_b, "list_accounts", {})

    assert res_b.ok, res_b.error
    assert res_b.data["accounts"] == []
