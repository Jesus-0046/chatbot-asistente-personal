"""Helpers para tests: BD SQLite en memoria y bundling de Services.

Usar `sqlite+aiosqlite://` (sin PostgreSQL ni red) para tests unitarios y
unitarios-de-repositorio. Los tests de `db`/integración requieren PG real
y se saltan si `TEST_DATABASE_URL` no está definido.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles

from asistente.config import Settings

if TYPE_CHECKING:
    from asistente.application.services import Services
    from asistente.persistence.db import Database


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json(element: Any, compiler: Any, **kw: Any) -> str:  # noqa: ANN401
    """SQLite no tiene JSONB: los tests unitarios lo crean como JSON."""
    return "JSON"


def make_in_memory_db(echo: bool = False) -> Database:
    from asistente.persistence.db import Database

    return Database(url="sqlite+aiosqlite:///:memory:", echo=echo)


def make_settings(**overrides: Any) -> Settings:
    # `openai_base_url` vacío = proveedor oficial: los tests no dependen del
    # .env real del desarrollador (que apunta a un Ollama local).
    defaults: dict[str, Any] = {
        "telegram_token": "dummy-token-for-tests",
        "openai_base_url": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_services(db: Database | None = None, **settings_overrides: Any) -> Services:
    """Construye el bundle completo de servicios sobre la BD dada (o en memoria)."""
    from asistente.application.confirmations import ConfirmationService
    from asistente.application.ratelimit import RateLimiter
    from asistente.application.services import Services
    from asistente.domain.accounts import AccountService
    from asistente.domain.agenda import AgendaService
    from asistente.domain.budgets import BudgetService
    from asistente.domain.channels import ChannelIdentityService
    from asistente.domain.debts import DebtService
    from asistente.domain.events import EventService
    from asistente.domain.finance import FinanceService
    from asistente.domain.habits import HabitService
    from asistente.domain.homes import HomeService
    from asistente.domain.intelligence import FinancialIntelligenceService
    from asistente.domain.memory import MemoryService
    from asistente.domain.notes import NoteService
    from asistente.domain.people import PersonService
    from asistente.domain.planned import PlannedTransactionService
    from asistente.domain.proactivity import ProactivityService
    from asistente.domain.recurring import RecurringTransactionService
    from asistente.domain.reminders import ReminderService
    from asistente.domain.savings import SavingsGoalService
    from asistente.domain.shared_expenses import SharedExpenseService
    from asistente.domain.subscriptions import SubscriptionService
    from asistente.domain.summaries import SummaryService
    from asistente.domain.tasks import TaskService
    from asistente.domain.transactions import TransactionService
    from asistente.domain.transfers import TransferService
    from asistente.domain.trips import TripService
    from asistente.domain.users import UserService
    from asistente.domain.vehicles import VehicleService
    from asistente.domain.web_auth import WebAuthService
    from asistente.persistence.repositories import (
        AccountRepository,
        BudgetRepository,
        ChannelIdentityRepository,
        ConfirmationRepository,
        ConversationRepository,
        DebtRepository,
        EventRepository,
        FuelEntryRepository,
        HabitLogRepository,
        HabitRepository,
        HomeMaintenanceRepository,
        HomeRepository,
        MemoryRuleRepository,
        NoteRepository,
        PersonRepository,
        PlannedTransactionRepository,
        ProactiveNotificationRepository,
        RecurrenceRuleRepository,
        RecurringTransactionRepository,
        ReminderRepository,
        SavingsContributionRepository,
        SavingsGoalRepository,
        SharedExpensePaymentRepository,
        SharedExpenseRepository,
        SharedExpenseShareRepository,
        SubscriptionRepository,
        TaskRepository,
        TransactionRepository,
        TransferRepository,
        TripChecklistItemRepository,
        TripRepository,
        UserProactivitySettingsRepository,
        UserRepository,
        VehicleInsuranceRepository,
        VehicleMaintenanceRepository,
        VehicleRepository,
        WebBindingCodeRepository,
        WebCredentialRepository,
        WebSessionRepository,
    )

    database = db if db is not None else make_in_memory_db()
    settings = make_settings(**settings_overrides)
    events = EventRepository(database)
    tasks = TaskRepository(database)
    reminders = ReminderRepository(database)
    rules = RecurrenceRuleRepository(database)
    accounts = AccountRepository(database)
    transactions = TransactionRepository(database)
    transfers = TransferRepository(database)
    budgets = BudgetRepository(database)
    goals = SavingsGoalRepository(database)
    contributions = SavingsContributionRepository(database)
    recurring_repo = RecurringTransactionRepository(database)
    planned_repo = PlannedTransactionRepository(database)
    subs = SubscriptionRepository(database)
    people = PersonRepository(database)
    vehicles = VehicleRepository(database)
    fuel = FuelEntryRepository(database)
    vehicle_maint = VehicleMaintenanceRepository(database)
    vehicle_ins = VehicleInsuranceRepository(database)
    homes = HomeRepository(database)
    home_maint = HomeMaintenanceRepository(database)
    habits = HabitRepository(database)
    habit_logs = HabitLogRepository(database)
    trips = TripRepository(database)
    trip_checklist = TripChecklistItemRepository(database)
    proactivity_settings = UserProactivitySettingsRepository(database)
    proactive_notifications = ProactiveNotificationRepository(database)
    tx_svc = TransactionService(transactions, accounts)
    recurring_svc = RecurringTransactionService(recurring_repo, planned_repo, accounts, subs)
    people_svc = PersonService(people)
    agenda_svc = AgendaService(events, tasks, reminders, rules)
    planned_svc = PlannedTransactionService(planned_repo, tx_svc)
    return Services(
        users=UserService(UserRepository(database)),
        channel_identities=ChannelIdentityService(ChannelIdentityRepository(database)),
        notes=NoteService(NoteRepository(database)),
        memory=MemoryService(MemoryRuleRepository(database)),
        confirmations=ConfirmationService(
            ConfirmationRepository(database), ttl_minutes=settings.confirmation_ttl_minutes
        ),
        conversations=ConversationRepository(database),
        rate_limit=RateLimiter(settings.rate_limit_max_per_minute),
        settings=settings,
        events=EventService(events, rules, reminder_repo=reminders),
        tasks=TaskService(tasks, rules, reminders),
        reminders=ReminderService(reminders, events, tasks, rules),
        agenda=agenda_svc,
        accounts=AccountService(accounts, transactions, transfers),
        transactions=tx_svc,
        transfers=TransferService(transfers, accounts),
        finance=FinanceService(accounts, transactions, transfers),
        budgets=BudgetService(budgets, transactions),
        savings_goals=SavingsGoalService(goals, contributions, transfers, accounts),
        recurring=recurring_svc,
        planned=planned_svc,
        subscriptions=SubscriptionService(subs, recurring_svc),
        people=people_svc,
        debts=DebtService(DebtRepository(database), people),
        shared_expenses=SharedExpenseService(
            SharedExpenseRepository(database),
            SharedExpenseShareRepository(database),
            SharedExpensePaymentRepository(database),
            people,
        ),
        vehicles=VehicleService(vehicles, fuel, vehicle_maint, vehicle_ins, tx_svc),
        homes=HomeService(homes, home_maint, tx_svc),
        habits=HabitService(habits, habit_logs),
        intelligence=FinancialIntelligenceService(
            finance=FinanceService(accounts, transactions, transfers),
            transactions=tx_svc,
            budgets=BudgetService(budgets, transactions),
            savings_goals=SavingsGoalService(goals, contributions, transfers, accounts),
            recurring=recurring_svc,
            planned=planned_svc,
            debts=DebtService(DebtRepository(database), people),
            subscriptions=subs,
        ),
        trips=TripService(trips, trip_checklist, transactions, tasks, events, reminders),
        summaries=SummaryService(agenda_svc, tasks, planned_svc, trips, trip_checklist),
        proactivity=ProactivityService(
            proactivity_settings,
            proactive_notifications,
            agenda_svc,
            tasks,
            trips,
            trip_checklist,
            planned_svc,
        ),
        web_auth=WebAuthService(
            credentials=WebCredentialRepository(database),
            sessions=WebSessionRepository(database),
            binding_codes=WebBindingCodeRepository(database),
            users=UserRepository(database),
            channels=ChannelIdentityRepository(database),
            settings=settings,
        ),
    )


async def make_connected_services() -> Services:
    """Services sobre SQLite en memoria conectada + esquema completo creado.

    Equivalente a `make_services()` pero con `Database.connect()` y
    `Base.metadata.create_all`, para tests que ejercitan repositorios reales
    (Fase 7: confirmaciones, transacciones, tareas, notas...).
    """
    from asistente.persistence.db import Base

    database = make_in_memory_db()
    database.connect()
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return make_services(database)
