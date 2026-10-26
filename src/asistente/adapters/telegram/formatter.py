"""Formateo seguro para Telegram (Fase 0: mínimo).

En fases posteriores aquí vivirá la conversión de respuestas del orquestador a
Markdown/HTML de Telegram con escape de contenido no fiable.
"""

from __future__ import annotations


def escape_html(text: str | None) -> str:
    """Escapa caracteres especiales HTML para evitar inyección en los mensajes."""
    if text is None:
        return ""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
