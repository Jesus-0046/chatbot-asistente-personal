"""Tests unitarios: registro de herramientas (validación, confirmación, errores)."""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pydantic import BaseModel, Field

from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolRegistry, ToolResult


class FakeServices:
    """Suficiente para construir un ToolContext en tests aislados."""


def make_ctx(user_id: int = 1) -> ToolContext:
    return ToolContext(user_id=user_id, services=FakeServices())


async def _ok_handler(ctx: ToolContext, **_: Any) -> ToolResult:
    return ToolResult(ok=True, data={"handled": ctx.user_id})


async def _boom_handler(ctx: ToolContext, **_: Any) -> ToolResult:
    raise RuntimeError("kaboom")


def make_definition(
    *,
    name: str = "echo",
    description: str = "Test tool",
    args_model: type[BaseModel] | None = None,
    handler: Any = _ok_handler,
    requires_confirmation: bool = False,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=description,
        input_schema={"type": "object", "properties": {}},
        handler=handler,
        args_model=args_model,
        requires_confirmation=requires_confirmation,
    )


def test_register_y_get() -> None:
    registry = ToolRegistry()
    tool = make_definition()
    registry.register(tool)
    assert registry.get("echo") is tool


def test_get_tool_inexistente_devuelve_none() -> None:
    registry = ToolRegistry()
    assert registry.get("nope") is None


def test_execute_tool_inexistente_falla_limpio() -> None:
    registry = ToolRegistry()
    result = asyncio_run(registry.execute(make_ctx(), "nope", {}))
    assert result.ok is False
    assert "nope" in (result.error or "")


def test_execute_valida_argumentos_con_pydantic() -> None:
    class NoteArgs(BaseModel):
        title: str = Field(min_length=1)
        body: str = Field(default="", max_length=10)

    registry = ToolRegistry()
    registry.register(make_definition(name="create_note", args_model=NoteArgs))

    result = asyncio_run(registry.execute(make_ctx(), "create_note", {"title": "Lista", "body": "leche"}))
    assert result.ok is True


def test_execute_argumentos_invalidos_son_rechazados() -> None:
    class NoteArgs(BaseModel):
        title: str = Field(min_length=1)
        body: str = Field(default="", max_length=10)

    registry = ToolRegistry()
    registry.register(make_definition(name="create_note", args_model=NoteArgs))

    result = asyncio_run(registry.execute(make_ctx(), "create_note", {"title": ""}))
    assert result.ok is False
    assert result.error is not None


def test_execute_pasa_kwargs_validados_al_handler() -> None:
    seen: dict[str, Any] = {}

    async def record_handler(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        seen.update(kwargs)
        return ToolResult(ok=True)

    class NoteArgs(BaseModel):
        title: str = Field(min_length=1)
        body: str = Field(default="", max_length=10)

    registry = ToolRegistry()
    registry.register(make_definition(name="create_note", args_model=NoteArgs, handler=record_handler))

    result = asyncio_run(registry.execute(make_ctx(), "create_note", {"title": "Lista", "body": "leche"}))
    assert result.ok is True
    assert seen == {"title": "Lista", "body": "leche"}


def test_confirmacion_requerida_bloquea_sin_confirmed() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="delete_note", requires_confirmation=True))

    result = asyncio_run(registry.execute(make_ctx(), "delete_note", {"note_id": 1}))
    assert result.ok is False
    assert result.needs_confirmation is True


def test_confirmacion_requerida_permite_con_confirmed() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="delete_note", requires_confirmation=True))

    result = asyncio_run(registry.execute(make_ctx(), "delete_note", {"note_id": 1}, confirmed=True))
    assert result.ok is True


def test_error_no_controlado_se_captura_sin_filtrar_el_texto() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="boom", handler=_boom_handler))

    result = asyncio_run(registry.execute(make_ctx(), "boom", {}))
    assert result.ok is False
    assert "kaboom" not in (result.error or "")
    assert result.error.startswith("Error interno")


def test_error_no_controlado_no_filtra_texto_interno() -> None:
    async def nasty_handler(ctx: ToolContext, **_: Any) -> ToolResult:
        raise ValueError("campo raro con detalle interno")

    registry = ToolRegistry()
    registry.register(make_definition(name="nasty", handler=nasty_handler))

    result = asyncio_run(registry.execute(make_ctx(), "nasty", {}))
    assert result.ok is False
    # El usuario recibe un mensaje genérico; el detalle solo vive en los logs.
    assert result.error == "Error interno al ejecutar la acción. Inténtalo de nuevo."
    assert "campo raro" not in (result.error or "")


def test_fuga_de_nivel_bajo_nunca_llega_al_usuario() -> None:
    async def sql_handler(ctx: ToolContext, **_: Any) -> ToolResult:
        raise RuntimeError("psycopg.errors.UniqueViolation: duplicate key value violates unique constraint")

    registry = ToolRegistry()
    registry.register(make_definition(name="sql", handler=sql_handler))

    result = asyncio_run(registry.execute(make_ctx(), "sql", {}))
    assert result.ok is False
    assert "UniqueViolation" not in (result.error or "")
    assert "psycopg" not in (result.error or "")


def test_specs_devuelven_formato_openai() -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="echo", description="Desc"))
    specs = registry.specs()
    assert specs == [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Desc",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


# --------------------------------------------------------------------------- #
# Instrumentación: duración por ejecución de herramienta                        #
# --------------------------------------------------------------------------- #
def test_registra_duracion_de_la_herramienta(caplog: pytest.LogCaptureFixture) -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="echo"))
    with caplog.at_level(logging.INFO, logger="asistente.tools.registry"):
        result = asyncio_run(registry.execute(make_ctx(), "echo", {}))
    assert result.ok is True
    record = _last_timing_record(caplog)
    assert record is not None
    text = record.getMessage()
    assert "tool=echo" in text
    assert "ok=True" in text
    assert "duration_ms=" in text


def test_registra_duracion_de_herramienta_que_falla(caplog: pytest.LogCaptureFixture) -> None:
    registry = ToolRegistry()
    registry.register(make_definition(name="boom", handler=_boom_handler))
    with caplog.at_level(logging.INFO, logger="asistente.tools.registry"):
        result = asyncio_run(registry.execute(make_ctx(), "boom", {}))
    assert result.ok is False
    record = _last_timing_record(caplog)
    assert record is not None
    text = record.getMessage()
    assert "tool=boom" in text
    assert "ok=False" in text
    assert "duration_ms=" in text


def _last_timing_record(caplog: pytest.LogCaptureFixture) -> logging.LogRecord | None:
    for record in reversed(caplog.records):
        if "tool_executed" in record.getMessage():
            return record
    return None


def asyncio_run(awaitable: Any) -> Any:
    import asyncio

    return asyncio.run(awaitable)
