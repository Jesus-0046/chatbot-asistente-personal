"""Tests unitarios: logging (redacción de secretos)."""

from __future__ import annotations

import io
import logging

from asistente.logging_setup import _redact_event_dict, setup_logging


def test_redact_oculta_tokens_y_keys() -> None:
    payload = {
        "telegram_token": "12345",
        "OPENAI_API_KEY": "sk-secret",
        "password": "pw",
        "message": "hola",
    }
    out = _redact_event_dict(None, "", payload)
    assert out["telegram_token"] == "[redacted]"
    assert out["OPENAI_API_KEY"] == "[redacted]"
    assert out["password"] == "[redacted]"
    assert out["message"] == "hola"


def test_setup_logging_fija_los_clientes_http_en_warning() -> None:
    """Aunque se pida DEBUG, httpx/httpcore nunca bajan de WARNING."""
    setup_logging("DEBUG")
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        assert logger.isEnabledFor(logging.WARNING)
        assert not logger.isEnabledFor(logging.INFO)
        assert not logger.isEnabledFor(logging.DEBUG)


def test_httpx_no_filtra_el_token_de_telegram_en_los_logs() -> None:
    """El log INFO de httpx con la URL de Telegram (token en el path) no se emite."""
    setup_logging("DEBUG")
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(handler)
    try:
        logging.getLogger("httpx").info(
            'HTTP Request: POST https://api.telegram.org/bot123456:AAsecreto/getMe "HTTP/1.1 200 OK"'
        )
    finally:
        root.removeHandler(handler)
    assert "AAsecreto" not in stream.getvalue()
