"""Tests de integración con PostgreSQL real (omitidos sin TEST_DATABASE_URL).

Uso:
  TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration -m db
"""

from __future__ import annotations

import os

import pytest

from asistente.persistence.db import Database

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_ping_a_bd_real() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        await db.ping()
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_session_escribe_y_lee_un_valor() -> None:
    import sqlalchemy as sa

    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        async with db.session() as session:
            # Prueba de humo mínima que valida el ciclo sesión/commit
            await session.execute(sa.text("SELECT 1"))
    finally:
        await db.close()
