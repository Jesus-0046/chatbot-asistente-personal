"""Repositorio de usuarios.

La identidad vive en `user_channels`: `(channel, external_user_id)` resuelve a
`users.id`. `users.telegram_user_id` se conserva (nullable) para las
notificaciones de los pollers y para compatibilidad con los datos anteriores
a la Fase 9. El `id` interno es el scope del resto de la aplicación.
"""

from __future__ import annotations

from sqlalchemy import select

from asistente.application.channels import CHANNEL_TELEGRAM, CHANNEL_WEB
from asistente.persistence.models import User, UserChannel
from asistente.persistence.repositories.base import Repository


class UserRepository(Repository):
    async def ensure_user(
        self,
        *,
        channel: str = CHANNEL_TELEGRAM,
        external_user_id: str | None = None,
        telegram_user_id: int | None = None,
        name: str | None,
        username: str | None,
        timezone: str,
        lang: str,
    ) -> User:
        """Devuelve el usuario existente o lo crea (junto a su identidad de canal).

        Compatibilidad hacia atrás: si solo se pasa `telegram_user_id` (canal
        'telegram' por defecto), se deriva `external_user_id = str(id)` igual
        que hace el backfill de la migración 0014.
        """
        if channel == CHANNEL_TELEGRAM and telegram_user_id is not None:
            ext = str(telegram_user_id)
        elif external_user_id is not None:
            ext = str(external_user_id)
        elif telegram_user_id is not None:
            ext = str(telegram_user_id)
        else:
            raise ValueError("ensure_user requiere external_user_id o telegram_user_id")

        async with self.db.transaction() as session:
            linked = (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == channel,
                        UserChannel.external_user_id == ext,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            if linked is not None and linked.user_id is not None:
                linked_user = await session.get(User, linked.user_id)
                if linked_user is not None:
                    return linked_user

            # Usuarios Telegram anteriores a la Fase 9 (sin fila en user_channels).
            user: User | None = None
            if channel == CHANNEL_TELEGRAM and telegram_user_id is not None:
                user = (
                    await session.execute(select(User).where(User.telegram_user_id == telegram_user_id))
                ).scalar_one_or_none()

            if user is None:
                user = User(
                    telegram_user_id=telegram_user_id if channel == CHANNEL_TELEGRAM else None,
                    name=name,
                    username=username,
                    timezone=timezone,
                    lang=lang,
                    settings={},
                )
                session.add(user)
                await session.flush()

            session.add(
                UserChannel(
                    user_id=user.id,
                    channel=channel,
                    external_user_id=ext,
                    status="linked",
                )
            )
            await session.flush()
            return user

    async def ensure_new_web_user(
        self,
        *,
        email: str,
        name: str | None = None,
        username: str | None = None,
        timezone: str,
        lang: str,
    ) -> User | None:
        """Crea un usuario NUEVO con identidad Web 'linked'; None si el email ya existe.

        Es transaccional y NUNCA devuelve un usuario existente. Esto cierra la
        vía de 'account takeover' del registro Web: si el email ya tiene una
        identidad 'linked' en el canal web, devolver None obliga al flujo de
        'bind' (que exige un código emitido desde Telegram).
        """
        async with self.db.transaction() as session:
            existing = (
                await session.execute(
                    select(UserChannel).where(
                        UserChannel.channel == CHANNEL_WEB,
                        UserChannel.external_user_id == email,
                        UserChannel.status == "linked",
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return None
            user = User(
                telegram_user_id=None,
                name=name,
                username=username,
                timezone=timezone,
                lang=lang,
                settings={},
            )
            session.add(user)
            await session.flush()
            session.add(
                UserChannel(
                    user_id=user.id,
                    channel=CHANNEL_WEB,
                    external_user_id=email,
                    status="linked",
                )
            )
            await session.flush()
            return user

    async def delete(self, user_id: int) -> None:
        """Borra un usuario (cascada a channels/sesiones/credenciales). Solo uso interno."""
        async with self.db.session() as session:
            user = await session.get(User, user_id)
            if user is not None:
                await session.delete(user)
                await session.commit()

    async def get(self, user_id: int) -> User | None:
        """Usuario por id interno, o None si no existe."""
        async with self.db.session() as session:
            return await session.get(User, user_id)

    async def timezone_for(self, user_id: int) -> str | None:
        """Zona horaria configurada del usuario (si existe)."""
        async with self.db.session() as session:
            result = await session.execute(select(User.timezone).where(User.id == user_id))
            return result.scalar_one_or_none()

    async def telegram_id_for(self, user_id: int) -> int | None:
        """`telegram_user_id` del usuario en el canal (para notificaciones)."""
        async with self.db.session() as session:
            result = await session.execute(select(User.telegram_user_id).where(User.id == user_id))
            return result.scalar_one_or_none()
