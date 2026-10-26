"""Entrypoint de la aplicación: cablea las capas y arranca el bot.

- Configuración (env)
- Base de datos y servicios (domain + application)
- Cliente LLM (OpenAI) y registro de tools
- Orquestador (canal-agnóstico)
- Adaptador de Telegram
- (Fase 9) Adaptador de WhatsApp: webhook HTTP + cliente de la Cloud API

Con `WHATSAPP_ENABLED=false` (por defecto) se arranca solo Telegram vía
polling (`run_telegram`, bloqueante). Con WhatsApp activo, la Application de
PTB se construye manualmente y convive con el servidor aiohttp del webhook en
el MISMO event loop (ver `_run_whatsapp_app`).
"""

from __future__ import annotations

import asyncio

from aiohttp import web

from asistente.adapters.telegram.bot import build_application
from asistente.adapters.telegram.bot import run as run_telegram
from asistente.adapters.whatsapp import WhatsAppChannel, WhatsAppClient, build_webhook_app
from asistente.ai.llm import OpenAIClient
from asistente.application.confirmations import ConfirmationService
from asistente.application.orchestrator import Orchestrator
from asistente.application.ratelimit import RateLimiter
from asistente.application.services import Services
from asistente.config import ConfigError, Settings, as_dict_for_logging, validate_critical_env
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
from asistente.logging_setup import get_logger, setup_logging
from asistente.persistence.db import Database
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
from asistente.tools import create_tool_registry

logger = get_logger("main")


def build_services(settings: Settings, db: Database) -> Services:
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
    recurring = RecurringTransactionRepository(db)
    planned = PlannedTransactionRepository(db)
    subs = SubscriptionRepository(db)
    people = PersonRepository(db)
    tx_svc = TransactionService(transactions, accounts)
    recurring_svc = RecurringTransactionService(recurring, planned, accounts, subs)
    people_svc = PersonService(people)
    vehicles = VehicleRepository(db)
    homes = HomeRepository(db)
    finance_svc = FinanceService(accounts, transactions, transfers)
    budget_svc = BudgetService(budgets, transactions)
    savings_svc = SavingsGoalService(goals, contributions, transfers, accounts)
    planned_svc = PlannedTransactionService(planned, tx_svc)
    debt_svc = DebtService(DebtRepository(db), people)
    intelligence_svc = FinancialIntelligenceService(
        finance=finance_svc,
        transactions=tx_svc,
        budgets=budget_svc,
        savings_goals=savings_svc,
        recurring=recurring_svc,
        planned=planned_svc,
        debts=debt_svc,
        subscriptions=subs,
    )
    trips = TripRepository(db)
    trip_checklist = TripChecklistItemRepository(db)
    proactivity_settings = UserProactivitySettingsRepository(db)
    proactive_notifications = ProactiveNotificationRepository(db)
    trip_svc = TripService(trips, trip_checklist, transactions, tasks, events, reminders)
    agenda_svc = AgendaService(events, tasks, reminders, rules)
    vehicle_svc = VehicleService(
        vehicles,
        FuelEntryRepository(db),
        VehicleMaintenanceRepository(db),
        VehicleInsuranceRepository(db),
        tx_svc,
    )
    home_svc = HomeService(homes, HomeMaintenanceRepository(db), tx_svc)
    habit_svc = HabitService(HabitRepository(db), HabitLogRepository(db))
    summary_svc = SummaryService(
        agenda_svc,
        tasks,
        planned_svc,
        trips,
        trip_checklist,
        vehicles=vehicle_svc,
        homes=home_svc,
        habits=habit_svc,
        reminders=reminders,
    )
    proactivity_svc = ProactivityService(
        proactivity_settings,
        proactive_notifications,
        agenda_svc,
        tasks,
        trips,
        trip_checklist,
        planned_svc,
        vehicles=vehicle_svc,
        homes=home_svc,
        reminders=reminders,
    )
    return Services(
        users=UserService(UserRepository(db)),
        channel_identities=ChannelIdentityService(ChannelIdentityRepository(db)),
        notes=NoteService(NoteRepository(db)),
        memory=MemoryService(MemoryRuleRepository(db)),
        confirmations=ConfirmationService(ConfirmationRepository(db), ttl_minutes=settings.confirmation_ttl_minutes),
        conversations=ConversationRepository(db),
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
        planned=PlannedTransactionService(planned, tx_svc),
        subscriptions=SubscriptionService(subs, recurring_svc),
        people=people_svc,
        debts=DebtService(DebtRepository(db), people),
        shared_expenses=SharedExpenseService(
            SharedExpenseRepository(db),
            SharedExpenseShareRepository(db),
            SharedExpensePaymentRepository(db),
            people,
        ),
        vehicles=vehicle_svc,
        homes=home_svc,
        habits=habit_svc,
        intelligence=intelligence_svc,
        trips=trip_svc,
        summaries=summary_svc,
        proactivity=proactivity_svc,
        web_auth=WebAuthService(
            credentials=WebCredentialRepository(db),
            sessions=WebSessionRepository(db),
            binding_codes=WebBindingCodeRepository(db),
            users=UserRepository(db),
            channels=ChannelIdentityRepository(db),
            settings=settings,
        ),
    )


def _run_whatsapp_app(
    *,
    settings: Settings,
    services: Services,
    orchestrator: Orchestrator,
    db: Database,
) -> None:
    """Arranca Telegram + webhook de WhatsApp en el MISMO event loop.

    PTB `run_polling()` gestiona su propio loop y no puede convivir con el
    servidor aiohttp en un único proceso; por eso aquí se construye la
    Application manualmente (`async with app`) y se levanta el servidor de
    webhook a su lado. El `Database` se usa así en un único loop.
    """

    async def _run() -> None:
        app = build_application(settings, services=services, orchestrator=orchestrator, db=db)
        gateway = WhatsAppClient(settings)
        channel = WhatsAppChannel(services=services, orchestrator=orchestrator, gateway=gateway)
        web_app = build_webhook_app(channel, settings)
        async with app:
            updater = app.updater
            if updater is None:
                raise RuntimeError("PTB updater no disponible")
            await updater.start_polling()
            runner = web.AppRunner(web_app)
            await runner.setup()
            site = web.TCPSite(runner, settings.whatsapp_webhook_host, settings.whatsapp_webhook_port)
            await site.start()
            logger.info(
                "whatsapp_webhook_started",
                port=settings.whatsapp_webhook_port,
                path="/webhook",
            )
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                pass
            finally:
                await site.stop()
                await runner.cleanup()
                await gateway.close()
                if app.updater is not None:
                    await app.updater.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("application_stopping")


def main() -> None:
    """Arranca la aplicación; con WhatsApp activado, Telegram + webhook."""
    settings = Settings()
    setup_logging(settings.log_level)
    logger = get_logger("main")
    logger.info("application_starting", settings=as_dict_for_logging(settings))

    try:
        validate_critical_env(settings)
    except ConfigError as exc:
        logger.error("configuration_error", error=str(exc))
        raise SystemExit(str(exc)) from None

    db = Database(settings.database_url, echo=settings.db_echo)
    db.connect()
    services = build_services(settings, db)
    orchestrator = Orchestrator(
        services=services,
        llm=OpenAIClient(settings),
        tools=create_tool_registry(),
    )

    if settings.whatsapp_enabled:
        _run_whatsapp_app(settings=settings, services=services, orchestrator=orchestrator, db=db)
        return

    try:
        run_telegram(settings, services=services, orchestrator=orchestrator, db=db)
    except KeyboardInterrupt:
        logger.info("application_stopping")
        return
    logger.info("application_stopped")


if __name__ == "__main__":
    main()
