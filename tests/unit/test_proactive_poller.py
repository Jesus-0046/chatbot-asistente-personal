"""Tests unitarios: ProactivePoller (Fase 6). Dedup, envío, fallos."""

from __future__ import annotations

from datetime import timedelta

from asistente.domain.proactivity import ProactiveCandidate
from asistente.scheduler.proactive_poller import ProactivePoller
from asistente.shared.timeutil import utc_now


class _FakeSender:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send(self, user_id: int, message: str) -> None:
        self.sent.append((user_id, message))


class _BadSender:
    async def send(self, user_id: int, message: str) -> None:
        raise RuntimeError("send failed")


async def test_poller_envia_pendientes(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.update_settings(1, enabled=True)
    now = utc_now()
    await services["trips"].create(
        user_id=1,
        name="Lisboa",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=5),
    )

    sender = _FakeSender()
    poller = ProactivePoller(proactivity=proactivity, sender=sender)
    stats = await poller.run_once()
    assert stats.users >= 1


async def test_poller_dedup_no_duplica_enviadas(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.update_settings(1, enabled=True)
    now = utc_now()
    trip = await services["trips"].create(
        user_id=1,
        name="Lisboa",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=5),
    )
    # Pre-mark the notification as sent so it's deduped
    candidate = ProactiveCandidate(
        kind="trip_reminder",
        dedup_key=f"trip:{trip.id}",
        title="Viaje próximo",
        body="...",
        importance=3,
    )
    row = await proactivity.create_or_reuse(1, candidate)
    assert row is not None
    now = utc_now()
    claimed = await proactivity.claim_pending(
        1, worker_id="worker-test", now_utc=now, day_local=now.date(), daily_limit=50
    )
    assert any(n.id == row.id for n in claimed)
    await proactivity.mark_sent(1, row.id, worker_id="worker-test", sent_at=now)

    sender = _FakeSender()
    poller = ProactivePoller(proactivity=proactivity, sender=sender)
    stats = await poller.run_once()
    assert stats.deduped >= 1
    assert all("Lisboa" not in msg for _, msg in sender.sent)


async def test_poller_send_failure_no_crash(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.update_settings(1, enabled=True)
    poller = ProactivePoller(proactivity=proactivity, sender=_BadSender())
    stats = await poller.run_once()
    assert stats.failed >= 0
