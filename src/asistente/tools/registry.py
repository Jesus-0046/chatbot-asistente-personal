"""Registro de herramientas para el LLM (function calling).

Cada tool se registra con:
- `name`                   nombre único en el registro
- `description`            descripción visible por el modelo
- `input_schema`           JSON Schema de los argumentos (para el LLM)
- `args_model`             modelo Pydantic que valida los argumentos al ejecutar
- `handler`                callable que recibe (ctx, **kwargs) -> ToolResult
- `requires_confirmation`  si True, el orquestador creará una confirmación pendiente

Las tools se devuelven al LLM como `tools` en el formato OpenAI.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from structlog import get_logger

from asistente.tools.context import ToolContext

_log = get_logger("tools.registry")
_timing_log = logging.getLogger("asistente.tools.registry")

_INTERNAL_TOOL_ERROR = "Error interno al ejecutar la acción. Inténtalo de nuevo."


@dataclass(frozen=True, slots=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    needs_confirmation: bool = False
    confirmation_id: int | None = None
    proposed_reply: str = ""


@dataclass(slots=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[..., Any]
    requires_confirmation: bool = False
    args_model: type[BaseModel] | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def specs(self) -> list[dict[str, Any]]:
        """Devuelve la lista de tools en formato OpenAI."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]

    def specs_for(self, names: Sequence[str]) -> list[dict[str, Any]]:
        """Specs de las tools indicadas, en ese orden (ignora las desconocidas)."""
        specs: list[dict[str, Any]] = []
        for name in names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )
        return specs

    async def execute(
        self,
        ctx: ToolContext,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        """Ejecuta una herramienta midiendo su duración (ms).

        La instrumentación no altera el resultado ni el comportamiento de
        errores: solo registra el nombre de la herramienta y los milisegundos.
        Si `_execute` lanzara una excepción no controlada, el `finally` la
        sigue midiendo y la propaga sin cambios (`ok=None`).
        """
        started = time.perf_counter()
        ok: bool | None = None
        try:
            result = await self._execute(ctx, name, arguments, confirmed=confirmed)
            ok = result.ok
            return result
        finally:
            duration_ms = (time.perf_counter() - started) * 1000.0
            _timing_log.info(
                "tool_executed tool=%s ok=%s duration_ms=%.1f",
                name,
                ok,
                duration_ms,
            )

    async def _execute(
        self,
        ctx: ToolContext,
        name: str,
        arguments: dict[str, Any],
        *,
        confirmed: bool = False,
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, error=f"La herramienta '{name}' no existe.")

        if tool.requires_confirmation and not confirmed:
            return ToolResult(ok=False, needs_confirmation=True)

        if tool.args_model is not None:
            try:
                validated = tool.args_model.model_validate(arguments)
            except ValidationError as exc:
                errors = exc.errors(include_url=False)
                return ToolResult(
                    ok=False,
                    error=errors[0]["msg"] if errors else "Argumentos no válidos",
                )
            kwargs = validated.model_dump()
        else:
            kwargs = dict(arguments)

        try:
            result = await tool.handler(ctx, **kwargs)
        except TypeError as exc:
            _log.warning(
                "tool_bad_arguments",
                tool=name,
                user_id=ctx.user_id,
                exc_info=exc,
            )
            return ToolResult(ok=False, error=f"Argumentos no válidos para '{name}'.")
        except Exception as exc:
            # Nunca se filtra el texto de la excepción interna (SQLAlchemy,
            # red, tracebacks) al usuario: se registra en los logs y se
            # devuelve un mensaje genérico.
            _log.error(
                "tool_unexpected_error",
                tool=name,
                user_id=ctx.user_id,
                exc_info=exc,
            )
            return ToolResult(ok=False, error=_INTERNAL_TOOL_ERROR)

        if not isinstance(result, ToolResult):
            return ToolResult(ok=False, error=f"La herramienta '{name}' devolvió un resultado no válido.")
        return result
