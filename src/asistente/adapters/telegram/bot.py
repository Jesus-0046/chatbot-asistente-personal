"""Adaptador de Telegram.

Construcción de la Application PTB, arranque del polling y wiring del
orquestador y del poller de recordatorios (JobQueue). Los handlers solo
traducen mensajes ⇄ `InboundMessage`/`OutboundMessage`: no hay lógica de
negocio ni llamadas a OpenAI aquí.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from telegram.ext import (
    Application,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

from asistente.adapters.telegram.handlers import (
    photo_message_handler,
    start_command,
    text_message_handler,
)
from asistente.adapters.telegram.notification import TelegramNotificationSender
from asistente.application.notifications import NotificationSender
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.config import Settings
from asistente.logging_setup import get_logger
from asistente.persistence.db import Database
from asistente.scheduler.proactive_poller import ProactivePoller
from asistente.scheduler.reminder_poller import ReminderPoller

logger = get_logger("telegram.bot")

PostInit = Callable[[Application], Awaitable[None]]


def _build_reminder_poller(app: Application) -> ReminderPoller:
    settings: Settings = app.bot_data["settings"]
    services: Services = app.bot_data["services"]
    sender: NotificationSender = TelegramNotificationSender(app, services.users)
    return ReminderPoller(
        services.reminders,
        sender,
        batch_size=settings.reminder_batch_size,
        recovery_window=timedelta(minutes=settings.reminder_recovery_window_minutes),
        claim_lease=timedelta(seconds=settings.reminder_claim_lease_seconds),
    )


def _schedule_reminder_poller(app: Application, poller: ReminderPoller) -> None:
    if app.job_queue is None:
        logger.warning("reminder_poller_disabled_no_job_queue")
        return
    settings: Settings = app.bot_data["settings"]
    interval = settings.reminder_poller_interval_seconds

    async def _job(_: CallbackContext) -> None:
        await poller.run_once()

    app.job_queue.run_repeating(_job, interval=interval, first=interval, name="reminder_poller")
    logger.info("reminder_poller_scheduled", interval_seconds=interval)


def _build_proactive_poller(app: Application) -> ProactivePoller:
    settings: Settings = app.bot_data["settings"]
    services: Services = app.bot_data["services"]
    sender: NotificationSender = TelegramNotificationSender(app, services.users)
    return ProactivePoller(
        services.proactivity,
        sender,
        max_users=settings.proactive_max_users_per_run,
        lease_seconds=settings.proactive_notification_lease_seconds,
    )


def _schedule_proactive_poller(app: Application, poller: ProactivePoller) -> None:
    if app.job_queue is None:
        logger.warning("proactive_poller_disabled_no_job_queue")
        return
    settings: Settings = app.bot_data["settings"]
    interval = settings.proactive_poller_interval_seconds

    async def _job(_: CallbackContext) -> None:
        await poller.run_once()

    app.job_queue.run_repeating(_job, interval=interval, first=interval, name="proactive_poller")
    logger.info("proactive_poller_scheduled", interval_seconds=interval)


def build_application(
    settings: Settings,
    *,
    services: Services,
    orchestrator: Orchestrator,
    db: Database,
) -> Application:
    """Construye la Application de PTB con los handlers de Fase 1 y el poller."""

    async def _post_init(app: Application) -> None:
        try:
            await db.ping()
            logger.info("database_connected")
        except Exception as exc:  # noqa: BLE001
            logger.exception("database_ping_failed")
            raise RuntimeError("database_unavailable") from exc
        _schedule_reminder_poller(app, _build_reminder_poller(app))
        _schedule_proactive_poller(app, _build_proactive_poller(app))

    app = Application.builder().token(settings.telegram_token.get_secret_value()).post_init(_post_init).build()
    app.bot_data["settings"] = settings
    app.bot_data["services"] = services
    app.bot_data["orchestrator"] = orchestrator
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_message_handler))
    return app


def run(settings: Settings, *, services: Services, orchestrator: Orchestrator, db: Database) -> None:
    """Arranca el adaptador y bloquea hasta que el bot se detenga.

    `Application.run_polling()` de PTB v22 es un método de bloqueo que gestiona
    él mismo el event loop: obtiene el loop con `asyncio.get_event_loop()` y
    ejecuta `loop.run_until_complete(...)` y `loop.run_forever()` sobre él.

    Por eso NO debe llamarse desde dentro de un bucle asyncio activo (con
    `asyncio.run()`, `await`, tareas, etc.): `get_event_loop()` devolvería un
    bucle ya en marcha y `run_until_complete` lanzaría
    `RuntimeError: event loop already running`.

    Este entrypoint es el único punto síncrono del proceso; los handlers de
    PTB, el acceso a BD y el resto de capas siguen siendo async por diseño.
    """
    app = build_application(settings, services=services, orchestrator=orchestrator, db=db)
    logger.info("telegram_bot_starting", app_env=settings.app_env)
    try:
        app.run_polling()
    finally:
        logger.info("telegram_bot_stopped")
