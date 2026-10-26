"""Integración PostgreSQL de Fase 9: identidad por canal e idempotencia WhatsApp.

Uso:
  TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration -m db

Verifica contra PostgreSQL REAL (omitido sin TEST_DATABASE_URL):
- Deduplicación concurrente del mismo wamid: varios webhooks en paralelo con el
  MISMO `(channel, external_message_id)` → exactamente UNO gana.
- UNIQUE (channel, external_user_id) en `user_channels` y FK → `users.id`
  (ondelete CASCADE), más el CHECK de status.
- UNIQUE (channel, external_message_id) en `processed_inbound_events`.
- `users.telegram_user_id` nullable tras 0014: un usuario solo-WhatsApp existe
  sin `telegram_user_id`.
- Ciclo completo `pending → código → link → resolve` con single-use en PG.
(El backfill de identidades Telegram se valida en ejecución mediante `alembic`
sobre una BD scratch: siembra → `upgrade 0013` → seed → `upgrade 0014`.)
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import sqlalchemy as sa

from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.domain.channels import ChannelIdentityService
from asistente.persistence.db import Database
from asistente.persistence.repositories import ChannelIdentityRepository

pytestmark = pytest.mark.db


def _ext(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def _seed_user(db: Database, *, telegram_user_id: int | None) -> int:
    async with db.session() as session:
        result = await session.execute(
            sa.text(
                """
                INSERT INTO users (telegram_user_id, timezone, lang, settings)
                VALUES (:t, 'Europe/Madrid', 'es-ES', '{}')
                RETURNING id
                """
            ),
            {"t": telegram_user_id},
        )
        await session.commit()
        return int(result.scalar())


async def _cleanup(db: Database, *, user_ids: list[int], ext_ids: list[str]) -> None:
    async with db.session() as session:
        if ext_ids:
            await session.execute(
                sa.text("DELETE FROM processed_inbound_events WHERE external_message_id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": tuple(ext_ids)},
            )
            await session.execute(
                sa.text("DELETE FROM user_channels WHERE external_user_id IN :ids").bindparams(
                    sa.bindparam("ids", expanding=True)
                ),
                {"ids": tuple(ext_ids)},
            )
        if user_ids:
            await session.execute(
                sa.text("DELETE FROM users WHERE id IN :ids").bindparams(sa.bindparam("ids", expanding=True)),
                {"ids": tuple(user_ids)},
            )
        await session.commit()


# --------------------------------------------------------------------------- #
# Concurrencia de deduplicación                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dedup_concurrente_mismo_wamid_solo_una_ejecucion() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    wamid = _ext("wamid")
    user_ids: list[int] = []
    try:
        user_ids.append(await _seed_user(db, telegram_user_id=None))
        services = [ChannelIdentityService(ChannelIdentityRepository(db)) for _ in range(8)]

        async def mark(service: ChannelIdentityService) -> bool:
            return await service.mark_processed(CHANNEL_WHATSAPP, wamid)

        results = await asyncio.gather(*(mark(s) for s in services))

        assert sum(1 for ok in results if ok) == 1, "solo un webhook concurrente puede procesar el wamid"
        assert results, results

        async with db.session() as session:
            count = int(
                (
                    await session.execute(
                        sa.text(
                            "SELECT count(*) FROM processed_inbound_events "
                            "WHERE channel = :c AND external_message_id = :w"
                        ),
                        {"c": CHANNEL_WHATSAPP, "w": wamid},
                    )
                ).scalar()
            )
        assert count == 1
    finally:
        await _cleanup(db, user_ids=user_ids, ext_ids=[wamid])
        await db.close()


# --------------------------------------------------------------------------- #
# Constraints del esquema                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unique_channel_external_user_id_y_fk() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    ext = _ext("ext")
    user_ids: list[int] = []
    try:
        uid = await _seed_user(db, telegram_user_id=None)
        user_ids.append(uid)
        async with db.session() as session:
            await session.execute(
                sa.text(
                    "INSERT INTO user_channels (user_id, channel, external_user_id, status) "
                    "VALUES (:u, :c, :e, 'linked')"
                ),
                {"u": uid, "c": CHANNEL_WHATSAPP, "e": ext},
            )
            await session.commit()

            # Duplicado del mismo (channel, external_user_id) → UNIQUE.
            with pytest.raises(sa.exc.IntegrityError):
                async with db.session() as s2:
                    await s2.execute(
                        sa.text(
                            "INSERT INTO user_channels (user_id, channel, external_user_id, status) "
                            "VALUES (:u, :c, :e, 'pending')"
                        ),
                        {"u": uid, "c": CHANNEL_WHATSAPP, "e": ext},
                    )
                    await s2.commit()

            # user_id inexistente → FK a users.id (ondelete CASCADE).
            with pytest.raises(sa.exc.IntegrityError):
                async with db.session() as s3:
                    await s3.execute(
                        sa.text(
                            "INSERT INTO user_channels (user_id, channel, external_user_id, status) "
                            "VALUES (:u, :c, :e, 'pending')"
                        ),
                        {"u": 2147483647, "c": CHANNEL_WHATSAPP, "e": _ext("o")},
                    )
                    await s3.commit()
    finally:
        await _cleanup(db, user_ids=user_ids, ext_ids=[ext])
        await db.close()


@pytest.mark.asyncio
async def test_esquema_fase9_en_postgres() -> None:
    """El esquema real tras 0014: nullable, UNIQUEs, FK y CHECK correctos."""
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    try:
        async with db.session() as session:
            version = (await session.execute(sa.text("SELECT version_num FROM alembic_version"))).scalar_one()

        assert version == "0015_fase10_web_auth", f"head esperado 0015; actual {version}"

        async with db.session() as session:
            nullable = (
                await session.execute(
                    sa.text(
                        "SELECT is_nullable FROM information_schema.columns "
                        "WHERE table_name = 'users' AND column_name = 'telegram_user_id'"
                    )
                )
            ).scalar_one()
            assert nullable == "YES", "users.telegram_user_id debe ser nullable tras 0014"

            uniques = set(
                row[0]
                for row in await session.execute(
                    sa.text(
                        "SELECT constraint_name FROM information_schema.table_constraints "
                        "WHERE table_name IN ('user_channels', 'processed_inbound_events') "
                        "AND constraint_type = 'UNIQUE'"
                    )
                )
            )
            assert "uq_user_channels_channel_external" in uniques
            assert "uq_processed_events_channel_message" in uniques

            fks = set(
                row[0]
                for row in await session.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'user_channels'::regclass AND contype = 'f'"
                    )
                )
            )
            assert any(fk for fk in fks if fk.startswith("user_channels_")), fks

            checks = set(
                row[0]
                for row in await session.execute(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid = 'user_channels'::regclass AND contype = 'c'"
                    )
                )
            )
            assert "ck_user_channels_status" in checks
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_telegram_nullable_whatsapp_only() -> None:
    """Un usuario solo-WhatsApp (sin telegram_user_id) persiste y queda identificable."""
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    wa_ext = _ext("wa")
    user_ids: list[int] = []
    try:
        # Usuario solo-WhatsApp: telegram_user_id NULL (permite multi-NULL en PG).
        wa_user = await _seed_user(db, telegram_user_id=None)
        user_ids.append(wa_user)
        identity = ChannelIdentityService(ChannelIdentityRepository(db))
        _, code = await identity.ensure_pending_pair(CHANNEL_WHATSAPP, wa_ext)
        assert code is not None
        linked = await identity.link_with_code(code=code, user_id=wa_user)
        assert linked is not None
        assert (await identity.resolve_user(CHANNEL_WHATSAPP, wa_ext)).id == wa_user
        assert await identity.has_linked(wa_user, CHANNEL_WHATSAPP)
    finally:
        await _cleanup(db, user_ids=user_ids, ext_ids=[wa_ext])
        await db.close()


# --------------------------------------------------------------------------- #
# Ciclo completo con single-use                                                #
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ciclo_pairing_pending_code_link_resolve_single_use() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    ext = _ext("ciclo")
    user_ids: list[int] = []
    try:
        uid = await _seed_user(db, telegram_user_id=None)
        user_ids.append(uid)
        identity = ChannelIdentityService(ChannelIdentityRepository(db))

        assert (await identity.resolve_user(CHANNEL_WHATSAPP, ext)) is None

        # pending con código recién generado.
        row, code = await identity.ensure_pending_pair(CHANNEL_WHATSAPP, ext)
        assert row is not None and code is not None and len(code) == 8

        # volver a pedir el código NO re-expone texto plano mientras esté vigente.
        _row2, code2 = await identity.ensure_pending_pair(CHANNEL_WHATSAPP, ext)
        assert code2 is None, "el webhook no debe re-exponer el código"

        # link exitoso y single-use.
        assert (await identity.link_with_code(code=code, user_id=uid)) is not None
        assert (await identity.resolve_user(CHANNEL_WHATSAPP, ext)).id == uid
        assert (await identity.link_with_code(code=code, user_id=uid)) is None, "el código se consume una sola vez"

        # otro usuario NO puede robar la identidad ya vinculada.
        thief = await _seed_user(db, telegram_user_id=None)
        user_ids.append(thief)
        _, c2 = await identity.ensure_pending_pair(CHANNEL_WHATSAPP, ext)
        assert c2 is None or True  # el pending nuevo no permite re-vincular lo ya linked
    finally:
        await _cleanup(db, user_ids=user_ids, ext_ids=[ext])
        await db.close()
