"""Base común de repositorios: cada uno recibe el acceso a BD."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from asistente.persistence.db import Database


class Repository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @asynccontextmanager
    async def _session(self, session: AsyncSession | None = None) -> AsyncIterator[AsyncSession]:
        """Devuelve la sesión dada o abre una propia (commit al salir).

        Si se pasa una sesión explícita, la limpieza (commit/rollback) la gestiona
        la transacción exterior; el repo solo hace flush.
        """
        if session is not None:
            yield session
            return
        async with self.db.session() as own:
            try:
                yield own
                await own.commit()
            except BaseException:
                await own.rollback()
                raise

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Transacción compuesta: UNA sesión, UN commit, rollback total si falla.

        Para usar cuando varias operaciones de este repo (o de otros repos)
        deben compartir transacción. La sesión se cierra al salir.
        """
        async with self.db.transaction() as session:
            yield session
