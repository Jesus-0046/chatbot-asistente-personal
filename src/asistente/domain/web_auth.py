"""Servicio de dominio de autenticación Web (Fase 10.0).

Implementa el ciclo completo de credenciales/sesiones/códigos de enlace sobre
los repositorios `web_*`. La API (FastAPI) es una capa DELGADA que solo valida
y serializa; toda la lógica de seguridad vive aquí y en la persistencia.

Invariantes de seguridad:
- Nunca se persiste el valor crudo de un token ni una contraseña.
- El email NO es la identidad del usuario: la identidad canal-agnóstica vive en
  `user_channels` (`channel='web'`). `users.id` es el scope del resto de la app.
- Registro: SOLO crea usuarios NUEVOS (`ensure_new_web_user`); jamás toma
  control de una cuenta existente aunque se conozca su email.
- Bind: exige un código emitido por el asistente en Telegram (tool `link_web`)
  y atado al propio email; consume single-use.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from asistente.application.channels import CHANNEL_WEB
from asistente.config import Settings
from asistente.persistence.models import User, WebSession
from asistente.persistence.repositories import (
    ChannelIdentityRepository,
    UserRepository,
    WebBindingCodeRepository,
    WebCredentialRepository,
    WebSessionRepository,
)
from asistente.shared.errors import BusinessError, InvalidCredentialsError
from asistente.shared.passwords import (
    generate_binding_code,
    generate_token,
    hash_password,
    is_valid_email,
    normalize_email,
    sha256_hex,
    verify_password,
)
from asistente.shared.timeutil import to_utc, utc_now

# Hash de relleno: igualar el coste del login con credencial inexistente para
# no filtrar por timing si el email está registrado o no.
_DUMMY_HASH = hash_password("dummy-password-for-timing-parity-not-real")


@dataclass(frozen=True, slots=True)
class AuthResult:
    """Resultado de un login/registro/bind: el token crudo se entrega UNA vez."""

    access_token: str
    expires_at: object
    user: User


class WebAuthService:
    def __init__(
        self,
        credentials: WebCredentialRepository,
        sessions: WebSessionRepository,
        binding_codes: WebBindingCodeRepository,
        users: UserRepository,
        channels: ChannelIdentityRepository,
        settings: Settings,
    ) -> None:
        self._credentials = credentials
        self._sessions = sessions
        self._binding = binding_codes
        self._users = users
        self._channels = channels
        self._settings = settings

    # ------------------------------------------------------------------ #
    # Registro (usuario nuevo)                                            #
    # ------------------------------------------------------------------ #
    async def register(
        self,
        *,
        email: str,
        password: str,
        timezone: str | None = None,
        lang: str | None = None,
        name: str | None = None,
    ) -> AuthResult:
        """Crea un usuario NUEVO con identidad Web y credenciales de acceso."""
        email_n = self._validate_email(email)
        self._validate_password(password)
        if await self._credentials.find_by_email(email_n) is not None:
            raise BusinessError("Ese email ya está registrado.")
        user = await self._users.ensure_new_web_user(
            email=email_n,
            name=name or None,
            username=None,
            timezone=timezone or self._settings.default_timezone,
            lang=lang or self._settings.default_lang,
        )
        if user is None:
            raise BusinessError("Ese email ya está registrado.")
        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            await self._credentials.create(user_id=user.id, email=email_n, password_hash=password_hash)
        except IntegrityError:
            # Carrera: otro registro tomó el email justo después de nuestro check.
            # Se elimina el usuario recién creado (cascada) para no dejar datos.
            await self._users.delete(user.id)
            raise BusinessError("Ese email ya está registrado.") from None
        return await self._issue_session(user)

    # ------------------------------------------------------------------ #
    # Login                                                               #
    # ------------------------------------------------------------------ #
    async def login(self, *, email: str, password: str) -> AuthResult:
        """Autentica por email+password y emite una sesión Bearer."""
        email_n = normalize_email(email)
        credential = await self._credentials.find_by_email(email_n)
        if credential is None:
            await asyncio.to_thread(verify_password, _DUMMY_HASH, password)
            raise InvalidCredentialsError("Credenciales no válidas.")
        if not await asyncio.to_thread(verify_password, credential.password_hash, password):
            raise InvalidCredentialsError("Credenciales no válidas.")
        user = await self._users.get(credential.user_id)
        if user is None:  # pragma: no cover - defensivo (FK cascade)
            raise InvalidCredentialsError("Credenciales no válidas.")
        return await self._issue_session(user)

    # ------------------------------------------------------------------ #
    # Sesiones                                                            #
    # ------------------------------------------------------------------ #
    async def authenticate(self, token: str) -> tuple[WebSession, User]:
        """Valida un Bearer token y devuelve (sesión, usuario). 401 si es inválido."""
        token = (token or "").strip()
        now = utc_now()
        found = await self._sessions.find_valid_with_user(sha256_hex(token), now=now)
        if found is None:
            raise InvalidCredentialsError("Sesión no válida o caducada.")
        session, user = found
        await self._sessions.mark_used(session.id, now=now)
        return session, user

    async def logout(self, token: str) -> None:
        """Revoca la sesión asociada al token (idempotente)."""
        token = (token or "").strip()
        found = await self._sessions.find_valid_with_user(sha256_hex(token), now=utc_now())
        if found is None:
            return
        session, _user = found
        await self._sessions.revoke(session.id, now=utc_now())

    async def revoke_all_for_user(self, user_id: int) -> None:
        """Revoca todas las sesiones del usuario (p. ej. cambio de password)."""
        await self._sessions.revoke_all_for_user(user_id, now=utc_now())

    # ------------------------------------------------------------------ #
    # Bind (usuario existente, código emitido desde Telegram)              #
    # ------------------------------------------------------------------ #
    async def issue_binding_code(self, user_id: int, *, email: str) -> str:
        """Genera (o remite) un código single-use para vincular credenciales Web.

        Lo invoca la tool `link_web` desde Telegram. El código va atado al
        `user_id` emisor y al email que el usuario escribirá en el formulario.
        """
        email_n = self._validate_email(email)
        now = utc_now()
        await self._binding.deactivate_for(user_id=user_id, email=email_n, now=now)
        code = generate_binding_code()
        expires = now + timedelta(minutes=self._settings.web_binding_ttl_minutes)
        await self._binding.create(
            user_id=user_id,
            email=email_n,
            code_hash=sha256_hex(code),
            expires_at=expires,
        )
        return code

    async def bind(self, *, email: str, code: str, password: str) -> AuthResult:
        """Vincula credenciales Web del usuario que posee el código."""
        email_n = self._validate_email(email)
        self._validate_password(password)
        consumed = await self._binding.consume(
            sha256_hex((code or "").strip().upper()),
            now=utc_now(),
        )
        if consumed is None:
            raise BusinessError("El código no es válido, ha caducado o ya se ha usado.")
        user_id, bound_email = consumed
        if bound_email != email_n:
            raise BusinessError("El código no coincide con el email indicado.")
        user = await self._users.get(user_id)
        if user is None:
            raise BusinessError("La cuenta asociada al código ya no existe.")
        if await self._credentials.find_by_user_id(user_id) is not None:
            raise BusinessError("Esa cuenta ya tiene credenciales Web.")
        final_user = await self._channels.ensure_linked(
            channel=CHANNEL_WEB,
            external_user_id=email_n,
            user_id=user_id,
        )
        if final_user is None:
            raise BusinessError("Ese email ya está vinculado a otra cuenta.")
        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            await self._credentials.create(user_id=user_id, email=email_n, password_hash=password_hash)
        except IntegrityError:
            raise BusinessError("Ese email ya está registrado.") from None
        return await self._issue_session(user)

    # ------------------------------------------------------------------ #
    # Consultas de perfil (para /api/v1/me)                               #
    # ------------------------------------------------------------------ #
    async def channels_of_user(self, user_id: int) -> list[dict]:
        rows = await self._channels.list_for_user(user_id)
        return [{"channel": r.channel, "external_user_id": r.external_user_id, "status": r.status} for r in rows]

    async def get_user(self, user_id: int) -> User | None:
        return await self._users.get(user_id)

    # ------------------------------------------------------------------ #
    # Internos                                                            #
    # ------------------------------------------------------------------ #
    def _validate_email(self, email: str) -> str:
        email_n = normalize_email(email)
        if not is_valid_email(email_n):
            raise BusinessError("El email no es válido.")
        return email_n

    def _validate_password(self, password: str) -> None:
        if len(password or "") < self._settings.web_password_min_length:
            raise BusinessError(
                f"La contraseña debe tener al menos {self._settings.web_password_min_length} caracteres."
            )

    async def _issue_session(self, user: User) -> AuthResult:
        now = utc_now()
        expires = now + timedelta(days=self._settings.web_session_ttl_days)
        token = generate_token()
        await self._sessions.create(user_id=user.id, token_hash=sha256_hex(token), expires_at=expires)
        return AuthResult(access_token=token, expires_at=to_utc(expires), user=user)
