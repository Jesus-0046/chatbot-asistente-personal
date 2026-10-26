"""Tests del claim atómico de recordatorios (Fase 2.1).

Simulan DOS workers (cada uno con su `ReminderService`) sobre EL MISMO
conjunto de recordatorios (store compartido), replicando fielmente la
semántica del repositorio real sin PostgreSQL:

- `claim_due` marca `processing` + `claimed_at` y saca la fila de las
  candidatas, así que un recordatorio solo tiene UN reclamador (aunque dos
  workers lo pidan "a la vez").
- Leaase expirado → otro worker puede recuperar el recordatorio.
- La liquidación del worker zombie (con `claimed_at` antiguo) es un no-op:
  jamás se envía dos veces ni se pisa el estado del worker actual.

La comprobación de verdad sobre PostgreSQL concurrente está en
`tests/integration/test_claim_concurrency_pg.py` (omitida sin
TEST_DATABASE_URL).
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from asistente.domain.reminders import ReminderService
from asistente.scheduler.reminder_poller import ReminderPoller


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class _Store:
    """Conjunto compartido de recordatorios (como una tabla en memoria)."""

    def __init__(self, reminders: list | None = None) -> None:
        self.items: list = list(reminders or [])

    def find(self, reminder_id: int):
        return next((r for r in self.items if r.id == reminder_id), None)


class _WorkerRepo:
    """Repo de un worker con la semántica del repo real (claim/lease/guarda)."""

    def __init__(self, store: _Store) -> None:
        self._store = store

    async def create(self, **kwargs):
        raise NotImplementedError

    async def find_one(self, user_id: int, reminder_id: int):
        r = self._store.find(reminder_id)
        return r if r is not None and r.user_id == user_id else None

    async def list_for_user(self, user_id: int, *, status=None, limit=100):
        rows = [r for r in self._store.items if r.user_id == user_id]
        if status:
            rows = [r for r in rows if r.status == status]
        return rows[:limit]

    async def claim_due(self, now_utc, limit, *, lease):
        expired_before = now_utc - lease
        candidates = [
            r
            for r in self._store.items
            if (r.status == "pending" and r.remind_at <= now_utc)
            or (r.status == "processing" and r.claimed_at is not None and r.claimed_at < expired_before)
        ]
        candidates = sorted(candidates, key=lambda r: r.remind_at)[:limit]
        for r in candidates:
            r.status = "processing"
            r.claimed_at = now_utc
        return candidates

    async def _settle(self, user_id, reminder_id, **changes):
        r = self._store.find(reminder_id)
        stale = r is None or r.user_id != user_id or r.status != "processing"
        # Guarda de propiedad: el `claimed_at` debe coincidir con el del worker.
        if changes.pop("expected_claimed_at") != getattr(r, "claimed_at", None):
            stale = True
        if stale:
            return None
        for k, v in changes.items():
            setattr(r, k, v)
        return r

    async def advance(self, user_id, reminder_id, new_remind_at, *, claimed_at):
        return await self._settle(
            user_id,
            reminder_id,
            expected_claimed_at=claimed_at,
            remind_at=new_remind_at,
            status="pending",
            claimed_at=None,
        )

    async def mark_sent(self, user_id, reminder_id, sent_at, *, claimed_at):
        return await self._settle(
            user_id,
            reminder_id,
            expected_claimed_at=claimed_at,
            status="sent",
            sent_at=sent_at,
            claimed_at=None,
        )

    async def cancel_claimed(self, user_id, reminder_id, *, claimed_at):
        return await self._settle(
            user_id,
            reminder_id,
            expected_claimed_at=claimed_at,
            status="cancelled",
            claimed_at=None,
        )

    async def release(self, user_id, reminder_id, *, claimed_at):
        return await self._settle(
            user_id,
            reminder_id,
            expected_claimed_at=claimed_at,
            status="pending",
            claimed_at=None,
        )

    async def delete(self, user_id, reminder_id):
        raise NotImplementedError


def _reminder(rid: int, remind_at):
    return _Fake(
        id=rid,
        user_id=1,
        message=f"Aviso {rid}",
        remind_at=remind_at,
        event_id=None,
        task_id=None,
        offset_minutes=None,
        timezone="Europe/Madrid",
        status="pending",
        claimed_at=None,
        sent_at=None,
    )


def _make_worker(store: _Store) -> ReminderService:
    return ReminderService(_WorkerRepo(store), _Fake(), _Fake(), _Fake())


def _week_ago(now):
    return now - timedelta(days=7)


@pytest.fixture
def store():
    return _Store()


async def test_dos_workers_no_reclaman_el_mismo_recordatorio(store) -> None:
    now = _now()
    reminder = _reminder(1, _week_ago(now))
    store.items.append(reminder)

    worker_a = _make_worker(store)
    worker_b = _make_worker(store)

    claimed_a = await worker_a.claim_due(now, 10, timedelta(minutes=5))
    claimed_b = await worker_b.claim_due(now, 10, timedelta(minutes=5))

    assert [r.id for r in claimed_a] == [1]
    assert claimed_b == []
    assert store.find(1).claimed_at is not None
    assert store.find(1).status == "processing"


async def test_mismo_worker_no_reclama_dos_veces_lo_ya_reclamado(store) -> None:
    now = _now()
    store.items.append(_reminder(1, _week_ago(now)))

    worker = _make_worker(store)

    first = await worker.claim_due(now, 10, timedelta(minutes=5))
    second = await worker.claim_due(now, 10, timedelta(minutes=5))

    assert len(first) == 1
    assert second == []


async def test_claim_respeta_el_lote(store) -> None:
    now = _now()
    store.items.extend([_reminder(i, _week_ago(now)) for i in range(1, 5)])

    worker = _make_worker(store)

    claimed = await worker.claim_due(now, 3, timedelta(minutes=5))

    assert len(claimed) == 3
    assert [r.remind_at for r in claimed] == sorted(r.remind_at for r in claimed)


async def test_lease_expirado_permite_recuperar_el_recordatorio(store) -> None:
    now = _now()
    store.items.append(_reminder(1, _week_ago(now)))

    worker_a = _make_worker(store)
    worker_b = _make_worker(store)

    await worker_a.claim_due(now, 10, timedelta(minutes=5))
    reclamar_b = await worker_b.claim_due(now + timedelta(minutes=6), 10, timedelta(minutes=5))

    assert [r.id for r in reclamar_b] == [1]
    assert reclamar_b[0].claimed_at is not None
    assert reclamar_b[0].status == "processing"


async def test_worker_zombie_no_liquida_tras_recuperacion(store) -> None:
    now = _now()
    store.items.append(_reminder(1, _week_ago(now)))

    worker_a = _make_worker(store)
    worker_b = _make_worker(store)

    claimed_a = await worker_a.claim_due(now, 10, timedelta(minutes=5))
    claimed_a_at = claimed_a[0].claimed_at
    claimed_b = await worker_b.claim_due(now + timedelta(minutes=6), 10, timedelta(minutes=5))
    assert len(claimed_b) == 1

    # El zombie (A) intenta liquidar con su claimed_at antiguo → no-op.
    zombie_sent = await worker_a.mark_sent(1, 1, claimed_at=claimed_a_at)
    assert zombie_sent is None

    row = store.find(1)
    assert row.status == "processing"
    assert row.claimed_at == claimed_b[0].claimed_at

    # El worker actual (B) sí liquida y lo envía EXACTAMENTE una vez.
    sent = await worker_b.mark_sent(1, 1, claimed_at=claimed_b[0].claimed_at)
    assert sent is not None
    assert store.find(1).status == "sent"

    re_claim = await worker_a.claim_due(now + timedelta(minutes=7), 10, timedelta(minutes=5))
    assert re_claim == []


async def test_poller_con_dos_workers_envia_una_sola_vez(store) -> None:
    now = _now()
    store.items.append(_reminder(1, now - timedelta(minutes=1)))

    class Sender:
        def __init__(self) -> None:
            self.delivered: list[str] = []

        async def send(self, user_id: int, message: str) -> None:
            self.delivered.append(message)

    sender_a = Sender()
    sender_b = Sender()

    poller_a = ReminderPoller(_make_worker(store), sender_a)
    poller_b = ReminderPoller(_make_worker(store), sender_b)

    ran_a = await poller_a.run_once()
    ran_b = await poller_b.run_once()

    assert ran_a.sent == 1
    assert ran_b.sent == 0
    assert sender_a.delivered == ["Aviso 1"]
    assert sender_b.delivered == []
    assert store.find(1).status == "sent"


def _now():
    from datetime import UTC, datetime

    return datetime.now(tz=UTC)
