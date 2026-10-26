"""Servicio de dominio de recurrencias financieras (Fase 3.3).

La recurrencia es la REGLA (tipo, importe, cuenta, frecuencia + ancla de mes).
Las ocurrencias se materializan como `planned_transactions` de forma perezosa
e idempotente (`generate_upcoming_planned_transactions`) y NO mueven dinero
hasta que se convierten en `transactions`.

Motor de fechas (ancla preservante):
- El `day_of_month` es el día ANCLA de la cadencia mensual/anual. Nunca se
  deriva del `next_due_at` saneado: eso provocaría desviación permanente.
- Saneo de fin de mes exigido por la spec:
  31 ene → 28/29 feb → 31 mar → 30 abr → 31 may.
- Las ocurrencias se computan a las 12:00 LOCAL (hora fija del mediodía) para
  evitar ambigüedades de DST; `next_due_at` se almacena UTC.
- `daily`: + interval días · `weekly`: + interval semanas (mismo día de semana)
- `monthly`: + interval meses con ancla de mes · `yearly`: + interval años con
  `month_of_year` (o mes de `start_date`) y ancla de día.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from itertools import count

from asistente.domain.amounts import CENTS
from asistente.domain.categories import classify_category, normalize_category
from asistente.domain.currencies import SUPPORTED_CURRENCY, normalize_currency
from asistente.persistence.models import Account, RecurringTransaction
from asistente.persistence.repositories import (
    AccountRepository,
    PlannedTransactionRepository,
    RecurringTransactionRepository,
    SubscriptionRepository,
)
from asistente.shared.errors import AuthorizationError, NotFoundError
from asistente.shared.recurrence import ALL_FREQS, FREQ_DAILY, FREQ_MONTHLY, FREQ_WEEKLY, FREQ_YEARLY
from asistente.shared.timeutil import now_in_timezone, resolve_timezone, to_local, to_utc, utc_now

TRANSACTION_TYPES = ("expense", "income")
MONTHLY_YEARLY_FREQS = (FREQ_MONTHLY, FREQ_YEARLY)
MAX_STEPS = 10_000
NOON_HOUR = 12
DEFAULT_GEN_HORIZON_DAYS = 90


def days_in_month(year: int, month: int) -> int:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    return (first_next - date(year, month, 1)).days


def month_step_anchored(source: date, months: int, anchor_day: int) -> date:
    """Suma `months` a `source` preservando el día ANCLA (saneado al fin de mes).

    31 ene → 28/29 feb → 31 mar → 30 abr → 31 may. El ancla se aplica SIEMPRE
    desde `anchor_day`, nunca desde el día saneado de una fecha previa.
    """
    total = source.year * 12 + source.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(anchor_day, days_in_month(year, month)))


def at_noon(d: date, timezone: str) -> datetime:
    """Mediodía local de la fecha (hora fija para evitar bordes de DST)."""
    tz = resolve_timezone(timezone)
    return datetime(d.year, d.month, d.day, NOON_HOUR, 0, tzinfo=tz)


def anchor_day_of(day_of_month: int | None, start_date: date | None) -> int | None:
    """Día ancla resuelto: `day_of_month` si se da, si no el día de `start_date`."""
    if day_of_month is not None:
        return day_of_month
    if start_date is not None:
        return start_date.day
    return None


def cadence_month_of(month_of_year: int | None, start_date: date | None, local_date: date) -> int:
    if month_of_year is not None:
        return month_of_year
    if start_date is not None:
        return start_date.month
    return local_date.month


def _candidate_at(
    base: date,
    *,
    frequency: str,
    interval: int,
    anchor: int,
    cadence_month: int,
    month_of_year: int | None,
    kind: int,
) -> date:
    """Fecha de la ocurrencia `kind`-ésima de la cadencia, partiendo de `base`."""
    if frequency == FREQ_DAILY:
        return base + timedelta(days=kind * interval)
    if frequency == FREQ_WEEKLY:
        return base + timedelta(weeks=kind * interval)
    if frequency == FREQ_MONTHLY:
        return month_step_anchored(base, kind * interval, anchor)
    year = base.year + kind * interval
    return date(year, cadence_month, min(anchor, days_in_month(year, cadence_month)))


def first_occurrence_after(
    local_now: datetime,
    *,
    frequency: str,
    interval: int,
    day_of_month: int | None,
    month_of_year: int | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str,
) -> datetime | None:
    """Primera ocurrencia (UTC) estrictamente posterior a `local_now`, o None."""
    anchor = anchor_day_of(day_of_month, start_date)
    base = start_date or local_now.date()
    cadence_month = cadence_month_of(month_of_year, start_date, local_now.date())
    if frequency in MONTHLY_YEARLY_FREQS and anchor is None:
        raise ValueError("Para recurrencias mensuales/anuales hace falta un día ancla.")
    for kind in count():
        if kind > MAX_STEPS:
            return None
        cand = _candidate_at(
            base,
            frequency=frequency,
            interval=interval,
            anchor=(anchor or 1),
            cadence_month=cadence_month,
            month_of_year=month_of_year,
            kind=kind,
        )
        if start_date is not None and cand < start_date:
            continue
        if end_date is not None and cand > end_date:
            return None
        cand_utc = to_utc(at_noon(cand, timezone))
        if cand_utc > to_utc(local_now):
            return cand_utc
    return None


def next_occurrence_dt(
    current_utc: datetime,
    *,
    frequency: str,
    interval: int,
    day_of_month: int | None,
    month_of_year: int | None,
    start_date: date | None,
    end_date: date | None,
    timezone: str,
) -> datetime | None:
    """Siguiente ocurrencia (UTC) tras `current_utc`, o None si la serie acaba."""
    anchor = anchor_day_of(day_of_month, start_date)
    if frequency in MONTHLY_YEARLY_FREQS and anchor is None:
        raise ValueError("Para recurrencias mensuales/anuales hace falta un día ancla.")
    cur_local = to_local(current_utc, timezone).date()
    if frequency == FREQ_DAILY:
        nxt = cur_local + timedelta(days=interval)
    elif frequency == FREQ_WEEKLY:
        nxt = cur_local + timedelta(weeks=interval)
    elif frequency == FREQ_MONTHLY:
        nxt = month_step_anchored(cur_local, interval, anchor or 1)
    elif month_of_year is not None:
        year = cur_local.year + interval
        nxt = date(year, month_of_year, min(anchor or 1, days_in_month(year, month_of_year)))
    else:
        nxt = month_step_anchored(cur_local, 12 * interval, anchor or 1)
    if end_date is not None and nxt > end_date:
        return None
    return to_utc(at_noon(nxt, timezone))


class RecurringTransactionService:
    def __init__(
        self,
        recurring_repo: RecurringTransactionRepository,
        planned_repo: PlannedTransactionRepository,
        account_repo: AccountRepository,
        subscription_repo: SubscriptionRepository | None = None,
    ) -> None:
        self._recurring = recurring_repo
        self._planned = planned_repo
        self._accounts = account_repo
        self._subscriptions = subscription_repo

    async def create(
        self,
        *,
        user_id: int,
        type: str,
        title: str,
        amount: Decimal,
        currency: str = "EUR",
        account_id: int | None = None,
        merchant: str | None = None,
        category: str | None = None,
        frequency: str,
        interval: int = 1,
        day_of_month: int | None = None,
        month_of_year: int | None = None,
        start_date: date | None = None,
        next_due_at: datetime | None = None,
        end_date: date | None = None,
        timezone: str = "Europe/Madrid",
        paused: bool = False,
        session=None,
    ) -> RecurringTransaction:
        title = (title or "").strip()
        if not title:
            raise ValueError("El título de la recurrencia no puede estar vacío.")
        if type not in TRANSACTION_TYPES:
            raise ValueError(f"Tipo no válido: {type}. Válidos: {', '.join(TRANSACTION_TYPES)}.")
        currency = normalize_currency(currency)
        amount = Decimal(str(amount)).quantize(CENTS)
        if amount <= 0:
            raise ValueError("El importe de la recurrencia debe ser mayor que cero.")
        if frequency not in ALL_FREQS:
            raise ValueError(f"Frecuencia no válida: {frequency}. Válidas: {', '.join(ALL_FREQS)}.")
        if int(interval) != interval or interval < 1:
            raise ValueError("El intervalo debe ser un entero >= 1.")
        if day_of_month is not None and not 1 <= day_of_month <= 31:
            raise ValueError("El día del mes debe estar entre 1 y 31.")
        if month_of_year is not None and not 1 <= month_of_year <= 12:
            raise ValueError("El mes del año debe estar entre 1 y 12.")
        resolve_timezone(timezone)
        if frequency in MONTHLY_YEARLY_FREQS and anchor_day_of(day_of_month, start_date) is None:
            raise ValueError(
                "Para recurrencias mensuales/anuales especifica 'día del mes' (1-31) o una fecha de inicio."
            )
        if frequency == FREQ_YEARLY and month_of_year is None and start_date is None:
            raise ValueError("Para recurrencias anuales especifica el mes (1-12) o una fecha de inicio.")

        account = await self._resolve_account(user_id, account_id)
        account_id = account.id

        effective_due = next_due_at
        if effective_due is None:
            effective_due = first_occurrence_after(
                now_in_timezone(timezone),
                frequency=frequency,
                interval=interval,
                day_of_month=day_of_month,
                month_of_year=month_of_year,
                start_date=start_date,
                end_date=end_date,
                timezone=timezone,
            )
        if effective_due is None:
            raise ValueError("No se puede calcular la primera ocurrencia con la configuración dada.")
        if end_date is not None and to_local(effective_due, timezone).date() > end_date:
            raise ValueError("La fecha fin debe ser posterior a la primera ocurrencia.")

        resolved_category = classify_category(merchant, category)
        return await self._recurring.create(
            user_id=user_id,
            type=type,
            title=title,
            amount=amount,
            currency=currency,
            account_id=account.id,
            merchant=(merchant or "").strip()[:300] or None,
            category=resolved_category,
            frequency=frequency,
            interval=int(interval),
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            start_date=start_date,
            next_due_at=effective_due,
            end_date=end_date,
            timezone=timezone,
            active=True,
            paused=paused,
            session=session,
        )

    async def _resolve_account(self, user_id: int, account_id: int | None) -> Account:
        if account_id is not None:
            account = await self._accounts.find_one(user_id, account_id)
            if account is None:
                raise NotFoundError("La cuenta de la recurrencia no existe o no te pertenece.")
            if not account.active:
                raise AuthorizationError("La cuenta está desactivada; elige una activa.")
            return account
        accounts = await self._accounts.list_for_user(user_id, only_active=True)
        if not accounts:
            raise AuthorizationError("No tienes ninguna cuenta activa. Crea una cuenta primero.")
        default = next((a for a in accounts if a.is_default), None)
        if default is not None:
            return default
        if len(accounts) == 1:
            return accounts[0]
        raise AuthorizationError(
            "Tienes varias cuentas activas y ninguna principal. Indica la cuenta de la recurrencia."
        )

    async def get(self, user_id: int, recurring_id: int) -> RecurringTransaction:
        recurring = await self._recurring.find_one(user_id, recurring_id)
        if recurring is None:
            raise NotFoundError("La recurrencia no existe o no te pertenece.")
        return recurring

    async def update(self, user_id: int, recurring_id: int, *, session=None, **fields) -> RecurringTransaction:
        current = await self.get(user_id, recurring_id)
        if "account_id" in fields:  # revalidación de propiedad/estado
            if fields["account_id"] is not None:
                account = await self._accounts.find_one(user_id, int(fields["account_id"]))
                if account is None:
                    raise NotFoundError("La cuenta de la recurrencia no existe o no te pertenece.")
                if not account.active:
                    raise AuthorizationError("La cuenta está desactivada; elige una activa.")
                fields["account_id"] = account.id
            else:
                fields["account_id"] = None
        if "amount" in fields:
            fields["amount"] = Decimal(str(fields["amount"])).quantize(CENTS)
            if fields["amount"] <= 0:
                raise ValueError("El importe de la recurrencia debe ser mayor que cero.")
        if "currency" in fields:
            fields["currency"] = normalize_currency(fields["currency"])
            if fields["currency"] != SUPPORTED_CURRENCY and fields["currency"] not in (None, ""):
                raise ValueError(f"Moneda no soportada: '{fields['currency']}'. Solo se admite {SUPPORTED_CURRENCY}.")
        if "title" in fields:
            fields["title"] = (fields["title"] or "").strip()
            if not fields["title"]:
                raise ValueError("El título de la recurrencia no puede estar vacío.")
        if "merchant" in fields:
            fields["merchant"] = (fields["merchant"] or "").strip()[:300] or None
        if "category" in fields:
            fields["category"] = normalize_category(fields["category"])
        if "day_of_month" in fields and fields["day_of_month"] is not None:
            if not 1 <= fields["day_of_month"] <= 31:
                raise ValueError("El día del mes debe estar entre 1 y 31.")
        if "month_of_year" in fields and fields["month_of_year"] is not None:
            if not 1 <= fields["month_of_year"] <= 12:
                raise ValueError("El mes del año debe estar entre 1 y 12.")
        if "frequency" in fields:
            if fields["frequency"] not in ALL_FREQS:
                raise ValueError(f"Frecuencia no válida: {fields['frequency']}.")
            if fields["frequency"] in MONTHLY_YEARLY_FREQS:
                anchor = anchor_day_of(
                    fields.get("day_of_month", current.day_of_month), fields.get("start_date", current.start_date)
                )
                if anchor is None:
                    raise ValueError("Para recurrencias mensuales/anuales hace falta un día ancla.")
        if "interval" in fields:
            if int(fields["interval"]) != fields["interval"] or fields["interval"] < 1:
                raise ValueError("El intervalo debe ser un entero >= 1.")
            fields["interval"] = int(fields["interval"])
        if "timezone" in fields:
            resolve_timezone(fields["timezone"])
        if "end_date" in fields and fields["end_date"] is not None:
            if (
                current.next_due_at is not None
                and to_local(current.next_due_at, current.timezone).date() > fields["end_date"]
            ):
                fields["next_due_at"] = None
        updated = await self._recurring.update(user_id, recurring_id, session=session, **fields)
        if updated is None:
            raise NotFoundError("La recurrencia no existe o no te pertenece.")
        return updated

    async def pause(self, user_id: int, recurring_id: int) -> RecurringTransaction:
        recurring = await self.get(user_id, recurring_id)
        if not recurring.active:
            raise ValueError("La recurrencia está cancelada; no se puede pausar.")
        if recurring.paused:
            raise ValueError("La recurrencia ya está pausada.")
        return await self.update(user_id, recurring_id, paused=True)

    async def resume(self, user_id: int, recurring_id: int) -> RecurringTransaction:
        recurring = await self.get(user_id, recurring_id)
        if not recurring.active:
            raise ValueError("La recurrencia está cancelada; reactiva la cadencia creándola de nuevo.")
        if not recurring.paused:
            raise ValueError("La recurrencia no está pausada.")
        if recurring.next_due_at is None:
            next_due = first_occurrence_after(
                now_in_timezone(recurring.timezone),
                frequency=recurring.frequency,
                interval=recurring.interval,
                day_of_month=recurring.day_of_month,
                month_of_year=recurring.month_of_year,
                start_date=recurring.start_date,
                end_date=recurring.end_date,
                timezone=recurring.timezone,
            )
            if next_due is None:
                raise ValueError("La recurrencia ya superó su fecha fin: no quedan ocurrencias por generar.")
            return await self.update(user_id, recurring_id, paused=False, next_due_at=next_due)
        return await self.update(user_id, recurring_id, paused=False)

    async def cancel(self, user_id: int, recurring_id: int, *, session=None) -> tuple[RecurringTransaction, int]:
        """Cancela la serie: inactiva la recurrencia y sus planificados futuros.

        Conserva el histórico (transactions convertidas NO se tocan).
        Devuelve (recurrencia, nº de planificados futuros cancelados).
        Si se pasa `session`, todas las escrituras comparten la misma
        transacción (usa el commit la transacción exterior).
        """
        updated = await self.update(
            user_id, recurring_id, session=session, active=False, paused=False, next_due_at=None
        )
        assert updated is not None
        cancelled = await self._planned.cancel_for_recurring(user_id, recurring_id, session=session)
        if self._subscriptions is not None:
            await self._subscriptions.deactivate_for_recurring(user_id, recurring_id, session=session)
        return updated, cancelled

    async def list(
        self, user_id: int, *, active_only: bool = False, include_paused: bool = True, limit: int = 100
    ) -> list[RecurringTransaction]:
        return await self._recurring.list_for_user(
            user_id, active_only=active_only, include_paused=include_paused, limit=limit
        )

    async def generate_upcoming_planned_transactions(
        self, user_id: int, *, horizon_days: int = DEFAULT_GEN_HORIZON_DAYS
    ) -> int:
        """Materializa de forma idempotente los planificados pendientes.

        Para cada recurrencia vencida crea la ocurrencia en `next_due_at` y
        AVANZA `next_due_at` en la misma transacción, hasta `now + horizon`.
        Una segunda ejecución no duplica nada.
        """
        now_utc = utc_now()
        due = await self._recurring.list_due(user_id, now_utc)
        horizon_utc = now_utc + timedelta(days=horizon_days)
        created = 0
        for rec in due:
            if rec.next_due_at is None:
                continue
            cursor = to_utc(rec.next_due_at)
            while cursor is not None and cursor <= horizon_utc:
                if rec.end_date is not None and to_local(cursor, rec.timezone).date() > rec.end_date:
                    await self.update(user_id, rec.id, next_due_at=None)
                    break
                nxt = next_occurrence_dt(
                    cursor,
                    frequency=rec.frequency,
                    interval=rec.interval,
                    day_of_month=rec.day_of_month,
                    month_of_year=rec.month_of_year,
                    start_date=rec.start_date,
                    end_date=rec.end_date,
                    timezone=rec.timezone,
                )
                planned = await self._recurring.create_occurrence_and_advance(
                    rec, planned_at_utc=cursor, next_due_utc=nxt
                )
                if planned is not None:
                    created += 1
                if nxt is None:
                    break
                cursor = nxt
        return created
