"""Parser de fechas/horas en lenguaje natural (es-ES).

Interpreta expresiones relativas del usuario usando su zona horaria local
(NUNCA UTC directamente): hoy, mañana, pasado mañana, días de la semana,
"dentro de N horas/días/minutos/meses", "el día N", "el N de M", "a las
HH:MM", "por la mañana/tarde/noche", "al mediodía", etc.

Salida (`ParsedDateTime`):
- `value`: datetime AWARE en la zona horaria del usuario (no UTC).
- `all_day`: True solo si llegó una fecha SIN hora (el usuario no la pidió).
- `candidates`: interpretaciones alternativas (ver ambigüedad).

Reglas de fecha adoptadas (documentadas y probadas):
- "el viernes" / "este viernes" / "viernes": el próximo viernes (si hoy es
  viernes, hoy, y se ofrece la semana que viene como candidato).
- "el próximo viernes" / "el viernes que viene": la semana venidera estricta.
- "el día N": día N de este mes; si ya pasó, del mes que viene.
- "el N de M": día N del mes M del año actual; si ya pasó, del año próximo.
- "la semana que viene": el lunes siguiente a la hora por defecto.
- "el próximo mes": el mismo día del mes que viene (saneado).
- "dentro de N horas/días": ahora + N (conserva la hora).
- Sin hora explícita sobre una fecha: se usa `default_hour` (09:00) y se
  marca `all_day=True`; el llamante decide si es evento de día completo.
- "a las 10" sin fecha: hoy a las 10; si ya pasó, mañana a las 10.

Ambigüedad real (§8): si el texto admite varias fechas plausibles se puebla
`candidates` y el servicio debe preguntar antes de crear nada.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from asistente.shared.timeutil import add_months, start_of_day

WEEKDAYS_LOWER: dict[str, int] = {
    "lunes": 0,
    "martes": 1,
    "miércoles": 2,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sábado": 5,
    "sabado": 5,
    "domingo": 6,
}

MONTHS_LOWER: dict[str, int] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "seetiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_MOMENTS: dict[str, tuple[int, int]] = {
    "mañana": (9, 0),
    "tarde": (16, 0),
    "noche": (21, 0),
    "madrugada": (4, 0),
    "mediodía": (12, 0),
    "mediodia": (12, 0),
    "medianoche": (0, 0),
}

_DAY_DELTA = {
    "hoy": 0,
    "mañana": 1,
    "manana": 1,
    "pasado mañana": 2,
    "pasado manana": 2,
}

_UNITS_MINUTES = ("minutos", "minuto", "mins", "min")
_UNITS_HOURS = ("hora", "horas", "hs", "hrs")
_UNITS_DAYS = ("día", "días", "dia", "dias")
_UNITS_WEEKS = ("semana", "semanas")
_UNITS_MONTHS = ("mes", "meses")

_SINGULAR_TO_PLURAL = {
    "minuto": "minutos",
    "hora": "horas",
    "día": "días",
    "dia": "días",
    "semana": "semanas",
    "mes": "meses",
}


class DateParseError(ValueError):
    """No se pudo interpretar la fecha/hora."""


@dataclass(frozen=True, slots=True)
class ParsedDateTime:
    value: datetime
    all_day: bool = False
    timezone: str = "Europe/Madrid"
    candidates: list[datetime] = field(default_factory=list)


# --------------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class _TimeSpec:
    explicit: tuple[int, int] | None  # (hora, minuto) si "a las HH:MM"
    band: str | None  # momento del día si "por la <banda>" / "al mediodía"
    date_text: str  # resto de la expresión sin la parte de hora


_RE_WEEKDAY = re.compile(
    r"^(?:el\s+)?(?:(este|próximo|proximo)\s+)?"
    r"(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)"
    r"(?:\s+que\s+viene)?$",
    re.IGNORECASE,
)
_RE_DAY_OF_MONTH = re.compile(r"^(?:el\s+día\s+|el\s+dia\s+|el\s+|día\s+|dia\s+)?(\d{1,2})$", re.IGNORECASE)
_RE_NAMED_DAY = re.compile(
    r"^(?:el\s+día\s+|el\s+dia\s+|el\s+|día\s+|dia\s+)?(\d{1,2})\s+de\s+"
    r"(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|"
    r"seetiembre|octubre|noviembre|diciembre)$",
    re.IGNORECASE,
)
_RE_DURATION = re.compile(
    r"^(?:dentro\s+de|en)\s+(\d+)\s+"
    r"(minutos?|min|horas?|hs|hrs|días?|dias?|semanas?|meses?)\s*$",
    re.IGNORECASE,
)
_RE_EXPLICIT_TIME = re.compile(
    r"a\s+(?:las?|el)\s+(\d{1,2})(?:[:.\s](\d{2}))?"
    r"(?:\s*de\s+la\s+(mañana|tarde|noche|madrugada))?",
    re.IGNORECASE,
)
_RE_BARE_TIME = re.compile(r"^\d{1,2}[:.]\d{2}$")
_RE_BAND = re.compile(
    r"\s(?:por\s+las?|por\s+el|por\s+la)\s+(mañana|tarde|noche|madrugada)\s*$",
    re.IGNORECASE,
)
_RE_MOMENT_PREFIX = re.compile(r"^(?:al|a\s+la|a\s+las?)\s+(mediodía|mediodia|medianoche)\s*$", re.IGNORECASE)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _resolve_band(explicit: tuple[int, int] | None, band: str | None) -> tuple[int, int]:
    """Prioriza la hora explícita; si no, usa la banda del día."""
    if explicit:
        return explicit
    if band and band in _MOMENTS:
        return _MOMENTS[band]
    raise DateParseError("Sin especificación de hora.")


def _extract_time_spec(text: str) -> _TimeSpec | None:
    """Extrae 'a las HH:MM' y 'por la X' / momentos del día del texto."""
    explicit: tuple[int, int] | None = None
    band: str | None = None
    rest = text

    if m := _RE_MOMENT_PREFIX.fullmatch(text):
        band = m.group(1).strip().lower()
        rest = ""
    else:
        if m := _RE_EXPLICIT_TIME.search(text):
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            band_word = (m.group(3) or "").strip().lower()
            if band_word in ("tarde", "noche") and hour < 12:
                hour += 12
            if hour > 23 or minute > 59:
                raise DateParseError(f"Hora no válida: {m.group(0)!r}")
            explicit = (hour, minute)
            rest = text[: m.start()] + " " + text[m.end() :]
        if _RE_BARE_TIME.fullmatch(rest.strip()):
            hh, mm = rest.strip().split(":")
            explicit = (int(hh), int(mm))
            rest = ""
        if (mb := _RE_BAND.search(rest)) is not None:
            band = mb.group(1).strip().lower()
            rest = rest[: mb.start()] + " "
        rest = _clean(rest)

    if not (explicit or band):
        return None
    return _TimeSpec(explicit=explicit, band=band, date_text=rest)


class NaturalDateParser:
    """Transforma expresiones de fecha/hora naturales en datetimes locales."""

    def __init__(self, *, default_hour: int = 9, default_minute: int = 0) -> None:
        self._hour = default_hour
        self._minute = default_minute

    def parse(
        self,
        expression: str,
        *,
        now_local: datetime,
        timezone: str = "Europe/Madrid",
    ) -> ParsedDateTime:
        """Interpreta `expression` frente a `now_local` (aware, en `timezone`)."""
        if now_local.tzinfo is None:
            raise DateParseError("now_local debe ser una datetime aware (zona del usuario).")
        text = _clean(expression)

        spec = _extract_time_spec(text)
        date_text = spec.date_text if spec else text

        # --- Duraciones: el resultado ya es el momento exacto. ------------ #
        if m := _RE_DURATION.match(date_text):
            base = self._duration(now_local, int(m.group(1)), m.group(2).strip())
            if spec and spec.explicit:
                hour, minute = spec.explicit
                base = base.replace(hour=hour, minute=minute)
                return ParsedDateTime(value=base, all_day=False, timezone=timezone)
            if spec and spec.band:
                hour, minute = _MOMENTS[spec.band]
                base = base.replace(hour=hour, minute=minute)
                return ParsedDateTime(value=base, all_day=False, timezone=timezone)
            return ParsedDateTime(value=base, all_day=False, timezone=timezone)

        # --- Días relativos: hoy / mañana / pasado mañana (+ hora). ------- #
        if date_text in _DAY_DELTA:
            base = start_of_day(now_local) + timedelta(days=_DAY_DELTA[date_text])
            return self._apply_time_or_default(base, spec, timezone, now_local)

        # --- Días de la semana. ------------------------------------------ #
        if m := _RE_WEEKDAY.match(date_text):
            wd = WEEKDAYS_LOWER[m.group(2)]
            modifier = m.group(1) or ""
            que_viene = bool(m.group(0).endswith("que viene"))
            return self._weekday(now_local, wd, modifier, que_viene, spec, timezone)

        # --- "la próxima semana" / "el próximo mes". ---------------------- #
        if date_text in ("la semana que viene", "la semana siguiente", "el próximo mes", "este mes"):
            if date_text in ("la semana que viene", "la semana siguiente"):
                base = start_of_day(now_local) - timedelta(days=now_local.weekday()) + timedelta(days=7)
            else:
                base = start_of_day(add_months(now_local, 1))
            return self._apply_time_or_default(base, spec, timezone, now_local)

        # --- "el día N" / "el N de M". ----------------------------------- #
        if m := _RE_DAY_OF_MONTH.match(date_text):
            base = self._day_of_month(now_local, int(m.group(1)))
            return self._apply_time_or_default(base, spec, timezone, now_local)

        if m := _RE_NAMED_DAY.match(date_text):
            base = self._named_day(now_local, int(m.group(1)), m.group(2).strip())
            return self._apply_time_or_default(base, spec, timezone, now_local)

        # --- Solo hora ("a las 10") o banda ("por la tarde"). ------------- #
        if spec:
            hour, minute = _resolve_band(spec.explicit, spec.band)
            value = self._rollover(now_local, start_of_day(now_local), hour, minute)
            return ParsedDateTime(value=value, all_day=False, timezone=timezone)

        raise DateParseError(f"No pude interpretar la fecha/hora: {expression!r}")

    # ------------------------------------------------------------------ #
    def _apply_time_or_default(
        self,
        base: datetime,
        spec: _TimeSpec | None,
        timezone: str,
        now_local: datetime,
        *,
        candidates: list[datetime] | None = None,
    ) -> ParsedDateTime:
        if spec and spec.explicit:
            hour, minute = spec.explicit
            value = self._rollover(now_local, base, hour, minute)
            return ParsedDateTime(value=value, all_day=False, timezone=timezone, candidates=candidates or [])
        if spec and spec.band:
            hour, minute = _MOMENTS[spec.band]
            value = self._rollover(now_local, base, hour, minute)
            return ParsedDateTime(value=value, all_day=False, timezone=timezone, candidates=candidates or [])
        return ParsedDateTime(
            value=base.replace(hour=self._hour, minute=self._minute),
            all_day=True,
            timezone=timezone,
            candidates=candidates or [],
        )

    def _rollover(self, now_local: datetime, day_start: datetime, hour: int, minute: int) -> datetime:
        """Aplica hora; si el día es hoy y la hora ya pasó, pasa a mañana."""
        value = day_start.replace(hour=hour, minute=minute)
        if value < now_local and day_start.date() == now_local.date():
            value += timedelta(days=1)
        return value

    def _weekday(
        self,
        now_local: datetime,
        target: int,
        modifier: str,
        que_viene: bool,
        spec: _TimeSpec | None,
        timezone: str,
    ) -> ParsedDateTime:
        today = start_of_day(now_local)
        days_ahead = (target - now_local.weekday()) % 7
        strict_next_week = modifier in ("próximo", "proximo") or que_viene
        base = today + timedelta(days=days_ahead)
        candidates: list[datetime] = []
        if strict_next_week:
            # "el próximo viernes" / "el viernes que viene" → semana siguiente.
            # Se ofrece la interpretación de esta semana como candidato si difiere.
            alt = base
            base = base + timedelta(days=7)
            if alt != base:
                candidates.append(alt)
        elif days_ahead == 0:
            candidates.append(base + timedelta(days=7))
        if spec and spec.explicit:
            return ParsedDateTime(
                value=self._rollover(now_local, base, *spec.explicit),
                all_day=False,
                timezone=timezone,
                candidates=candidates,
            )
        if spec and spec.band:
            return ParsedDateTime(
                value=self._rollover(now_local, base, *_MOMENTS[spec.band]),
                all_day=False,
                timezone=timezone,
                candidates=candidates,
            )
        return ParsedDateTime(
            value=base.replace(hour=self._hour, minute=self._minute),
            all_day=True,
            timezone=timezone,
            candidates=candidates,
        )

    def _day_of_month(self, now_local: datetime, day: int) -> datetime:
        base = start_of_day(now_local)
        try:
            value = base.replace(day=day)
        except ValueError:
            raise DateParseError(f"El día {day} no existe en este mes.") from None
        if value.date() <= base.date():
            try:
                value = add_months(base, 1).replace(day=day)
            except ValueError:
                raise DateParseError(
                    f"El día {day} no existe en los próximos meses (no se pudo construir la fecha solicitada)."
                ) from None
        return value

    def _named_day(self, now_local: datetime, day: int, month_name: str) -> datetime:
        month = MONTHS_LOWER.get(month_name)
        if month is None:
            raise DateParseError(f"No reconozco el mes: {month_name!r}")
        try:
            value = datetime(now_local.year, month, day, tzinfo=now_local.tzinfo)
        except ValueError:
            raise DateParseError(f"El {day} de {month_name} no existe.") from None
        if value.date() <= start_of_day(now_local).date():
            value = datetime(now_local.year + 1, month, day, tzinfo=now_local.tzinfo)
        return value

    def _duration(self, now_local: datetime, amount: int, unit: str) -> datetime:
        unit = _SINGULAR_TO_PLURAL.get(unit, unit)
        if unit in _UNITS_MINUTES:
            return now_local + timedelta(minutes=amount)
        if unit in _UNITS_HOURS:
            return now_local + timedelta(hours=amount)
        if unit in _UNITS_DAYS:
            return now_local + timedelta(days=amount)
        if unit in _UNITS_WEEKS:
            return now_local + timedelta(weeks=amount)
        if unit in _UNITS_MONTHS:
            return add_months(now_local, amount)
        raise DateParseError(f"Unidad temporal no soportada: {unit!r}")
