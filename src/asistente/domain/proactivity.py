"""Servicio de dominio de proactividad (Fase 6 / 6.2 / 6.2.1).

La proactividad es OPT-IN y está controlada: el usuario la activa con
`update_settings(enabled=True)` y el poller respeta sus horas de silencio
(`quiet_hours_start`/`quiet_hours_end`, "HH:MM" en la zona del usuario),
su límite diario (`daily_limit`) y la clave de dedup (user_id, kind,
dedup_key) para no repetir la misma notificación.

`build_candidates` genera los candidatos de la pasada (viajes próximos,
resumen del día, atrasos y semana en curso) con una `importance` 1..3. La
entrega real la hace `ProactivePoller` (capa scheduler); aquí solo se modelan
datos y reglas, como el resto de servicios de dominio.

Fase 6.2 / 6.2.1 (concurrencia): `claim_pending`/`mark_sent`/`mark_skipped`
exponen el claim con lease al poller, y `reserve_send`/`release_send`/`unclaim`
aplican el límite diario EN EL ENVÍO: cada envío compra su unidad de manera
atómica antes de emitirse, de modo que los envíos reales por usuario y día
local nunca superan `daily_limit` (los reclaims y reenvíos tras un crash
consumen unidades nuevas). `create_or_reuse` tolera la carrera de dedup: si dos
workers insertan el mismo (user_id, kind, dedup_key), el perdedor recibe
IntegrityError, recupera la fila ganadora y la reutiliza, sin tragarse errores
de integridad ajenos al índice de dedup.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy.exc import IntegrityError

from asistente.domain.agenda import AgendaService
from asistente.domain.planned import PlannedTransactionService
from asistente.persistence.models import ProactiveNotification
from asistente.persistence.repositories import (
    ProactiveNotificationRepository,
    TaskRepository,
    TripChecklistItemRepository,
    TripRepository,
    UserProactivitySettingsRepository,
)
from asistente.shared.errors import BusinessError
from asistente.shared.timeutil import resolve_timezone, to_local, to_utc

_DEDUP_INDEX_NAME = "ix_proactive_notifications_user_kind_dedup"

DEFAULT_QUIET_START = "22:00"
DEFAULT_QUIET_END = "08:00"
DEFAULT_DAILY_LIMIT = 3
_HHMM_RE = re.compile(r"^([0-1][0-9]|2[0-3]):[0-5][0-9]$")


def _is_dedup_error(exc: IntegrityError) -> bool:
    """True si el IntegrityError viene del índice único de dedup.

    Detecta el nombre del índice en la excepción nativa (asyncpg expone
    `constraint_name`, psycopg `diag.constraint_name`) y, como último recurso,
    el mensaje (SQLite). Cualquier otra violación de integridad devuelve False
    para no ocultar errores reales.
    """
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    constraint = getattr(orig, "constraint_name", None)
    if constraint is None:
        constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if constraint:
        return str(constraint) == _DEDUP_INDEX_NAME
    message = str(getattr(orig, "message", orig)).lower()
    return "dedup_key" in message or _DEDUP_INDEX_NAME in message


@dataclass(frozen=True, slots=True)
class ProactiveCandidate:
    kind: str
    dedup_key: str
    title: str
    body: str
    importance: int


def in_quiet_hours(now_local: datetime, quiet_start: str, quiet_end: str) -> bool:
    """True si `now_local` cae dentro de la franja de silencio "HH:MM".

    Intervalos nocturnos (start > end, p. ej. 22:00→08:00) atraviesan
    medianoche; start == end se interpreta como sin franja.
    """
    now_txt = now_local.strftime("%H:%M")
    start = (quiet_start or DEFAULT_QUIET_START).strip()
    end = (quiet_end or DEFAULT_QUIET_END).strip()
    if start == end:
        return False
    if start < end:
        return start <= now_txt < end
    return now_txt >= start or now_txt < end


class ProactivityService:
    def __init__(
        self,
        settings_repo: UserProactivitySettingsRepository,
        notifications_repo: ProactiveNotificationRepository,
        agenda: AgendaService,
        tasks: TaskRepository,
        trips: TripRepository,
        checklist: TripChecklistItemRepository,
        planned: PlannedTransactionService,
        vehicles=None,
        homes=None,
        reminders=None,
    ) -> None:
        self._settings = settings_repo
        self._notifications = notifications_repo
        self._agenda = agenda
        self._tasks = tasks
        self._trips = trips
        self._checklist = checklist
        self._planned = planned
        self._vehicles = vehicles
        self._homes = homes
        self._reminders = reminders

    # ------------------------------------------------------------------ #
    # Configuración                                                       #
    # ------------------------------------------------------------------ #
    async def get_settings(self, user_id: int) -> dict:
        settings = await self._settings.find_one(user_id)
        if settings is None:
            return {
                "user_id": user_id,
                "enabled": False,
                "quiet_hours_start": DEFAULT_QUIET_START,
                "quiet_hours_end": DEFAULT_QUIET_END,
                "daily_limit": DEFAULT_DAILY_LIMIT,
                "importance_threshold": 3,
                "timezone": "Europe/Madrid",
            }
        return {
            "user_id": user_id,
            "enabled": settings.enabled,
            "quiet_hours_start": settings.quiet_hours_start,
            "quiet_hours_end": settings.quiet_hours_end,
            "daily_limit": settings.daily_limit,
            "importance_threshold": settings.importance_threshold,
            "timezone": settings.timezone,
        }

    async def list_enabled_settings(self, *, limit: int = 500) -> list[dict]:
        rows = await self._settings.list_enabled(limit=limit)
        return [
            {
                "user_id": row.user_id,
                "enabled": row.enabled,
                "quiet_hours_start": row.quiet_hours_start,
                "quiet_hours_end": row.quiet_hours_end,
                "daily_limit": row.daily_limit,
                "importance_threshold": row.importance_threshold,
                "timezone": row.timezone,
            }
            for row in rows
        ]

    async def update_settings(
        self,
        user_id: int,
        *,
        enabled: bool | None = None,
        quiet_hours_start: str | None = None,
        quiet_hours_end: str | None = None,
        daily_limit: int | None = None,
        importance_threshold: int | None = None,
        timezone: str | None = None,
    ) -> dict:
        if quiet_hours_start is not None:
            self._validate_quiet_hour(quiet_hours_start)
        if quiet_hours_end is not None:
            self._validate_quiet_hour(quiet_hours_end)
        if daily_limit is not None:
            if not isinstance(daily_limit, int):
                raise BusinessError("El límite diario debe ser un número entero.")
            if not (0 <= daily_limit <= 50):
                raise BusinessError("El límite diario debe estar entre 0 y 50.")
        if importance_threshold is not None:
            if importance_threshold not in (1, 2, 3):
                raise BusinessError("El umbral de importancia debe ser 1, 2 o 3.")
        if timezone is not None:
            resolve_timezone(timezone)
        await self._settings.upsert(
            user_id,
            enabled=enabled,
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            daily_limit=daily_limit,
            importance_threshold=importance_threshold,
            timezone=timezone,
        )
        return await self.get_settings(user_id)

    @staticmethod
    def _validate_quiet_hour(value: str) -> None:
        text = (value or "").strip()
        if len(text) != 5 or text[2] != ":" or _HHMM_RE.match(text) is None:
            raise BusinessError("Las horas de silencio deben tener formato HH:MM (24h).")

    # ------------------------------------------------------------------ #
    # Generación de candidatos                                            #
    # ------------------------------------------------------------------ #
    async def build_candidates(self, user_id: int, now_utc: datetime, timezone: str) -> list[ProactiveCandidate]:
        candidates: list[ProactiveCandidate] = []
        local = now_utc.astimezone(resolve_timezone(timezone))
        local_date = local.date()

        # --- Viajes próximos ------------------------------------------ #
        soon = await self._trips.list_in_window(user_id, now_utc, now_utc + timedelta(days=2))
        upcoming = await self._trips.list_in_window(user_id, now_utc + timedelta(days=2), now_utc + timedelta(days=7))
        soon_ids = {t.id for t in soon}
        for trip in soon + upcoming:
            # El "hoy/mañana/en X días" se calcula en la zona local del usuario,
            # no sobre la representación UTC almacenada (evita desfases de ±1 día).
            days_left = (to_local(trip.start_at or local, timezone).date() - local_date).days
            importance = 3 if trip.id in soon_ids else 2
            body = f"Tienes el viaje «{trip.name}»"
            if trip.destination:
                body += f" a {trip.destination}"
            body += f". Empieza el {local_str(trip.start_at, timezone)}"
            if days_left <= 0:
                body += " (hoy)."
            elif days_left == 1:
                body += " (mañana)."
            else:
                body += f" (en {days_left} días)."
            pending = await self._checklist.count_open(user_id, trip.id)
            if pending:
                body += f" Quedan {pending} ítems pendientes en su checklist."
            candidates.append(
                ProactiveCandidate(
                    kind="trip_reminder",
                    dedup_key=f"trip:{trip.id}",
                    title="Viaje próximo",
                    body=body,
                    importance=importance,
                )
            )

        # --- Resumen del día ------------------------------------------ #
        day_start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
        day_start_utc = to_utc(day_start_local)
        day_end_utc = to_utc(day_start_local + timedelta(days=1))
        agenda_today = await self._agenda.during_period(user_id, day_start_utc, day_end_utc, timezone=timezone)
        planned_today = await self._planned.list_planned(
            user_id, status="planned", start_utc=day_start_utc, end_utc=day_end_utc, limit=100
        )
        parts = []
        day_events = len(agenda_today["events"])
        day_tasks = len(agenda_today["tasks"])
        day_planned = len(planned_today)
        if day_events:
            parts.append(f"{day_events} evento(s) hoy")
        if day_tasks:
            parts.append(f"{day_tasks} tarea(s) con vencimiento hoy")
        if day_planned:
            parts.append(f"{day_planned} pago(s) planificado(s) hoy")
        if parts:
            candidates.append(
                ProactiveCandidate(
                    kind="day_summary",
                    dedup_key=f"day:{local_date}",
                    title="Resumen de hoy",
                    body="Hoy tienes: " + ", ".join(parts) + ".",
                    importance=2,
                )
            )

        # --- Atrasos ------------------------------------------------ #
        tasks = await self._tasks.list_pending(user_id, limit=300)
        overdue_tasks = [t for t in tasks if t.due_at is not None and t.due_at < now_utc]
        if overdue_tasks:
            n = len(overdue_tasks)
            sample = " · ".join(t.title for t in overdue_tasks[:3])
            suffix = " y más." if n > 3 else ("." if n == 1 else ".")
            candidates.append(
                ProactiveCandidate(
                    kind="overdue_digest",
                    dedup_key=f"overdue:{local_date}",
                    title="Tienes cosas atrasadas",
                    body=f"Tienes {n} tarea(s) vencida(s): {sample}{suffix}",
                    importance=3,
                )
            )

        # --- Semana en curso ----------------------------------------- #
        end_week = now_utc + timedelta(days=7)
        agenda_week = await self._agenda.during_period(user_id, now_utc, end_week, timezone=timezone)
        trips_week = await self._trips.list_in_window(user_id, now_utc, end_week)
        week_parts = []
        week_events = len(agenda_week["events"])
        if week_events:
            week_parts.append(f"{week_events} evento(s)")
        if trips_week:
            week_parts.append(f"{len(trips_week)} viaje(s)")
        if week_parts:
            candidates.append(
                ProactiveCandidate(
                    kind="week_summary",
                    dedup_key=f"week:{local_date.isocalendar()[0]}-W{local_date.isocalendar()[1]}",
                    title="Semana en curso",
                    body="Los próximos 7 días tienes " + ", ".join(week_parts) + ".",
                    importance=2,
                )
            )

        # --- Mantenimiento de vehículos --------------------------------- #
        if self._vehicles is not None:
            try:
                exp = await self._vehicles.get_expirations(user_id)
                for v_item in exp.get("expirations", []):
                    v = v_item["vehicle"]
                    for ins in v_item.get("insurance", []):
                        if ins.end_date is not None:
                            days_left = (ins.end_date - local_date).days
                            imp = 3 if days_left <= 7 else 2
                            candidates.append(
                                ProactiveCandidate(
                                    kind="vehicle_insurance_expiry",
                                    dedup_key=f"vins:{v.id}:{ins.id}",
                                    title="Seguro de vehículo por vencer",
                                    body=f"El seguro de «{v.name}» vence el {ins.end_date.isoformat()} ({days_left} días).",
                                    importance=imp,
                                )
                            )
                    for m in v_item.get("itv", []):
                        if m.next_due_at is not None:
                            due_local = to_local(m.next_due_at, timezone).date()
                            days_left = (due_local - local_date).days
                            imp = 3 if days_left <= 7 else 2
                            candidates.append(
                                ProactiveCandidate(
                                    kind="vehicle_itv_expiry",
                                    dedup_key=f"vitv:{v.id}:{m.id}",
                                    title="ITV próxima",
                                    body=f"La ITV de «{v.name}» vence el {due_local.isoformat()} ({days_left} días).",
                                    importance=imp,
                                )
                            )
            except Exception:
                pass

        # --- Mantenimiento de casas ------------------------------------- #
        if self._homes is not None:
            try:
                upcoming = await self._homes.get_upcoming(user_id)
                for m in upcoming.get("upcoming", [])[:5]:
                    if m.next_due_at is not None:
                        due_local = to_local(m.next_due_at, timezone).date()
                        days_left = (due_local - local_date).days
                        imp = 3 if days_left <= 3 else 2
                        candidates.append(
                            ProactiveCandidate(
                                kind="home_maintenance",
                                dedup_key=f"hm:{m.id}",
                                title="Mantenimiento del hogar",
                                body=f"«{m.description}» vence el {due_local.isoformat()} ({days_left} días).",
                                importance=imp,
                            )
                        )
            except Exception:
                pass

        # --- Recordatorios pendientes ----------------------------------- #
        if self._reminders is not None:
            try:
                rems = await self._reminders.list_pending(user_id, limit=10)
                for r in rems:
                    remind_local = to_local(r.remind_at, timezone).date()
                    days_left = (remind_local - local_date).days
                    imp = 3 if days_left <= 1 else 2
                    candidates.append(
                        ProactiveCandidate(
                            kind="pending_reminder",
                            dedup_key=f"rem:{r.id}",
                            title="Recordatorio pendiente",
                            body=f"«{r.message}» programado para {remind_local.isoformat()}.",
                            importance=imp,
                        )
                    )
            except Exception:
                pass

        return candidates

    # ------------------------------------------------------------------ #
    # Persistencia / dedup                                               #
    # ------------------------------------------------------------------ #
    async def create_or_reuse(self, user_id: int, candidate: ProactiveCandidate) -> ProactiveNotification | None:
        """Crea la notificación o devuelve la fila previa del mismo hecho.

        - Sin fila previa → crea `pending`.
        - Fila previa `pending` → la devuelve (el poller reintenta el envío).
        - Fila previa `sent` → devuelve None (ya entregada, no se duplica).
        - Fila previa `skipped` → la reutiliza como intento pendiente.

        Race-safe (Fase 6.2): dos workers pueden ver "sin fila" a la vez e
        intentar insertar el mismo dedup. El INSERT perdedor levanta
        IntegrityError del índice único parcial; entonces se recupera la fila
        ganadora y se reutiliza. Un IntegrityError que NO venga del índice de
        dedup se re-lanza.
        """
        existing = await self._notifications.find_any(user_id, candidate.kind, candidate.dedup_key)
        if existing is not None:
            if existing.status == "sent":
                return None
            if existing.status == "skipped":
                return await self._notifications.mark_pending(
                    existing.id,
                    title=candidate.title,
                    body=candidate.body,
                    importance=candidate.importance,
                )
            return existing
        try:
            return await self._notifications.create(
                user_id=user_id,
                kind=candidate.kind,
                dedup_key=candidate.dedup_key,
                title=candidate.title,
                body=candidate.body,
                importance=candidate.importance,
                status="pending",
            )
        except IntegrityError as exc:
            if not _is_dedup_error(exc):
                raise
            won = await self._notifications.find_any(user_id, candidate.kind, candidate.dedup_key)
            if won is None:
                raise
            if won.status == "sent":
                return None
            if won.status == "skipped":
                return await self._notifications.mark_pending(
                    won.id,
                    title=candidate.title,
                    body=candidate.body,
                    importance=candidate.importance,
                )
            return won

    async def count_delivered_since(self, user_id: int, since_utc: datetime) -> int:
        return await self._notifications.count_delivered_since(user_id, since_utc)

    async def claim_pending(
        self,
        user_id: int,
        *,
        worker_id: str,
        now_utc: datetime,
        day_local: date,
        daily_limit: int,
        limit: int = 10,
        lease_seconds: int = 600,
    ) -> list[ProactiveNotification]:
        """Reclama candidatos pendientes con lease (Fase 6.2).

        Véase `ProactiveNotificationRepository.claim_pending`: el claim marca a
        `processing` con `worker_id` y `lease_until` y NO reserva presupuesto;
        el límite diario se impone por fila en `reserve_send` al enviar.
        """
        return await self._notifications.claim_pending(
            user_id,
            worker_id=worker_id,
            now_utc=now_utc,
            day_local=day_local,
            daily_limit=daily_limit,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def mark_sent(
        self,
        user_id: int,
        notification_id: int,
        *,
        worker_id: str,
        sent_at: datetime,
    ) -> ProactiveNotification | None:
        return await self._notifications.mark_sent(user_id, notification_id, worker_id=worker_id, sent_at=sent_at)

    async def mark_skipped(
        self,
        user_id: int,
        notification_id: int,
        *,
        worker_id: str,
        now_utc: datetime,
    ) -> ProactiveNotification | None:
        return await self._notifications.mark_skipped(user_id, notification_id, worker_id=worker_id, now_utc=now_utc)

    async def reserve_send(self, user_id: int, *, day_local: date, daily_limit: int) -> bool:
        """Reserva atómica de una unidad de presupuesto antes de un envío (Fase 6.2.1).

        Devuelve True si quedaba presupuesto diario (el envío puede emitirse) y
        False si el límite ya estaba agotado. Véase
        `ProactiveNotificationRepository.reserve_send`.
        """
        return await self._notifications.reserve_send(user_id, day_local=day_local, daily_limit=daily_limit)

    async def release_send(self, user_id: int, *, day_local: date) -> None:
        """Libera la reserva de un envío cuya entrega falló (Fase 6.2.1)."""
        await self._notifications.release_send(user_id, day_local=day_local)

    async def unclaim(self, user_id: int, notification_id: int, *, worker_id: str, now_utc: datetime) -> bool:
        """Devuelve a `pending` una fila `processing` reclamada sin presupuesto (Fase 6.2.1)."""
        return await self._notifications.unclaim(user_id, notification_id, worker_id=worker_id, now_utc=now_utc)


def to_midnight(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def local_str(dt: datetime | None, timezone: str) -> str:
    if dt is None:
        return "sin fecha fijada"
    return dt.astimezone(resolve_timezone(timezone)).strftime("%d/%m/%Y")
