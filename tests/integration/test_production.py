"""Pruebas de producción (Fase 10.1).

Cubren los contratos operativos del despliegue 24/7 que no ejercita el resto
de la suite:

- `/health` no depende de BD ni de OpenAI (liveness).
- `/ready` comprueba BD y configuración (readiness) sin llamadas externas.
- Validación de configuración crítica en startup (modo bot: Telegram siempre
  y la clave de OpenAI solo sin URL base local, WhatsApp si habilitado; modo
  API: DATABASE_URL), sin filtrar secretos.
- Ciclo de vida startup/shutdown de la app, con cierre de recursos.
- Persistencia de PostgreSQL a través de reconexiones (marco `db`).
- Docker: validación estática de Dockerfile y docker-compose (imagen,
  non-root, healthchecks, restart y volumen persistente).

Los tests `db` se omiten sin `TEST_DATABASE_URL` (fixture en conftest).
"""

from __future__ import annotations

import os
import re

import pytest

from asistente.config import ConfigError, Settings, validate_critical_env


def _empty_settings(**overrides) -> Settings:
    """Settings sin secretos (para ejercitar rutas de validación/liveness).

    Fija `openai_base_url=""` para ejercitar SIEMPRE el modo API oficial de
    OpenAI: con un proveedor local (Ollama) la clave no es obligatoria y el
    resultado del test no debe depender del `.env` real.
    """
    defaults = {"telegram_token": "", "openai_api_key": "", "openai_base_url": "", "whatsapp_enabled": False}
    defaults.update(overrides)
    return Settings(**defaults)


# --------------------------------------------------------------------------- #
# Health / readiness                                                           #
# --------------------------------------------------------------------------- #
def test_health_ok_sin_dependencias_externas() -> None:
    """Liveness: /health responde ok sin BD ni servicios cableados."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app

    app = create_app(settings=_empty_settings(), db=None, services=None)
    app.state.build_db = False
    app.state.build_services = False
    with TestClient(app) as client:
        body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["service"] == "asistente-web-api"


def test_ready_no_es_ok_sin_bd_ni_servicios() -> None:
    """Sin BD ni servicios cableados, /ready nunca responde 200."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app

    app = create_app(settings=_empty_settings(), db=None, services=None)
    app.state.build_db = False
    app.state.build_services = False
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code >= 500


def test_ready_no_error_si_falta_database_url() -> None:
    """Sin DATABASE_URL la API no arranca (fail-fast operativo)."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app

    app = create_app(settings=_empty_settings(database_url="  "), db=None, services=None)
    with pytest.raises(RuntimeError) as exc:
        with TestClient(app) as client:
            client.get("/health")
    assert "DATABASE_URL" in str(exc.value)


@pytest.mark.db
@pytest.mark.asyncio
@pytest.mark.db
@pytest.mark.asyncio
async def test_ready_ok_con_infraestructura_real() -> None:
    """Sobre PostgreSQL real y services reales, /ready responde 200."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app
    from asistente.main import build_services
    from asistente.persistence.db import Database

    settings = Settings(database_url=os.environ["TEST_DATABASE_URL"], telegram_token="t", openai_api_key="k")
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    app = create_app(settings=settings, db=db, services=None)
    app.state.services = build_services(settings, db)
    app.state.build_db = False
    app.state.build_services = False
    try:
        with TestClient(app) as client:
            response = client.get("/ready")
        assert response.status_code == 200, response.text
        assert response.json()["database"] == "ok"
        assert response.json()["api_enabled"] is False
    finally:
        await db.close()


# --------------------------------------------------------------------------- #
# Validación de configuración                                                  #
# --------------------------------------------------------------------------- #
def test_validate_critical_env_falla_sin_telegram() -> None:
    with pytest.raises(ConfigError) as exc:
        validate_critical_env(_empty_settings())
    assert "TELEGRAM_TOKEN" in str(exc.value)
    assert "OPENAI_API_KEY" in str(exc.value)


def test_validate_critical_env_exige_whatsapp_si_habilitado() -> None:
    with pytest.raises(ConfigError) as exc:
        validate_critical_env(_empty_settings(telegram_token="t", openai_api_key="k", whatsapp_enabled=True))
    for var in ("WHATSAPP_VERIFY_TOKEN", "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET"):
        assert var in str(exc.value)


def test_validate_critical_env_pasa_con_config_minima() -> None:
    s = _empty_settings(telegram_token="t", openai_api_key="k")
    validate_critical_env(s)  # no debe lanzar


def test_validate_critical_env_whatsapp_completo_pasa() -> None:
    s = _empty_settings(
        telegram_token="t",
        openai_api_key="k",
        whatsapp_enabled=True,
        whatsapp_verify_token="v",
        whatsapp_access_token="a",
        whatsapp_app_secret="s",
    )
    validate_critical_env(s)  # no debe lanzar


def test_config_redacta_secretos_para_logs() -> None:
    s = _empty_settings(telegram_token="tok3n-secreto", openai_api_key="key-secreta")
    from asistente.config import as_dict_for_logging

    logged = as_dict_for_logging(s)
    joined = " ".join(str(v) for v in logged.values())
    assert "tok3n-secreto" not in joined
    assert "key-secreta" not in joined


# --------------------------------------------------------------------------- #
# Startup / shutdown                                                           #
# --------------------------------------------------------------------------- #
def test_lifespan_no_cierra_bd_externa() -> None:
    """El lifespan no cierra una BD inyectada: el owner externo la gestiona."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app
    from asistente.persistence.db import Database

    db = Database("sqlite+aiosqlite:///:memory:")
    db.connect()  # owner externo: abre y cierra la conexión
    app = create_app(settings=_empty_settings(), db=db, services=None)
    with TestClient(app) as client:
        assert db.engine is not None  # abierta por el owner durante el lifespan
        assert client.get("/health").status_code == 200
    assert db.engine is not None  # la app no interfiere con la BD externa


@pytest.mark.db
@pytest.mark.asyncio
async def test_lifespan_conecta_y_cierra_bd_propia() -> None:
    """Con BD de su propiedad, el lifespan conecta en startup y cierra al salir."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app

    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"], telegram_token="t", openai_api_key="k", api_enabled=True
    )
    app = create_app(settings=settings, db=None, services=None)  # build_db/build_services = True
    with TestClient(app) as client:
        assert app.state.db.engine is not None  # conectada en startup
        assert client.get("/health").status_code == 200
    assert app.state.db.engine is None  # cerrada en shutdown


def test_lifespan_falla_rapido_si_config_incoherente() -> None:
    """Config incoherente (docs sin API) impide el arranque (fail-fast)."""
    from starlette.testclient import TestClient

    from asistente.api.app import create_app

    app = create_app(settings=_empty_settings(api_docs_enabled=True, api_enabled=False), db=None, services=None)
    with pytest.raises(RuntimeError) as exc:
        with TestClient(app) as client:
            client.get("/health")
    assert "api_docs_enabled" in str(exc.value)


# --------------------------------------------------------------------------- #
# Persistencia PostgreSQL (marco db)                                           #
# --------------------------------------------------------------------------- #
@pytest.mark.db
@pytest.mark.asyncio
async def test_persistencia_sobrevive_a_reconexion() -> None:
    """Datos escritos con una conexión deben leerse con otra (persistencia 24/7)."""
    import sqlalchemy as sa

    from asistente.persistence.db import Database

    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    user_id = None
    try:
        async with db.session() as session:
            result = await session.execute(
                sa.text(
                    "INSERT INTO users (timezone, lang, settings) VALUES ('Europe/Madrid', 'es-ES', '{}') RETURNING id"
                )
            )
            user_id = int(result.scalar())
            await session.commit()

        new_db = Database(os.environ["TEST_DATABASE_URL"])
        new_db.connect()
        try:
            async with new_db.session() as session:
                row = (
                    await session.execute(
                        sa.text("SELECT timezone FROM users WHERE id = :id"),
                        {"id": user_id},
                    )
                ).one()
            assert row.timezone == "Europe/Madrid"
        finally:
            await new_db.close()
    finally:
        if user_id is not None:
            async with db.session() as session:
                await session.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})
                await session.commit()
        await db.close()


# --------------------------------------------------------------------------- #
# Docker (validación estática; Docker engine puede no estar disponible)        #
# --------------------------------------------------------------------------- #
def test_dockerfile_y_compose_config_validos() -> None:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "Dockerfile"), encoding="utf-8") as fh:
        dockerfile = fh.read()
    with open(os.path.join(root, "docker-compose.yml"), encoding="utf-8") as fh:
        compose = fh.read()

    assert re.search(r"USER\s+\d+", dockerfile) or re.search(r"USER\s+\w+", dockerfile), "non-root user"
    assert "FROM python:3.12" in dockerfile, "imagen python 3.12"

    yaml = pytest.importorskip("yaml", reason="PyYAML no instalado en esta máquina")

    cfg = yaml.safe_load(compose)
    services = cfg.get("services", {})
    assert "db" in services and "bot" in services, "servicios min db+bot"
    assert "pgdata" in str(cfg.get("volumes", {})), "volumen persistente de PostgreSQL"
    for name in ("db", "bot"):
        assert "healthcheck" in services[name], f"healthcheck en {name}"
        assert "restart" in services[name], f"restart policy en {name}"
    assert "api" in services
