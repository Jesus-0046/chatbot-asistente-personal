"""Persistencia: engine async de SQLAlchemy y Base declarativa.

En Fase 0 no existen modelos de negocio. `Base.metadata` se deja listo para que
Alembic autogenere migraciones a partir de Fase 1.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Database:
    """Wrapper mínimo sobre el engine async y la fábrica de sesiones.

    Uso idiomático de SQLAlchemy async:

        async with db.session() as session:
            await session.execute(...)
            await session.commit()

    La sesión se cierra automáticamente al salir del bloque (incluso si se
    produce una excepción dentro de él).

    Para operaciones que deben ser atómicas (varios repos en una sola tx):

        async with db.transaction() as session:
            await repo_a.create(session=session, ...)
            await repo_b.update(session=session, ...)
    """

    def __init__(self, url: str, *, echo: bool = False) -> None:
        self.url = url
        self.echo = echo
        self.engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker[AsyncSession] | None = None

    def connect(self) -> None:
        self.engine = create_async_engine(self.url, echo=self.echo, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Async context manager: crea la sesión y la cierra al salir del bloque.

        Si la BD no está conectada, el error se lanza al ENTRAR en el bloque
        (`async with`, no al llamar a `session()`).
        """
        if self._session_factory is None:
            raise RuntimeError("Database not connected")
        async with self._session_factory() as session:
            yield session

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Transacción explícita: UNA sesión, UN commit, rollback total si falla.

        Las operaciones que participen deben recibir la sesión explícita
        para no abrir sesiones independientes.
        """
        if self._session_factory is None:
            raise RuntimeError("Database not connected")
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async def ping(self) -> None:
        """Comprobación de que la BD responde (SELECT 1)."""
        if self.engine is None:
            raise RuntimeError("Database not connected")
        import sqlalchemy as sa

        async with self.engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1"))
