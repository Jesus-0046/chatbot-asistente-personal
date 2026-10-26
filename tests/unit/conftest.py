"""Fixtures compartidos por los tests de dominio (Fase 2/2.1/3 y 3.3).

`services` construye los servicios de negocio sobre los fakes en memoria
(véase `tests.unit.fakes`), tal y como se cablean en producción pero sin BD
ni canales (a diferencia de `helpers.make_services`, que usa aiosqlite).
"""

from __future__ import annotations

import pytest
from tests.unit.fakes import (
    FakeAccountRepo,
    FakeBudgetRepo,
    FakeDebtRepo,
    FakeEventRepo,
    FakeFuelEntryRepo,
    FakeHabitLogRepo,
    FakeHabitRepo,
    FakeHomeMaintenanceRepo,
    FakeHomeRepo,
    FakePersonRepo,
    FakePlannedTransactionRepo,
    FakeProactiveNotificationRepo,
    FakeRecurringTransactionRepo,
    FakeReminderRepo,
    FakeRuleRepo,
    FakeSavingsContributionRepo,
    FakeSavingsGoalRepo,
    FakeSharedExpensePaymentRepo,
    FakeSharedExpenseRepo,
    FakeSharedExpenseShareRepo,
    FakeSubscriptionRepo,
    FakeTaskRepo,
    FakeTransactionRepo,
    FakeTransferRepo,
    FakeTripChecklistItemRepo,
    FakeTripRepo,
    FakeUserProactivitySettingsRepo,
    FakeVehicleInsuranceRepo,
    FakeVehicleMaintenanceRepo,
    FakeVehicleRepo,
)

from asistente.domain.accounts import AccountService
from asistente.domain.agenda import AgendaService
from asistente.domain.budgets import BudgetService
from asistente.domain.debts import DebtService
from asistente.domain.events import EventService
from asistente.domain.finance import FinanceService
from asistente.domain.habits import HabitService
from asistente.domain.homes import HomeService
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
from asistente.domain.vehicles import VehicleService


@pytest.fixture
def services():
    rules = FakeRuleRepo()
    events = FakeEventRepo()
    tasks = FakeTaskRepo()
    reminders = FakeReminderRepo()
    accounts = FakeAccountRepo()
    transactions = FakeTransactionRepo()
    transfers = FakeTransferRepo()
    budgets = FakeBudgetRepo()
    goals = FakeSavingsGoalRepo()
    contributions = FakeSavingsContributionRepo()
    recurring_store: list = []
    planned_store: list = []
    recurring_repo = FakeRecurringTransactionRepo(recurring_store=recurring_store, planned_store=planned_store)
    planned_repo = FakePlannedTransactionRepo(planned_store=planned_store, tx_store=transactions._store)
    subscription_repo = FakeSubscriptionRepo()
    tx_svc = TransactionService(transactions, accounts)
    recurring_svc = RecurringTransactionService(recurring_repo, planned_repo, accounts, subscription_repo)
    planned_svc = PlannedTransactionService(planned_repo, tx_svc)
    subscription_svc = SubscriptionService(subscription_repo, recurring_svc)
    people = FakePersonRepo()
    share_store: dict = {}
    payment_store: dict = {}
    shared_expense_repo = FakeSharedExpenseRepo(share_store=share_store, payment_store=payment_store)
    share_repo = FakeSharedExpenseShareRepo(store=share_store)
    payment_repo = FakeSharedExpensePaymentRepo(store=payment_store)
    people_svc = PersonService(people)
    debts_svc = DebtService(FakeDebtRepo(), people)
    shared_svc = SharedExpenseService(shared_expense_repo, share_repo, payment_repo, people)
    vehicles_repo = FakeVehicleRepo()
    fuel_repo = FakeFuelEntryRepo()
    vehicle_maint_repo = FakeVehicleMaintenanceRepo()
    vehicle_ins_repo = FakeVehicleInsuranceRepo()
    homes_repo = FakeHomeRepo()
    home_maint_repo = FakeHomeMaintenanceRepo()
    habits_repo = FakeHabitRepo()
    habit_logs_repo = FakeHabitLogRepo()
    trips_repo = FakeTripRepo()
    trip_checklist_repo = FakeTripChecklistItemRepo()
    proactivity_settings_repo = FakeUserProactivitySettingsRepo()
    proactive_notifications_repo = FakeProactiveNotificationRepo()
    agenda_svc = AgendaService(events, tasks, reminders, rules)
    return {
        "rules": rules,
        "events": EventService(events, rules, reminder_repo=reminders),
        "tasks": TaskService(tasks, rules, reminders),
        "reminders": ReminderService(reminders, events, tasks, rules),
        "agenda": agenda_svc,
        "accounts": AccountService(accounts, transactions, transfers),
        "transactions": tx_svc,
        "transfers": TransferService(transfers, accounts),
        "finance": FinanceService(accounts, transactions, transfers),
        "budgets": BudgetService(budgets, transactions),
        "savings_goals": SavingsGoalService(goals, contributions, transfers, accounts),
        "recurring_store": recurring_store,
        "planned_store": planned_store,
        "recurring_repo": recurring_repo,
        "planned_repo": planned_repo,
        "subscription_repo": subscription_repo,
        "recurring": recurring_svc,
        "planned": planned_svc,
        "subscriptions": subscription_svc,
        "people": people,
        "debts": debts_svc,
        "shared_expenses": shared_svc,
        "people_svc": people_svc,
        "vehicles": VehicleService(vehicles_repo, fuel_repo, vehicle_maint_repo, vehicle_ins_repo, tx_svc),
        "homes": HomeService(homes_repo, home_maint_repo, tx_svc),
        "habits": HabitService(habits_repo, habit_logs_repo),
        "vehicles_repo": vehicles_repo,
        "fuel_repo": fuel_repo,
        "vehicle_maint_repo": vehicle_maint_repo,
        "vehicle_ins_repo": vehicle_ins_repo,
        "homes_repo": homes_repo,
        "home_maint_repo": home_maint_repo,
        "habits_repo": habits_repo,
        "habit_logs_repo": habit_logs_repo,
        "trips": TripService(trips_repo, trip_checklist_repo, transactions, tasks, events, reminders),
        "summaries": SummaryService(agenda_svc, tasks, planned_svc, trips_repo, trip_checklist_repo),
        "proactivity": ProactivityService(
            proactivity_settings_repo,
            proactive_notifications_repo,
            agenda_svc,
            tasks,
            trips_repo,
            trip_checklist_repo,
            planned_svc,
        ),
        "trips_repo": trips_repo,
        "trip_checklist_repo": trip_checklist_repo,
        "proactivity_settings_repo": proactivity_settings_repo,
        "proactive_notifications_repo": proactive_notifications_repo,
    }


@pytest.fixture
def event_repo(services) -> FakeEventRepo:
    return services["events"]._events


@pytest.fixture
def task_repo(services) -> FakeTaskRepo:
    return services["tasks"]._tasks


@pytest.fixture
def reminder_repo(services) -> FakeReminderRepo:
    return services["reminders"]._reminders


@pytest.fixture
def account_repo(services) -> FakeAccountRepo:
    return services["accounts"]._accounts


@pytest.fixture
def transaction_repo(services) -> FakeTransactionRepo:
    return services["transactions"]._tx


@pytest.fixture
def transfer_repo(services) -> FakeTransferRepo:
    return services["transfers"]._transfers


@pytest.fixture
def budget_repo(services) -> FakeBudgetRepo:
    return services["budgets"]._budgets


@pytest.fixture
def savings_goal_repo(services) -> FakeSavingsGoalRepo:
    return services["savings_goals"]._goals


@pytest.fixture
def savings_contribution_repo(services) -> FakeSavingsContributionRepo:
    return services["savings_goals"]._contributions
