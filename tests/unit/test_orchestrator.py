"""Tests unitarios del orquestador (canal-agnóstico) con fakes de LLM y servicios."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest
from tests.unit.helpers import make_settings

from asistente.ai.llm import LLMError, LLMResponse, LLMToolCall
from asistente.application.context import InboundMessage
from asistente.application.local_mode import LOCAL_HISTORY_MESSAGES
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolRegistry, ToolResult


@dataclass
class FakeUser:
    id: int


class FakeUsers:
    def __init__(self) -> None:
        self.calls: list[tuple[int, str | None]] = []

    async def ensure_user(
        self,
        *,
        channel: str = "telegram",
        external_user_id: str | None = None,
        telegram_user_id: int | None = None,
        name: str | None = None,
        **_: Any,
    ) -> FakeUser:
        uid = telegram_user_id if telegram_user_id is not None else int(external_user_id or 0)
        self.calls.append((uid, name))
        return FakeUser(id=uid)

    async def timezone_for(self, user_id: int) -> str | None:
        return None


class FakeConversations:
    """Guarda historial por usuario para verificar aislamiento."""

    def __init__(self) -> None:
        self._store: dict[int, list[tuple[str, str]]] = {}
        self.recent_calls: list[int] = []

    async def recent(self, user_id: int, limit: int) -> list[Any]:
        self.recent_calls.append(user_id)
        rows = self._store.get(user_id, [])[-limit:]
        return [_Row(role=r, content=c) for r, c in rows]

    async def add_many(self, user_id: int, rows: list[tuple[str, str, dict]]) -> None:
        stored = self._store.setdefault(user_id, [])
        stored.extend((r, c) for r, c, _ in rows)

    def count(self, user_id: int) -> int:
        return len(self._store.get(user_id, []))


@dataclass
class _Row:
    role: str
    content: str


class FakeMemory:
    async def active_rules(self, user_id: int) -> list[Any]:
        return []


class FakeConfirmations:
    def __init__(self) -> None:
        self.pending: dict | None = None
        self.created: int = 0
        self.confirmed: int = 0
        self.cancelled: int = 0

    async def find_open(self, user_id: int) -> dict | None:
        return self.pending

    async def create_pending(self, **_: Any) -> dict:
        self.created += 1
        return {"id": 1, "topic": "test", "expires_at": "2026-01-01T00:00:00+00:00"}

    async def confirm(self, user_id: int, confirmation_id: int) -> dict | None:
        self.confirmed += 1
        return {"status": "confirmed"}

    async def cancel(self, user_id: int, confirmation_id: int) -> bool:
        self.cancelled += 1
        return True


class FakeNotes:
    async def create(self, **_: Any) -> Any:
        return _Note(id=1, title="t")


@dataclass
class _Note:
    id: int
    title: str


class FakeRateLimiter:
    def __init__(self, allowed: bool = True) -> None:
        self._allowed = allowed

    def is_allowed(self, user_id: int) -> bool:
        return self._allowed


class _Noop:
    async def __getattr__(self, name: str) -> Any:
        async def _method(*args: Any, **kwargs: Any) -> Any:
            return None

        return _method


class FakeEvents(_Noop):
    pass


class FakeTasks(_Noop):
    pass


class FakeReminders(_Noop):
    pass


class FakeAgenda(_Noop):
    pass


class FakeAccounts(_Noop):
    pass


class FakeTransactions(_Noop):
    pass


class FakeTransfers(_Noop):
    pass


class FakeFinance(_Noop):
    pass


class FakeBudgets(_Noop):
    pass


class FakeSavingsGoals(_Noop):
    pass


class FakeRecurring(_Noop):
    pass


class FakePlanned(_Noop):
    pass


class FakeSubscriptions(_Noop):
    pass


class FakePeople(_Noop):
    pass


class FakeDebts(_Noop):
    pass


class FakeSharedExpenses(_Noop):
    pass


class FakeVehicles(_Noop):
    pass


class FakeHomes(_Noop):
    pass


class FakeHabits(_Noop):
    pass


class FakeIntelligence(_Noop):
    pass


class FakeTrips(_Noop):
    pass


class FakeSummaries(_Noop):
    pass


class FakeProactivity(_Noop):
    pass


def _fake_agenda_fields():
    return {
        "events": FakeEvents(),
        "tasks": FakeTasks(),
        "reminders": FakeReminders(),
        "agenda": FakeAgenda(),
        "accounts": FakeAccounts(),
        "transactions": FakeTransactions(),
        "transfers": FakeTransfers(),
        "finance": FakeFinance(),
        "budgets": FakeBudgets(),
        "savings_goals": FakeSavingsGoals(),
        "recurring": FakeRecurring(),
        "planned": FakePlanned(),
        "subscriptions": FakeSubscriptions(),
        "people": FakePeople(),
        "debts": FakeDebts(),
        "shared_expenses": FakeSharedExpenses(),
        "vehicles": FakeVehicles(),
        "homes": FakeHomes(),
        "habits": FakeHabits(),
        "intelligence": FakeIntelligence(),
        "trips": FakeTrips(),
        "summaries": FakeSummaries(),
        "proactivity": FakeProactivity(),
    }


class ScriptedLLM:
    """Devuelve respuestas en secuencia; opcionalmente lanza un error."""

    def __init__(
        self,
        responses: list[LLMResponse] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._responses = list(responses or [])
        self._error = error
        self.calls: int = 0
        self.calls_log: list[dict[str, Any]] = []

    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        self.calls += 1
        self.calls_log.append({"messages": list(messages), "tools": tools})
        if self._error is not None:
            raise self._error
        if not self._responses:
            return LLMResponse(content="ok", tool_calls=[])
        return self._responses.pop(0)


def text_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[])


def tool_response(name: str) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[LLMToolCall(id="call_1", function_name=name, arguments={})],
    )


async def _noop_tool(ctx: ToolContext, **_: Any) -> ToolResult:
    return ToolResult(ok=True, data={"done": True})


def make_services(
    *,
    llm_max_tool_iterations: int = 5,
    rate_limit_enabled: bool = True,
    allowed: bool = True,
    settings_overrides: dict[str, Any] | None = None,
) -> Services:
    settings = make_settings(
        llm_max_tool_iterations=llm_max_tool_iterations,
        rate_limit_enabled=rate_limit_enabled,
        **(settings_overrides or {}),
    )
    return Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=allowed),
        settings=settings,
        **_fake_agenda_fields(),
    )


LOCAL_BASE_URL = "http://localhost:11434/v1"


def make_local_orchestrator(
    *,
    services: Services,
    llm: ScriptedLLM,
    extra_tools: list[ToolDefinition] | None = None,
) -> Orchestrator:
    from asistente.tools import create_tool_registry

    registry = create_tool_registry()
    if extra_tools:
        for tool in extra_tools:
            registry.register(tool)
    return Orchestrator(services=services, llm=llm, tools=registry)


def make_orchestrator(
    *,
    services: Services,
    llm: ScriptedLLM,
) -> Orchestrator:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="noop",
            description="Tool de prueba",
            input_schema={"type": "object", "properties": {}},
            handler=_noop_tool,
        )
    )
    return Orchestrator(services=services, llm=llm, tools=registry)


def inbound(user_id: int, text: str) -> InboundMessage:
    return InboundMessage(user_id=user_id, text=text, display_name="Test")


async def test_mensaje_simple_devuelve_texto_del_llm() -> None:
    services = make_services()
    llm = ScriptedLLM([text_response("Hola 👋")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "hola"))

    assert out.text == "Hola 👋"
    assert services.conversations.count(1) == 2


async def test_tool_call_se_ejecuta_y_el_resultado_llega_al_llm() -> None:
    services = make_services()
    llm = ScriptedLLM([tool_response("noop"), text_response("Listo")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "haz algo"))

    assert llm.calls == 2
    assert out.text == "Listo"


async def test_limitador_de_herramientas_se_respeta() -> None:
    settings = make_settings(
        llm_max_tool_iterations=2,
        rate_limit_enabled=True,
    )

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="noop",
            description="Tool de prueba",
            input_schema={"type": "object", "properties": {}},
            handler=_noop_tool,
        )
    )
    services = make_services(llm_max_tool_iterations=2)
    llm = ScriptedLLM([tool_response("noop"), tool_response("noop"), text_response("no importa")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "repite mucho"))

    assert "detenerme" in out.text
    assert settings.llm_max_tool_iterations == 2


async def test_error_del_llm_devuelve_mensaje_seguro() -> None:
    services = make_services()
    llm = ScriptedLLM([], error=LLMError("boom"))
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "hola"))

    assert "No he podido procesar" in out.text


async def test_excepcion_interna_devuelve_mensaje_generico() -> None:
    services = make_services()

    class ExplodingLLM(ScriptedLLM):
        def chat(self, **_: Any) -> LLMResponse:
            raise RuntimeError("estado raro")

    orch = make_orchestrator(services=services, llm=ExplodingLLM())

    out = await orch.handle(inbound(1, "explota"))

    assert "error interno" in out.text


async def test_confirmacion_pendiente_se_procesa_al_confirmar() -> None:
    settings = make_settings(rate_limit_enabled=True)
    users = FakeUsers()
    conversations = FakeConversations()
    confirmations = FakeConfirmations()
    confirmations.pending = {
        "id": 7,
        "topic": "delete_note",
        "payload": {"tool": "noop", "arguments": {}},
        "proposed_reply": "¿Confirmas?",
    }
    services = Services(  # type: ignore[arg-type]
        users=users,
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=confirmations,
        conversations=conversations,
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        **_fake_agenda_fields(),
    )
    llm = ScriptedLLM([text_response("Hecho.")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "sí"))

    assert confirmations.confirmed == 1
    assert out.text == "Hecho."


async def test_confirmacion_se_cancela_con_no() -> None:
    settings = make_settings(rate_limit_enabled=True)
    confirmations = FakeConfirmations()
    confirmations.pending = {
        "id": 7,
        "topic": "delete_note",
        "payload": {"tool": "noop", "arguments": {}},
        "proposed_reply": "¿Confirmas?",
    }
    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=confirmations,
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        **_fake_agenda_fields(),
    )
    orch = make_orchestrator(services=services, llm=ScriptedLLM())

    out = await orch.handle(inbound(1, "no"))

    assert confirmations.cancelled == 1
    assert "cancelado" in out.text


async def test_aislamiento_entre_usuarios() -> None:
    services = make_services()
    llm = ScriptedLLM([text_response("para ti, 1"), text_response("para ti, 2")])
    orch = make_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "hola"))
    await orch.handle(inbound(2, "hola"))

    assert services.conversations.count(1) == 2
    assert services.conversations.count(2) == 2


async def test_rate_limit_superado_devuelve_mensaje() -> None:
    services = make_services(allowed=False)
    orch = make_orchestrator(services=services, llm=ScriptedLLM())

    out = await orch.handle(inbound(1, "hola"))

    assert "límite de mensajes" in out.text


async def test_rate_limit_desactivado_permite_pasar() -> None:
    settings = make_settings(rate_limit_enabled=False)
    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=False),
        settings=settings,
        **_fake_agenda_fields(),
    )
    orch = make_orchestrator(services=services, llm=ScriptedLLM([text_response("ok")]))

    out = await orch.handle(inbound(1, "hola"))

    assert out.text == "ok"


# --------------------------------------------------------------------------- #
# Fase 2 — flujo de agenda a través del orquestador con la tool real           #
# --------------------------------------------------------------------------- #
@dataclass
class _EventOut:
    id: int
    title: str
    kind: str
    start_at: Any
    end_at: Any | None
    all_day: bool
    location: str | None
    priority: str
    person: str | None
    recurrence_id: int | None


class RecordingEvents(FakeEvents):
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, **kwargs: Any) -> _EventOut:
        self.created.append(kwargs)
        return _EventOut(
            id=1,
            title=kwargs["title"],
            kind=kwargs.get("kind", "event"),
            start_at=kwargs["start_at_utc"],
            end_at=kwargs.get("end_at_utc"),
            all_day=kwargs.get("all_day", False),
            location=kwargs.get("location"),
            priority=kwargs.get("priority", "medium"),
            person=kwargs.get("person"),
            recurrence_id=kwargs.get("recurrence_id"),
        )


def make_agenda_orchestrator(llm: ScriptedLLM) -> tuple[Orchestrator, RecordingEvents]:
    from asistente.tools.agenda import AGENDA_TOOLS
    from asistente.tools.registry import ToolRegistry

    registry = ToolRegistry()
    registry.register(next(t for t in AGENDA_TOOLS if t.name == "create_event"))
    events = RecordingEvents()
    settings = make_settings(default_timezone="Europe/Madrid")
    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        events=events,
        tasks=FakeTasks(),
        reminders=FakeReminders(),
        agenda=FakeAgenda(),
        accounts=FakeAccounts(),
        transactions=FakeTransactions(),
        transfers=FakeTransfers(),
        finance=FakeFinance(),
        budgets=FakeBudgets(),
        savings_goals=FakeSavingsGoals(),
        recurring=FakeRecurring(),
        planned=FakePlanned(),
        subscriptions=FakeSubscriptions(),
        people=FakePeople(),
        debts=FakeDebts(),
        shared_expenses=FakeSharedExpenses(),
        vehicles=FakeVehicles(),
        homes=FakeHomes(),
        habits=FakeHabits(),
        intelligence=FakeIntelligence(),
        trips=FakeTrips(),
        summaries=FakeSummaries(),
        proactivity=FakeProactivity(),
    )
    return Orchestrator(services=services, llm=llm, tools=registry), events


async def test_flujo_agenda_crea_evento_con_la_tool_real() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="c1",
                        function_name="create_event",
                        arguments={"title": "Reunión con Ana", "when": "2026-10-05T10:00:00"},
                    )
                ],
            ),
            text_response("Apuntado en la agenda."),
        ]
    )
    orch, events = make_agenda_orchestrator(llm)

    out = await orch.handle(inbound(1, "apunta una reunión con Ana el 5 de octubre a las 10"))

    assert out.text == "Apuntado en la agenda."
    assert llm.calls == 2
    assert len(events.created) == 1
    assert events.created[0]["user_id"] == 1
    assert events.created[0]["title"] == "Reunión con Ana"


async def test_flujo_agenda_con_error_de_tool_devuelve_explicacion_al_llm() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[LLMToolCall(id="c1", function_name="create_event", arguments={"title": "Sin fecha"})],
            ),
            text_response("Me falta la fecha, pregúntame cuándo."),
        ]
    )
    orch, events = make_agenda_orchestrator(llm)

    out = await orch.handle(inbound(1, "guarda un evento sin fecha"))

    assert out.text == "Me falta la fecha, pregúntame cuándo."
    assert llm.calls == 2
    assert events.created == []


# --------------------------------------------------------------------------- #
# Fase 4 — flujo de coche/casa/salud a través del orquestador con tools reales #
# --------------------------------------------------------------------------- #
def make_fase4_orchestrator(llm: ScriptedLLM):
    from tests.unit.fakes import (
        FakeAccountRepo,
        FakeFuelEntryRepo,
        FakeHabitLogRepo,
        FakeHabitRepo,
        FakeHomeMaintenanceRepo,
        FakeHomeRepo,
        FakeTransactionRepo,
        FakeVehicleInsuranceRepo,
        FakeVehicleMaintenanceRepo,
        FakeVehicleRepo,
    )

    from asistente.domain.habits import HabitService
    from asistente.domain.homes import HomeService
    from asistente.domain.transactions import TransactionService
    from asistente.domain.vehicles import VehicleService
    from asistente.tools.habits import HABIT_TOOLS
    from asistente.tools.home import HOME_TOOLS
    from asistente.tools.registry import ToolRegistry
    from asistente.tools.vehicle import VEHICLE_TOOLS

    registry = ToolRegistry()
    for tool in (*VEHICLE_TOOLS, *HOME_TOOLS, *HABIT_TOOLS):
        registry.register(tool)

    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    tx_svc = TransactionService(transactions, accounts)
    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        accounts=accounts,
        transactions=tx_svc,
        vehicles=VehicleService(
            FakeVehicleRepo(),
            FakeFuelEntryRepo(),
            FakeVehicleMaintenanceRepo(),
            FakeVehicleInsuranceRepo(),
            tx_svc,
        ),
        homes=HomeService(FakeHomeRepo(), FakeHomeMaintenanceRepo(), tx_svc),
        habits=HabitService(FakeHabitRepo(), FakeHabitLogRepo()),
        events=FakeEvents(),
        tasks=FakeTasks(),
        reminders=FakeReminders(),
        agenda=FakeAgenda(),
        transfers=FakeTransfers(),
        finance=FakeFinance(),
        budgets=FakeBudgets(),
        savings_goals=FakeSavingsGoals(),
        recurring=FakeRecurring(),
        planned=FakePlanned(),
        subscriptions=FakeSubscriptions(),
        people=FakePeople(),
        debts=FakeDebts(),
        shared_expenses=FakeSharedExpenses(),
        intelligence=FakeIntelligence(),
        trips=FakeTrips(),
        summaries=FakeSummaries(),
        proactivity=FakeProactivity(),
    )
    return Orchestrator(services=services, llm=llm, tools=registry), services


def _flow_call(name: str, **args: Any) -> ScriptedLLM:
    return ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[LLMToolCall(id="c1", function_name=name, arguments=args)],
            ),
            text_response("Listo"),
        ]
    )


async def test_flujo_fase4_repostaje_crea_movimiento() -> None:
    from decimal import Decimal

    llm = _flow_call("record_fuel", vehicle="Auris", amount="40,00")
    orch, services = make_fase4_orchestrator(llm)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services.vehicles.create(user_id=1, name="Auris")

    out = await orch.handle(inbound(1, "reposté 40 euros en el Auris"))

    assert out.text == "Listo"
    assert llm.calls == 2
    assert len(services.transactions._tx._store) == 1
    assert services.transactions._tx._store[0].merchant == "Combustible"
    assert len(services.vehicles._fuel._store) == 1


async def test_flujo_fase4_combustible_sin_cuenta_devuelve_error_al_llm() -> None:
    llm = _flow_call("record_fuel", vehicle="Auris", amount="40")
    orch, services = make_fase4_orchestrator(llm)
    await services.vehicles.create(user_id=1, name="Auris")

    out = await orch.handle(inbound(1, "reposté 40 euros"))

    assert out.text == "Listo"
    assert llm.calls == 2
    assert len(services.transactions._tx._store) == 0
    assert len(services.vehicles._fuel._store) == 0


async def test_flujo_fase4_desactivar_vehiculo_pide_confirmacion() -> None:
    llm = _flow_call("deactivate_vehicle", vehicle_id=1)
    orch, services = make_fase4_orchestrator(llm)
    vehicle = await services.vehicles.create(user_id=1, name="Auris")

    out = await orch.handle(inbound(1, "quita el Auris"))

    assert services.confirmations.created == 1
    assert out.text.startswith("¿Confirmas")
    vehicle = await services.vehicles._vehicles.find_one(1, vehicle.id)
    assert vehicle is not None
    assert vehicle.active is True
    assert llm.calls == 1


async def test_flujo_fase4_se_confirma_y_desactiva() -> None:
    orch, services = make_fase4_orchestrator(ScriptedLLM([text_response("Vehículo desactivado.")]))
    vehicle = await services.vehicles.create(user_id=1, name="Auris")
    services.confirmations.pending = {
        "id": 7,
        "topic": "deactivate_vehicle",
        "payload": {"tool": "deactivate_vehicle", "arguments": {"vehicle_id": vehicle.id}},
        "proposed_reply": "¿Confirmas?",
    }

    out = await orch.handle(inbound(1, "sí"))

    assert services.confirmations.confirmed == 1
    vehicle = await services.vehicles._vehicles.find_one(1, vehicle.id)
    assert vehicle is not None
    assert vehicle.active is False
    assert out.text == "Vehículo desactivado."


async def test_flujo_fase4_mantenimiento_casa_con_coste_mueve_dinero() -> None:
    from decimal import Decimal

    llm = _flow_call("record_home_maintenance", home="Mi piso", title="Arreglar la caldera", cost="180")
    orch, services = make_fase4_orchestrator(llm)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    home = await services.homes.create(user_id=1, name="Mi piso")

    out = await orch.handle(inbound(1, "he arreglado la caldera por 180 euros"))

    assert out.text == "Listo"
    assert llm.calls == 2
    assert len(services.transactions._tx._store) == 1
    assert services.transactions._tx._store[0].merchant == "Mantenimiento casa"
    hidden = await services.homes._maintenance.list_for_home(1, home.id)
    assert hidden[0].cost == Decimal("180")


async def test_flujo_fase4_log_habit_por_nombre() -> None:
    llm = _flow_call("log_habit", habit="Agua", value="2")
    orch, services = make_fase4_orchestrator(llm)
    await services.habits.create(user_id=1, name="Agua", unit="litros", target_per_period="2", period="day")

    out = await orch.handle(inbound(1, "he bebido 2 litros de agua"))

    assert out.text == "Listo"
    assert llm.calls == 2
    assert len(services.habits._logs._store) == 1


# --------------------------------------------------------------------------- #
# Fase 5 — flujo de inteligencia financiera con tools reales                   #
# --------------------------------------------------------------------------- #
def make_intelligence_orchestrator(llm: ScriptedLLM):
    from tests.unit.fakes import (
        FakeAccountRepo,
        FakeBudgetRepo,
        FakeDebtRepo,
        FakeFuelEntryRepo,
        FakeHabitLogRepo,
        FakeHabitRepo,
        FakeHomeMaintenanceRepo,
        FakeHomeRepo,
        FakePersonRepo,
        FakePlannedTransactionRepo,
        FakeRecurringTransactionRepo,
        FakeSavingsContributionRepo,
        FakeSavingsGoalRepo,
        FakeSubscriptionRepo,
        FakeTransactionRepo,
        FakeTransferRepo,
        FakeVehicleInsuranceRepo,
        FakeVehicleMaintenanceRepo,
        FakeVehicleRepo,
    )

    from asistente.domain.accounts import AccountService
    from asistente.domain.budgets import BudgetService
    from asistente.domain.debts import DebtService
    from asistente.domain.finance import FinanceService
    from asistente.domain.habits import HabitService
    from asistente.domain.homes import HomeService
    from asistente.domain.intelligence import FinancialIntelligenceService
    from asistente.domain.people import PersonService
    from asistente.domain.planned import PlannedTransactionService
    from asistente.domain.recurring import RecurringTransactionService
    from asistente.domain.savings import SavingsGoalService
    from asistente.domain.subscriptions import SubscriptionService
    from asistente.domain.transactions import TransactionService
    from asistente.domain.transfers import TransferService
    from asistente.domain.vehicles import VehicleService
    from asistente.tools.intelligence import INTELLIGENCE_TOOLS
    from asistente.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in INTELLIGENCE_TOOLS:
        registry.register(tool)

    settings = make_settings(default_timezone="Europe/Madrid")
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    transfers = FakeTransferRepo()
    budgets = FakeBudgetRepo()
    goals = FakeSavingsGoalRepo()
    contributions = FakeSavingsContributionRepo()
    recurring_repo = FakeRecurringTransactionRepo(recurring_store=[], planned_store=[])
    planned_repo = FakePlannedTransactionRepo(planned_store=[], tx_store=transactions._store)
    subs = FakeSubscriptionRepo()
    people = FakePersonRepo()

    accounts_svc = AccountService(accounts, transactions, transfers)
    tx_svc = TransactionService(transactions, accounts)
    transfers_svc = TransferService(transfers, accounts)
    recurring_svc = RecurringTransactionService(recurring_repo, planned_repo, accounts, subs)
    planned_svc = PlannedTransactionService(planned_repo, tx_svc)
    finance_svc = FinanceService(accounts, transactions, transfers)
    budgets_svc = BudgetService(budgets, transactions)
    savings_svc = SavingsGoalService(goals, contributions, transfers, accounts)
    people_svc = PersonService(people)
    debts_svc = DebtService(FakeDebtRepo(), people)

    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=FakeConfirmations(),
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        events=FakeEvents(),
        tasks=FakeTasks(),
        reminders=FakeReminders(),
        agenda=FakeAgenda(),
        accounts=accounts_svc,
        transactions=tx_svc,
        transfers=transfers_svc,
        finance=finance_svc,
        budgets=budgets_svc,
        savings_goals=savings_svc,
        recurring=recurring_svc,
        planned=planned_svc,
        subscriptions=SubscriptionService(subs, recurring_svc),
        people=people_svc,
        debts=debts_svc,
        shared_expenses=FakeSharedExpenses(),
        vehicles=VehicleService(
            FakeVehicleRepo(),
            FakeFuelEntryRepo(),
            FakeVehicleMaintenanceRepo(),
            FakeVehicleInsuranceRepo(),
            tx_svc,
        ),
        homes=HomeService(FakeHomeRepo(), FakeHomeMaintenanceRepo(), tx_svc),
        habits=HabitService(FakeHabitRepo(), FakeHabitLogRepo()),
        intelligence=FinancialIntelligenceService(
            finance=finance_svc,
            transactions=tx_svc,
            budgets=budgets_svc,
            savings_goals=savings_svc,
            recurring=recurring_svc,
            planned=planned_svc,
            debts=debts_svc,
            subscriptions=subs,
        ),
        trips=FakeTrips(),
        summaries=FakeSummaries(),
        proactivity=FakeProactivity(),
    )
    return Orchestrator(services=services, llm=llm, tools=registry), services


async def test_flujo_inteligencia_disponible_estimado_se_ejecuta() -> None:
    from decimal import Decimal

    llm = _flow_call("get_estimated_available", period="este mes")
    orch, services = make_intelligence_orchestrator(llm)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))

    out = await orch.handle(inbound(1, "¿cuánto me queda para gastar este mes?"))

    assert out.text == "Listo"
    assert llm.calls == 2


async def test_flujo_inteligencia_detecta_gasto_inusual() -> None:
    from decimal import Decimal

    llm = _flow_call("detect_unusual_expenses", period="este mes")
    orch, services = make_intelligence_orchestrator(llm)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))

    out = await orch.handle(inbound(1, "hay algún gasto raro este mes"))

    assert out.text == "Listo"
    assert llm.calls == 2


async def test_flujo_inteligencia_error_de_periodo_llega_al_llm() -> None:
    llm = ScriptedLLM(
        [
            LLMResponse(
                content=None,
                tool_calls=[
                    LLMToolCall(
                        id="c1", function_name="get_estimated_available", arguments={"period": "la semana próxima"}
                    )
                ],
            ),
            text_response("No he podido resolver ese periodo."),
        ]
    )
    orch, _ = make_intelligence_orchestrator(llm)

    out = await orch.handle(inbound(1, "¿cuánto tendré este mes?"))

    assert out.text == "No he podido resolver ese periodo."
    assert llm.calls == 2


async def test_flujo_inteligencia_no_escribe_en_la_bd() -> None:
    from decimal import Decimal

    llm = _flow_call("get_financial_overview")
    orch, services = make_intelligence_orchestrator(llm)
    await services.accounts.create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("500"))

    out = await orch.handle(inbound(1, "¿cómo están mis finanzas?"))

    assert out.text == "Listo"
    assert len(services.transactions._tx._store) == 0
    assert len(services.transfers._transfers._store) == 0
    assert services.budgets._budgets._store == []


# --------------------------------------------------------------------------- #
# Instrumentación: duración del turno completo y de cada herramienta           #
# --------------------------------------------------------------------------- #
def _last_turn_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord | None:
    for record in reversed(caplog.records):
        if "turn_done" in record.getMessage():
            return record
    return None


async def test_registra_duracion_del_turno(caplog: pytest.LogCaptureFixture) -> None:
    services = make_services()
    orch = make_orchestrator(services=services, llm=ScriptedLLM([text_response("Hola")]))

    with caplog.at_level(logging.INFO, logger="asistente.application.orchestrator"):
        out = await orch.handle(inbound(1, "hola"))

    assert out.text == "Hola"
    record = _last_turn_record(caplog)
    assert record is not None
    text = record.getMessage()
    assert "duration_ms=" in text
    assert "outcome=ok" in text


async def test_registra_duracion_del_turno_en_error_llm(caplog: pytest.LogCaptureFixture) -> None:
    services = make_services()
    orch = make_orchestrator(services=services, llm=ScriptedLLM([], error=LLMError("boom")))

    with caplog.at_level(logging.INFO, logger="asistente.application.orchestrator"):
        out = await orch.handle(inbound(1, "hola"))

    assert "No he podido procesar" in out.text
    record = _last_turn_record(caplog)
    assert record is not None
    text = record.getMessage()
    assert "duration_ms=" in text
    assert "outcome=llm_error" in text


async def test_registra_duracion_de_la_herramienta_en_el_turno(caplog: pytest.LogCaptureFixture) -> None:
    services = make_services()
    orch = make_orchestrator(services=services, llm=ScriptedLLM([tool_response("noop"), text_response("Listo")]))

    with caplog.at_level(logging.INFO, logger="asistente.tools.registry"):
        out = await orch.handle(inbound(1, "haz algo"))

    assert out.text == "Listo"
    assert any("tool=noop" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# Respuesta final: nunca el payload crudo de la herramienta al usuario          #
# --------------------------------------------------------------------------- #
RAW_PENDING_JSON = (
    '{"timezone": "Europe/Madrid", "task_count": 3, "overdue_task_count": 1, '
    '"tasks": [{"id": 7, "title": "Renovar el seguro"}], "planned_payment_count": 1}'
)


async def test_payload_crudo_se_regenera_en_lenguaje_natural() -> None:
    services = make_services()
    llm = ScriptedLLM(
        [
            tool_response("noop"),
            text_response(RAW_PENDING_JSON),
            text_response("Tienes 3 tareas pendientes."),
        ]
    )
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "que me queda pendiente"))

    assert out.text == "Tienes 3 tareas pendientes."
    assert llm.calls == 3
    # La regeneracion se pide SIN herramientas para no volver a copiar el JSON.
    assert llm.calls_log[2]["tools"] == []
    assert "{" not in out.text


async def test_payload_crudo_persistente_usa_resumen_determinista() -> None:
    services = make_services()
    llm = ScriptedLLM(
        [
            tool_response("noop"),
            text_response(RAW_PENDING_JSON),
            text_response(RAW_PENDING_JSON),
        ]
    )
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "que me queda pendiente"))

    assert out.text != RAW_PENDING_JSON
    assert "{" not in out.text
    assert "task_count" not in out.text
    assert llm.calls == 3


async def test_respuesta_normal_no_se_regenera() -> None:
    services = make_services()
    llm = ScriptedLLM([tool_response("noop"), text_response("Listo")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "haz algo"))

    assert out.text == "Listo"
    assert llm.calls == 2


async def test_confirmacion_con_payload_crudo_se_regenera() -> None:
    settings = make_settings(rate_limit_enabled=True)
    confirmations = FakeConfirmations()
    confirmations.pending = {
        "id": 7,
        "topic": "noop",
        "payload": {"tool": "noop", "arguments": {}},
        "proposed_reply": "Confirmas?",
    }
    services = Services(  # type: ignore[arg-type]
        users=FakeUsers(),
        notes=FakeNotes(),
        memory=FakeMemory(),
        confirmations=confirmations,
        conversations=FakeConversations(),
        rate_limit=FakeRateLimiter(allowed=True),
        settings=settings,
        **_fake_agenda_fields(),
    )
    llm = ScriptedLLM([text_response(RAW_PENDING_JSON), text_response("Listo, ya esta.")])
    orch = make_orchestrator(services=services, llm=llm)

    out = await orch.handle(inbound(1, "si"))

    assert out.text == "Listo, ya esta."
    assert llm.calls == 2
    assert llm.calls_log[1]["tools"] == []


# --------------------------------------------------------------------------- #
# Modo local (proveedor con ventana de contexto pequeña)                       #
# --------------------------------------------------------------------------- #


async def test_modo_local_usa_prompt_compacto_y_fecha_en_el_mensaje() -> None:
    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    llm = ScriptedLLM([text_response("Hola")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "que tal"))

    system = llm.calls_log[0]["messages"][0]["content"]
    user = llm.calls_log[0]["messages"][-1]["content"]
    assert "Eres un asistente personal" in system
    assert "Fecha y hora actuales" not in system
    assert user.startswith("[Ahora: ")
    assert user.endswith("que tal")


async def test_modo_oficial_mantiene_prompt_completo_y_todas_las_tools() -> None:
    services = make_services()
    llm = ScriptedLLM([text_response("Hola")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "que tal"))

    system = llm.calls_log[0]["messages"][0]["content"]
    user = llm.calls_log[0]["messages"][-1]["content"]
    assert "Fecha y hora actuales" in system
    assert user == "que tal"
    tools = llm.calls_log[0]["tools"]
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    assert "add_vehicle" in names


async def test_modo_local_anuncia_solo_el_subconjunto_local() -> None:
    from asistente.tools.catalog import CORE_TOOL_NAMES

    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    llm = ScriptedLLM([text_response("Hola")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "que tal"))

    tools = llm.calls_log[0]["tools"]
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    assert names == list(CORE_TOOL_NAMES)
    assert "list_trips" not in names


async def test_modo_local_anuncia_grupos_extra_si_el_mensaje_casa() -> None:
    from asistente.tools.catalog import CORE_TOOL_NAMES

    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    llm = ScriptedLLM([text_response("Hola")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "apunta el ITV del coche"))

    tools = llm.calls_log[0]["tools"]
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    assert names[: len(CORE_TOOL_NAMES)] == list(CORE_TOOL_NAMES)
    assert "add_vehicle" in names
    assert "get_pending_summary" in names
    assert names.index("add_vehicle") > len(CORE_TOOL_NAMES) - 1


async def test_modo_local_recorta_el_resultado_de_la_tool() -> None:
    big = {"items": [{"id": i, "title": "t" * 50} for i in range(40)]}

    async def _big_tool(ctx: ToolContext, **_: Any) -> ToolResult:
        return ToolResult(ok=True, data=big)

    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    llm = ScriptedLLM([tool_response("get_pending_summary"), text_response("Listo")])
    orch = make_local_orchestrator(
        services=services,
        llm=llm,
        extra_tools=[
            ToolDefinition(
                name="get_pending_summary",
                description="Resumen de pendientes",
                input_schema={"type": "object", "properties": {}},
                handler=_big_tool,
            )
        ],
    )

    await orch.handle(inbound(1, "que me queda"))

    tool_msg = [m for m in llm.calls_log[1]["messages"] if m["role"] == "tool"][-1]
    assert len(tool_msg["content"]) <= 1200
    assert "items" in tool_msg["content"]


async def test_modo_oficial_no_recorta_el_resultado_de_la_tool() -> None:
    big = {"items": [{"id": i, "title": "t" * 50} for i in range(40)]}

    async def _big_tool(ctx: ToolContext, **_: Any) -> ToolResult:
        return ToolResult(ok=True, data=big)

    services = make_services()
    llm = ScriptedLLM([tool_response("noop"), text_response("Listo")])
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="noop",
            description="Tool de prueba",
            input_schema={"type": "object", "properties": {}},
            handler=_big_tool,
        )
    )
    orch = Orchestrator(services=services, llm=llm, tools=registry)

    await orch.handle(inbound(1, "que me queda"))

    tool_msg = [m for m in llm.calls_log[1]["messages"] if m["role"] == "tool"][-1]
    assert len(tool_msg["content"]) > 1200


async def test_modo_local_anuncia_el_mismo_conjunto_de_tools_en_cada_turno() -> None:
    # Si el conjunto anunciado cambiase, el prefijo del prompt cambiaria y la
    # cache de prefijo del servidor dejaria de servir (prefill completo).
    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    llm = ScriptedLLM([text_response("Uno"), text_response("Dos")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "primera pregunta"))
    await orch.handle(inbound(1, "segunda pregunta distinta"))

    assert llm.calls_log[0]["tools"] == llm.calls_log[1]["tools"]


async def test_modo_local_recorta_el_historial_que_no_cabe() -> None:
    services = make_services(settings_overrides={"openai_base_url": LOCAL_BASE_URL})
    conversations = services.conversations
    assert isinstance(conversations, FakeConversations)
    for _ in range(8):
        await conversations.add_many(1, [("user", "u" * 900, {}), ("assistant", "a" * 900, {})])
    llm = ScriptedLLM([text_response("Hola")])
    orch = make_local_orchestrator(services=services, llm=llm)

    await orch.handle(inbound(1, "hola"))

    history = [m for m in llm.calls_log[0]["messages"] if m["role"] != "system"]
    assert len(history) <= LOCAL_HISTORY_MESSAGES + 1
