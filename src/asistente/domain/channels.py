"""Servicio de dominio de identidad por canal (Fase 9).

Resuelve `(channel, external_user_id)` → `users.id` y gestiona el ciclo del
código de enlace de WhatsApp (crear → confirmar → consumir una sola vez).
Ningún nombre impostable (display_name/profile_name) participa en la identidad.
"""

from __future__ import annotations

from datetime import timedelta

from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.application.pairing import (
    PAIR_CODE_TTL_SECONDS,
    generate_pair_code,
    hash_pair_code,
    looks_like_pair_code,
)
from asistente.persistence.models import User, UserChannel
from asistente.persistence.repositories import ChannelIdentityRepository
from asistente.shared.timeutil import to_utc, utc_now


class ChannelIdentityService:
    def __init__(self, channels: ChannelIdentityRepository) -> None:
        self._channels = channels

    async def resolve_user(self, channel: str, external_user_id: str) -> User | None:
        """Usuario interno vinculado a la identidad (o None si no está vinculado)."""
        return await self._channels.find_linked_user(channel, external_user_id)

    async def has_linked(self, user_id: int, channel: str) -> bool:
        return await self._channels.has_linked(user_id, channel)

    async def ensure_pending_pair(self, channel: str, external_user_id: str) -> tuple[UserChannel, str | None]:
        """Garantiza una identidad 'pending' con código de enlace vigente.

        Devuelve (fila, código en texto plano). `código=None` si ya existía un
        código sin caducar: el usuario puede reintentar desde Telegram sin que
        el webhook re-exponga el código (el texto plano solo se muestra al
        generarse y nunca se persiste).
        """
        row = await self._channels.find_pending(channel, external_user_id)
        now = utc_now()
        if row is not None and row.pair_code_hash is not None and row.pair_expires_at is not None:
            if to_utc(row.pair_expires_at) > now:
                return row, None
            row = None

        code = generate_pair_code()
        expires = now + timedelta(seconds=PAIR_CODE_TTL_SECONDS)
        code_hash = hash_pair_code(code)
        if row is None:
            created = await self._channels.create_pending(
                channel=channel,
                external_user_id=external_user_id,
                pair_code_hash=code_hash,
                pair_expires_at=expires,
            )
            # Si la carrera devolvió una fila ya existente con otro código,
            # no podemos re-exponer el código generado: se responde genérico.
            if created.pair_code_hash != code_hash:
                return created, None
            return created, code
        await self._channels.update_pair_code(row.id, code_hash, expires)
        return row, code

    async def link_with_code(self, *, code: str, user_id: int, channel: str = CHANNEL_WHATSAPP) -> User | None:
        """Vincula al usuario interno con la identidad pendiente del código.

        - Código de formato no válido → None.
        - Caducado / ya usado / usuario ya vinculado por ese canal → None.
        """
        clean = (code or "").strip().upper()
        if not looks_like_pair_code(clean):
            return None
        return await self._channels.link_pending(
            code_hash=hash_pair_code(clean),
            user_id=user_id,
            channel=channel,
        )

    async def mark_processed(self, channel: str, external_message_id: str) -> bool:
        """True si el evento es nuevo; False si ya se procesó (duplicado)."""
        return await self._channels.mark_processed(channel, external_message_id)
