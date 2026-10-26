"""Repositorios de datos (persistencia).

Todos los repositorios reciben la sesión por llamada y SIEMPRE filtran por
`user_id` proporcionado por el contexto autenticado de la aplicación — nunca
por un id que provenga del LLM.
"""

from __future__ import annotations

from asistente.persistence.repositories.accounts import AccountRepository
from asistente.persistence.repositories.base import Repository
from asistente.persistence.repositories.budgets import BudgetRepository
from asistente.persistence.repositories.channels import ChannelIdentityRepository
from asistente.persistence.repositories.confirmations import ConfirmationRepository
from asistente.persistence.repositories.conversations import ConversationRepository
from asistente.persistence.repositories.debts import DebtRepository
from asistente.persistence.repositories.events import EventRepository
from asistente.persistence.repositories.habits import HabitLogRepository, HabitRepository
from asistente.persistence.repositories.homes import HomeMaintenanceRepository, HomeRepository
from asistente.persistence.repositories.memory_rules import MemoryRuleRepository
from asistente.persistence.repositories.notes import NoteRepository
from asistente.persistence.repositories.people import PersonRepository
from asistente.persistence.repositories.planned import PlannedTransactionRepository
from asistente.persistence.repositories.proactivity import (
    ProactiveNotificationRepository,
    UserProactivitySettingsRepository,
)
from asistente.persistence.repositories.recurrence_rules import RecurrenceRuleRepository
from asistente.persistence.repositories.recurring import RecurringTransactionRepository
from asistente.persistence.repositories.reminders import ReminderRepository
from asistente.persistence.repositories.savings import SavingsContributionRepository, SavingsGoalRepository
from asistente.persistence.repositories.shared_expenses import (
    SharedExpensePaymentRepository,
    SharedExpenseRepository,
    SharedExpenseShareRepository,
)
from asistente.persistence.repositories.subscriptions import SubscriptionRepository
from asistente.persistence.repositories.tasks import TaskRepository
from asistente.persistence.repositories.transactions import TransactionRepository
from asistente.persistence.repositories.transfers import TransferRepository
from asistente.persistence.repositories.trips import (
    TripChecklistItemRepository,
    TripRepository,
)
from asistente.persistence.repositories.users import UserRepository
from asistente.persistence.repositories.vehicles import (
    FuelEntryRepository,
    VehicleInsuranceRepository,
    VehicleMaintenanceRepository,
    VehicleRepository,
)
from asistente.persistence.repositories.web_auth import (
    WebBindingCodeRepository,
    WebCredentialRepository,
    WebSessionRepository,
)

__all__ = [
    "Repository",
    "UserRepository",
    "ChannelIdentityRepository",
    "ConversationRepository",
    "ConfirmationRepository",
    "MemoryRuleRepository",
    "NoteRepository",
    "EventRepository",
    "TaskRepository",
    "ReminderRepository",
    "RecurrenceRuleRepository",
    "AccountRepository",
    "TransactionRepository",
    "TransferRepository",
    "BudgetRepository",
    "SavingsGoalRepository",
    "SavingsContributionRepository",
    "RecurringTransactionRepository",
    "PlannedTransactionRepository",
    "SubscriptionRepository",
    "PersonRepository",
    "DebtRepository",
    "SharedExpenseRepository",
    "SharedExpenseShareRepository",
    "SharedExpensePaymentRepository",
    "VehicleRepository",
    "FuelEntryRepository",
    "VehicleMaintenanceRepository",
    "VehicleInsuranceRepository",
    "HomeRepository",
    "HomeMaintenanceRepository",
    "HabitRepository",
    "HabitLogRepository",
    "TripRepository",
    "TripChecklistItemRepository",
    "UserProactivitySettingsRepository",
    "ProactiveNotificationRepository",
    "WebCredentialRepository",
    "WebSessionRepository",
    "WebBindingCodeRepository",
]
