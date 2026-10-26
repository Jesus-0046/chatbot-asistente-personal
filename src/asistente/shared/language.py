"""Helpers de lenguaje natural (es-ES) para prioridades y recurrencias."""

from __future__ import annotations

import re
from dataclasses import dataclass

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"

_HIGH_WORDS = ("urgente", "muy importante", "crítico", "critico", "máxima", "máximo", "maximo")
_MEDIUM_WORDS = ("importante", "normal", "media", "medio", "moderada")
_LOW_WORDS = ("poca importancia", "poco importante", "baja", "bajo", "cuando pueda", "sin prisa")


def parse_priority(text: str | None) -> str:
    """Clasifica una prioridad expresada en lenguaje natural.

    Reglas adoptadas: urgente/muy importante/crítico → high;
    importante/normal → medium; poca importancia/baja → low. Sin texto →
    medium.
    """
    if not text:
        return PRIORITY_MEDIUM
    lowered = text.strip().lower()
    if any(w in lowered for w in _LOW_WORDS):
        return PRIORITY_LOW
    if any(w in lowered for w in _HIGH_WORDS):
        return PRIORITY_HIGH
    if any(w in lowered for w in _MEDIUM_WORDS):
        return PRIORITY_MEDIUM
    return PRIORITY_MEDIUM


@dataclass(frozen=True, slots=True)
class RecurrencePhrase:
    freq: str
    interval: int = 1
    days_of_week: tuple[int, ...] = ()


_WEEKDAYS_REC = {
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

_RE_DAILY = re.compile(
    r"^(?:todos?\s+los\s+días|todos?\s+los\s+dias|cada\s+día|cada\s+dia|a\s+diario|diariamente|diaria|diario)\s*$",
    re.IGNORECASE,
)
_RE_EVERY_N_DAYS = re.compile(r"^(?:cada|todos\s+los)\s+(\d+)\s+(?:día|dias|días)\s*$", re.IGNORECASE)
_RE_WEEKLY_BY_DAYS = re.compile(
    r"^todos?\s+los\s+((?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)(?:\s+y\s+(?:lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo))*)\s*$",
    re.IGNORECASE,
)
_RE_SEMANAL = re.compile(
    r"^(?:todos?\s+los\s+(?:días)?\s+de\s+la\s+semana|cada\s+semana|semanal|semanas)\s*$", re.IGNORECASE
)
_RE_EVERY_N_WEEKS = re.compile(r"^cada\s+(\d+)\s+semanas?\s*$", re.IGNORECASE)
_RE_MENSUAL = re.compile(r"^(?:todos?\s+los\s+meses|cada\s+mes|mensual|mensualmente)\s*$", re.IGNORECASE)
_RE_EVERY_N_MONTHS = re.compile(r"^cada\s+(\d+)\s+mes(?:es)?\s*$", re.IGNORECASE)
_RE_ANUAL = re.compile(
    r"^(?:todos?\s+los\s+años|todos?\s+los\s+anos|cada\s+año|cada\s+ano|anual|anualmente)\s*$", re.IGNORECASE
)


def parse_recurrence(text: str | None) -> RecurrencePhrase | None:
    """Interpreta una recurrencia natural; None si no la reconoce."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower())
    if _RE_DAILY.match(t):
        return RecurrencePhrase(freq="daily")
    if m := _RE_EVERY_N_DAYS.match(t):
        return RecurrencePhrase(freq="daily", interval=int(m.group(1)))
    if m := _RE_WEEKLY_BY_DAYS.match(t):
        names = re.split(r"\s+y\s+", m.group(1))
        days = tuple(sorted(_WEEKDAYS_REC[n.strip()] for n in names))
        return RecurrencePhrase(freq="weekly", days_of_week=days)
    if _RE_SEMANAL.match(t):
        return RecurrencePhrase(freq="weekly")
    if m := _RE_EVERY_N_WEEKS.match(t):
        return RecurrencePhrase(freq="weekly", interval=int(m.group(1)))
    if _RE_MENSUAL.match(t):
        return RecurrencePhrase(freq="monthly")
    if m := _RE_EVERY_N_MONTHS.match(t):
        return RecurrencePhrase(freq="monthly", interval=int(m.group(1)))
    if _RE_ANUAL.match(t):
        return RecurrencePhrase(freq="yearly")
    return None
