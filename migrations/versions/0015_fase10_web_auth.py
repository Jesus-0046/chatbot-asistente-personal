"""Fase 10.0: autenticación Web (credenciales, sesiones y códigos de enlace).

Crea tres tablas estrictamente de acceso Web:

- `web_credentials`: UN Credencial por `users.id` (email único, normalizado) y
  password Argon2id (`password_hash` PHC). El email NO es identidad: esta vive
  en `user_channels` (`channel='web'`), de modo que el mismo `users.id` puede
  hablar por Telegram, WhatsApp y Web a la vez.

- `web_sessions`: sesiones persistentes Bearer. Solo se persiste el SHA-256 del
  token (`token_hash`, 64 hex): una fuga de BD no expone tokens válidos. TTL de
  vida (`expires_at`), revocación (`revoked_at`) y `last_used_at`.

- `web_binding_codes`: códigos single-use con TTL usados por usuarios
  EXISTENTES para vincular credenciales Web a su cuenta. El código lo genera el
  asistente en Telegram (tool `link_web`) y va atado al email que el usuario
  escribirá en el formulario Web; NO se acepta un "email + código arbitrario".
  `(user_id, email)` único => un solo código pendiente por par; `code_hash`
  único => single-use a nivel de BD.

Regla transversal: el `users.id` interno es el scope del resto de la aplicación
y los routers NUNCA derivan identidad del cuerpo de la petición.

Revision ID: 0015_fase10_web_auth
Revises: 0014_fase9_whatsapp
Create Date: 2026-09-22
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_fase10_web_auth"
down_revision: str | None = "0014_fase9_whatsapp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_credentials",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_web_credentials_user_id"),
        sa.UniqueConstraint("email", name="uq_web_credentials_email"),
    )

    op.create_table(
        "web_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_web_sessions_token_hash"),
    )
    op.create_index("ix_web_sessions_user_id", "web_sessions", ["user_id"])

    op.create_table(
        "web_binding_codes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "email", name="uq_web_binding_codes_user_email"),
        sa.UniqueConstraint("code_hash", name="uq_web_binding_codes_code_hash"),
    )
    op.create_index("ix_web_binding_codes_user_id", "web_binding_codes", ["user_id"])
    op.create_check_constraint("ck_web_binding_codes_code_hash", "web_binding_codes", "length(code_hash) = 64")


def downgrade() -> None:
    op.drop_index("ix_web_binding_codes_user_id", table_name="web_binding_codes")
    op.drop_table("web_binding_codes")
    op.drop_index("ix_web_sessions_user_id", table_name="web_sessions")
    op.drop_table("web_sessions")
    op.drop_table("web_credentials")
