"""Poller de proactividad (capa scheduler, canal-agnóstico).

Recorre los usuarios con proactividad ACTIVA (`list_enabled_settings`) y, para
cada uno:

1. Respeta las horas de silencio: si `now` local cae en la franja, se salta al
   usuario sin persistir nada (se reintentará en la siguiente pasada).
2. Genera los candidatos (`build_candidates`), los filtra por `importance_threshold`
   y los ordena por importancia descendente.
3. La dedup la aplica `ProactivityService.create_or_reuse`: si el hecho ya se
   entregó (o ya está pendiente) no se crea una fila nueva. La creación es
   race-safe frente a otros workers (ver `create_or_reuse`).
4. Claim/lease (Fase 6.2): reclamación atómica vía
   `ProactivityService.claim_pending`, con reclaim de leases expirados y cap
   best-effort para candidatos fresh. Las filas reclamadas pasan a `processing`
   con `worker_id` y `lease_until`.
5. Presupuesto por envío (Fase 6.2.1): por cada fila reclamada se reserva una
   unidad del límite diario (`reserve_send`, UPDATE atómico sobre
   `proactive_daily_counters`) justo antes de emitir. Si no queda presupuesto,
   la fila vuelve a `pending` (`unclaim`) y se cuenta como `limited`. Tras el
   envío se finaliza con `mark_sent`, que solo tiene efecto si este worker sigue
   siendo el dueño del lease.

Semántica de entrega: "at least once" — la dedup previene duplicados entre
pasadas, pero un crash después del envío y ANTES de `mark_sent` puede causar un
reenvío (la entrega al canal es externa). Ese reenvío reserva una unidad nueva
del límite diario antes de emitirse. La invariante de Fase 6.2.1: los ENVÍOS
EXTERNOS reales por usuario y día local nunca superan `daily_limit`, aunque
haya reclaims y reenvíos. Un crash ANTES del envío no reserva nada y solo el
reintento que emite consume presupuesto. Un envío que falla en `sender.send()`
libera su reserva (`release_send`). NO se ofrecen garantías "exactly-once"; el
claim evita que dos workers envíen la MISMA fila a la vez, pero no puede evitar
el reenvío tras un crash.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from asistente.application.notifications import NotificationSender
from asistente.domain.proactivity import ProactivityService, in_quiet_hours
from asistente.logging_setup import get_logger
from asistente.shared.timeutil import resolve_timezone, to_utc, utc_now

logger = get_logger("scheduler.proactive_poller")


@dataclass
class ProactiveRunStats:
    users: int = 0
    sent: int = 0
    deduped: int = 0
    quiet: int = 0
    limited: int = 0
    failed: int = 0
    filtered: int = 0

    @property
    def total(self) -> int:
        return self.sent + self.deduped + self.quiet + self.limited + self.failed + self.filtered


class ProactivePoller:
    def __init__(
        self,
        proactivity: ProactivityService,
        sender: NotificationSender,
        *,
        max_users: int = 500,
        worker_id: str | None = None,
        lease_seconds: int = 600,
    ) -> None:
        self._proactivity = proactivity
        self._sender = sender
        self._max_users = max(1, max_users)
        # Identidad del worker: cada instancia del poller tiene un UUID propio
        # que firma sus claims y verifica su propiedad en mark_sent.
        self._worker_id = worker_id or str(uuid.uuid4())
        self._lease_seconds = max(1, int(lease_seconds))

    async def run_once(self) -> ProactiveRunStats:
        now = utc_now()
        stats = ProactiveRunStats()
        enabled = await self._proactivity.list_enabled_settings(limit=self._max_users)
        for settings in enabled:
            stats.users += 1
            user_id = int(settings["user_id"])
            timezone = settings.get("timezone") or "Europe/Madrid"
            now_local = now.astimezone(resolve_timezone(timezone))
            if in_quiet_hours(
                now_local,
                settings.get("quiet_hours_start", ""),
                settings.get("quiet_hours_end", ""),
            ):
                stats.quiet += 1
                continue
            daily_limit = int(settings.get("daily_limit", 0))
            if daily_limit <= 0:
                stats.limited += 1
                continue
            importance_threshold = int(settings.get("importance_threshold", 3))
            day_local = now_local.date()
            sent = await self._deliver(
                user_id,
                now=now,
                timezone=timezone,
                day_local=day_local,
                daily_limit=daily_limit,
                importance_threshold=importance_threshold,
                stats=stats,
            )
            if sent:
                logger.info("proactive_delivered", user_id=user_id, count=sent)
        logger.info(
            "proactive_poll_done",
            users=stats.users,
            sent=stats.sent,
            deduped=stats.deduped,
            quiet=stats.quiet,
            limited=stats.limited,
            failed=stats.failed,
            filtered=stats.filtered,
        )
        return stats

    async def _deliver(
        self,
        user_id: int,
        *,
        now: datetime,
        timezone: str,
        day_local: date,
        daily_limit: int,
        importance_threshold: int,
        stats: ProactiveRunStats,
    ) -> int:
        candidates = await self._proactivity.build_candidates(user_id, now, timezone)
        candidates.sort(key=lambda c: c.importance, reverse=True)
        filtered_candidates = [c for c in candidates if c.importance >= importance_threshold]
        stats.filtered += len(candidates) - len(filtered_candidates)
        candidates = filtered_candidates
        if not candidates:
            return 0

        # Persistir los candidatos de la pasada (dedup race-safe).
        for candidate in candidates:
            row = await self._proactivity.create_or_reuse(user_id, candidate)
            if row is None:
                stats.deduped += 1

        # Claim atómico (lease) SIN reservar presupuesto: la reserva real se hace
        # por fila en `reserve_send`, justo antes de emitir cada envío.
        claimed = await self._proactivity.claim_pending(
            user_id,
            worker_id=self._worker_id,
            now_utc=now,
            day_local=day_local,
            daily_limit=daily_limit,
            limit=len(candidates),
            lease_seconds=self._lease_seconds,
        )
        delivered = 0
        for row in claimed:
            # Presupuesto por ENVÍO: si ya se agotó, la fila vuelve a `pending` y
            # no se emite nada (invariante: envíos reales <= daily_limit).
            if not await self._proactivity.reserve_send(user_id, day_local=day_local, daily_limit=daily_limit):
                if await self._proactivity.unclaim(user_id, row.id, worker_id=self._worker_id, now_utc=now):
                    stats.limited += 1
                else:
                    # Perdimos la propiedad justo ahora (lease/reclaim): otro
                    # worker gestiona la fila; no contamos nada aquí.
                    logger.info("proactive_unclaim_contended", user_id=user_id, notification_id=row.id)
                continue
            try:
                await self._sender.send(user_id, f"{row.title}\n{row.body}")
            except Exception as exc:  # noqa: BLE001
                logger.warning("proactive_send_failed", user_id=user_id, kind=row.kind, error=repr(exc))
                stats.failed += 1
                # La entrega falló (no se emitió): se libera la reserva para que
                # el reintento (reclaim al expirar el lease) vuelva a tener cupo.
                await self._proactivity.release_send(user_id, day_local=day_local)
                continue
            settled = await self._proactivity.mark_sent(user_id, row.id, worker_id=self._worker_id, sent_at=now)
            if settled is None:
                # La reserva se mantiene contabilizada aunque no confirmemos la
                # fila: el envío ya se emitió y un reclaim posterior tendrá que
                # reservar de nuevo (contado) si queda presupuesto. Así los
                # reenvíos nunca superan el límite diario.
                continue
            delivered += 1
            stats.sent += 1
        return delivered


def start_of_local_day_utc(now_local: datetime, timezone: str) -> datetime:
    day_start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return to_utc(day_start_local)


__all__ = ["ProactivePoller", "ProactiveRunStats", "start_of_local_day_utc"]
