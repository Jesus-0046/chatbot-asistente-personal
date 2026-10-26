"""Repositorios de proactividad (Fase 6 / 6.2), scoped por user_id.

- `user_proactivity_settings`: preferencias por usuario (una fila por usuario,
  clave única). `get_or_create` crea los valores por defecto la primera vez.
- `proactive_notifications`: candidatos generados por el poller. La dedup la
  aplica el servicio comparando (user_id, kind, dedup_key) con entradas que NO
  sean `skipped` (las saltadas se pueden volver a intentar en otra pasada).
- `proactive_daily_counters`: contador atómico del límite diario local por
  usuario (Fase 6.2 / 6.2.1); ver `reserve_send`.

Claim real (Fase 6.2 / 6.2.1):
`claim_pending` hace de claim y lease: reclama filas con
`FOR UPDATE SKIP LOCKED` (PostgreSQL), pasándolas de `pending` a `processing`
con `worker_id`, `claimed_at` y `lease_until`. Las filas `processing` con
lease expirado se recuperan (reclaim). El presupuesto diario NO se reserva en
el claim: se aplica en `reserve_send`, justo antes de emitir cada envío, con
un UPDATE atómico sobre `proactive_daily_counters` (la fila bloqueada
serializa a los workers). Así la invariante es sobre ENVÍOS efectivos y no
sobre claims: incluso con reclaims y reenvíos tras un crash, los envíos
reales por usuario y día local nunca superan `daily_limit`. `release_send`
devuelve la reserva si el envío falla; `unclaim` devuelve a `pending` una fila
reclamada que ya no tiene presupuesto. `mark_sent`/`mark_skipped` verifican
que el worker siga siendo dueño: mismo `worker_id` y lease no expirado.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.dialects import postgresql, sqlite

from asistente.persistence.models import (
    ProactiveDailyCounter,
    ProactiveNotification,
    UserProactivitySettings,
)
from asistente.persistence.repositories.base import Repository

_SENT_STATUSES = ("sent",)
_DEFAULT_LEASE_SECONDS = 600


class UserProactivitySettingsRepository(Repository):
    async def find_one(self, user_id: int) -> UserProactivitySettings | None:
        async with self.db.session() as session:
            result = await session.execute(
                select(UserProactivitySettings).where(UserProactivitySettings.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def get_or_create(
        self,
        user_id: int,
        *,
        timezone: str = "Europe/Madrid",
    ) -> UserProactivitySettings:
        existing = await self.find_one(user_id)
        if existing is not None:
            return existing
        async with self.db.session() as session:
            row = UserProactivitySettings(user_id=user_id, timezone=timezone)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def upsert(
        self,
        user_id: int,
        *,
        enabled: bool | None = None,
        quiet_hours_start: str | None = None,
        quiet_hours_end: str | None = None,
        daily_limit: int | None = None,
        importance_threshold: int | None = None,
        timezone: str | None = None,
    ) -> UserProactivitySettings:
        row = await self.get_or_create(user_id, timezone=timezone or "Europe/Madrid")
        updates: dict[str, object] = {}
        if enabled is not None:
            updates["enabled"] = enabled
        if quiet_hours_start is not None:
            updates["quiet_hours_start"] = quiet_hours_start
        if quiet_hours_end is not None:
            updates["quiet_hours_end"] = quiet_hours_end
        if daily_limit is not None:
            updates["daily_limit"] = daily_limit
        if importance_threshold is not None:
            updates["importance_threshold"] = importance_threshold
        if timezone is not None:
            updates["timezone"] = timezone
        if not updates:
            return row
        async with self.db.session() as session:
            current = await session.get(UserProactivitySettings, row.id)
            if current is None:
                return row
            for key, value in updates.items():
                setattr(current, key, value)
            await session.commit()
            await session.refresh(current)
            return current

    async def list_enabled(self, *, limit: int = 500) -> list[UserProactivitySettings]:
        async with self.db.session() as session:
            result = await session.execute(
                select(UserProactivitySettings)
                .where(UserProactivitySettings.enabled.is_(True))
                .order_by(UserProactivitySettings.id.asc())
                .limit(limit)
            )
            return list(result.scalars().all())


class ProactiveNotificationRepository(Repository):
    async def find_existing(self, user_id: int, kind: str, dedup_key: str) -> ProactiveNotification | None:
        """Notificación previa del mismo hecho que NO fue saltada.

        `skipped` (horas de silencio o fuera de límite) no cuenta: el candidato
        se puede volver a dar por intentado en una pasada posterior.
        """
        async with self.db.session() as session:
            result = await session.execute(
                select(ProactiveNotification).where(
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.kind == kind,
                    ProactiveNotification.dedup_key == dedup_key,
                    ProactiveNotification.status != "skipped",
                )
            )
            return result.scalar_one_or_none()

    async def find_any(self, user_id: int, kind: str, dedup_key: str) -> ProactiveNotification | None:
        """Notificación previa del mismo hecho, sea cual sea su estado."""
        async with self.db.session() as session:
            result = await session.execute(
                select(ProactiveNotification).where(
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.kind == kind,
                    ProactiveNotification.dedup_key == dedup_key,
                )
            )
            return result.scalar_one_or_none()

    async def create(
        self,
        *,
        user_id: int,
        kind: str,
        dedup_key: str,
        title: str,
        body: str,
        importance: int = 2,
        status: str = "pending",
    ) -> ProactiveNotification:
        async with self.db.session() as session:
            row = ProactiveNotification(
                user_id=user_id,
                kind=kind,
                dedup_key=dedup_key,
                title=title,
                body=body,
                importance=importance,
                status=status,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def count_delivered_since(self, user_id: int, since_utc: datetime) -> int:
        async with self.db.session() as session:
            result = await session.execute(
                select(func.count(ProactiveNotification.id)).where(
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.status.in_(_SENT_STATUSES),
                    ProactiveNotification.sent_at.is_not(None),
                    ProactiveNotification.sent_at >= since_utc,
                )
            )
            return int(result.scalar_one() or 0)

    async def mark_sent(
        self,
        user_id: int,
        notification_id: int,
        *,
        worker_id: str,
        sent_at: datetime,
    ) -> ProactiveNotification | None:
        """Finaliza un claim como entregado, SI solo si el worker es dueño.

        Verifica que la fila siga en `processing`, que `worker_id` coincida con
        el reclamador y que el lease no haya expirado. Si otro worker recuperó
        la fila (reclaim) o el lease expiró, el UPDATE no afecta a ninguna fila
        y se devuelve `None` (el callander zombie NO puede marcar como enviada).
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(ProactiveNotification)
                .where(
                    ProactiveNotification.id == notification_id,
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.status == "processing",
                    ProactiveNotification.worker_id == worker_id,
                    ProactiveNotification.lease_until.is_not(None),
                    ProactiveNotification.lease_until > sent_at,
                )
                .values(
                    status="sent",
                    sent_at=sent_at,
                    claimed_at=None,
                    lease_until=None,
                    worker_id=None,
                )
                .returning(ProactiveNotification)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def mark_skipped(
        self,
        user_id: int,
        notification_id: int,
        *,
        worker_id: str,
        now_utc: datetime,
    ) -> ProactiveNotification | None:
        """Marca un claim como saltado, SI solo si el worker es dueño.

        Misma verificación de propiedad que `mark_sent`. Tras descartar una
        notificación reclamada, la fila queda `skipped` (fuera de la clave de
        dedup) y puede regenerarse en otra pasada.
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(ProactiveNotification)
                .where(
                    ProactiveNotification.id == notification_id,
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.status == "processing",
                    ProactiveNotification.worker_id == worker_id,
                    ProactiveNotification.lease_until.is_not(None),
                    ProactiveNotification.lease_until > now_utc,
                )
                .values(
                    status="skipped",
                    sent_at=None,
                    claimed_at=None,
                    lease_until=None,
                    worker_id=None,
                )
                .returning(ProactiveNotification)
            )
            row = result.scalar_one_or_none()
            await session.commit()
            return row

    async def mark_pending(
        self,
        notification_id: int,
        *,
        title: str,
        body: str,
        importance: int,
    ) -> ProactiveNotification | None:
        """Reutiliza una fila `skipped` como intento pendiente con datos frescos."""
        async with self.db.session() as session:
            row = await session.get(ProactiveNotification, notification_id)
            if row is None:
                return None
            row.status = "pending"
            row.sent_at = None
            row.title = title
            row.body = body
            row.importance = importance
            await session.commit()
            await session.refresh(row)
            return row

    async def _claim_select(self, session, *, user_id: int, status: str, now_utc: datetime, limit: int):
        """SELECT de candidatos a claim con `FOR UPDATE SKIP LOCKED` (PG).

        En SQLite el locking no se emite (no lo soporta), pero la semántica de
        estado + `commit` sigue protegiendo contra duplicados en un solo worker.
        """

        stmt = select(ProactiveNotification.id)
        if status == "processing":
            # Reclaim: filas reclamadas cuyo lease ya venció (proceso muerto).
            stmt = stmt.where(
                ProactiveNotification.user_id == user_id,
                ProactiveNotification.status == "processing",
                ProactiveNotification.worker_id.is_not(None),
                ProactiveNotification.lease_until.is_not(None),
                ProactiveNotification.lease_until < now_utc,
            )
        else:
            stmt = stmt.where(
                ProactiveNotification.user_id == user_id,
                ProactiveNotification.status == "pending",
            )
        stmt = stmt.order_by(ProactiveNotification.id.asc()).limit(limit)
        if session.get_bind().dialect.name == "postgresql":
            stmt = stmt.with_for_update(skip_locked=True)
        return stmt

    async def claim_pending(
        self,
        user_id: int,
        *,
        worker_id: str,
        now_utc: datetime,
        day_local: date,
        daily_limit: int,
        limit: int = 10,
        lease_seconds: int = _DEFAULT_LEASE_SECONDS,
    ) -> list[ProactiveNotification]:
        """Reclama atómicamente candidatos pendientes (claim + lease).

        Garantías (Fase 6.2.1):

        - Reclaim: las filas `processing` cuyo `lease_until` ya venció vuelven a
          ser elegibles y se devuelven SIEMPRE, sin tocar el contador.
        - Fresh: candidatos `pending` limitados por una ASPIRACIÓN best-effort:
          se lee el contador diario SIN bloqueo y no se reclaman más fresh de
          los que el día aún podría reservar
          (`min(limit - len(reclaims), max(0, daily_limit - counted))`).
        - El límite REAL lo impone `reserve_send` por fila en el momento del
          envío (UPDATE atómico con lock), no este claim. Por eso un claim puede
          devolver más filas que el presupuesto restante: el poller las libera
          con `unclaim` cuando `reserve_send` se las deniega.
        - Cada fila pasa a `processing` con `worker_id`, `claimed_at` y
          `lease_until = now_utc + lease_seconds`. Este método NO escribe en
          `proactive_daily_counters`.
        """
        lease_seconds = max(1, int(lease_seconds))

        async with self.db.session() as session:
            # 1) Lectura SIN lock del contador: solo informa el cap de fresh.
            counter_stmt = select(ProactiveDailyCounter.count).where(
                ProactiveDailyCounter.user_id == user_id,
                ProactiveDailyCounter.day == day_local,
            )
            counter_result = await session.execute(counter_stmt)
            counted = int(counter_result.scalar_one_or_none() or 0)
            remaining = max(0, daily_limit - counted)

            # 2) Reclaims de leases expirados (sin restricción de presupuesto).
            reclaim_stmt = await self._claim_select(
                session, user_id=user_id, status="processing", now_utc=now_utc, limit=limit
            )
            reclaim_rows = list((await session.execute(reclaim_stmt)).scalars().all())

            # 3) Candidatos fresh (pending), cap best-effort por el contador.
            fresh_limit = min(limit - len(reclaim_rows), remaining)
            if fresh_limit > 0:
                fresh_stmt = await self._claim_select(
                    session, user_id=user_id, status="pending", now_utc=now_utc, limit=fresh_limit
                )
                fresh_rows = list((await session.execute(fresh_stmt)).scalars().all())
            else:
                fresh_rows = []

            claim_ids = reclaim_rows + fresh_rows
            if not claim_ids:
                return []

            # 4) Marcar como processing con el lease del worker.
            result = await session.execute(
                update(ProactiveNotification)
                .where(ProactiveNotification.id.in_(claim_ids))
                .values(
                    status="processing",
                    worker_id=worker_id,
                    claimed_at=now_utc,
                    lease_until=now_utc + timedelta(seconds=lease_seconds),
                )
                .returning(ProactiveNotification)
            )
            claimed: list[ProactiveNotification] = list(result.scalars().all())
            claimed.sort(key=lambda n: n.id)
            await session.commit()
            return claimed

    async def reserve_send(self, user_id: int, *, day_local: date, daily_limit: int) -> bool:
        """Reserva atómicamente UNA unidad del límite diario (envío real).

        Invariante (Fase 6.2.1): para cada (user_id, day_local), el número de
        unidades reservadas (`count`) nunca supera `daily_limit`. Se ejecuta
        justo ANTES de emitir un envío: `UPDATE ... SET count = count + 1
        WHERE count < daily_limit RETURNING count`. El UPDATE bloquea la fila
        del contador, serializando a los workers incluso aunque el claim previo
        usara `FOR UPDATE SKIP LOCKED`. Devuelve True si había presupuesto (el
        envío puede emitirse) y False si ya estaba agotado. Cada envío que se
        emite compra su unidad aquí; un crash TRAS el envío mantiene la reserva
        (conservador, evita sobre-envíos), un crash ANTES no reservó nada.
        """
        if daily_limit <= 0:
            return False
        async with self.db.session() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                await session.execute(
                    postgresql.insert(ProactiveDailyCounter)
                    .values(user_id=user_id, day=day_local, count=0)
                    .on_conflict_do_nothing(index_elements=["user_id", "day"])
                )
            else:
                await session.execute(
                    sqlite.insert(ProactiveDailyCounter)
                    .values(user_id=user_id, day=day_local, count=0)
                    .on_conflict_do_nothing(index_elements=["user_id", "day"])
                )
            result = await session.execute(
                update(ProactiveDailyCounter)
                .where(
                    ProactiveDailyCounter.user_id == user_id,
                    ProactiveDailyCounter.day == day_local,
                    ProactiveDailyCounter.count < daily_limit,
                )
                .values(count=ProactiveDailyCounter.count + 1)
                .returning(ProactiveDailyCounter.count)
            )
            reserved = result.scalar_one_or_none() is not None
            await session.commit()
            return reserved

    async def release_send(self, user_id: int, *, day_local: date) -> None:
        """Devuelve la reserva de un envío que NO llegó a entregarse.

        El poller la invoca cuando su `sender.send()` lanza (entrega fallida):
        esa unidad de presupuesto vuelve a estar disponible para un reintento.
        Nunca deja el contador por debajo de 0 (`WHERE count > 0`, reforzado por
        el CHECK `ck_proactive_daily_counters_count`).
        """
        async with self.db.session() as session:
            await session.execute(
                update(ProactiveDailyCounter)
                .where(
                    ProactiveDailyCounter.user_id == user_id,
                    ProactiveDailyCounter.day == day_local,
                    ProactiveDailyCounter.count > 0,
                )
                .values(count=ProactiveDailyCounter.count - 1)
            )
            await session.commit()

    async def unclaim(
        self,
        user_id: int,
        notification_id: int,
        *,
        worker_id: str,
        now_utc: datetime,
    ) -> bool:
        """Devuelve una fila `processing` a `pending` si el worker aún la posee.

        Se usa cuando ya no queda presupuesto diario (`reserve_send` devuelve
        False): en vez de dejar la fila huérfana en `processing` (condenada a
        reclaims que tampoco podrán enviarla), se libera para que un día con
        presupuesto (u otra pasada tras `release_send`) vuelva a intentarla.
        Verificación de propiedad igual que `mark_sent`: mismo `worker_id` y
        lease no expirado.
        """
        async with self.db.session() as session:
            result = await session.execute(
                update(ProactiveNotification)
                .where(
                    ProactiveNotification.id == notification_id,
                    ProactiveNotification.user_id == user_id,
                    ProactiveNotification.status == "processing",
                    ProactiveNotification.worker_id == worker_id,
                    ProactiveNotification.lease_until.is_not(None),
                    ProactiveNotification.lease_until > now_utc,
                )
                .values(status="pending", sent_at=None, claimed_at=None, lease_until=None, worker_id=None)
                .returning(ProactiveNotification.id)
            )
            ok = result.scalar_one_or_none() is not None
            await session.commit()
            return ok
