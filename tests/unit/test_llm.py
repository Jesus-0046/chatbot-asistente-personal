"""Tests unitarios: cliente LLM (API oficial de OpenAI y modo local Ollama).

Cubren el contrato introducido para ejecutar Ollama local sin tocar el resto
del sistema:

- `create_openai_client` propaga `openai_base_url` y, en modo local sin clave
  configurada, envía un valor ficticio porque el SDK exige un `api_key` no nulo.
- `OpenAIClient.chat` usa el modelo configurado (`qwen3:8b`), el formato de
  tools estándar y `tool_choice="auto"`; parsea `tool_calls` y tolera
  argumentos JSON inválidos.
- `validate_critical_env` solo exige `OPENAI_API_KEY` contra la API oficial.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import httpx
import openai
import pytest

from asistente.ai.llm import LLMError, LLMResponse, OpenAIClient, create_openai_client
from asistente.config import ConfigError, Settings, validate_critical_env

_OLLAMA_BASE_URL = "http://host.docker.internal:11434/v1"

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Suma dos números",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                "required": ["a", "b"],
            },
        },
    }
]


def _settings(**overrides: Any) -> Settings:
    """Settings deterministas: no dependen del `.env` real del operador."""
    base: dict[str, Any] = {
        "telegram_token": "t",
        "openai_api_key": "",
        "openai_base_url": "",
        "openai_model": "gpt-4o-mini",
        "openai_timeout_seconds": 60.0,
        "openai_max_retries": 2,
        "openai_max_tokens": 0,
        "whatsapp_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)


def _capture_client(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Intercepta el constructor del SDK y devuelve los kwargs capturados."""
    captured: dict[str, Any] = {}

    def _fake(**kwargs: Any) -> Any:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("asistente.ai.llm.openai.OpenAI", _fake)
    return captured


class _StubCompletions:
    def __init__(
        self,
        message: SimpleNamespace,
        finish_reason: str,
        *,
        error: Exception | None = None,
    ) -> None:
        self._message = message
        self._finish_reason = finish_reason
        self._error = error
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.kwargs = kwargs
        if self._error is not None:
            raise self._error
        choice = SimpleNamespace(message=self._message, finish_reason=self._finish_reason)
        return SimpleNamespace(choices=[choice], model=kwargs["model"])


def _client_with_stub(
    monkeypatch: pytest.MonkeyPatch,
    message: SimpleNamespace,
    finish_reason: str = "stop",
    *,
    error: Exception | None = None,
    **overrides: Any,
) -> tuple[OpenAIClient, _StubCompletions]:
    completions = _StubCompletions(message, finish_reason, error=error)
    stub = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr("asistente.ai.llm.create_openai_client", lambda settings: stub)
    base: dict[str, Any] = {"openai_model": "qwen3:8b"}
    base.update(overrides)
    return OpenAIClient(_settings(**base)), completions


def _last_record(caplog: pytest.LogCaptureFixture, needle: str) -> logging.LogRecord | None:
    for record in reversed(caplog.records):
        if needle in record.getMessage():
            return record
    return None


# --------------------------------------------------------------------------- #
# Construcción del cliente (URL base y clave)                                  #
# --------------------------------------------------------------------------- #
def test_sin_base_url_usa_api_oficial(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch)
    create_openai_client(_settings())
    assert captured["base_url"] is None
    assert captured["api_key"] == ""
    assert captured["max_retries"] == 2
    assert captured["timeout"] == 60.0


def test_base_url_local_propaga_y_tolera_clave_vacia(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch)
    create_openai_client(_settings(openai_base_url=_OLLAMA_BASE_URL))
    assert captured["base_url"] == _OLLAMA_BASE_URL
    assert captured["api_key"]  # no vacía: el SDK lo exige y Ollama la ignora


def test_base_url_local_conserva_la_clave_si_existe(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch)
    create_openai_client(_settings(openai_base_url=_OLLAMA_BASE_URL, openai_api_key="clave-real"))
    assert captured["base_url"] == _OLLAMA_BASE_URL
    assert captured["api_key"] == "clave-real"


def test_base_url_en_blanco_equivale_a_api_oficial(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_client(monkeypatch)
    create_openai_client(_settings(openai_base_url="   "))
    assert captured["base_url"] is None


# --------------------------------------------------------------------------- #
# Selección de modelo y compatibilidad de tools                                #
# --------------------------------------------------------------------------- #
def test_chat_usa_el_modelo_configurado_y_tool_choice_auto(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message)
    response = client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert completions.kwargs["model"] == "qwen3:8b"
    assert completions.kwargs["tools"] == _TOOLS
    assert completions.kwargs["tool_choice"] == "auto"
    assert "max_tokens" not in completions.kwargs  # 0 = sin límite explícito
    assert isinstance(response, LLMResponse)
    assert response.content == "hola"
    assert response.tool_calls == []
    assert response.raw == {"model": "qwen3:8b", "finish_reason": "stop"}


def test_chat_sin_tools_no_envia_tool_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="ok", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message)
    client.chat(messages=[{"role": "user", "content": "hola"}])
    assert "tools" not in completions.kwargs
    assert "tool_choice" not in completions.kwargs


def test_chat_parsea_tool_calls_del_modelo(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_call = SimpleNamespace(id="call_abc", function=SimpleNamespace(name="add", arguments='{"a": 1, "b": 2}'))
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    client, _ = _client_with_stub(monkeypatch, message, finish_reason="tool_calls")
    response = client.chat(messages=[{"role": "user", "content": "suma"}], tools=_TOOLS)
    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "call_abc"
    assert response.tool_calls[0].function_name == "add"
    assert response.tool_calls[0].arguments == {"a": 1, "b": 2}


def test_chat_arguments_json_invalido_no_rompe(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="add", arguments="no-json"))
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    client, _ = _client_with_stub(monkeypatch, message, finish_reason="tool_calls")
    response = client.chat(messages=[{"role": "user", "content": "suma"}], tools=_TOOLS)
    assert response.tool_calls[0].arguments == {"raw": "no-json"}


# --------------------------------------------------------------------------- #
# Validación de configuración crítica                                          #
# --------------------------------------------------------------------------- #
def test_validate_critical_env_modo_local_no_exige_clave() -> None:
    validate_critical_env(_settings(openai_base_url=_OLLAMA_BASE_URL))  # no lanza


def test_validate_critical_env_api_oficial_exige_clave() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        validate_critical_env(_settings())


def test_validate_critical_env_base_url_en_blanco_exige_clave() -> None:
    with pytest.raises(ConfigError, match="OPENAI_API_KEY"):
        validate_critical_env(_settings(openai_base_url="   "))


# --------------------------------------------------------------------------- #
# Razonamiento desactivado SOLO en el proveedor local                          #
# --------------------------------------------------------------------------- #
def test_modo_local_desactiva_el_razonamiento(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message, openai_base_url=_OLLAMA_BASE_URL)
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert completions.kwargs["reasoning_effort"] == "none"


def test_modo_oficial_no_envia_razonamiento(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message)
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert "reasoning_effort" not in completions.kwargs


def test_base_url_en_blanco_no_activa_el_modo_local(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message, openai_base_url="   ")
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert "reasoning_effort" not in completions.kwargs


def test_modo_local_conserva_tools_y_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    tool_call = SimpleNamespace(id="call_abc", function=SimpleNamespace(name="add", arguments='{"a": 1, "b": 2}'))
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    client, completions = _client_with_stub(monkeypatch, message, "tool_calls", openai_base_url=_OLLAMA_BASE_URL)
    response = client.chat(messages=[{"role": "user", "content": "suma"}], tools=_TOOLS)
    assert completions.kwargs["reasoning_effort"] == "none"
    assert completions.kwargs["tools"] == _TOOLS
    assert completions.kwargs["tool_choice"] == "auto"
    assert [tc.function_name for tc in response.tool_calls] == ["add"]
    assert response.tool_calls[0].arguments == {"a": 1, "b": 2}


# --------------------------------------------------------------------------- #
# Keep-alive SOLO en el proveedor local                                        #
# --------------------------------------------------------------------------- #
def test_modo_local_envia_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message, openai_base_url=_OLLAMA_BASE_URL)
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert completions.kwargs["extra_body"] == {"keep_alive": "1h"}


def test_modo_oficial_no_envia_keep_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(monkeypatch, message)
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert "extra_body" not in completions.kwargs


def test_keep_alive_configurable_y_vacio_lo_omite(monkeypatch: pytest.MonkeyPatch) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, completions = _client_with_stub(
        monkeypatch, message, openai_base_url=_OLLAMA_BASE_URL, openai_keep_alive="30m"
    )
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert completions.kwargs["extra_body"] == {"keep_alive": "30m"}

    client, completions = _client_with_stub(
        monkeypatch, message, openai_base_url=_OLLAMA_BASE_URL, openai_keep_alive="  "
    )
    client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    assert "extra_body" not in completions.kwargs


# --------------------------------------------------------------------------- #
# Instrumentación: duración por llamada al LLM                                 #
# --------------------------------------------------------------------------- #
def test_registra_duracion_de_la_llamada_al_llm(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    message = SimpleNamespace(content="hola", tool_calls=None)
    client, _ = _client_with_stub(monkeypatch, message)
    with caplog.at_level(logging.INFO, logger="asistente.ai.llm"):
        client.chat(messages=[{"role": "user", "content": "hola"}], tools=_TOOLS)
    record = _last_record(caplog, "llm_call")
    assert record is not None
    text = record.getMessage()
    assert "duration_ms=" in text
    assert "outcome=ok" in text
    assert "requested_tools=[]" in text


def test_llm_error_se_mide_y_se_convierte_en_llmerror(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    message = SimpleNamespace(content=None, tool_calls=None)
    error = openai.APITimeoutError(request=httpx.Request("POST", "http://localhost"))
    client, _ = _client_with_stub(monkeypatch, message, error=error)
    with caplog.at_level(logging.INFO, logger="asistente.ai.llm"):
        with pytest.raises(LLMError, match="Tiempo de espera"):
            client.chat(messages=[{"role": "user", "content": "hola"}])
    record = _last_record(caplog, "llm_call")
    assert record is not None
    text = record.getMessage()
    assert "duration_ms=" in text
    assert "outcome=error" in text


def test_error_no_openai_se_mide_y_se_propaga_sin_cambios(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    message = SimpleNamespace(content=None, tool_calls=None)
    client, _ = _client_with_stub(monkeypatch, message, error=RuntimeError("boom"))
    with caplog.at_level(logging.INFO, logger="asistente.ai.llm"):
        with pytest.raises(RuntimeError, match="boom"):
            client.chat(messages=[{"role": "user", "content": "hola"}])
    record = _last_record(caplog, "llm_call")
    assert record is not None
    assert "outcome=error" in record.getMessage()
