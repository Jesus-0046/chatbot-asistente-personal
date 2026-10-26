"""Tests del saneado de la respuesta final (nada de JSON crudo al usuario)."""

from __future__ import annotations

import json

from asistente.application.answers import (
    GENERIC_FALLBACK,
    build_fallback_reply,
    looks_like_raw_payload,
)
from asistente.tools.registry import ToolResult

PENDING_PAYLOAD = {
    "timezone": "Europe/Madrid",
    "task_count": 3,
    "overdue_task_count": 1,
    "overdue_tasks": [{"id": 7, "title": "Renovar el seguro", "due_at": "2026-10-19T18:00:00"}],
    "tasks": [
        {"id": 7, "title": "Renovar el seguro"},
        {"id": 8, "title": "Comprar cadeau"},
        {"id": 9, "title": "Llamar al taller"},
    ],
    "planned_payment_count": 1,
    "planned_total_amount": "45.00",
    "planned": [{"id": 3, "title": "Alquiler", "amount": "45.00"}],
    "open_checklist": [],
    "open_checklist_total": 0,
}


def test_detecta_json_completo() -> None:
    assert looks_like_raw_payload('{"task_count": 3, "tasks": []}') is True
    assert looks_like_raw_payload('[{"title": "a"}]') is True


def test_detecta_json_truncado() -> None:
    truncado = '{"timezone": "Europe/Madrid", "task_count": 3, "tasks": [{"title": "Renovar'
    assert looks_like_raw_payload(truncado) is True


def test_no_detecta_texto_normal() -> None:
    assert looks_like_raw_payload("Tienes 3 tareas pendientes.") is False
    assert looks_like_raw_payload("") is False
    assert looks_like_raw_payload(None) is False
    assert looks_like_raw_payload("  ") is False


def test_no_detecta_json_pequeno_en_prosa() -> None:
    assert looks_like_raw_payload('El campo "estado" está vacío.') is False


def test_detecta_json_incrustado_en_prosa() -> None:
    texto = "Aquí tienes el detalle: " + json.dumps(PENDING_PAYLOAD) + " avísame si quieres más."
    assert looks_like_raw_payload(texto) is True


def test_fallback_resume_el_pending_sin_json() -> None:
    reply = build_fallback_reply([ToolResult(ok=True, data=PENDING_PAYLOAD)])

    assert "3 tareas pendientes" in reply
    assert "1 vencida" in reply
    assert "Renovar el seguro" in reply
    assert "45,00" in reply
    assert "{" not in reply
    assert "task_count" not in reply


def test_fallback_sin_datos_devuelve_mensaje_generico() -> None:
    reply = build_fallback_reply([ToolResult(ok=True, data={"done": True})])

    assert reply == GENERIC_FALLBACK
    assert "{" not in reply


def test_fallback_sin_resultados_devuelve_mensaje_generico() -> None:
    assert build_fallback_reply([]) == GENERIC_FALLBACK
    assert build_fallback_reply(None) == GENERIC_FALLBACK


def test_fallback_ignora_resultados_con_error() -> None:
    reply = build_fallback_reply([ToolResult(ok=False, error="boom")])

    assert reply == GENERIC_FALLBACK
