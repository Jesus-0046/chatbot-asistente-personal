"""Contexto que las tools reciben al ejecutar.

Nunca recibe `user_id` del LLM; siempre viene del contexto autenticado.
"""

from __future__ import annotations

from dataclasses import dataclass

from asistente.application.services import Services


@dataclass(frozen=True, slots=True)
class ToolContext:
    user_id: int
    services: Services
