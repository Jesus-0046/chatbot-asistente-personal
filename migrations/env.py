"""Entorno de migración de Alembic, invocado por `alembic` desde la raíz.

La URL se lee de `Settings` (variables de entorno / `.env`): en desarrollo
local apunta a `localhost`, dentro de Docker al host `db`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from asistente.config import Settings
from asistente.persistence import models  # noqa: F401  (importa los modelos futuros)
from asistente.persistence.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = Settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _ensure_alembic_version_table(connection: Connection) -> None:
    """Garantiza que `alembic_version.version_num` tenga ancho suficiente.

    Los revision ids de este proyecto superan los 32 caracteres que Alembic usa
    por defecto para `version_num` (p. ej. `0007_fase3_3_recurrencias_*`),
    con lo que un `upgrade` sobre una BD nueva falla al actualizar la versión.
    Se crea aquí la tabla con columna ancha (o se ensancha la existente) ANTES
    de que el motor de Alembic la controle, porque Alembic respeta la tabla si
    ya existe. No altera las migraciones históricas.
    """
    exists = connection.execute(text("SELECT to_regclass('alembic_version')")).scalar()
    if exists is None:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)"))
        connection.execute(text("ALTER TABLE alembic_version ADD PRIMARY KEY (version_num)"))
    else:
        connection.execute(text("ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(255)"))
    # Commit explícito: si dejamos la conexión en transacción implícita, Alembic
    # no gestiona su propio COMMIT y al cerrar se revierte TODO (DIU transient).
    connection.commit()


def do_run_migrations(connection: object) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_ensure_alembic_version_table)
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
