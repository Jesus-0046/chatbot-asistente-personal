"""Tests unitarios: persistencia (engine/sesión, sin BD real)."""

from __future__ import annotations

import pytest

from asistente.persistence.db import Database


def make_db() -> Database:
    return Database("postgresql+asyncpg://u:p@localhost:5432/x")


def test_session_devuelve_async_context_manager() -> None:
    """`db.session()` es un async context manager usable con `async with`."""
    db = make_db()
    db.connect()
    session_cm = db.session()
    assert hasattr(session_cm, "__aenter__")
    assert hasattr(session_cm, "__aexit__")


@pytest.mark.asyncio
async def test_session_antes_de_conectar_lanza_error_al_entrar() -> None:
    """Sin conexión no falla al llamar `session()`, sino al ENTRAR en el bloque."""
    db = make_db()
    with pytest.raises(RuntimeError, match="not connected"):
        async with db.session() as session:
            assert session is not None


@pytest.mark.asyncio
async def test_ping_antes_de_conectar_lanza_error() -> None:
    db = make_db()
    with pytest.raises(RuntimeError, match="not connected"):
        await db.ping()


@pytest.mark.asyncio
async def test_session_context_manager_entrar_y_salir_sin_bd() -> None:
    """Entrar/salir del bloque no abre conexión a BD y cierra la sesión al salir."""
    db = make_db()
    db.connect()
    try:
        async with db.session() as session:
            assert session is not None
    finally:
        await db.close()

    assert db.engine is None
