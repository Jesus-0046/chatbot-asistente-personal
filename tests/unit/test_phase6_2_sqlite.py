"""Tests de repositorio real (Fase 6.2 / 6.2.1) sobre SQLite en archivo.

Verifican las transiciones de estado SQL del claim/lease, la reserva del
límite diario EN EL ENVÍO (`reserve_send`/`release_send`/`unclaim`) y el
manejo SQL del índice de dedup con repositorios REALES (`make_services`),
end-to-end hasta la BD. No intentan validar concurrencia (SQLite no ofrece
`FOR UPDATE SKIP LOCKED`); eso es responsabilidad de los tests de PostgreSQL
en `tests/integration/test_proactive_claim_concurrency_pg.py`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func as _func
from sqlalchemy import select as _select
from sqlalchemy.exc import IntegrityError
from tests.unit.helpers import make_services

from asistente.domain.proactivity import ProactiveCandidate
from asistente.persistence.db import Base, Database
from asistente.persistence.models import (
    ProactiveDailyCounter,
    ProactiveNotification,
)
from asistente.persistence.repositories import ProactiveNotificationRepository

NOW = datetime(2026, 9, 18, 8, 0, tzinfo=UTC)

_TABLES = [
    ProactiveNotification.__table__,
    ProactiveDailyCounter.__table__,
]


def _candidate(kind: str, dedup: str) -> ProactiveCandidate:
    return ProactiveCandidate(kind=kind, dedup_key=dedup, title="T", body="B", importance=3)


async def _counter_state(proactivity, user_id: int, day: date) -> tuple[int, int]:
    """Devuelve (nº de filas del contador, valor count) para (user_id, day)."""
    repo: ProactiveNotificationRepository = proactivity._notifications
    async with repo.db.session() as session:
        result = await session.execute(
            _select(
                _func.count(ProactiveDailyCounter.id),
                _func.coalesce(_func.max(ProactiveDailyCounter.count), 0),
            ).where(ProactiveDailyCounter.user_id == user_id, ProactiveDailyCounter.day == day)
        )
        nrows, value = result.one()
        return int(nrows), int(value)


async def _statuses(proactivity, user_id: int) -> dict[str, int]:
    """Mapa status -> nº de notificaciones del usuario."""
    repo: ProactiveNotificationRepository = proactivity._notifications
    async with repo.db.session() as session:
        result = await session.execute(
            _select(ProactiveNotification.status, _func.count())
            .where(ProactiveNotification.user_id == user_id)
            .group_by(ProactiveNotification.status)
        )
        return {status: int(count) for status, count in result}


@pytest.fixture
async def services(tmp_path):
    database = Database(url=f"sqlite+aiosqlite:///{tmp_path / 'proactive.db'}", echo=False)
    database.connect()
    async with database.engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=_TABLES))
    services = make_services(database)
    yield services
    await database.close()


async def test_repo_claim_y_reserve_flow(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "k1"))

    claimed = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=3, lease_seconds=600
    )

    assert len(claimed) == 1
    row = claimed[0]
    assert row.status == "processing"
    assert row.worker_id == "w1"
    # SQLite no conserva el offset; comparamos en hora naive.
    naive_now = NOW.replace(tzinfo=None)
    assert row.claimed_at == naive_now
    assert row.lease_until == naive_now + timedelta(seconds=600)

    # Fase 6.2.1: el claim NO crea el contador; la reserva ocurre al ENVIAR.
    nrows, value = await _counter_state(proactivity, 1, NOW.date())
    assert (nrows, value) == (0, 0)

    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    sent = await proactivity.mark_sent(1, row.id, worker_id="w1", sent_at=NOW + timedelta(seconds=1))
    assert sent is not None
    assert sent.status == "sent"
    assert sent.worker_id is None and sent.lease_until is None
    nrows, value = await _counter_state(proactivity, 1, NOW.date())
    assert (nrows, value) == (1, 1)


async def test_repo_claim_hint_y_reserva_limitan(services) -> None:
    proactivity = services.proactivity
    for i in range(4):
        await proactivity.create_or_reuse(1, _candidate("sql", f"k{i}"))

    first = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=2, limit=10
    )
    second = await proactivity.claim_pending(
        1, worker_id="w2", now_utc=NOW, day_local=NOW.date(), daily_limit=2, limit=10
    )
    # Cada worker lee un contador sin reservar (a 0) y reclama su cupo fresh...
    assert len(first) == 2
    assert len(second) == 2
    nrows, _ = await _counter_state(proactivity, 1, NOW.date())
    assert nrows == 0

    # ...pero el límite REAL lo impone reserve_send: UPDATE atómico
    # `count = count + 1 WHERE count < daily_limit`.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert not await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    nrows, value = await _counter_state(proactivity, 1, NOW.date())
    assert (nrows, value) == (1, 2)

    # Un tercer claim ya no reclama fresh (hint: contador en 2/2).
    third = await proactivity.claim_pending(
        1, worker_id="w3", now_utc=NOW, day_local=NOW.date(), daily_limit=2, limit=10
    )
    assert third == []


async def test_repo_mark_sent_requiere_propiedad(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "k"))
    claimed = await proactivity.claim_pending(
        1, worker_id="wA", now_utc=NOW, day_local=NOW.date(), daily_limit=3, lease_seconds=600
    )
    row = claimed[0]

    stolen = await proactivity.mark_sent(1, row.id, worker_id="wB", sent_at=NOW + timedelta(seconds=1))
    assert stolen is None

    ok = await proactivity.mark_sent(1, row.id, worker_id="wA", sent_at=NOW + timedelta(seconds=1))
    assert ok is not None and ok.status == "sent"


async def test_repo_lease_expirado_reclaim_y_zombie(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "k"))
    await proactivity.claim_pending(
        1, worker_id="wA", now_utc=NOW, day_local=NOW.date(), daily_limit=3, lease_seconds=60
    )

    later = NOW + timedelta(seconds=120)
    reclaimed = await proactivity.claim_pending(
        1, worker_id="wB", now_utc=later, day_local=NOW.date(), daily_limit=3, lease_seconds=600
    )
    assert len(reclaimed) == 1
    assert reclaimed[0].worker_id == "wB"

    zombie = await proactivity.mark_sent(1, reclaimed[0].id, worker_id="wA", sent_at=later)
    assert zombie is None

    ok = await proactivity.mark_sent(1, reclaimed[0].id, worker_id="wB", sent_at=later)
    assert ok is not None


async def test_repo_mark_skipped_verifica_lease(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "k"))
    await proactivity.claim_pending(
        1, worker_id="wA", now_utc=NOW, day_local=NOW.date(), daily_limit=3, lease_seconds=600
    )
    later = NOW + timedelta(days=10)
    expired = await proactivity.mark_skipped(1, 1, worker_id="wA", now_utc=later)
    assert expired is None

    ok = await proactivity.mark_skipped(1, 1, worker_id="wA", now_utc=NOW + timedelta(seconds=60))
    assert ok is not None
    assert ok.status == "skipped"


async def test_repo_dedup_conflicto_sql_se_recupera(services) -> None:
    proactivity = services.proactivity
    candidate = _candidate("sql", "kdup")
    row1 = await proactivity.create_or_reuse(1, candidate)

    # Espejo del índice único: un INSERT directo duplicado debe fallar.
    repo = proactivity._notifications
    with pytest.raises(IntegrityError):
        await repo.create(
            user_id=1,
            kind=candidate.kind,
            dedup_key=candidate.dedup_key,
            title="Dup",
            body="x",
            importance=3,
            status="pending",
        )

    # El servicio recupera la fila existente sin romperse.
    row2 = await proactivity.create_or_reuse(1, candidate)
    assert row2 is not None
    assert row2.id == row1.id

    # Marcada como sent → dedup devuelve None (no se reenvía otro día).
    claimed = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=5, lease_seconds=600
    )
    assert len(claimed) == 1
    await proactivity.mark_sent(1, row1.id, worker_id="w1", sent_at=NOW + timedelta(seconds=1))
    assert await proactivity.create_or_reuse(1, candidate) is None


async def test_repo_release_send_no_deja_contador_negativo(services) -> None:
    proactivity = services.proactivity
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    await proactivity.release_send(1, day_local=NOW.date())
    assert await _counter_state(proactivity, 1, NOW.date()) == (1, 1)

    await proactivity.release_send(1, day_local=NOW.date())
    # Liberar más veces de lo reservado no baja de 0 (guard count > 0).
    await proactivity.release_send(1, day_local=NOW.date())
    assert await _counter_state(proactivity, 1, NOW.date()) == (1, 0)


async def test_repo_unclaim_devuelve_fila_a_pending(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "k"))
    claimed = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=3, lease_seconds=600
    )
    row = claimed[0]

    # Un tercero no puede liberar la fila de w1.
    assert not await proactivity.unclaim(1, row.id, worker_id="wX", now_utc=NOW)
    # Con lease vigente, w1 devuelve su claim a pending.
    assert await proactivity.unclaim(1, row.id, worker_id="w1", now_utc=NOW)
    # Sin claim previo (la fila ya está pending) no hace nada.
    assert not await proactivity.unclaim(1, row.id, worker_id="w1", now_utc=NOW)
    assert await _statuses(proactivity, 1) == {"pending": 1}


async def test_repo_crash_tras_send_y_reclaim_respeta_limite(services) -> None:
    proactivity = services.proactivity
    await proactivity.create_or_reuse(1, _candidate("sql", "kA"))
    await proactivity.create_or_reuse(1, _candidate("sql", "kB"))
    rows = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=2, lease_seconds=600
    )
    orphan, fresh = rows[0], rows[1]

    # El fresh se entrega y confirma; el orphan "envía" y muere sin confirmar
    # (su reserva queda contabilizada).
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.mark_sent(1, fresh.id, worker_id="w1", sent_at=NOW + timedelta(seconds=1)) is not None
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await _counter_state(proactivity, 1, NOW.date()) == (1, 2)

    # Reclaim del orphan tras expirar su lease: límite lleno → sin reenvío.
    later = NOW + timedelta(seconds=700)
    reclaimed = await proactivity.claim_pending(
        1, worker_id="w2", now_utc=later, day_local=NOW.date(), daily_limit=2, limit=10
    )
    assert [r.id for r in reclaimed] == [orphan.id]
    assert not await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.unclaim(1, orphan.id, worker_id="w2", now_utc=later)

    assert await _statuses(proactivity, 1) == {"sent": 1, "pending": 1}
    assert await _counter_state(proactivity, 1, NOW.date()) == (1, 2)
