"""Saneado de la respuesta final del LLM.

Motivo: con modelos pequeños (p. ej. un LLM local) la respuesta final puede
devolverse como el payload crudo de la última herramienta (JSON) en lugar de
texto natural. Al usuario nunca se le debe mostrar ese JSON.

Este módulo:
- detecta si un texto es (o contiene) un payload crudo de herramienta;
- construye un resumen legible y determinista a partir del resultado de las
  herramientas ya ejecutadas (nunca JSON);
- define la instrucción de reparación que se envía al LLM para que regenere
  la respuesta en lenguaje natural.

No depende del LLM ni de la base de datos: solo de los resultados ya
obtenidos por el orquestador.
"""

from __future__ import annotations

import json
import re
from typing import Any

MAX_TITLES = 5

GENERIC_FALLBACK = "He consultado tus datos, pero no he conseguido resumirlos."

FINAL_ANSWER_INSTRUCTION = (
    "Ya tienes el resultado de la herramienta. Responde al usuario en español de España, "
    "en 2-4 frases naturales y concisas. No copies el JSON ni los nombres de las claves, "
    "y no inventes datos que no estén en el resultado."
)

_KEY_RE = re.compile(r'"[^"\n]{1,60}"\s*:')


def looks_like_raw_payload(text: str | None) -> bool:
    """Indica si `text` es (o contiene) el payload crudo de una herramienta."""
    stripped = (text or "").strip()
    if not stripped:
        return False

    if stripped[0] in "{[":
        if stripped[-1] in "}]":
            try:
                json.loads(stripped)
            except ValueError:
                return _KEY_RE.search(stripped[:400]) is not None
            return True
        # JSON truncado: empieza por llave/bracket y parece contener claves.
        return _KEY_RE.search(stripped[:400]) is not None

    # JSON incrustado en una frase ("aquí tienes: {...}").
    start = stripped.find("{")
    while start != -1:
        end = stripped.rfind("}")
        if end > start:
            chunk = stripped[start : end + 1]
            if len(chunk) > 200:
                try:
                    json.loads(chunk)
                except ValueError:
                    pass
                else:
                    return True
        start = stripped.find("{", start + 1)
    return False


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _titles(items: Any, limit: int = MAX_TITLES) -> list[str]:
    titles: list[str] = []
    if not isinstance(items, list):
        return titles
    for item in items[:limit]:
        if isinstance(item, dict):
            title = (
                item.get("title")
                or item.get("name")
                or item.get("description")
                or item.get("merchant")
                or item.get("concept")
            )
            if title:
                titles.append(str(title))
        elif item:
            titles.append(str(item))
    return titles


def _format_amount(value: Any) -> str:
    try:
        amount = float(str(value))
    except (TypeError, ValueError):
        return str(value)
    return f"{amount:.2f}".replace(".", ",")


def build_fallback_reply(results: list[Any] | None) -> str:
    """Resumen legible y determinista del último resultado de herramienta.

    Nunca devuelve JSON: solo texto en español con las cifras principales.
    """
    data: dict[str, Any] | None = None
    for result in reversed(results or []):
        if getattr(result, "ok", False) and getattr(result, "data", None):
            candidate = getattr(result, "data", None)
            if isinstance(candidate, dict):
                data = candidate
                break
    if data is None:
        return GENERIC_FALLBACK

    parts: list[str] = []

    task_count = _as_int(data.get("task_count"))
    overdue_count = _as_int(data.get("overdue_task_count"))
    if task_count:
        sentence = f"Tienes {task_count} {_plural(task_count, 'tarea pendiente', 'tareas pendientes')}"
        if overdue_count:
            sentence += f", {_as_int(overdue_count)} {_plural(overdue_count, 'vencida', 'vencidas')}"
        parts.append(sentence + ".")

    event_count = _as_int(data.get("event_count"))
    if event_count:
        parts.append(f"Hay {event_count} {_plural(event_count, 'evento', 'eventos')} en la agenda.")

    titles = _titles(data.get("overdue_tasks")) or _titles(data.get("tasks"))
    if titles:
        parts.append("Lo más urgente: " + ", ".join(titles) + ".")

    planned_count = _as_int(data.get("planned_payment_count"))
    if planned_count:
        amount = data.get("planned_total_amount")
        suffix = f" ({_format_amount(amount)} €)" if amount else ""
        parts.append(
            f"Tienes {planned_count} {_plural(planned_count, 'pago planificado', 'pagos planificados')}{suffix}."
        )

    checklist_total = _as_int(data.get("open_checklist_total"))
    if checklist_total:
        parts.append(
            f"Además quedan {checklist_total} "
            f"{_plural(checklist_total, 'punto de checklist', 'puntos de checklist')} sin marcar."
        )

    if not parts:
        return GENERIC_FALLBACK
    return " ".join(parts)
