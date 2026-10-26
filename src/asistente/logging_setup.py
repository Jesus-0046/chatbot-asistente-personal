"""Logging estructurado con structlog.

Nunca se registran secretos: un procesador de redacción elimina cualquier campo
cuyo nombre contenga indicadores de credenciales antes de emitir la línea.

Además, los clientes HTTP de terceros (`httpx`/`httpcore`) se fijan en WARNING:
registran a INFO/DEBUG las URL completas y, en Telegram, la URL incluye el token
del bot en el path (`/bot<TOKEN>/getMe`). Esos records de `logging` estándar no
pasan por el procesador de redacción de structlog y filtrarían el token.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_REDACT_KEYS = ("token", "password", "secret", "api_key", "apikey", "key", "authorization")


def _redact_event_dict(_: object, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for field in list(event_dict.keys()):
        if any(flag in field.lower() for flag in _REDACT_KEYS):
            event_dict[field] = "[redacted]"
    return event_dict


def setup_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=numeric_level, stream=sys.stdout, format="%(message)s")

    # No bajar nunca de WARNING en los clientes HTTP: sus logs de INFO/DEBUG
    # incluyen la URL completa (con el token de Telegram) y no pasan por la
    # redacción de structlog. Se fija por logger para que LOG_LEVEL=DEBUG no
    # vuelva a exponerlos.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact_event_dict,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    if json_output:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(*args: Any, **kwargs: Any) -> Any:
    return structlog.get_logger(*args, **kwargs)
