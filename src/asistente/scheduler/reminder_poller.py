"""Poller de recordatorios vencidos (capa scheduler, canal-agnóstico).

Reclama los recordatorios candidatos (`claim_due`), que pasan a `processing`
con `claimed_at` (lease), los entrega vía un `NotificationSender` y después
los liquida:

- recordatorio recurrente (`compute_next` → nueva fecha): `advance` → vuelve a
  `pending` con la próxima ocurrencia;
- recordatorio único: `mark_sent` → `sent`.

Los recordatorios demasiado vencidos (más de la ventana de recuperación) se
cancelan y se registran en el log. Si el envío falla, el recordatorio se
`release` (vuelve a `pending`) para reintentarse en la siguiente pasada; si el
proceso muere tras el claim y antes de enviar, otro worker lo recupera cuando
el lease (`claim_lease`) expira.

Todas las operaciones de liquidación llevan el `claimed_at` que el worker
observó al reclamar: si otro worker ya recuperó el recordatorio (lease
expirado), la liquidación del worker zombie es un no-op (no duplica envíos).

La periodicidad y el arranque los gestiona la aplicación host (JobQueue de PTB);
esta clase solo expone `run_once`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from asistente.application.notifications import NotificationSender
from asistente.domain.reminders import ReminderService
from asistente.logging_setup import get_logger
from asistente.persistence.models import Reminder
from asistente.shared.timeutil import utc_now

logger = get_logger("scheduler.reminder_poller")


@dataclass
class PollRunStats:
    sent: int = 0
    advanced: int = 0
    stale: int = 0
    failed: int = 0

    @property
    def total(self) -> int:
        return self.sent + self.advanced + self.stale + self.failed


class ReminderPoller:
    def __init__(
        self,
        reminders: ReminderService,
        sender: NotificationSender,
        *,
        batch_size: int = 20,
        recovery_window: timedelta = timedelta(minutes=60),
        claim_lease: timedelta = timedelta(minutes=5),
    ) -> None:
        self._reminders = reminders
        self._sender = sender
        self._batch_size = max(1, batch_size)
        self._recovery_window = recovery_window
        self._claim_lease = claim_lease

    async def run_once(self) -> PollRunStats:
        now = utc_now()
        claimed = await self._reminders.claim_due(now, self._batch_size, self._claim_lease)
        stats = PollRunStats()
        for reminder in claimed:
            claimed_at = reminder.claimed_at
            if claimed_at is None:
                logger.warning("reminder_claimed_without_lease", reminder_id=reminder.id)
                stats.stale += 1
                continue
            if now - reminder.remind_at > self._recovery_window:
                await self._reminders.cancel_claimed(reminder.user_id, reminder.id, claimed_at=claimed_at)
                stats.stale += 1
                continue
            try:
                await self._sender.send(reminder.user_id, reminder.message)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "reminder_send_failed",
                    reminder_id=reminder.id,
                    user_id=reminder.user_id,
                    error=repr(exc),
                )
                await self._reminders.release(reminder.user_id, reminder.id, claimed_at=claimed_at)
                stats.failed += 1
                continue
            if await self._settle(reminder, claimed_at):
                stats.advanced += 1
            else:
                stats.sent += 1
        logger.info(
            "reminder_poll_done",
            claimed=len(claimed),
            sent=stats.sent,
            advanced=stats.advanced,
            stale=stats.stale,
            failed=stats.failed,
        )
        return stats

    async def _settle(self, reminder: Reminder, claimed_at: datetime) -> bool:
        """Avanza si hay recurrencia; si no, marca enviado. Devuelve advanced."""
        nxt = await self._reminders.compute_next(reminder.user_id, reminder)
        if nxt is not None:
            await self._reminders.advance(reminder.user_id, reminder.id, nxt, claimed_at=claimed_at)
            return True
        await self._reminders.mark_sent(reminder.user_id, reminder.id, claimed_at=claimed_at)
        return False
