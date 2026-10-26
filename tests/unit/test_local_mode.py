"""Tests del modo local: prompt compacto, subconjunto de tools y recorte."""

from __future__ import annotations

from datetime import datetime

from asistente.ai.prompts import (
    LOCAL_SYSTEM_PROMPT,
    LOCAL_SYSTEM_PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_local_system_prompt,
    build_system_prompt,
)
from asistente.application.local_mode import (
    LOCAL_HISTORY_MESSAGES,
    LOCAL_PROMPT_CHAR_BUDGET,
    cap_tool_result,
    compact_specs,
    is_local_mode,
    local_tool_names,
    local_user_text,
    trim_history,
)
from asistente.config import Settings
from asistente.tools.local import LOCAL_TOOL_NAMES, compact_spec, select_local_tool_names
from asistente.tools.registry import ToolDefinition, ToolRegistry


def _settings(**overrides) -> Settings:
    base = {"telegram_token": "dummy", "openai_base_url": ""}
    base.update(overrides)
    return Settings(**base)


def test_modo_local_se_detecta_por_el_base_url() -> None:
    assert is_local_mode(_settings()) is False
    assert is_local_mode(_settings(openai_base_url="   ")) is False
    assert is_local_mode(_settings(openai_base_url="http://localhost:11434/v1")) is True


def test_el_mensaje_del_usuario_lleva_la_fecha() -> None:
    now = datetime(2026, 10, 21, 18, 30)
    text = local_user_text("que me queda", now=now, timezone="Europe/Madrid")
    assert text.startswith("[Ahora: 21/10/2026 18:30 (Europe/Madrid)]")
    assert text.endswith("que me queda")


def test_el_prompt_local_es_mas_corto_que_el_oficial() -> None:
    oficial = build_system_prompt(current_datetime=datetime(2026, 10, 21), timezone="Europe/Madrid")
    local = build_local_system_prompt()
    assert len(local) < len(oficial) / 2
    assert len(local) < 4000
    assert "Fecha y hora actuales" not in local
    assert "Fecha y hora actuales" in oficial


def test_el_prompt_local_no_lleva_la_fecha_pero_incluye_la_memoria() -> None:
    prompt = build_local_system_prompt(
        active_memory_summary="- alias: jesus",
        pending_summary="crear evento",
    )
    assert "jesus" in prompt
    assert "crear evento" in prompt
    assert LOCAL_SYSTEM_PROMPT_VERSION != "5.1.0"
    assert LOCAL_SYSTEM_PROMPT in prompt


def test_el_subconjunto_local_es_fijo() -> None:
    # El conjunto anunciado no puede cambiar entre turnos: si cambia, el
    # prefijo del prompt cambia y la cache de prefijo del servidor no sirve.
    assert select_local_tool_names("hola") == select_local_tool_names("pon el coche a revisar")
    assert select_local_tool_names("cualquier cosa") == LOCAL_TOOL_NAMES
    assert local_tool_names("hola") == LOCAL_TOOL_NAMES
    assert local_tool_names() == LOCAL_TOOL_NAMES


def test_el_subconjunto_local_cubre_las_areas_principales() -> None:
    for name in (
        "get_pending_summary",
        "list_tasks",
        "create_event",
        "create_expense",
        "get_balance_overview",
        "get_budget_status",
        "create_note",
    ):
        assert name in LOCAL_TOOL_NAMES


def test_el_spec_compactado_conserva_los_tipos_y_los_obligatorios() -> None:
    spec = {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": "x" * 400,
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Titulo"},
                    "when": {"type": "string", "description": "Cuando"},
                    "priority": {"type": "string", "enum": ["low", "high"], "default": "medium"},
                },
                "required": ["title"],
            },
        },
    }
    compacted = compact_spec(spec)
    params = compacted["function"]["parameters"]
    assert len(compacted["function"]["description"]) <= 160
    assert params["required"] == ["title"]
    assert params["properties"]["title"] == {"type": "string"}
    assert params["properties"]["priority"]["enum"] == ["low", "high"]


def test_el_spec_compactado_ahorra_tokens() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="create_event",
            description="Crea un evento. " * 40,
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Titulo del evento"},
                    "when": {"type": "string", "description": "Cuando (natural o ISO)"},
                },
                "required": ["title"],
            },
            handler=lambda ctx, **_: None,  # type: ignore[arg-type,misc]
        )
    )
    specs = registry.specs()
    assert len(str(compact_specs(specs))) < len(str(specs))


def test_el_resultado_de_la_tool_se_recorta_conservando_el_shape() -> None:
    data = {"items": [{"id": i, "title": "t" * 80} for i in range(30)], "total": 30}
    capped = cap_tool_result(data, max_chars=400)
    assert len(capped) <= 400
    assert "items" in capped
    assert "total" in capped


def test_el_resultado_corto_no_se_toca() -> None:
    data = {"ok": True, "count": 2}
    assert cap_tool_result(data) == '{"ok": true, "count": 2}'


def test_el_historial_local_esta_acotado() -> None:
    assert 1 <= LOCAL_HISTORY_MESSAGES <= 10


def test_el_historial_se_recorta_por_el_presupuesto_de_caracteres() -> None:
    messages = [
        {"role": "user", "content": "a" * 2000},
        {"role": "assistant", "content": "b" * 2000},
        {"role": "user", "content": "c" * 2000},
        {"role": "assistant", "content": "d" * 2000},
    ]
    kept = trim_history(messages, system_chars=2700, tools_chars=9000)
    assert len(kept) < len(messages)
    assert kept[-1]["content"].startswith("d")


def test_el_historial_no_se_recorta_si_cabe() -> None:
    messages = [{"role": "user", "content": "hola"}]
    kept = trim_history(messages, system_chars=2700, tools_chars=9000)
    assert kept == messages


def test_specs_for_devuelve_las_tools_pedidas() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_pending_summary",
            description="Resumen",
            input_schema={"type": "object", "properties": {}},
            handler=lambda ctx, **_: None,  # type: ignore[arg-type,misc]
        )
    )
    specs = registry.specs_for(["get_pending_summary", "no_existe"])
    assert [s["function"]["name"] for s in specs] == ["get_pending_summary"]


def test_el_prompt_oficial_no_cambia() -> None:
    assert SYSTEM_PROMPT.startswith("Eres un asistente personal")
    prompt = build_system_prompt(current_datetime=datetime(2026, 10, 21), timezone="Europe/Madrid")
    assert "21/10/2026" in prompt
    assert LOCAL_PROMPT_CHAR_BUDGET > 10_000
