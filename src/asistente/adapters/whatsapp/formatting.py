"""Formato de salida para WhatsApp (Fase 9).

WhatsApp limita los mensajes de texto (~4096 caracteres por la Cloud API). Se
envía el texto tal cual produce el orquestador (sin formateo markdown, igual
que Telegram hoy); si excede el límite se parte en trozos que respetan el
orden, no rompen números/URLs cuando se puede evitar, y solo cortan a nivel de
carácter cuando un único token es más largo que el límite.
"""

from __future__ import annotations

WHATSAPP_MAX_TEXT_CHARS = 4096


def split_for_send(text: str, max_chars: int = WHATSAPP_MAX_TEXT_CHARS) -> list[str]:
    """Divide `text` en trozos de <= `max_chars` conservando el orden.

    Reglas:
    1. Si cabe entero, se devuelve tal cual.
    2. Se acumulan tokens separados por espacio rellenando cada trozo hasta
       `max_chars` (los saltos de línea incrustados se conservan dentro de su
       token).
    3. Un token más largo que `max_chars` (p. ej. una URL gigante) se corta
       por caracteres; los números y URLs normales nunca se parten.
    """
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    tokens: list[str] = text.split(" ")
    current = ""

    for token in tokens:
        candidate = token if not current else f"{current} {token}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(token) > max_chars:
            # Token aislado más largo que el límite: partir por caracteres.
            for start in range(0, len(token), max_chars):
                chunks.append(token[start : start + max_chars])
            current = ""
        else:
            current = token

    if current:
        chunks.append(current)
    return chunks
