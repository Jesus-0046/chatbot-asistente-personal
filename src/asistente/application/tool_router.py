"""Router determinista de grupos de tools y compositor del presupuesto local.

El modo local anuncia el núcleo fijo más los grupos que casan con el mensaje
del usuario. El router es determinista (frases clave, sin LLM) y "pegajoso" un
turno: si el mensaje actual no casa con ningún grupo, se reutilizan los grupos
del mensaje anterior del usuario (los seguimientos del tipo "y del coche?"
funcionan sin repetir la palabra clave).

El compositor decide qué cabe en `LOCAL_PROMPT_CHAR_BUDGET`. Orden de recorte:

  1. reducir historial (el presupuesto restante se le pasa a `trim_history`),
  2. reducir el límite del resultado de la tool (1.200 → 600 chars),
  3. eliminar grupos secundarios (el último del enrutado primero; el núcleo
     nunca se toca).

No se trunca el prompt: o cabe, o se anuncia menos.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from asistente.application.local_mode import LOCAL_PROMPT_CHAR_BUDGET
from asistente.tools.catalog import (
    GROUP_KEYWORDS,
    GROUP_ORDER,
    ROUTE_PRIORITY,
    TRIP_EDIT_VERBS,
    TRIP_LINK_VERBS,
    groups_of_tool,
    tool_names_for_groups,
)

# Límites de resultado que el compositor puede elegir (de mayor a menor).
LOCAL_RESULT_CAPS: tuple[int, ...] = (1200, 1000, 800, 600)
# Reserva para el mensaje assistant(tool_call) + tool(result) del turno.
LOCAL_TOOL_CALL_RESERVE = 200

_NON_WORD = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Minúsculas, sin acentos y con espacios simples (para casar frases)."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    ascii_like = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_NON_WORD.sub(" ", ascii_like).split())


def _contains(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


def _matches(text: str, phrases: Iterable[str]) -> bool:
    return any(_contains(text, phrase) for phrase in phrases)


def _matched_groups(normalized: str) -> set[str]:
    matched = {group for group, keywords in GROUP_KEYWORDS.items() if _matches(normalized, keywords)}
    # Los viajes comparten la palabra "viaje": los subgrupos de edición y de
    # vínculos se añaden solo si además hay un verbo que lo justifique.
    if "trips_read" in matched:
        if _matches(normalized, TRIP_EDIT_VERBS):
            matched.add("trips_edit")
        if _matches(normalized, TRIP_LINK_VERBS):
            matched.add("trips_links")
    return matched


def route_groups(text: str, previous_text: str | None = None) -> tuple[str, ...]:
    """Grupos que casan con el mensaje, en orden de prioridad de enrutado.

    `previous_text` es el último mensaje del usuario: si el actual no casa con
    nada, se reutilizan sus grupos (dominio pegajoso durante un turno).
    """
    normalized = _normalize(text or "")
    matched = _matched_groups(normalized)
    if not matched and previous_text:
        matched = _matched_groups(_normalize(previous_text))
    return tuple(group for group in ROUTE_PRIORITY if group in matched)


def groups_for_tool(tool_name: str) -> tuple[str, ...]:
    """Grupos necesarios para anunciar una tool concreta (confirmaciones)."""
    return groups_of_tool(tool_name)


@dataclass(frozen=True, slots=True)
class Announcement:
    """Decisión de anuncio para un turno."""

    groups: tuple[str, ...]
    tool_names: tuple[str, ...]
    tools_chars: int
    result_max_chars: int
    history_budget_chars: int
    dropped: tuple[str, ...] = ()


def _announcement(
    routed: Sequence[str],
    *,
    tool_names: tuple[str, ...],
    tools_chars: int,
    result_max_chars: int,
    history_budget_chars: int,
    dropped: Sequence[str],
) -> Announcement:
    selected = set(routed) - set(dropped)
    return Announcement(
        groups=tuple(group for group in GROUP_ORDER if group in selected),
        tool_names=tool_names,
        tools_chars=tools_chars,
        result_max_chars=result_max_chars,
        history_budget_chars=max(0, history_budget_chars),
        dropped=tuple(dropped),
    )


def compose_announcement(
    routed: Sequence[str],
    *,
    system_chars: int,
    user_chars: int,
    measure_tools_chars: Callable[[Sequence[str]], int],
    budget_chars: int = LOCAL_PROMPT_CHAR_BUDGET,
) -> Announcement:
    """Compone el anuncio que cabe en el presupuesto, recortando por prioridad."""
    kept = [group for group in ROUTE_PRIORITY if group in set(routed)]
    dropped: list[str] = []

    while True:
        tool_names = tool_names_for_groups(kept)
        tools_chars = measure_tools_chars(tool_names)
        fixed_base = system_chars + user_chars + tools_chars + LOCAL_TOOL_CALL_RESERVE
        for cap in LOCAL_RESULT_CAPS:
            history_budget = budget_chars - fixed_base - cap
            if history_budget >= 0:
                return _announcement(
                    routed,
                    tool_names=tool_names,
                    tools_chars=tools_chars,
                    result_max_chars=cap,
                    history_budget_chars=history_budget,
                    dropped=dropped,
                )
        if not kept:
            # Ni el núcleo con el límite mínimo cabe: se anuncia igualmente y
            # el orquestador recortará el historial a cero. No se trunca nada.
            return _announcement(
                routed,
                tool_names=tool_names,
                tools_chars=tools_chars,
                result_max_chars=LOCAL_RESULT_CAPS[-1],
                history_budget_chars=0,
                dropped=dropped,
            )
        dropped.insert(0, kept.pop())
