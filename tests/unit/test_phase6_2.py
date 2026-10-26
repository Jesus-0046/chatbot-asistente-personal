"""Tests de Fase 6.2 / 6.2.1: claim/lease, límite diario y dedup del poller.

Cubren con fakes la semántica de claim/lease (propiedad, expiry y reclaim), la
reserva del límite diario EN EL ENVÍO (`reserve_send`/`release_send`/
`unclaim`, Fase 6.2.1), el flujo del poller, el manejo race-safe de dedup y el
aislamiento por usuario.

La verificación de concurrencia real de PostgreSQL (`FOR UPDATE SKIP LOCKED` +
UPDATE atómico del contador en `reserve_send`) vive en
`tests/integration/test_proactive_claim_concurrency_pg.py` y requiere
`TEST_DATABASE_URL`; aquí se validan las reglas de negocio puras.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from asistente.domain.proactivity import ProactiveCandidate
from asistente.domain.trips import TripService
from asistente.scheduler.proactive_poller import ProactivePoller
from asistente.shared.timeutil import utc_now

NOW = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)


def _candidate(kind: str, dedup: str, importance: int = 3) -> ProactiveCandidate:
    return ProactiveCandidate(kind=kind, dedup_key=dedup, title="T", body="B", importance=importance)


async def _claim(proactivity, *, worker="w1", now=NOW, daily_limit=3, limit=10, lease=600, user_id=1):
    return await proactivity.claim_pending(
        user_id,
        worker_id=worker,
        now_utc=now,
        day_local=NOW.date(),
        daily_limit=daily_limit,
        limit=limit,
        lease_seconds=lease,
    )


class _FakeSender:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send(self, user_id: int, message: str) -> None:
        self.sent.append((user_id, message))


class _BadSender:
    async def send(self, user_id: int, message: str) -> None:
        raise RuntimeError("send failed")


# ───────────────────────────────────────────────────────────────── #
# Claim (6)                                                          #
# ───────────────────────────────────────────────────────────────── #


async def test_claim_marca_processing_con_lease_sin_consumir_contador(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("c1", "k1"))

    claimed = await _claim(proactivity, worker="w1", daily_limit=3)

    assert len(claimed) == 1
    row = claimed[0]
    assert row.status == "processing"
    assert row.worker_id == "w1"
    assert row.claimed_at == NOW
    assert row.lease_until == NOW + timedelta(seconds=600)
    # Fase 6.2.1: el claim NO consume presupuesto (se reserva al ENVIAR).
    assert (1, NOW.date()) not in repo._counters


async def test_claim_fresh_sin_contador_limitado_por_hint(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(5):
        await proactivity.create_or_reuse(1, _candidate("c2", f"k{i}"))

    claimed = await _claim(proactivity, worker="w1", daily_limit=2, limit=10)

    assert len(claimed) == 2
    pending = [n for n in repo._store if n.status == "pending"]
    assert len(pending) == 3
    assert (1, NOW.date()) not in repo._counters


async def test_claim_sin_presupuesto_no_reclama_fresh(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(3):
        await proactivity.create_or_reuse(1, _candidate("c3", f"k{i}"))
    # El presupuesto del día se agota reservando en el envío (simula 2 envíos
    # ya emitidos hoy): el claim solo lo lee como hint y no reclama fresh.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)

    second = await _claim(proactivity, worker="w1", daily_limit=2, limit=10)

    assert second == []
    assert repo._counters[(1, NOW.date())] == 2


async def test_claim_vacio_sin_candidatos(services) -> None:
    proactivity = services["proactivity"]
    assert await _claim(proactivity, worker="w0") == []


async def test_claim_devuelve_reclaims_antes_que_fresh(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(3):
        await proactivity.create_or_reuse(1, _candidate("ord", f"r{i}"))
    # R0 con lease corto (expirará), R1 con lease largo (vigente), R2 pendiente.
    claim = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=NOW, day_local=NOW.date(), daily_limit=5, limit=1, lease_seconds=1
    )
    assert claim[0].dedup_key == "r0"
    # t1 llega ANTES de que expire el lease de r0 (ahí se reclama r1 fresh).
    t1 = NOW + timedelta(milliseconds=500)
    more = await proactivity.claim_pending(
        1, worker_id="w1", now_utc=t1, day_local=NOW.date(), daily_limit=5, limit=1, lease_seconds=600
    )
    assert more[0].dedup_key == "r1"
    # R2 sigue pending (fresh); pasará a ser reclamado tras un reclaim.

    t2 = NOW + timedelta(seconds=5)
    reclaimed = await proactivity.claim_pending(
        1, worker_id="w2", now_utc=t2, day_local=NOW.date(), daily_limit=5, limit=3, lease_seconds=600
    )
    # 1 reclaim (R0 expirado) + 1 fresh (R2): el reclaim va primero.
    assert [r.dedup_key for r in reclaimed] == ["r0", "r2"]
    assert [r.worker_id for r in reclaimed] == ["w2", "w2"]
    # Fase 6.2.1: ni el reclaim ni el fresh consumen presupuesto en el claim;
    # el contador solo cambia al reservar en el envío (reserve_send).
    assert (1, NOW.date()) not in repo._counters


async def test_claim_no_duplica_misma_fila_con_lease_vigente(services) -> None:
    proactivity = services["proactivity"]
    for i in range(3):
        await proactivity.create_or_reuse(1, _candidate("c4", f"k{i}"))
    first = await _claim(proactivity, worker="w1", daily_limit=5, limit=10)

    second = await _claim(proactivity, worker="w2", daily_limit=5, limit=10)

    assert len(first) == 3
    assert second == []


# ───────────────────────────────────────────────────────────────── #
# Lease (5)                                                          #
# ───────────────────────────────────────────────────────────────── #


async def test_lease_expirado_es_reclaimable(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.create_or_reuse(1, _candidate("l1", "k"))
    claimed = await _claim(proactivity, worker="w1", daily_limit=3, lease=60)
    row = claimed[0]

    later = NOW + timedelta(seconds=120)
    reclaimed = await _claim(proactivity, worker="w2", now=later, daily_limit=3)

    assert len(reclaimed) == 1
    assert reclaimed[0].id == row.id
    assert reclaimed[0].worker_id == "w2"
    assert reclaimed[0].lease_until == later + timedelta(seconds=600)


async def test_lease_vigente_no_reclaimable(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.create_or_reuse(1, _candidate("l2", "k"))
    await _claim(proactivity, worker="w1", daily_limit=3, lease=600)

    later = NOW + timedelta(seconds=300)
    second = await _claim(proactivity, worker="w2", now=later, daily_limit=3)

    assert second == []


async def test_mark_sent_denegado_tras_reclaim(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("l3", "k"))
    await _claim(proactivity, worker="wA", daily_limit=3, lease=60)
    later = NOW + timedelta(seconds=120)
    await _claim(proactivity, worker="wB", now=later, daily_limit=3)

    settled = await proactivity.mark_sent(1, repo._store[0].id, worker_id="wA", sent_at=later)

    assert settled is None
    assert repo._store[0].status == "processing"
    assert repo._store[0].worker_id == "wB"


async def test_mark_sent_denegado_sin_claim(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    row = await proactivity.create_or_reuse(1, _candidate("l4", "k"))

    settled = await proactivity.mark_sent(1, row.id, worker_id="w1", sent_at=NOW)

    assert settled is None
    assert repo._store[0].status == "pending"


async def test_mark_sent_limpia_claim_y_mueve_a_sent(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.create_or_reuse(1, _candidate("l5", "k"))
    claimed = await _claim(proactivity, worker="w1", daily_limit=3, lease=600)

    settled = await proactivity.mark_sent(1, claimed[0].id, worker_id="w1", sent_at=NOW)

    assert settled is not None
    assert settled.status == "sent"
    assert settled.sent_at == NOW
    assert settled.worker_id is None
    assert settled.claimed_at is None
    assert settled.lease_until is None


# ───────────────────────────────────────────────────────────────── #
# Poller (5)                                                         #
# ───────────────────────────────────────────────────────────────── #


async def test_poller_envía_tras_claim_y_limpia_lease(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.update_settings(1, enabled=True)
    now = utc_now()
    trips: TripService = services["trips"]
    await trips.create(
        user_id=1,
        name="Madrid",
        start_at=now + timedelta(days=1),
        end_at=now + timedelta(days=3),
    )

    sender = _FakeSender()
    poller = ProactivePoller(proactivity=proactivity, sender=sender, worker_id="poller-1", lease_seconds=600)
    stats = await poller.run_once()

    assert stats.sent >= 1
    sent_rows = [n for n in repo._store if n.status == "sent"]
    assert sent_rows
    assert all(n.worker_id is None and n.lease_until is None for n in sent_rows)
    assert all("Madrid" in msg for _, msg in sender.sent)
    # Cada envío reservó una unidad: el contador refleja los envíos reales.
    assert sum(repo._counters.values()) == stats.sent


async def test_poller_respeta_daily_limit(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.update_settings(1, enabled=True, daily_limit=1)
    now = utc_now()
    trips: TripService = services["trips"]
    await trips.create(user_id=1, name="A", start_at=now + timedelta(days=1), end_at=now + timedelta(days=2))
    await trips.create(user_id=1, name="B", start_at=now + timedelta(days=2), end_at=now + timedelta(days=3))

    poller = ProactivePoller(proactivity=proactivity, sender=_FakeSender(), worker_id="poller-1")
    stats = await poller.run_once()

    assert stats.sent == 1
    assert len([n for n in repo._store if n.status == "sent"]) == 1
    assert sum(repo._counters.values()) == 1


async def test_send_failure_deja_processing_y_es_reclaimable(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.update_settings(1, enabled=True)
    now = utc_now()
    trips: TripService = services["trips"]
    await trips.create(user_id=1, name="C", start_at=now + timedelta(days=1), end_at=now + timedelta(days=2))

    bad_poller = ProactivePoller(proactivity=proactivity, sender=_BadSender(), worker_id="bad", lease_seconds=60)
    stats = await bad_poller.run_once()
    assert stats.failed >= 1
    assert len([n for n in repo._store if n.status == "processing"]) >= 1
    assert len([n for n in repo._store if n.status == "sent"]) == 0
    # La entrega falló: el poller liberó la reserva (release_send).
    assert sum(repo._counters.values()) == 0

    # La fila queda processing con lease; al expirar, una pasada con buen
    # sender la reclama y la entrega (reservando una unidad en el envío).
    for n in repo._store:
        if n.status == "processing":
            n.lease_until = utc_now() - timedelta(seconds=1)
    good_poller = ProactivePoller(proactivity=proactivity, sender=_FakeSender(), worker_id="good")
    stats2 = await good_poller.run_once()
    assert stats2.sent >= 1
    assert len([n for n in repo._store if n.status == "sent"]) >= 1
    assert sum(repo._counters.values()) == stats2.sent


async def test_daily_limit_cero_marca_limited(services) -> None:
    proactivity = services["proactivity"]
    await proactivity.update_settings(1, enabled=True, daily_limit=0)
    now = utc_now()
    trips: TripService = services["trips"]
    await trips.create(user_id=1, name="D", start_at=now + timedelta(days=1), end_at=now + timedelta(days=2))

    poller = ProactivePoller(proactivity=proactivity, sender=_FakeSender())
    stats = await poller.run_once()

    assert stats.limited == 1
    assert stats.sent == 0


async def test_reclaim_entre_pasadas_reserva_solo_al_enviar(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.update_settings(1, enabled=True, daily_limit=2)
    now = utc_now()
    trips: TripService = services["trips"]
    for name in ("G", "H", "I"):
        await trips.create(user_id=1, name=name, start_at=now + timedelta(days=1), end_at=now + timedelta(days=2))

    # Pasada 1 con sender que falla: reclama 2 (hint del contador, que aún está
    # a 0) y cada envío fallido libera su reserva → contador vuelve a 0.
    stats1 = await ProactivePoller(
        proactivity=proactivity, sender=_BadSender(), worker_id="bad1", lease_seconds=60
    ).run_once()
    assert stats1.failed == 2
    assert len([n for n in repo._store if n.status == "processing"]) == 2
    assert sum(repo._counters.values()) == 0

    # Los leases expiran y la pasada 2 los reclaima. La reserva la hace el
    # ENVÍO (reserve_send), no el reclaim: el contador solo refleja entregas.
    for n in repo._store:
        if n.status == "processing":
            n.lease_until = utc_now() - timedelta(seconds=1)
    sender = _FakeSender()
    stats2 = await ProactivePoller(proactivity=proactivity, sender=sender, worker_id="good1").run_once()
    assert stats2.sent == 2
    assert len(sender.sent) == 2
    assert len([n for n in repo._store if n.status == "sent"]) == 2
    assert sum(n.status == "processing" for n in repo._store) == 0
    assert sum(repo._counters.values()) == 2


# ───────────────────────────────────────────────────────────────── #
# Límite diario (Fase 6.2.1)                                        #
# ───────────────────────────────────────────────────────────────── #


async def test_contador_mide_envios_no_claims(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(4):
        await proactivity.create_or_reuse(1, _candidate("d1", f"k{i}"))

    a = await _claim(proactivity, worker="w1", daily_limit=2, limit=10)
    b = await _claim(proactivity, worker="w2", daily_limit=2, limit=10)

    assert len(a) == 2
    assert len(b) == 2
    # El claim solo lee el contador como hint: reclamar N filas no reserva N.
    assert (1, NOW.date()) not in repo._counters

    # El límite real lo aplica reserve_send en el momento del envío.
    sent = 0
    for batch in (a, b):
        for row in batch:
            if await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2):
                sent += 1
                assert await proactivity.mark_sent(1, row.id, worker_id=row.worker_id, sent_at=NOW) is not None
            else:
                assert await proactivity.unclaim(1, row.id, worker_id=row.worker_id, now_utc=NOW)
    assert sent == 2
    assert repo._counters[(1, NOW.date())] == 2
    assert sum(n.status == "pending" for n in repo._store) == 2
    assert sum(n.status == "processing" for n in repo._store) == 0


async def test_veinte_candidatos_con_limite_cinco(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(20):
        await proactivity.create_or_reuse(1, _candidate("d3", f"k{i}"))

    claimed = await _claim(proactivity, worker="w1", daily_limit=5, limit=20)

    assert len(claimed) == 5
    assert len([n for n in repo._store if n.status == "pending"]) == 15
    # El claim no reservó presupuesto: el contador se mueve al enviar.
    assert (1, NOW.date()) not in repo._counters


async def test_mark_sent_no_alter_el_contador(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(3):
        await proactivity.create_or_reuse(1, _candidate("d4", f"k{i}"))
    claimed = await _claim(proactivity, worker="w1", daily_limit=3, limit=10)
    for _row in claimed:
        assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert repo._counters[(1, NOW.date())] == 3

    for row in claimed:
        await proactivity.mark_sent(1, row.id, worker_id="w1", sent_at=NOW)

    # mark_sent finaliza el claim; la reserva ya se pagó en reserve_send.
    assert repo._counters[(1, NOW.date())] == 3


async def test_diez_workers_no_superan_limite(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    for i in range(20):
        await proactivity.create_or_reuse(1, _candidate("d5", f"k{i}"))

    results = await asyncio.gather(
        *[
            proactivity.claim_pending(
                1,
                worker_id=f"w{i}",
                now_utc=NOW,
                day_local=NOW.date(),
                daily_limit=5,
                limit=20,
                lease_seconds=600,
            )
            for i in range(10)
        ]
    )
    claimed = [r for batch in results for r in batch]
    # El claim ya no frena el total (cada worker lee un contador a 0 y reclama
    # hasta 5 fresh): reclamar sin límite es seguro porque la invariante real
    # la impone reserve_send por fila.
    assert len(claimed) == 20
    assert len({r.id for r in claimed}) == 20

    sent = 0
    for row in claimed:
        if await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=5):
            sent += 1
            assert await proactivity.mark_sent(1, row.id, worker_id=row.worker_id, sent_at=NOW) is not None
        else:
            assert await proactivity.unclaim(1, row.id, worker_id=row.worker_id, now_utc=NOW)
    assert sent == 5
    assert repo._counters[(1, NOW.date())] == 5
    assert sum(n.status == "pending" for n in repo._store) == 15
    assert sum(n.status == "processing" for n in repo._store) == 0


# ───────────────────────────────────────────────────────────────── #
# Nueva semántica 6.2.1 (6)                                         #
# ───────────────────────────────────────────────────────────────── #


async def test_envio_fresh_reserva_y_contabiliza_una_unidad(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("f62", "k1"))
    row = (await _claim(proactivity, worker="w1", daily_limit=3))[0]

    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    await proactivity.mark_sent(1, row.id, worker_id="w1", sent_at=NOW)

    assert repo._counters[(1, NOW.date())] == 1
    assert repo._store[0].status == "sent"


async def test_fallo_del_sender_libera_la_reserva(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("sf", "k1"))
    await _claim(proactivity, worker="w1", daily_limit=3, lease=600)

    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    # El sender lanza → el poller llama release_send (entrega no emitida).
    await proactivity.release_send(1, day_local=NOW.date())

    assert repo._counters[(1, NOW.date())] == 0
    assert len([n for n in repo._store if n.status == "processing"]) == 1
    # El presupuesto vuelve a estar disponible para el reintento.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert repo._counters[(1, NOW.date())] == 1


async def test_crash_antes_del_envio_no_consume_y_reenvio_reserva_una_vez(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("cb", "k1"))
    row = (await _claim(proactivity, worker="w1", daily_limit=3, lease=600))[0]

    # Crash ANTES de reservar/enviar: ni contador ni entrega; fila processing.
    assert (1, NOW.date()) not in repo._counters
    later = NOW + timedelta(seconds=700)
    reclaimed = await _claim(proactivity, worker="w2", now=later, daily_limit=3, limit=10)
    assert len(reclaimed) == 1 and reclaimed[0].id == row.id

    # El reenvío reserva UNA unidad (la reserva se hace en el envío del
    # reintento, no en el claim fallido).
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert await proactivity.mark_sent(1, row.id, worker_id="w2", sent_at=later) is not None
    assert repo._counters[(1, NOW.date())] == 1
    assert repo._store[0].status == "sent"


async def test_crash_tras_envio_y_reclaim_reenvia_at_least_once(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("aco", "k1"))
    row = (await _claim(proactivity, worker="w1", daily_limit=3, lease=600))[0]

    # Envío 1: reserva + send exitoso, luego CRASH sin mark_sent. La reserva se
    # mantiene: su cupo ya está "comprado" (conservador contra sobre-envíos).
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)

    # El lease expira; otro worker reclaima y reenvía. Con daily_limit=3 hay
    # cupo para el reenvío, que se contabiliza como una nueva unidad.
    later = NOW + timedelta(seconds=700)
    reclaimed = await _claim(proactivity, worker="w2", now=later, daily_limit=3, limit=10)
    assert len(reclaimed) == 1 and reclaimed[0].id == row.id
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert await proactivity.mark_sent(1, row.id, worker_id="w2", sent_at=later) is not None

    # 2 envíos externos (original + reenvío) = 2 unidades reservadas.
    assert repo._counters[(1, NOW.date())] == 2
    assert sum(n.status == "sent" for n in repo._store) == 1
    # El límite es 3: aún cabe una tercera unidad, pero la cuarta ya no.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert not await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=3)
    assert repo._counters[(1, NOW.date())] == 3


async def test_reclaim_y_fresh_comparten_limite_diario(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("mix", "k1"))  # será el "huérfano"
    await proactivity.create_or_reuse(1, _candidate("mix", "k2"))  # fresh que confirma
    rows = await _claim(proactivity, worker="w1", daily_limit=2, limit=10)
    orphan, fresh = rows[0], rows[1]

    # El fresh se envía y confirma: reserva + mark_sent.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.mark_sent(1, fresh.id, worker_id=fresh.worker_id, sent_at=NOW) is not None
    # El huérfano también envió (reserva hecha), pero murió antes de confirmar.
    assert await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert repo._counters[(1, NOW.date())] == 2

    # Reclaim del huérfano (lease expirado): el límite (2/2) ya está lleno, así
    # que el reenvío se bloquea y la fila vuelve a pending.
    later = NOW + timedelta(seconds=700)
    reclaimed = await _claim(proactivity, worker="w2", now=later, daily_limit=2, limit=10)
    assert len(reclaimed) == 1 and reclaimed[0].id == orphan.id
    assert not await proactivity.reserve_send(1, day_local=NOW.date(), daily_limit=2)
    assert await proactivity.unclaim(1, orphan.id, worker_id="w2", now_utc=later)

    # Estado final: 1 sentido hoy (fresh), 1 pendiente para otro día, 0 en
    # processing, y el contador == envíos externos reales (2).
    assert sum(n.status == "sent" for n in repo._store) == 1
    assert sum(n.status == "pending" for n in repo._store) == 1
    assert sum(n.status == "processing" for n in repo._store) == 0
    assert repo._counters[(1, NOW.date())] == 2


# ───────────────────────────────────────────────────────────────── #
# Dedup race-safe (3)                                               #
# ───────────────────────────────────────────────────────────────── #


async def test_dedup_perdedor_recupera_fila_ganadora(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    candidate = _candidate("race", "clave")

    winner = await proactivity.create_or_reuse(1, candidate)
    assert winner is not None

    # El perdedor no vio la fila (find_any miss) pero su INSERT colisiona.
    repo.find_any_miss = True
    repo.fail_unique_create = True
    loser = await proactivity.create_or_reuse(1, candidate)

    assert loser is not None
    assert loser.id == winner.id
    assert loser.status == "pending"
    assert len([n for n in repo._store if n.id == winner.id]) == 1


async def test_dedup_reusa_fila_skipped(services) -> None:
    proactivity = services["proactivity"]
    candidate = _candidate("r2", "clave2")
    row = await proactivity.create_or_reuse(1, candidate)
    claimed = await _claim(proactivity, worker="w1", daily_limit=5, limit=10)
    assert any(n.id == row.id for n in claimed)
    await proactivity.mark_skipped(1, row.id, worker_id="w1", now_utc=NOW)

    re_attempt = await proactivity.create_or_reuse(1, candidate)

    assert re_attempt is not None
    assert re_attempt.id == row.id
    assert re_attempt.status == "pending"


async def test_integrity_error_no_dedup_se_relanza(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]

    async def bad_create(**kwargs):
        raise IntegrityError("INSERT", {}, Exception("FOREIGN KEY constraint failed: bar.id"))

    repo.create = bad_create  # type: ignore[method-assign, assignment]
    candidate = _candidate("r3", "clave3")
    with pytest.raises(IntegrityError):
        await proactivity.create_or_reuse(1, candidate)


# ───────────────────────────────────────────────────────────────── #
# Aislamiento por usuario (1)                                       #
# ───────────────────────────────────────────────────────────────── #


async def test_aislamiento_por_usuario(services) -> None:
    proactivity = services["proactivity"]
    repo = services["proactive_notifications_repo"]
    await proactivity.create_or_reuse(1, _candidate("u1", "k1"))

    # El claim del usuario 2 no debe tocar filas del usuario 1.
    stranger = await _claim(proactivity, now=NOW, daily_limit=5, user_id=2)
    assert stranger == []
    assert repo._store[0].status == "pending"
    assert repo._counters.get((1, NOW.date())) is None

    # mark_sent del usuario 2 sobre una fila del usuario 1 → None.
    assert await proactivity.mark_sent(2, repo._store[0].id, worker_id="wX", sent_at=NOW) is None
    assert repo._store[0].status == "pending"
