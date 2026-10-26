"""Capa de dominio — servicios usados por el orquestador y las tools.

El LLM y las tools nunca tocan la BD directamente: toda mutación pasa por
estos servicios (scoped por `user_id` del contexto autenticado).
"""

from __future__ import annotations

from asistente.domain.memory import MemoryService
from asistente.domain.notes import NoteService
from asistente.domain.users import UserService

__all__ = ["UserService", "NoteService", "MemoryService"]
