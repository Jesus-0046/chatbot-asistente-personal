"""Repositorio de identidad por canal e idempotencia de eventos entrantes.

`user_channels` es la única fuente de verdad de `(channel, external_user_id)`
→ `users.id`. `processed_inbound_events` garantiza que un mismo mensaje
entrante (p. ej. un wamid de WhatsApp) se procese una sola vez.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy import text as sa_text

from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.persistence.models import User, UserChannel
from asistente.persistence.repositories.base import Repository
from asistente.shared.timeutil import utc_now


class ChannelIdentityRepository(Repository):
    # ------------------------------------------------------------------ #
    # Identidad por canal                                                 #
    # ------------------------------------------------------------------ #
    async def find_linked_user(self, channel: str, external_user_id: str) -> User | None:
        """Usuario interno vinculado a una identidad de canal 'linked'."""
        async with self.db.session() as session:
            row = (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.external_user_id == external_user_id,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.user_id is None:
                return None
            return await session.get(User, row.user_id)

    async def find_pending(self, channel: str, external_user_id: str) -> UserChannel | None:
        async with self.db.session() as session:
            return (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.external_user_id == external_user_id,
                        UserChannel.status == "pending",
                    )
                )
            ).scalar_one_or_none()

    async def create_pending(
        self,
        *,
        channel: str,
        external_user_id: str,
        pair_code_hash: str,
        pair_expires_at: datetime,
    ) -> UserChannel:
        """Crea una identidad 'pending'. Si llega a existir (carrera del webhook),
        devuelve la fila existente sin pisarla.
        """
        from sqlalchemy.exc import IntegrityError

        row = UserChannel(
            channel=channel,
            external_user_id=external_user_id,
            status="pending",
            pair_code_hash=pair_code_hash,
            pair_expires_at=pair_expires_at,
        )
        async with self.db.session() as session:
            try:
                session.add(row)
                await session.commit()
                await session.refresh(row)
                return row
            except IntegrityError:
                await session.rollback()
                result = await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.external_user_id == external_user_id,
                    )
                )
                return result.scalar_one()

    async def update_pair_code(self, row_id: int, pair_code_hash: str, pair_expires_at: datetime) -> None:
        async with self.db.session() as session:
            row = await session.get(UserChannel, row_id)
            if row is None:
                return
            row.pair_code_hash = pair_code_hash
            row.pair_expires_at = pair_expires_at
            await session.commit()

    async def ensure_linked(self, *, channel: str, external_user_id: str, user_id: int) -> int | None:
        """Inserta una identidad 'linked' de forma idempotente (Fase 10.0).

        Devuelve el `user_id` final vinculado a `(channel, external_user_id)` o
        `None` si esa identidad ya está vinculada a OTRO usuario. Cierra el
        flujo del 'bind' Web: un email jamás acaba siendo identidad de un
        usuario distinto del que posee el código.
        """
        async with self.db.transaction() as session:
            existing = (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.external_user_id == external_user_id,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing.user_id if existing.user_id == user_id else None
            session.add(
                UserChannel(
                    user_id=user_id,
                    channel=channel,
                    external_user_id=external_user_id,
                    status="linked",
                )
            )
            await session.commit()
            return user_id

    async def has_linked(self, user_id: int, channel: str) -> bool:
        async with self.db.session() as session:
            row = (
                await session.execute(
                    select(UserChannel.id).where(
                        UserChannel.user_id == user_id,
                        UserChannel.channel == channel,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            return row is not None

    async def list_for_user(self, user_id: int) -> list[UserChannel]:
        """Identidades vinculadas del usuario (para /api/v1/me)."""
        async with self.db.session() as session:
            rows = (
                (
                    await session.execute(
                        select(UserChannel)
                        .where(UserChannel.user_id == user_id, UserChannel.status == "linked")
                        .order_by(UserChannel.id)
                    )
                )
                .scalars()
                .all()
            )
            return list(rows)

    async def link_pending(
        self,
        *,
        code_hash: str,
        user_id: int,
        channel: str = CHANNEL_WHATSAPP,
    ) -> User | None:
        """Consume un código de enlace (single-use, no expirado) de forma atómica.

        Devuelve el `User` vinculado o `None` si el código no existe, caducó,
        ya se usó, o el usuario ya tiene otra identidad 'linked' en ese canal.
        """
        async with self.db.transaction() as session:
            row = (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.status == "pending",
                        UserChannel.pair_code_hash == code_hash,
                        UserChannel.pair_expires_at > utc_now(),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            existing = (
                await session.execute(
                    select(UserChannel.id).where(
                        UserChannel.user_id == user_id,
                        UserChannel.channel == channel,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            row.status = "linked"
            row.user_id = user_id
            row.pair_code_hash = None
            row.pair_expires_at = None
            await session.flush()
            return await session.get(User, user_id)

    # ------------------------------------------------------------------ #
    # Idempotencia de eventos entrantes                                   #
    # ------------------------------------------------------------------ #
    async def mark_processed(self, channel: str, external_message_id: str) -> bool:
        """Registra el evento (channel, external_message_id).

        Devuelve True si es la PRIMERA vez (el webhook procede) y False si ya
        se había procesado (viene duplicado, se ignora). El insert es atómico
        (`ON CONFLICT DO NOTHING`): concurrencia segura en PostgreSQL y en
        SQLite 3.24+ (RETURNING en 3.35+).
        """
        stmt = sa_text(
            "INSERT INTO processed_inbound_events (channel, external_message_id) "
            "VALUES (:channel, :external_message_id) "
            "ON CONFLICT (channel, external_message_id) DO NOTHING "
            "RETURNING id"
        )
        async with self.db.session() as session:
            result = await session.execute(
                stmt,
                {"channel": channel, "external_message_id": external_message_id},
            )
            await session.commit()
            return result.scalar_one_or_none() is not None
