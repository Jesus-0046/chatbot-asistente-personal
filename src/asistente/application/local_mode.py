"""Ajustes de contexto para el proveedor local (ventana de contexto pequeña).

Los modelos locales (p. ej. Ollama con qwen3) trabajan con una ventana de
contexto de 4096 tokens. Con el prompt largo + las 135 tools + el historial el
prompt NO cabe: el servidor lo recorta (el modelo deja de ver la mayoría de
las tools y de las reglas) y la cache de prefijo no se puede reutilizar, asi
que cada turno paga un prefill completo (y el prefill es lo lento: ~28 tok/s).

En modo local:
  - el system prompt es compacto y NO lleva fecha (la fecha viaja en el
    mensaje del usuario) para que el prefijo [system][tools] sea estable,
  - el historial se recorta,
  - el resultado de las tools se limita en tamaño,
  - solo se anuncian el núcleo fijo + los grupos que casan con el mensaje
    del usuario; el registro conserva las 135 tools.

El proveedor oficial (sin `openai_base_url`) no se ve afectado: usa el prompt
completo, el historial completo, las 135 tools y los resultados sin recortar.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from asistente.config import Settings
from asistente.tools.catalog import CORE_TOOL_NAMES
from asistente.tools.local import compact_spec, select_local_tool_names

# Historial (mensajes) que se pasan al modelo en modo local.
LOCAL_HISTORY_MESSAGES = 4
# Tamaño máximo del resultado de una tool en el prompt (modo local).
LOCAL_TOOL_RESULT_MAX_CHARS = 1200
# Presupuesto aproximado del prompt en caracteres (~4,275 chars por token con
# qwen3). 17.000 chars ≈ 3.976 tokens: deja margen dentro de los 4096.
LOCAL_PROMPT_CHAR_BUDGET = 17_000


def is_local_mode(settings: Settings) -> bool:
    """True si el LLM es un proveedor local compatible con la API OpenAI."""
    return bool((settings.openai_base_url or "").strip())


def local_user_text(text: str, *, now: datetime, timezone: str) -> str:
    """Antepone la fecha/hora actual al mensaje del usuario (modo local)."""
    stamp = now.strftime("%d/%m/%Y %H:%M")
    return f"[Ahora: {stamp} ({timezone})]\n{text}"


def local_tool_names(text: str | None = None) -> tuple[str, ...]:
    """Tools a anunciar en modo local (núcleo estable)."""
    return select_local_tool_names(text)


def core_tool_names() -> tuple[str, ...]:
    """Núcleo fijo del modo local."""
    return CORE_TOOL_NAMES


def compact_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Specs compactados (descripcion corta, esquema sin metadatos)."""
    return [compact_spec(spec) for spec in specs]


def trim_history(
    messages: list[dict[str, Any]],
    *,
    system_chars: int,
    tools_chars: int,
    extra_chars: int = 0,
    budget_chars: int | None = None,
) -> list[dict[str, Any]]:
    """Recorta el historial para que el prompt quepa en la ventana del modelo.

    Se conservan los mensajes MÁS RECIENTES (el final del historial es el
    contexto inmediato) hasta agotar el presupuesto de caracteres.

    Si `budget_chars` se pasa, se usa directamente (lo calcula el compositor).
    """
    if budget_chars is None:
        budget = LOCAL_PROMPT_CHAR_BUDGET - system_chars - tools_chars - extra_chars
    else:
        budget = budget_chars
    if budget <= 0:
        return []
    kept: list[dict[str, Any]] = []
    used = 0
    for message in reversed(messages):
        size = len(message.get("content") or "")
        if used + size > budget:
            break
        kept.append(message)
        used += size
    return list(reversed(kept))


def cap_tool_result(data: Any, *, max_chars: int = LOCAL_TOOL_RESULT_MAX_CHARS) -> str:
    """Serializa el resultado de una tool limitando su tamaño (modo local).

    Conserva el shape (claves, tipos y orden) y va reduciendo las listas hasta
    que el JSON cabe en `max_chars`. El shape importa: el modelo debe poder
    distinguir "lista vacia" de "sin datos" sin inventar campos.
    """
    text = _serialize(data, list_items=5, str_limit=300)
    for list_items, str_limit in ((3, 160), (1, 80), (0, 40)):
        if len(text) <= max_chars:
            break
        text = _serialize(data, list_items=list_items, str_limit=str_limit)
    if len(text) > max_chars:
        text = json.dumps(
            {"error": "demasiados datos", "resumen": str(data)[:200]},
            ensure_ascii=False,
        )
    return text


def _serialize(data: Any, *, list_items: int, str_limit: int) -> str:
    return json.dumps(_shrink(data, list_items=list_items, str_limit=str_limit), ensure_ascii=False)


def _shrink(value: Any, *, list_items: int, str_limit: int) -> Any:
    if isinstance(value, dict):
        return {key: _shrink(val, list_items=list_items, str_limit=str_limit) for key, val in value.items()}
    if isinstance(value, list):
        return [_shrink(item, list_items=list_items, str_limit=str_limit) for item in value[:list_items]]
    if isinstance(value, str) and len(value) > str_limit:
        return value[:str_limit] + "..."
    return value
