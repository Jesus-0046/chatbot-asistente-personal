"""Motor de recurrencias (Fase 2).

Opera sobre `RecurrenceSpec` (regla desacoplada del ORM). El cómputo de
ocurrencias se hace SIEMPRE en la zona horaria del usuario para respetar DST;
las fechas se convierten a UTC en los límites de consulta.

Reglas:
- daily:   base + N días cada vez.
- weekly:  N semanas por bloque; ocurren los días en `days_of_week` a la hora
           de `start_at`. Si `days_of_week` está vacío, solo el día de
           `start_at`.
- monthly: + N meses (día del mes saneado: 31 en febrero → último día).
- yearly:  + N años (29-feb saneado a 28/29 según año).

`end_at` es inclusivo (fecha del último día válido) y se interpreta en la
zona del usuario.

Generación acotada: busca en una ventana finita (`horizon_days`) y con tope
`max_occurrences` para no explotar en reglas degeneradas.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta

from asistente.shared.timeutil import add_months, resolve_timezone, to_utc

FREQ_DAILY = "daily"
FREQ_WEEKLY = "weekly"
FREQ_MONTHLY = "monthly"
FREQ_YEARLY = "yearly"
ALL_FREQS = (FREQ_DAILY, FREQ_WEEKLY, FREQ_MONTHLY, FREQ_YEARLY)

DEFAULT_HORIZON_DAYS = 365 * 2
DEFAULT_MAX_OCCURRENCES = 10_000


@dataclass(frozen=True, slots=True)
class RecurrenceSpec:
    id: int | None
    freq: str
    interval: int = 1
    days_of_week: tuple[int, ...] = ()  # 0=lunes..6=domingo (solo semanal)
    start_at_utc: datetime | None = None  # primera ocurrencia (UTC, aware)
    end_at_utc: datetime | None = None  # última fecha válida (UTC, inversa)
    timezone: str = "Europe/Madrid"

    def __post_init__(self) -> None:
        if self.freq not in ALL_FREQS:
            raise ValueError(f"Frecuencia no soportada: {self.freq!r}")
        if self.interval < 1:
            raise ValueError("El intervalo debe ser >= 1")


def normalize_days(days: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    """Normaliza y ordena una lista de días de la semana (0=lunes..6=domingo)."""
    return tuple(sorted({d % 7 for d in days}))


def iter_occurrences(
    spec: RecurrenceSpec,
    local_start: datetime,
    local_end: datetime,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_occurrences: int = DEFAULT_MAX_OCCURRENCES,
) -> list[datetime]:
    """Devuelve las ocurrencias de la regla en [local_start, local_end).

    `local_start`/`local_end` son aware en la zona del usuario.
    """
    tz = resolve_timezone(spec.timezone)
    if spec.start_at_utc is not None:
        base = to_utc(spec.start_at_utc).astimezone(tz)
    else:
        base = local_start.astimezone(tz)
    if base > local_end:
        return []
    end_utc = to_utc(spec.end_at_utc) if spec.end_at_utc is not None else None
    horizon = local_end + timedelta(days=horizon_days)

    out: list[datetime] = []
    count = 0
    for cand in _candidate_stream(spec, base):
        count += 1
        if count > max_occurrences:
            break
        if cand < base:
            continue
        if cand < local_start or cand > local_end:
            if cand > horizon:
                break
            continue
        if end_utc is not None and to_utc(cand) > end_utc:
            break
        out.append(cand)
    out = sorted(set(out))
    return [o for o in out if local_start <= o <= local_end]


def next_occurrence(
    spec: RecurrenceSpec,
    after_utc: datetime,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    max_occurrences: int = DEFAULT_MAX_OCCURRENCES,
) -> datetime | None:
    """Primera ocurrencia estrictamente posterior a `after_utc` (UTC aware).

    Si no hay más (límite de `end_at` o ventana agotada), devuelve None.
    """
    local = resolve_timezone(spec.timezone)
    after_local = to_utc(after_utc).astimezone(local)
    if spec.start_at_utc is None:
        return None
    base = to_utc(spec.start_at_utc).astimezone(local)
    horizon = after_local + timedelta(days=horizon_days)
    end_utc = to_utc(spec.end_at_utc) if spec.end_at_utc is not None else None
    count = 0
    for cand in _candidate_stream(spec, base):
        count += 1
        if count > max_occurrences:
            break
        if cand < base or cand <= after_local:
            continue
        if cand > horizon:
            break
        if end_utc is not None and to_utc(cand) > end_utc:
            break
        return cand
    return None


# --------------------------------------------------------------------------- #
def _candidate_stream(
    spec: RecurrenceSpec,
    base: datetime,
) -> Iterator[datetime]:
    """Generador de ocurrencias brutas ascendentes partiendo de `base`.

    Siempre emite `base` (u ocurrencias >= base). Semanal con `days_of_week`
    recorre semanas completas; el resto avanza desde la base.
    """
    assert base.tzinfo is not None
    if spec.freq == FREQ_DAILY:
        cur = base
        while True:
            yield cur
            cur = cur + timedelta(days=spec.interval)
    elif spec.freq == FREQ_WEEKLY:
        days = normalize_days(list(spec.days_of_week) or [base.weekday()])
        ref_monday = base.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=base.weekday())
        week_idx = 0
        while True:
            monday = ref_monday + timedelta(weeks=week_idx * spec.interval)
            for d in days:
                cand = monday + timedelta(days=d)
                cand = cand.replace(
                    hour=base.hour, minute=base.minute, second=base.second, microsecond=base.microsecond
                )
                if cand >= base:
                    yield cand
            week_idx += 1
    elif spec.freq == FREQ_MONTHLY:
        k = 0
        while True:
            yield add_months(base, k * spec.interval)
            k += 1
    else:  # yearly
        k = 0
        while True:
            yield add_months(base, k * 12 * spec.interval)
            k += 1
