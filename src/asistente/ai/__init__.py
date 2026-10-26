"""Capa de IA: cliente LLM intercambiable y sistema de prompts (Fase 1)."""

from __future__ import annotations

from asistente.ai.llm import LLMClient, LLMError, LLMResponse, LLMToolCall, OpenAIClient
from asistente.ai.prompts import SYSTEM_PROMPT_VERSION, build_system_prompt

__all__ = [
    "LLMClient",
    "LLMToolCall",
    "LLMResponse",
    "LLMError",
    "OpenAIClient",
    "SYSTEM_PROMPT_VERSION",
    "build_system_prompt",
]
