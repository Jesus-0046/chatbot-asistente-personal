"""Dependencias de la API Web (Fase 10.0): contexto autenticado.

`get_current_user` resuelve la sesión Bearer mediante
`services.web_auth.authenticate`: cualquier error de credenciales/sesión
produce 401 `unauthorized`. Los rate limits de `/api/v1/auth/*` se aplican por
IP del cliente (X-Forwarded-For si está presente).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request

from asistente.application.services import Services
from asistente.persistence.models import User, WebSession


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return "unknown"


def get_services(request: Request) -> Services:
    services = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=503, detail="Servicios no inicializados.")
    return cast(Services, services)


def get_settings(request: Request):
    from asistente.config import Settings

    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else Settings()


def get_rate_limiter(request: Request):
    return getattr(request.app.state, "rate_limiter", None)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    user_id: int
    user: User
    session: WebSession
    token: str


def require_web_auth(services: Services) -> None:
    if services.web_auth is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")


async def get_current_user(request: Request) -> CurrentUser:
    services = get_services(request)
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Autenticación requerida.")
    require_web_auth(services)
    session, user = await wa.authenticate(token.strip())
    return CurrentUser(user_id=user.id, user=user, session=session, token=token.strip())


CurrentUserDeps = Annotated[CurrentUser, Depends(get_current_user)]
ServicesDeps = Annotated[Services, Depends(get_services)]


def require_auth_rate_limited(request: Request) -> None:
    """Límite para POST /auth/login, /auth/bind y /auth/logout por IP."""
    services = get_services(request)
    limiter = get_rate_limiter(request)
    if limiter is None:
        return
    limit = services.settings.api_rate_limit_auth_per_minute
    if not limiter.is_allowed(_client_ip(request), max_per_minute=limit):
        raise HTTPException(status_code=429, detail="Demasiadas peticiones. Inténtalo de nuevo en un minuto.")


def require_register_rate_limited(request: Request) -> None:
    """Límite para POST /auth/register por IP."""
    services = get_services(request)
    limiter = get_rate_limiter(request)
    if limiter is None:
        return
    limit = services.settings.api_rate_limit_register_per_minute
    if not limiter.is_allowed(_client_ip(request), max_per_minute=limit):
        raise HTTPException(status_code=429, detail="Demasiados registros. Inténtalo de nuevo en un minuto.")
