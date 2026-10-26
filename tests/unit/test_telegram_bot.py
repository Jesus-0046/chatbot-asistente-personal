"""Tests unitarios: ciclo de vida y ensamblado de Telegram (sin red ni token real).

Cubren el contrato de arranque de PTB v22: `run()` debe ser síncrono (no una
coroutine), porque `Application.run_polling()` gestiona él mismo el event loop.
Si `run()` fuera `async`, `python -m asistente.main` introduciría un bucle
asyncio anidado/incompatible (`RuntimeError: event loop already running`).
"""

from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any

import pytest
from telegram.error import InvalidToken
from telegram.ext import CommandHandler

from asistente.adapters.telegram.bot import build_application, run
from asistente.application.context import OutboundMessage
from asistente.config import Settings
from asistente.main import main
from asistente.persistence.db import Database


def make_settings(token: str = "dummy-token-12345", **kwargs: Any) -> Settings:
    return Settings(telegram_token=token, owner_telegram_ids=[123], **kwargs)


class _FakeOrchestrator:
    async def handle(self, inbound: Any) -> OutboundMessage:
        return OutboundMessage(text="ok")


def make_db() -> Database:
    return Database("sqlite+aiosqlite:///:memory:")


def make_services(settings: Settings) -> Any:
    from asistente.application.confirmations import ConfirmationService
    from asistente.application.ratelimit import RateLimiter
    from asistente.application.services import Services
    from asistente.domain.accounts import AccountService
    from asistente.domain.agenda import AgendaService
    from asistente.domain.budgets import BudgetService
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
    from asistente.persistence.repositories import (
        AccountRepository,
        BudgetRepository,
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
    )

    db = make_db()
    events = EventRepository(db)
    tasks = TaskRepository(db)
    reminders = ReminderRepository(db)
    rules = RecurrenceRuleRepository(db)
    accounts = AccountRepository(db)
    transactions = TransactionRepository(db)
    transfers = TransferRepository(db)
    budgets = BudgetRepository(db)
    goals = SavingsGoalRepository(db)
    contributions = SavingsContributionRepository(db)
    recurring_repo = RecurringTransactionRepository(db)
    planned_repo = PlannedTransactionRepository(db)
    subs = SubscriptionRepository(db)
    people = PersonRepository(db)
    trips = TripRepository(db)
    trip_checklist = TripChecklistItemRepository(db)
    proactivity_settings = UserProactivitySettingsRepository(db)
    proactive_notifications = ProactiveNotificationRepository(db)
    tx_svc = TransactionService(transactions, accounts)
    recurring_svc = RecurringTransactionService(recurring_repo, planned_repo, accounts, subs)
    agenda_svc = AgendaService(events, tasks, reminders, rules)
    planned_svc = PlannedTransactionService(planned_repo, tx_svc)
    return Services(
        users=UserService(UserRepository(db)),
        notes=NoteService(NoteRepository(db)),
        memory=MemoryService(MemoryRuleRepository(db)),
        confirmations=ConfirmationService(ConfirmationRepository(db), ttl_minutes=10),
        conversations=ConversationRepository(db),
        rate_limit=RateLimiter(10),
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
        people=PersonService(people),
        debts=DebtService(DebtRepository(db), people),
        shared_expenses=SharedExpenseService(
            SharedExpenseRepository(db),
            SharedExpenseShareRepository(db),
            SharedExpensePaymentRepository(db),
            people,
        ),
        vehicles=VehicleService(
            VehicleRepository(db),
            FuelEntryRepository(db),
            VehicleMaintenanceRepository(db),
            VehicleInsuranceRepository(db),
            tx_svc,
        ),
        homes=HomeService(HomeRepository(db), HomeMaintenanceRepository(db), tx_svc),
        habits=HabitService(HabitRepository(db), HabitLogRepository(db)),
        intelligence=FinancialIntelligenceService(
            finance=FinanceService(accounts, transactions, transfers),
            transactions=tx_svc,
            budgets=BudgetService(budgets, transactions),
            savings_goals=SavingsGoalService(goals, contributions, transfers, accounts),
            recurring=recurring_svc,
            planned=planned_svc,
            debts=DebtService(DebtRepository(db), people),
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
    )


def test_run_es_funcion_sync() -> None:
    """run() no debe ser una coroutine: PTB v22 gestiona su propio event loop."""
    assert not inspect.iscoroutinefunction(run)


def test_main_es_entrypoint_sync() -> None:
    """main() tampoco debe ser coroutine (entrypoint de consola)."""
    assert not inspect.iscoroutinefunction(main)


def test_build_application_registra_handlers() -> None:
    settings = make_settings()
    app = build_application(
        settings,
        services=make_services(settings),
        orchestrator=_FakeOrchestrator(),  # type: ignore[arg-type]
        db=make_db(),
    )
    handlers = [handler for group_handlers in app.handlers.values() for handler in group_handlers]
    assert len(handlers) == 3  # /start + texto + foto (Fase 7)
    assert isinstance(handlers[0], CommandHandler)


def test_build_application_guarda_settings_en_bot_data() -> None:
    settings = make_settings()
    app = build_application(
        settings,
        services=make_services(settings),
        orchestrator=_FakeOrchestrator(),  # type: ignore[arg-type]
        db=make_db(),
    )
    assert app.bot_data.get("settings") is settings
    assert app.bot_data.get("orchestrator") is not None


def test_build_application_con_token_vacio_lanza_error_rapido() -> None:
    """Sin token configurado (Settings por defecto) falla al ensamblar, sin red."""
    settings = make_settings(token="")
    with pytest.raises(InvalidToken):
        build_application(
            settings,
            services=make_services(settings),
            orchestrator=_FakeOrchestrator(),  # type: ignore[arg-type]
            db=make_db(),
        )


def test_reminder_poller_se_programa_en_la_job_queue() -> None:
    from asistente.adapters.telegram.bot import _build_reminder_poller, _schedule_reminder_poller

    settings = make_settings()
    app = build_application(
        settings,
        services=make_services(settings),
        orchestrator=_FakeOrchestrator(),  # type: ignore[arg-type]
        db=make_db(),
    )

    poller = _build_reminder_poller(app)
    assert app.job_queue is not None
    _schedule_reminder_poller(app, poller)

    jobs = app.job_queue.jobs()
    assert len(jobs) == 1
    assert jobs[0].name == "reminder_poller"
    assert jobs[0].trigger.interval == timedelta(seconds=settings.reminder_poller_interval_seconds)
