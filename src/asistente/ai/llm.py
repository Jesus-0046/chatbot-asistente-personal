"""Capa de cliente LLM (OpenAI).

`LLMClient` es un protocolo de Python que el orquestador usa para hacer
llamadas al modelo.  `OpenAIClient` es la implementación real usando el SDK
oficial.  Para tests se puede crear un fake que implemente el protocolo sin
gastar llamadas reales.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import openai

from asistente.config import Settings

logger = logging.getLogger("asistente.ai.llm")


@dataclass
class LLMToolCall:
    id: str
    function_name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[LLMToolCall]
    raw: dict = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        """Llamada al LLM; devuelve texto y/o llamadas a herramientas."""
        ...


class LLMError(Exception):
    """Fallo de la llamada al LLM (timeout, auth, etc.)."""


def create_openai_client(settings: Settings):
    """Cliente HTTP OpenAI compartido por LLM y visión (Fase 7).

    Un único punto de construcción de la integración para no duplicarla:
    `OpenAIClient` (texto/tools) y el proveedor de imágenes usan la misma
    clave, timeout y reintentos (`openai_max_retries`). Un `Timeout` por
    conexión no se configura explícitamente: el SDK usa el de httpx (10 s).

    Con `openai_base_url` configurada el SDK habla a un servidor compatible
    con la API de OpenAI (p. ej. Ollama local en `/v1`); en ese modo local la
    clave puede estar vacía y se envía un valor ficticio porque el SDK exige
    un `api_key` no nulo (el servidor local ignora la cabecera Authorization).
    """
    base_url = settings.openai_base_url.strip() or None
    api_key = settings.openai_api_key.get_secret_value()
    if base_url and not api_key:
        api_key = "local"
    return openai.OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=settings.openai_max_retries,
        timeout=settings.openai_timeout_seconds,
    )


class OpenAIClient:
    def __init__(self, settings: Settings) -> None:
        self._client = create_openai_client(settings)
        self._model = settings.openai_model
        self._max_tokens = settings.openai_max_tokens
        # Proveedor local (p. ej. Ollama en `/v1`): desactiva el razonamiento
        # extendido. `qwen3` piensa por defecto y multiplica la latencia; el
        # valor `none` solo se envía contra un `base_url` configurado, nunca
        # contra la API oficial de OpenAI.
        self._local_mode = bool(settings.openai_base_url.strip())
        # Solo en modo local: el servidor (p. ej. Ollama) mantiene el modelo
        # cargado y su caché de prefijo durante este tiempo, para que un turno
        # tras una pausa no pague la recarga ni el prefill completo.
        self._keep_alive = settings.openai_keep_alive.strip()

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
        }
        if self._max_tokens:
            kwargs["max_tokens"] = self._max_tokens
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self._local_mode:
            kwargs["reasoning_effort"] = "none"
            if self._keep_alive:
                kwargs["extra_body"] = {"keep_alive": self._keep_alive}

        started = time.perf_counter()
        outcome = "error"
        requested_tools: list[str] = []
        try:
            try:
                response = self._client.chat.completions.create(**kwargs)
                choice = response.choices[0]
                message = choice.message
            except openai.AuthenticationError as exc:
                raise LLMError("No se pudo autenticar con OpenAI (clave no válida).") from exc
            except openai.APITimeoutError as exc:
                raise LLMError("Tiempo de espera agotado al llamar a OpenAI.") from exc
            except openai.APIError as exc:
                raise LLMError(f"Error de la API de OpenAI: {exc}") from exc

            tool_calls: list[LLMToolCall] = []
            if message.tool_calls:
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}
                    tool_calls.append(LLMToolCall(id=tc.id, function_name=tc.function.name, arguments=args))

            raw_content = message.content
            requested_tools = [tc.function_name for tc in tool_calls]
            outcome = "ok"
            return LLMResponse(
                content=raw_content or "",
                tool_calls=tool_calls,
                raw={"model": response.model, "finish_reason": choice.finish_reason},
            )
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            logger.info(
                "llm_call duration_ms=%.1f outcome=%s model=%s requested_tools=%s",
                duration_ms,
                outcome,
                self._model,
                requested_tools,
            )
