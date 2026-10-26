"""Autenticación Web (Fase 10.0): /api/v1/auth/*.

La lógica vive en `WebAuthService`; aquí solo se validan los cuerpos, se
aplican los rate limits por IP y se serializa el resultado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from asistente.api.dependencies import (
    CurrentUserDeps,
    ServicesDeps,
    require_auth_rate_limited,
    require_register_rate_limited,
)
from asistente.api.serializers import deep_json, user_to_dict

router = APIRouter(tags=["auth"])


class RegisterIn(BaseModel):
    email: str
    password: str
    name: str | None = None
    timezone: str | None = None
    lang: str | None = None


class LoginIn(BaseModel):
    email: str
    password: str


class BindIn(BaseModel):
    email: str
    code: str
    password: str


def _auth_payload(result) -> dict:
    return {
        "access_token": result.access_token,
        "token_type": "bearer",
        "expires_at": deep_json(result.expires_at),
        "user": user_to_dict(result.user),
    }


@router.post("/register", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_register_rate_limited)])
async def register(
    body: RegisterIn,
    services: ServicesDeps,
) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    result = await wa.register(
        email=body.email,
        password=body.password,
        name=body.name,
        timezone=body.timezone,
        lang=body.lang,
    )
    return _auth_payload(result)


@router.post("/login", dependencies=[Depends(require_auth_rate_limited)])
async def login(
    body: LoginIn,
    services: ServicesDeps,
) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    result = await wa.login(email=body.email, password=body.password)
    return _auth_payload(result)


@router.post("/bind", dependencies=[Depends(require_auth_rate_limited)])
async def bind(
    body: BindIn,
    services: ServicesDeps,
) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    result = await wa.bind(email=body.email, code=body.code, password=body.password)
    return _auth_payload(result)


@router.post("/logout", dependencies=[Depends(require_auth_rate_limited)])
async def logout(
    current: CurrentUserDeps,
    services: ServicesDeps,
) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    await wa.logout(current.token)
    return {"status": "logged_out"}


@router.get("/session")
async def session(current: CurrentUserDeps, services: ServicesDeps) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    channels = await wa.channels_of_user(current.user_id)
    return {
        "session": {
            "id": current.session.id,
            "expires_at": deep_json(current.session.expires_at),
            "created_at": deep_json(current.session.created_at),
            "last_used_at": deep_json(current.session.last_used_at),
        },
        "user": user_to_dict(current.user),
        "channels": channels,
    }
