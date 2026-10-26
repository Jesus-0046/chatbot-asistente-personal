"""Subconjunto de tools para el proveedor local (ventana de contexto pequeña).

Los modelos locales (p. ej. Ollama con qwen3) tienen una ventana de contexto
de 4096 tokens. Las 135 tools describen ~17k tokens, así que el prompt completo
no cabe: el servidor recorta el prompt, el modelo deja de ver la mayoría de las
tools y la latencia se dispara porque la cache de prefijo no se puede reutilizar
(el prefill va a ~28 tok/s).

El modo local anuncia un NÚCLEO FIJO de 22 tools más grupos especializados
bajo demanda (router determinista). El prefijo [system][tools] del núcleo se
mantiene estable entre turnos para reutilizar la cache; solo se añaden o
quitan grupos (nunca se reordena el núcleo). El registro sigue teniendo las
135 tools: solo cambia lo que se anuncia.

Para meter más tools en el mismo presupuesto, el spec local va "compactado":
descripción corta y esquema sin las descripciones de los parámetros (se
conservan los tipos, los obligatorios y los enums, que es lo que necesita el
modelo para llamar a la tool con argumentos válidos).
"""

from __future__ import annotations

from typing import Any

from asistente.tools.catalog import CORE_TOOL_NAMES

# Núcleo fijo anunciado en modo local (22 tools, orden exacto).
LOCAL_TOOL_NAMES: tuple[str, ...] = CORE_TOOL_NAMES

# Longitud máxima de la descripción de una tool en el spec local.
LOCAL_MAX_DESCRIPTION_CHARS = 160


def select_local_tool_names(text: str | None = None) -> tuple[str, ...]:
    """Tools del subconjunto local (núcleo estable)."""
    return LOCAL_TOOL_NAMES


def compact_spec(spec: dict[str, Any], *, max_description: int = LOCAL_MAX_DESCRIPTION_CHARS) -> dict[str, Any]:
    """Compacta un spec de tool: descripción corta y esquema sin metadatos."""
    function = spec.get("function", {})
    description = (function.get("description") or "").strip()
    if len(description) > max_description:
        description = description[: max_description - 1].rstrip() + "…"
    return {
        "type": "function",
        "function": {
            "name": function.get("name", ""),
            "description": description,
            "parameters": _compact_schema(function.get("parameters") or {}),
        },
    }


def _compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if schema.get("type") != "object" or "properties" not in schema:
        return schema
    properties: dict[str, Any] = {}
    for name, prop in (schema.get("properties") or {}).items():
        if not isinstance(prop, dict):
            properties[name] = prop
            continue
        compact: dict[str, Any] = {"type": prop.get("type", "string")}
        if "enum" in prop:
            compact["enum"] = prop["enum"]
        if prop.get("type") == "array":
            items = prop.get("items")
            if isinstance(items, dict):
                compact["items"] = {"type": items.get("type", "string")}
                if "enum" in items:
                    compact["items"]["enum"] = items["enum"]
        properties[name] = compact
    compacted: dict[str, Any] = {"type": "object", "properties": properties}
    required = schema.get("required")
    if required:
        compacted["required"] = required
    return compacted
