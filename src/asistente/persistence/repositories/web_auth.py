"""Repositorios de autenticación Web (Fase 10.0).

`web_credentials`, `web_sessions` y `web_binding_codes` viven aisladas del
resto del dominio: contienen exclusivamente material de acceso (hash Argon2id,
SHA-256 de tokens/códigos). Nunca se expone aquí el valor crudo de un token.

Regla de oro del aislamiento: una identidad Web ya vinculada a un usuario NO
puede re-vincularse por una simple elección de email — eso lo garantiza
`ChannelIdentityRepository.ensure_linked` y `consume` single-use de los códigos.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from asistente.persistence.models import User, WebBindingCode, WebCredential, WebSession
from asistente.persistence.repositories.base import Repository
from asistente.shared.timeutil import to_utc, utc_now


class WebCredentialRepository(Repository):
    async def find_by_email(self, email: str) -> WebCredential | None:
        async with self.db.session() as session:
            return (
                await session.execute(select(WebCredential).where(WebCredential.email == email))
            ).scalar_one_or_none()

    async def find_by_user_id(self, user_id: int) -> WebCredential | None:
        async with self.db.session() as session:
            return (
                await session.execute(select(WebCredential).where(WebCredential.user_id == user_id))
            ).scalar_one_or_none()

    async def create(self, *, user_id: int, email: str, password_hash: str) -> WebCredential:
        row = WebCredential(user_id=user_id, email=email, password_hash=password_hash)
        async with self.db.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise
            await session.refresh(row)
            return row


class WebSessionRepository(Repository):
    async def create(self, *, user_id: int, token_hash: str, expires_at: datetime) -> WebSession:
        row = WebSession(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        async with self.db.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise
            await session.refresh(row)
            return row

    async def find_valid_with_user(self, token_hash: str, *, now: datetime) -> tuple[WebSession, User] | None:
        async with self.db.session() as session:
            row = (
                await session.execute(select(WebSession).where(WebSession.token_hash == token_hash))
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.revoked_at is not None:
                return None
            if to_utc(row.expires_at) <= now:
                return None
            user = await session.get(User, row.user_id)
            if user is None:
                return None
            return row, user

    async def mark_used(self, session_id: int, *, now: datetime) -> None:
        async with self.db.session() as session:
            result: Any = cast(
                Any,
                await session.execute(update(WebSession).where(WebSession.id == session_id).values(last_used_at=now)),
            )
            if result.rowcount:
                await session.commit()

    async def revoke(self, session_id: int, *, now: datetime) -> None:
        async with self.db.session() as session:
            result: Any = cast(
                Any, await session.execute(update(WebSession).where(WebSession.id == session_id).values(revoked_at=now))
            )
            if result.rowcount:
                await session.commit()

    async def revoke_all_for_user(self, user_id: int, *, now: datetime) -> None:
        async with self.db.session() as session:
            await session.execute(
                update(WebSession)
                .where(WebSession.user_id == user_id, WebSession.revoked_at.is_(None))
                .values(revoked_at=now)
            )
            await session.commit()


class WebBindingCodeRepository(Repository):
    async def create(self, *, user_id: int, email: str, code_hash: str, expires_at: datetime) -> WebBindingCode:
        row = WebBindingCode(user_id=user_id, email=email, code_hash=code_hash, expires_at=expires_at)
        async with self.db.session() as session:
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                raise
            await session.refresh(row)
            return row

    async def deactivate_for(self, *, user_id: int, email: str, now: datetime) -> None:
        """Marca como consumidos los códigos pendientes (user, email) para reemitir."""
        async with self.db.session() as session:
            await session.execute(
                update(WebBindingCode)
                .where(
                    WebBindingCode.user_id == user_id,
                    WebBindingCode.email == email,
                    WebBindingCode.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            await session.commit()

    async def consume(self, code_hash: str, *, now: datetime) -> tuple[int, str] | None:
        """Consume atómicamente un código vigente (single-use, no expirado).

        UPDATE con guardas (`consumed_at IS NULL` y `expires_at > now`) y
        RETURNING: aunque dos peticiones lleguen a la vez, solo una obtiene la
        fila. Devuelve `(user_id, email)` o None si no hay código válido.
        """
        stmt = (
            update(WebBindingCode)
            .where(
                WebBindingCode.code_hash == code_hash,
                WebBindingCode.consumed_at.is_(None),
                WebBindingCode.expires_at > utc_now(),
            )
            .values(consumed_at=now)
            .returning(WebBindingCode.user_id, WebBindingCode.email)
        )
        async with self.db.transaction() as session:
            row = (await session.execute(stmt)).first()
            if row is None:
                return None
            return int(row[0]), str(row[1])
