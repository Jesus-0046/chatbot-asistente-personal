"""Tests del ReminderPoller (scheduler) con ReminderService y sender falsos.

Cubre: envío de recordatorios sueltos (→ sent), avance de recurrentes
(→ pending con nueva fecha), cancelación de vencidos demasiado antiguos
(ventana de recuperación) y reintento cuando el canal falla.

Claim atómico (2.1): las pruebas de concurrencia se validan en
`test_claim_concurrency.py` contra un fake repo con estado real.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from asistente.persistence.models import Reminder
from asistente.scheduler.reminder_poller import ReminderPoller
from asistente.shared.timeutil import utc_now


def _reminder(rid: int, *, remind_at_utc, message="Aviso") -> Reminder:
    return Reminder(
        id=rid,
        user_id=1,
        message=message,
        remind_at=remind_at_utc,
        timezone="Europe/Madrid",
        status="pending",
    )


class FakeReminderService:
    def __init__(self) -> None:
        self.due: list[Reminder] = []
        self.advanced: list[tuple[int, Any]] = []
        self.sent: list[int] = []
        self.cancelled: list[int] = []
        self.released: list[int] = []
        self.next_by_id: dict[int, Any] = {}
        # Lease simulado: las llamadas a claim_due con lease > 0 funcionan;
        # los fake reminders se devuelven con `claimed_at=now` al reclamar.
        self._now_at_claim: Any = None

    async def claim_due(self, now_utc: Any, limit: int, lease: Any) -> list[Reminder]:
        self._now_at_claim = now_utc
        out = self.due[:limit]
        for r in out:
            r.status = "processing"
            r.claimed_at = now_utc
        return out

    async def compute_next(self, user_id: int, reminder: Reminder) -> Any:
        return self.next_by_id.get(reminder.id)

    async def advance(self, user_id: int, reminder_id: int, new_remind_at: Any, *, claimed_at: Any) -> None:
        self.advanced.append((reminder_id, new_remind_at))

    async def mark_sent(self, user_id: int, reminder_id: int, *, claimed_at: Any) -> None:
        self.sent.append(reminder_id)

    async def cancel_claimed(self, user_id: int, reminder_id: int, *, claimed_at: Any) -> None:
        self.cancelled.append(reminder_id)

    async def release(self, user_id: int, reminder_id: int, *, claimed_at: Any) -> None:
        self.released.append(reminder_id)


class RecordingSender:
    def __init__(self, *, fail: bool = False) -> None:
        self.delivered: list[tuple[int, str]] = []
        self.fail = fail

    async def send(self, user_id: int, message: str) -> None:
        if self.fail:
            raise RuntimeError("canal caído")
        self.delivered.append((user_id, message))


async def test_poller_envia_y_marca_enviado() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(1, remind_at_utc=now - timedelta(minutes=1))]
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender, batch_size=10)

    stats = await poller.run_once()

    assert sender.delivered == [(1, "Aviso")]
    assert svc.sent == [1]
    assert svc.advanced == []
    assert (stats.sent, stats.advanced, stats.failed, stats.stale) == (1, 0, 0, 0)


async def test_poller_avanza_recurrentes_sin_marcar_enviado() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(2, remind_at_utc=now - timedelta(minutes=1))]
    svc.next_by_id[2] = now + timedelta(days=1)
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender)

    stats = await poller.run_once()

    assert svc.advanced == [(2, svc.next_by_id[2])]
    assert svc.sent == []
    assert stats.advanced == 1
    assert stats.sent == 0


async def test_poller_cancela_vencidos_mas_alla_de_la_ventana() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(3, remind_at_utc=now - timedelta(hours=2))]
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender, recovery_window=timedelta(minutes=60))

    stats = await poller.run_once()

    assert sender.delivered == []
    assert svc.cancelled == [3]
    assert stats.stale == 1


async def test_poller_no_cancela_vencidos_dentro_de_la_ventana() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(4, remind_at_utc=now - timedelta(minutes=30))]
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender, recovery_window=timedelta(minutes=60))

    stats = await poller.run_once()

    assert sender.delivered == [(1, "Aviso")]
    assert svc.cancelled == []
    assert stats.sent == 1


async def test_poller_reintenta_cuando_el_canal_falla() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(5, remind_at_utc=now - timedelta(minutes=1))]
    sender = RecordingSender(fail=True)
    poller = ReminderPoller(svc, sender)

    stats = await poller.run_once()

    assert svc.sent == []
    assert svc.advanced == []
    assert svc.released == [5]
    assert stats.failed == 1


async def test_poller_sin_vencidos_no_llama_al_sender() -> None:
    svc = FakeReminderService()
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender)

    stats = await poller.run_once()

    assert sender.delivered == []
    assert stats.total == 0


async def test_poller_respeta_el_tamano_de_lote() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(i, remind_at_utc=now - timedelta(minutes=1)) for i in range(1, 6)]
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender, batch_size=3)

    stats = await poller.run_once()

    assert len(sender.delivered) == 3
    assert stats.sent == 3


async def test_falla_de_un_elemento_no_aborta_el_resto() -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [
        _reminder(1, remind_at_utc=now - timedelta(minutes=1)),
        _reminder(2, remind_at_utc=now - timedelta(minutes=1)),
    ]
    svc.next_by_id[2] = now + timedelta(days=1)

    class PartialSender:
        def __init__(self) -> None:
            self.delivered: list[str] = []
            self._attempts = 0

        async def send(self, user_id: int, message: str) -> None:
            self._attempts += 1
            if self._attempts == 1:
                raise RuntimeError("primer envío cae")
            self.delivered.append(message)

    sender = PartialSender()
    poller = ReminderPoller(svc, sender)

    stats = await poller.run_once()

    assert (stats.failed, stats.advanced) == (1, 1)
    assert svc.advanced == [(2, svc.next_by_id[2])]


@pytest.mark.parametrize("window_minutes", [0, -5])
async def test_ventana_de_recuperacion_muy_pequena_cancela_todo(window_minutes: int) -> None:
    now = utc_now()
    svc = FakeReminderService()
    svc.due = [_reminder(1, remind_at_utc=now - timedelta(seconds=1))]
    sender = RecordingSender()
    poller = ReminderPoller(svc, sender, recovery_window=timedelta(minutes=window_minutes))

    stats = await poller.run_once()

    assert svc.cancelled == [1]
    assert stats.stale == 1
