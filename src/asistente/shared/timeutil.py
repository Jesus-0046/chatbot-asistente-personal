"""Utilidades de tiempo: zona horaria del usuario y formato local.

Almacenamos siempre UTC; presentamos siempre en la zona horaria del usuario
(IANA). Para Fase 1 basta `now_in_timezone` y `format_datetime`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asistente.shared.errors import BusinessError

ES_ES_HUMAN = "%H:%M"
ES_ES_FULL = "%d/%m/%Y %H:%M"


def utc_now() -> datetime:
    """Momento actual en UTC (aware)."""
    return datetime.now(tz=UTC)


def resolve_timezone(timezone_name: str | None, fallback: str = "Europe/Madrid") -> ZoneInfo:
    """Resuelve un nombre IANA; si no existe, se usa el fallback del proyecto."""
    name = (timezone_name or fallback).strip()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - defensivo
        raise BusinessError(f"Zona horaria no válida: {name}") from exc


def now_in_timezone(timezone_name: str | None, fallback: str = "Europe/Madrid") -> datetime:
    return datetime.now(tz=resolve_timezone(timezone_name, fallback))


def format_datetime(dt: datetime, fmt: str = ES_ES_FULL) -> str:
    """Formatea una datetime (aware) en texto local legible."""
    return dt.strftime(fmt)


def to_utc(dt: datetime) -> datetime:
    """Convierte una datetime (aware o naive-UTC) a UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_local(dt: datetime, timezone_name: str | None, fallback: str = "Europe/Madrid") -> datetime:
    """Convierte una datetime UTC a hora local del usuario."""
    tz = resolve_timezone(timezone_name, fallback)
    return to_utc(dt).astimezone(tz)


def start_of_day(dt: datetime) -> datetime:
    """Medianoche local del día de `dt`."""
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_day(dt: datetime) -> datetime:
    """Último instante del día de `dt` (inclusive)."""
    return start_of_day(dt) + timedelta(days=1) - timedelta(microseconds=1)


def add_months(dt: datetime, months: int) -> datetime:
    """Suma meses a una fecha manteniendo saneado el día del mes.

    Si el día no existe en el mes destino (p. ej. 31 en febrero) se usa el
    último día válido del mes.
    """
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        next_month = datetime(year + 1, 1, 1)
    else:
        next_month = datetime(year, month + 1, 1)
    return (next_month - datetime(year, month, 1)).days


def isoformat_utc(dt: datetime) -> str:
    return to_utc(dt).isoformat()
