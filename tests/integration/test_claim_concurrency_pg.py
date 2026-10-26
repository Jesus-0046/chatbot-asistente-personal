"""Integración PG: claim atómico concurrente (omitido sin TEST_DATABASE_URL).

Uso:
  TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration -m db

Verifica el invariante de la Fase 2.1 sobre PostgreSQL real: dos workers
(`claim_due` concurrentes con FOR UPDATE SKIP LOCKED) reclamando el mismo
lote NO pueden quedarse el mismo recordatorio, y el claim queda persistido
en `processing` + `claimed_at` (incluso si el proceso muriese después).
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from asistente.persistence.db import Database
from asistente.persistence.repositories import ReminderRepository

pytestmark = pytest.mark.db

_TEST_USER = 919191
_LEASE = timedelta(minutes=5)


def _now() -> datetime:
    return datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_claim_due_concurrente_no_reclama_dos_veces() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await _seed(db)
        due_before = await _count_due(db)

        now = _now()
        lease = _LEASE

        repo_a = ReminderRepository(db)
        repo_b = ReminderRepository(db)

        async def claim(repo: ReminderRepository) -> list:
            return await repo.claim_due(now, 50, lease=lease)

        result_a, result_b = await asyncio.gather(claim(repo_a), claim(repo_b))
        ids_a = {r.id for r in result_a}
        ids_b = {r.id for r in result_b}

        assert ids_a.isdisjoint(ids_b), "two workers reclaimed the same reminder"
        assert len(ids_a) + len(ids_b) == due_before

        rows = await _status_counts(db)
        assert rows.get("processing", 0) == len(ids_a) + len(ids_b), "claims must persist in processing"
        assert all(r.claimed_at is not None for r in result_a + result_b), "lease must be stamped"
    finally:
        await _cleanup(db)
        await db.close()


async def _seed(db: Database) -> None:
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
                INSERT INTO reminders (user_id, message, remind_at, timezone, status)
                SELECT :uid, 'concurrente-' || g, now() - interval '1 hour', 'Europe/Madrid', 'pending'
                FROM generate_series(1, 10) AS g
                ON CONFLICT DO NOTHING
                """
            ),
            {"uid": _TEST_USER},
        )
        await session.commit()


async def _count_due(db: Database) -> int:
    async with db.session() as session:
        result = await session.execute(
            sa.text("SELECT count(*) FROM reminders WHERE user_id = :uid AND status = 'pending' AND remind_at <= :now"),
            {"uid": _TEST_USER, "now": _now()},
        )
        return int(result.scalar() or 0)


async def _status_counts(db: Database) -> dict:
    async with db.session() as session:
        result = await session.execute(
            sa.text("SELECT status, count(*) FROM reminders WHERE user_id = :uid GROUP BY status"),
            {"uid": _TEST_USER},
        )
        return {row[0]: int(row[1]) for row in result}


async def _cleanup(db: Database) -> None:
    async with db.session() as session:
        await session.execute(sa.text("DELETE FROM reminders WHERE user_id = :uid"), {"uid": _TEST_USER})
        await session.execute(sa.text("DELETE FROM users WHERE id = :uid"), {"uid": _TEST_USER})
        await session.commit()
