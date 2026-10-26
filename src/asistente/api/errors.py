"""Handlers de excepción de la API Web (Fase 10.0).

Mapea las excepciones de dominio/persistencia a respuestas HTTP uniformes:

    {"error": {"code": ..., "message": ..., "request_id": ...}}

Sin filtraciones: nunca se devuelve stack trace, SQL ni secretos. La
excepción interna sí se registra con stack en el log del servidor.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError

from asistente.shared.errors import (
    AuthorizationError,
    BusinessError,
    InvalidCredentialsError,
    NotFoundError,
)

logger = logging.getLogger("asistente.api.errors")


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def _response(status_code: int, code: str, message: Any, request: Request, *, headers: dict | None = None) -> Any:
    from fastapi.responses import JSONResponse

    body: dict[str, Any] = {"error": {"code": code, "message": message, "request_id": _request_id(request)}}
    resp_headers = {"X-Request-ID": _request_id(request) or ""}
    if headers:
        resp_headers.update(headers)
    return JSONResponse(status_code=status_code, content=body, headers=resp_headers)


def _message(exc: Exception) -> str:
    text = str(exc).strip()
    return text or exc.__class__.__name__


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException) -> Any:
        message = exc.detail if isinstance(exc.detail, str) else "Error en la petición."
        code = {
            400: "invalid_request",
            401: "unauthorized",
            403: "forbidden",
            404: "not_found",
            409: "conflict",
            422: "validation_error",
            429: "rate_limited",
            503: "service_unavailable",
        }.get(exc.status_code, "invalid_request")
        headers = getattr(exc, "headers", None)
        return _response(exc.status_code, code, message, request, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError) -> Any:
        details = [
            {"loc": ".".join(str(p) for p in err.get("loc", [])), "msg": err.get("msg", str(err))}
            for err in exc.errors()
        ]
        return _response(
            422, "validation_error", {"message": "Datos de entrada no válidos.", "details": details}, request
        )

    @app.exception_handler(InvalidCredentialsError)
    async def _invalid_credentials(request: Request, exc: InvalidCredentialsError) -> Any:
        return _response(401, "unauthorized", _message(exc), request)

    @app.exception_handler(AuthorizationError)
    async def _forbidden(request: Request, exc: AuthorizationError) -> Any:
        return _response(403, "forbidden", _message(exc), request)

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError) -> Any:
        return _response(404, "not_found", _message(exc), request)

    @app.exception_handler(BusinessError)
    async def _conflict(request: Request, exc: BusinessError) -> Any:
        return _response(409, "conflict", _message(exc), request)

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError) -> Any:
        return _response(422, "validation_error", _message(exc), request)

    @app.exception_handler(IntegrityError)
    async def _integrity_error(request: Request, exc: IntegrityError) -> Any:
        return _response(409, "conflict", "La operación entra en conflicto con datos existentes.", request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> Any:
        logger.exception("unhandled_api_error", exc_info=exc)
        return _response(500, "internal_error", "Error interno del servidor.", request)
