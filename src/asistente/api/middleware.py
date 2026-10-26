"""Middleware de identificación de peticiones (Fase 10.0).

- Acepta `X-Request-ID` del cliente solo si coincide con `[A-Za-z0-9._-]{1,64}`.
- Si no llega o es inválido, genera uno `uuid4().hex` (32 chars) en el server.
- Siempre lo devuelve en el header de respuesta y lo deja en
  `request.state.request_id` para logs y cuerpos de error.
"""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        incoming = request.headers.get("x-request-id", "")
        if incoming and _REQUEST_ID_RE.fullmatch(incoming):
            request_id = incoming
        else:
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
