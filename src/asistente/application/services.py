"""Bundle de servicios que el orquestador y las tools necesitan.

Evita pasar múltiples argumentos individuales. Se construye UNA vez en
`main.py` y se inyecta como `bot_data["services"]` en la aplicación de PTB.
"""

from __future__ import annotations

from dataclasses import dataclass

from asistente.application.confirmations import ConfirmationService
from asistente.application.ratelimit import RateLimiter
from asistente.config import Settings
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
from asistente.persistence.repositories import ConversationRepository


@dataclass(frozen=True, slots=True)
class Services:
    users: UserService
    notes: NoteService
    memory: MemoryService
    confirmations: ConfirmationService
    conversations: ConversationRepository
    rate_limit: RateLimiter
    settings: Settings

    # Fase 2 — agenda, tareas y recordatorios
    events: EventService
    tasks: TaskService
    reminders: ReminderService
    agenda: AgendaService

    # Fase 3 — núcleo financiero
    accounts: AccountService
    transactions: TransactionService
    transfers: TransferService
    finance: FinanceService

    # Fase 3.2 — presupuestos y objetivos de ahorro
    budgets: BudgetService
    savings_goals: SavingsGoalService

    # Fase 3.3 — recurrencias, pagos planificados y suscripciones
    recurring: RecurringTransactionService
    planned: PlannedTransactionService
    subscriptions: SubscriptionService

    # Fase 3.4 — personas, deudas y gastos compartidos
    people: PersonService
    debts: DebtService
    shared_expenses: SharedExpenseService

    # Fase 4 — coche, casa y salud/hábitos
    vehicles: VehicleService
    homes: HomeService
    habits: HabitService

    # Fase 5 — inteligencia financiera, previsión y patrones (solo lectura)
    intelligence: FinancialIntelligenceService

    # Fase 6 — viajes, resúmenes de vida y proactividad controlada
    trips: TripService
    summaries: SummaryService
    proactivity: ProactivityService

    # Fase 9 — identidad por canal (user_channels) e idempotencia de eventos
    # entrantes. En producción lo construye `main.build_services`; los tests de
    # orquestador con fakes lo omiten (None) porque no ejercitan el canal.
    channel_identities: ChannelIdentityService | None = None

    # Fase 10.0 — acceso Web (credenciales, sesiones Bearer, códigos de enlace).
    web_auth: WebAuthService | None = None
