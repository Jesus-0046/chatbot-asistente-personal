"""Integración PG: claim/lease concurrente del Proactive Poller (omitido sin TEST_DATABASE_URL).

Uso:
  TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration -m db

Verifica sobre PostgreSQL real los invariantes de Fase 6.2 / 6.2.1:

- `claim_pending` con `FOR UPDATE SKIP LOCKED`: ninguna fila se reclama dos
  veces y los reclaims de leases expirados vuelven a ser elegibles. El claim
  NO reserva presupuesto (solo lee el contador como hint best-effort).
- `reserve_send`: UPDATE atómico (`count = count + 1 WHERE count < daily_limit`)
  que serializa a los workers → el total de ENVÍOS reales nunca supera
  `daily_limit`, aunque el claim haya entregado más filas de las que el día
  permite (se liberan con `unclaim`).
- Un crash después del envío y antes de `mark_sent` mantiene la reserva: un
  reclaim posterior no puede reenviar más allá del límite.
- `mark_sent`/`mark_skipped` exigen propiedad del worker (worker_id + lease vigente).
- La carrera de dedup (INSERT concurrente del mismo (user_id, kind, dedup_key))
  genera IntegrityError del índice único y la fila ganadora se recupera.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta

import pytest
import sqlalchemy as sa

from asistente.domain.proactivity import _is_dedup_error
from asistente.persistence.db import Database
from asistente.persistence.repositories import ProactiveNotificationRepository

pytestmark = pytest.mark.db

_TEST_USER = 919192
_KIND = "pg_claim"
DAY: date = date(2026, 9, 18)


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _repo(db: Database) -> ProactiveNotificationRepository:
    return ProactiveNotificationRepository(db)


@pytest.mark.asyncio
async def test_claim_paralelo_con_skip_locked_no_duplica_filas() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=20)
        now = _now()

        async def claim(worker: str) -> list[int]:
            rows = await _repo(db).claim_pending(
                _TEST_USER,
                worker_id=worker,
                now_utc=now,
                day_local=DAY,
                daily_limit=5,
                limit=20,
                lease_seconds=600,
            )
            return [r.id for r in rows]

        results = await asyncio.gather(*[claim(f"w{i}") for i in range(10)])
        claimed_ids = [rid for batch in results for rid in batch]

        # Fase 6.2.1: el claim ya no frena el total (cada worker lee un
        # contador sin reservar y reclama su cupo fresh). Lo prohibido es que
        # UNA fila se reclame dos veces: SKIP LOCKED lo garantiza.
        assert len(claimed_ids) == len(set(claimed_ids)), "una fila se reclamó dos veces"
        assert len(claimed_ids) == 20

        rows = await _claim_info(db)
        assert len(rows) == 20
        assert all(r["worker_id"] is not None for r in rows)
        assert all(r["status"] == "processing" for r in rows)
        assert all(r["lease_until"] is not None for r in rows)
        # El claim no tocó el contador: sigue sin existir para este día.
        assert await _counter(db) == 0
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_reclaim_lease_expirado_solo_reserva_al_reenviar() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=3)
        now = _now()
        repo = _repo(db)

        first = await repo.claim_pending(
            _TEST_USER, worker_id="wA", now_utc=now, day_local=DAY, daily_limit=2, limit=10, lease_seconds=1
        )
        assert len(first) == 2
        # Como haría el poller, cada envío reserva su unidad por adelantado.
        assert await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert await _counter(db) == 2

        # Expira el lease de TODAS las filas reclamadas (simula proceso muerto).
        await _expire_leases(db, now - timedelta(seconds=30))

        later = now + timedelta(seconds=30)
        reclaimed = await repo.claim_pending(
            _TEST_USER, worker_id="wB", now_utc=later, day_local=DAY, daily_limit=2, limit=10, lease_seconds=600
        )
        assert len(reclaimed) == 2
        assert all(r.worker_id == "wB" for r in reclaimed)
        # El reclaim NO reserva presupuesto: solo `reserve_send` lo consume.
        assert await _counter(db) == 2
        # Límite lleno: el reenvío de los crashes queda bloqueado.
        assert not await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert await _counter(db) == 2
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_mark_sent_exige_propiedad_del_worker() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=2)
        now = _now()
        repo = _repo(db)

        claimed = await repo.claim_pending(
            _TEST_USER, worker_id="wA", now_utc=now, day_local=DAY, daily_limit=3, limit=10, lease_seconds=600
        )
        row = claimed[0]

        # Otro worker no puede finalizar el claim de wA.
        assert await repo.mark_sent(_TEST_USER, row.id, worker_id="wB", sent_at=now + timedelta(seconds=5)) is None
        assert await repo.mark_skipped(_TEST_USER, row.id, worker_id="wB", now_utc=now + timedelta(seconds=5)) is None

        # wB reclama tras expirar el lease: wA queda sin propiedad.
        await _expire_leases(db, now - timedelta(seconds=1))
        later = now + timedelta(seconds=1)
        reclaimed = await repo.claim_pending(
            _TEST_USER, worker_id="wB", now_utc=later, day_local=DAY, daily_limit=3, limit=10, lease_seconds=600
        )
        assert any(r.id == row.id for r in reclaimed)
        assert await repo.mark_sent(_TEST_USER, row.id, worker_id="wA", sent_at=later) is None
        winner = await repo.mark_sent(_TEST_USER, row.id, worker_id="wB", sent_at=later)
        assert winner is not None and winner.status == "sent"
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_skip_release_claim_correcta() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=1)
        now = _now()
        repo = _repo(db)

        claimed = await repo.claim_pending(
            _TEST_USER, worker_id="wA", now_utc=now, day_local=DAY, daily_limit=3, limit=10, lease_seconds=600
        )
        row = claimed[0]
        skipped = await repo.mark_skipped(_TEST_USER, row.id, worker_id="wA", now_utc=now + timedelta(seconds=1))
        assert skipped is not None
        assert skipped.status == "skipped"
        assert skipped.worker_id is None and skipped.lease_until is None
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_dedup_concurrente_genera_integrity_error_recuperable() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed_user(db)
        dedup_key = "pg-dedup-clave"
        repo = _repo(db)

        async def insert_one() -> int:
            row = await repo.create(
                user_id=_TEST_USER,
                kind=_KIND,
                dedup_key=dedup_key,
                title="T",
                body="B",
                importance=3,
                status="pending",
            )
            return row.id

        # Dos INSERT concurrentes del mismo hecho: uno gana, otro falla.
        result_a, result_b = await asyncio.gather(insert_one(), insert_one(), return_exceptions=True)
        ok_ids = [r for r in (result_a, result_b) if isinstance(r, int)]
        errors = [e for e in (result_a, result_b) if not isinstance(e, int)]

        assert len(ok_ids) == 1, "ambos inserts persistieron: dedup roto"
        assert len(errors) == 1
        err = errors[0]
        from sqlalchemy.exc import IntegrityError as _IntegrityError

        assert isinstance(err, _IntegrityError)
        assert _is_dedup_error(err), f"el error de dedup no se clasificó: {err!r}"

        # La fila ganadora se recupera con find_any (la usa create_or_reuse).
        found = await repo.find_any(_TEST_USER, _KIND, dedup_key)
        assert found is not None and found.id == ok_ids[0]
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_crash_tras_send_y_reclaim_con_limite_real() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=2)
        now = _now()
        repo = _repo(db)

        claimed = await repo.claim_pending(
            _TEST_USER, worker_id="wA", now_utc=now, day_local=DAY, daily_limit=2, limit=10, lease_seconds=600
        )
        orphan, fresh = claimed[0], claimed[1]

        # Fresh: reserva + send + mark_sent (confirmado). Orphan: reserva +
        # send exitoso, luego CRASH antes de mark_sent (la reserva se mantiene).
        assert await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert (
            await repo.mark_sent(_TEST_USER, fresh.id, worker_id="wA", sent_at=now + timedelta(seconds=1)) is not None
        )
        assert await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert await _counter(db) == 2

        # El lease del orphan expira; el reclaim no puede reenviar (2/2) y la
        # fila se libera a pending para otro día.
        await _expire_leases(db, now - timedelta(seconds=1))
        later = now + timedelta(seconds=1)
        reclaimed = await repo.claim_pending(
            _TEST_USER, worker_id="wB", now_utc=later, day_local=DAY, daily_limit=2, limit=10, lease_seconds=600
        )
        assert [r.id for r in reclaimed] == [orphan.id]
        assert not await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=2)
        assert await repo.unclaim(_TEST_USER, orphan.id, worker_id="wB", now_utc=later)

        statuses = await _status_counts(db)
        assert statuses.get("sent", 0) == 1
        assert statuses.get("pending", 0) == 1
        assert statuses.get("processing", 0) == 0
    finally:
        await _cleanup(db)
        await db.close()


@pytest.mark.asyncio
async def test_stress_10_workers_mezcla_fresh_y_expired_limite_5() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db, count=20)
        now = _now()
        repo = _repo(db)

        # Un worker "muerto" reclama 5 filas con lease corto y muere ANTES de
        # enviar: quedan processing con lease expirado (reclaimables).
        pre = await repo.claim_pending(
            _TEST_USER, worker_id="dead", now_utc=now, day_local=DAY, daily_limit=5, limit=5, lease_seconds=1
        )
        assert len(pre) == 5
        await _expire_leases(db, now - timedelta(seconds=1))

        async def worker(i: int) -> int:
            worker_id = f"mix-{i}"
            claimed = await repo.claim_pending(
                _TEST_USER,
                worker_id=worker_id,
                now_utc=now,
                day_local=DAY,
                daily_limit=5,
                limit=20,
                lease_seconds=600,
            )
            sent = 0
            for row in claimed:
                # La reserva (UPDATE atómico) es la barrera real del límite.
                if not await repo.reserve_send(_TEST_USER, day_local=DAY, daily_limit=5):
                    await repo.unclaim(_TEST_USER, row.id, worker_id=worker_id, now_utc=now)
                    continue
                settled = await repo.mark_sent(
                    _TEST_USER, row.id, worker_id=worker_id, sent_at=now + timedelta(seconds=1)
                )
                if settled is not None:
                    sent += 1
            return sent

        sent_by_worker = await asyncio.gather(*[worker(i) for i in range(10)])
        total_sent = sum(sent_by_worker)

        # 10 workers compiten por 20 candidatos (5 reclaims expirados + 15
        # fresh): el límite diario=5 se mantiene pese a la mezcla.
        assert total_sent == 5, f"el total enviado ({total_sent}) superó el límite diario=5"
        assert await _counter(db) == 5
        statuses = await _status_counts(db)
        assert statuses.get("sent", 0) == 5
        assert statuses.get("pending", 0) == 15
        assert statuses.get("processing", 0) == 0
    finally:
        await _cleanup(db)
        await db.close()


# ── helpers SQL ────────────────────────────────────────────────── #


async def _seed_user(db: Database) -> None:
    async with db.session() as session:
        await session.execute(
            sa.text(
                """
                INSERT INTO users (id, telegram_user_id, timezone, lang, settings)
                VALUES (:uid, :uid, 'Europe/Madrid', 'es-ES', '{}')
                ON CONFLICT (telegram_user_id) DO UPDATE SET telegram_user_id = EXCLUDED.telegram_user_id
                """
            ),
            {"uid": _TEST_USER},
        )
        await session.commit()


async def _seed(db: Database, *, count: int) -> None:
    async with db.session() as session:
        await session.execute(
            sa.text(
                """
                INSERT INTO users (id, telegram_user_id, timezone, lang, settings)
                VALUES (:uid, :uid, 'Europe/Madrid', 'es-ES', '{}')
                ON CONFLICT (telegram_user_id) DO UPDATE SET telegram_user_id = EXCLUDED.telegram_user_id
                """
            ),
            {"uid": _TEST_USER},
        )
        await session.execute(
            sa.text(
                """
                INSERT INTO proactive_notifications (user_id, kind, dedup_key, title, body, importance, status)
                SELECT :uid, :kind, 'pg-' || g, 'Titulo ' || g, 'Cuerpo ' || g, 3, 'pending'
                FROM generate_series(1, :count) AS g
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": _TEST_USER, "kind": _KIND, "count": count},
        )
        await session.commit()


async def _counter(db: Database) -> int:
    async with db.session() as session:
        result = await session.execute(
            sa.text("SELECT count FROM proactive_daily_counters WHERE user_id = :uid AND day = :day"),
            {"uid": _TEST_USER, "day": DAY},
        )
        return int(result.scalar() or 0)


async def _claim_info(db: Database) -> list[dict]:
    async with db.session() as session:
        result = await session.execute(
            sa.text(
                """
                SELECT id, worker_id, status, lease_until
                FROM proactive_notifications
                WHERE user_id = :uid AND status = 'processing'
                ORDER BY id
                """
            ),
            {"uid": _TEST_USER},
        )
        return [dict(row._mapping) for row in result]


async def _expire_leases(db: Database, before: datetime) -> None:
    async with db.session() as session:
        await session.execute(
            sa.text(
                """
                UPDATE proactive_notifications
                SET lease_until = :before
                WHERE user_id = :uid AND status = 'processing'
                """
            ),
            {"uid": _TEST_USER, "before": before},
        )
        await session.commit()


async def _status_counts(db: Database) -> dict:
    async with db.session() as session:
        result = await session.execute(
            sa.text("SELECT status, count(*) FROM proactive_notifications WHERE user_id = :uid GROUP BY status"),
            {"uid": _TEST_USER},
        )
        return {row[0]: int(row[1]) for row in result}


async def _cleanup(db: Database) -> None:
    async with db.session() as session:
        await session.execute(sa.text("DELETE FROM proactive_notifications WHERE user_id = :uid"), {"uid": _TEST_USER})
        await session.execute(sa.text("DELETE FROM proactive_daily_counters WHERE user_id = :uid"), {"uid": _TEST_USER})
        await session.execute(sa.text("DELETE FROM users WHERE id = :uid"), {"uid": _TEST_USER})
        await session.commit()
