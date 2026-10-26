"""Tests unitarios: system prompt (Fase 1)."""

from __future__ import annotations

from datetime import UTC, datetime

from asistente.ai.prompts import SYSTEM_PROMPT_VERSION, build_system_prompt


def _build() -> str:
    return build_system_prompt(current_datetime=datetime(2026, 9, 14, 12, 0, tzinfo=UTC))


def test_system_prompt_responde_en_espanol_de_espana() -> None:
    prompt = _build()
    assert "español de España" in prompt
    assert "es-ES" in prompt


def test_system_prompt_requiere_confirmacion_para_acciones_sensibles() -> None:
    prompt = _build()
    assert "confirm" in prompt.lower()


def test_system_prompt_no_activa_memoria_automaticamente() -> None:
    prompt = _build()
    assert "no actives" in prompt.lower()
    assert "automáticamente" not in prompt


def test_system_prompt_contiene_reglas_de_presupuesto_y_ahorro() -> None:
    prompt = _build()
    assert "presupuesto" in prompt.lower()
    assert "objetivos de ahorro" in prompt.lower()
    assert "transferencias y los ingresos NO son gastos" in prompt
    assert "contribuciones a objetivos de ahorro NO son gastos" in prompt
    assert "saldo de ahorro" in prompt


def test_system_prompt_contiene_reglas_de_recurrencias_y_suscripciones() -> None:
    prompt = _build()
    assert "recurrencia" in prompt.lower()
    assert "pago planificado" in prompt.lower()
    assert "suscripción" in prompt.lower() or "suscripciones" in prompt.lower()
    assert "confirmar" in prompt.lower()


def test_system_prompt_contiene_reglas_de_personas_deudas_y_gastos_compartidos() -> None:
    prompt = _build()
    assert "persona" in prompt.lower()
    assert "deuda" in prompt.lower()
    assert "gasto compartido" in prompt.lower()
    assert "no crees deudas duplicadas" in prompt
    assert "person_id" in prompt or "list_people" in prompt


def test_system_prompt_contiene_reglas_de_coche_casa_y_salud() -> None:
    prompt = _build()
    assert "repostar" in prompt.lower() or "combustible" in prompt.lower()
    assert "mantenimiento" in prompt.lower()
    assert "category='salud'" in prompt
    assert "no" in prompt.lower() and "diagnóstico" in prompt.lower()
    assert "confirmación" in prompt.lower()


def test_system_prompt_contiene_reglas_de_inteligencia_financiera() -> None:
    prompt = _build()
    assert "inteligencia financiera" in prompt.lower()
    assert "solo lectura" in prompt.lower()
    assert "get_estimated_available" in prompt
    assert "patrimonio" in prompt.lower()
    assert "disponible" in prompt.lower()
    assert "no calcules" in prompt.lower()
    assert "aviso" in prompt.lower() or "estimaciones" in prompt.lower()


def test_system_prompt_contiene_reglas_de_imagenes_y_extraccion() -> None:
    prompt = _build()
    low = prompt.lower()
    assert "imagen" in low
    assert "extracción" in low or "extraccion" in low
    assert "apply_extracted_data" in prompt
    assert "confirmación" in low or "confirmacion" in low
    assert "duplic" in low  # no duplicar gastos
    assert "importe" in low or "importes" in low
    assert "fecha" in low


def test_system_prompt_versionado() -> None:
    assert SYSTEM_PROMPT_VERSION
    assert "." in SYSTEM_PROMPT_VERSION
    assert SYSTEM_PROMPT_VERSION >= "4.0.0"
