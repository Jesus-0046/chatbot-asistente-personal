"""Fábrica de la aplicación FastAPI (Fase 10.0) y entrypoint `python -m asistente.api`.

`create_app` admite inyección de `settings`, `db` y `services` para tests; en
producción se construye todo en el lifespan (BD conectada + `build_services`).

Endpoints de infraestructura (sin autenticación):
- GET /health   → liveness (la app responde).
- GET /ready    → readiness (verifica que la BD responde y que la capa web_auth está cableada).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps, get_services
from asistente.api.errors import register_error_handlers
from asistente.api.middleware import RequestIDMiddleware
from asistente.api.ratelimit import RateLimiter
from asistente.api.serializers import deep_json, user_to_dict
from asistente.config import ConfigError, Settings, validate_api_env
from asistente.logging_setup import get_logger, setup_logging

logger = get_logger("api")

health_router = APIRouter(tags=["health"])

me_router = APIRouter(tags=["me"])


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    try:
        validate_api_env(settings)
    except ConfigError as exc:
        logger.error("api_configuration_error", error=str(exc))
        raise RuntimeError(str(exc)) from None

    db = app.state.db
    if app.state.build_db:
        db.connect()

    services = app.state.services
    if app.state.build_services:
        from asistente.main import build_services

        services = build_services(settings, db)
        app.state.services = services

    app.state.rate_limiter = RateLimiter()
    try:
        yield
    finally:
        if app.state.build_db:
            await db.close()
        logger.info("api_shutdown_complete")


def create_app(settings: Settings | None = None, db=None, services=None) -> FastAPI:
    settings = settings or Settings()
    docs_enabled = settings.api_docs_enabled

    from asistente.persistence.db import Database

    app = FastAPI(
        title="Asistente Web API",
        version="10.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=_lifespan,
    )
    app.state.settings = settings
    # La BD/y servicios se construyen por defecto si no se inyectan (tests):
    # el lifespan conecta/cierra la BD cuando la app es su dueña.
    app.state.db = db if db is not None else Database(settings.database_url)
    app.state.services = services
    app.state.build_db = db is None
    app.state.build_services = services is None

    if settings.api_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.api_cors_origins],
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["X-Request-ID"],
        )
    app.add_middleware(RequestIDMiddleware)
    register_error_handlers(app)
    _register_routes(app)
    return app


def _register_routes(app: FastAPI) -> None:
    from asistente.api.routers import (
        agenda,
        auth,
        budgets,
        confirmations,
        dashboard,
        finance,
        life,
        people,
        trips,
    )

    app.include_router(health_router)
    app.include_router(auth.router, prefix="/api/v1/auth")
    app.include_router(me_router, prefix="/api/v1")
    app.include_router(dashboard.router, prefix="/api/v1")
    app.include_router(agenda.router, prefix="/api/v1")
    app.include_router(finance.router, prefix="/api/v1")
    app.include_router(budgets.router, prefix="/api/v1")
    app.include_router(people.router, prefix="/api/v1")
    app.include_router(life.router, prefix="/api/v1")
    app.include_router(trips.router, prefix="/api/v1")
    app.include_router(confirmations.router, prefix="/api/v1")


@health_router.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "asistente-web-api"}


@health_router.get("/ready")
async def ready(request: Request) -> dict:
    services = get_services(request)
    settings: Settings = request.app.state.settings
    db = getattr(request.app.state, "db", None)
    try:
        if db is not None:
            await db.ping()
    except Exception:
        logger.exception("ready_db_unavailable")
        raise HTTPException(status_code=503, detail="Base de datos no disponible.") from None
    if services.web_auth is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    return dict(
        deep_json(
            {
                "status": "ready",
                "api_enabled": settings.api_enabled,
                "database": "ok",
                "web_auth": True,
            }
        )
    )


@me_router.get("/me")
async def me(current: CurrentUserDeps) -> dict:
    return {"user": user_to_dict(current.user)}


@me_router.delete("/me/sessions")
async def revoke_all_sessions(current: CurrentUserDeps, services: ServicesDeps) -> dict:
    wa = services.web_auth
    if wa is None:
        raise HTTPException(status_code=503, detail="Autenticación Web no disponible.")
    await wa.revoke_all_for_user(current.user_id)
    return {"status": "revoked"}


def main() -> None:
    settings = Settings()
    setup_logging(settings.log_level, json_output=True)
    uvicorn.run(
        "asistente.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


app = create_app()


if __name__ == "__main__":
    main()
