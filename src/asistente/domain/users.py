"""Servicio de dominio de usuarios.

Identidad: el identificador del canal llega del contexto autenticado del
adaptador (NUNCA del LLM ni de nombres impostables). La aplicación solo conoce
el `id` interno que aquí se obtiene o crea.
"""

from __future__ import annotations

from asistente.application.channels import CHANNEL_TELEGRAM
from asistente.persistence.models import User
from asistente.persistence.repositories import UserRepository


class UserService:
    def __init__(self, user_repo: UserRepository) -> None:
        self._users = user_repo

    async def ensure_user(
        self,
        *,
        channel: str = CHANNEL_TELEGRAM,
        external_user_id: str | None = None,
        telegram_user_id: int | None = None,
        name: str | None = None,
        username: str | None = None,
        timezone: str,
        lang: str,
    ) -> User:
        return await self._users.ensure_user(
            channel=channel,
            external_user_id=external_user_id,
            telegram_user_id=telegram_user_id,
            name=name,
            username=username,
            timezone=timezone,
            lang=lang,
        )

    async def timezone_for(self, user_id: int) -> str | None:
        """Zona horaria configurada del usuario; None si aún no existe."""
        return await self._users.timezone_for(user_id)

    async def telegram_id_for(self, user_id: int) -> int | None:
        """`telegram_user_id` del usuario en el canal; None si no existe."""
        return await self._users.telegram_id_for(user_id)
