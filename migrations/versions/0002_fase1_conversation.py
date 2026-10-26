"""Fase 1: tablas del núcleo de conversación.

Tablas creadas:
- users:                  identidad (telegram_user_id único) + perfil
- conversation_messages:  memoria corta (ventana móvil)
- pending_confirmations:  confirmaciones genéricas reutilizables
- memory_rules:           reglas de memoria (propuestas/activas/revocadas)
- notes:                  notas (tool mínima de Fase 1)

Aislamiento por usuario: todas las tablas de negocio llevan `user_id` FK a
`users.id`.  No hay balances ni entidades financieras (llegan en Fase 2+).

Revision ID: 0002_fase1_conversation
Revises: 0001_baseline
Create Date: 2026-09-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_fase1_conversation"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("telegram_user_id", sa.BigInteger(), unique=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("username", sa.String(200), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("lang", sa.String(16), nullable=False, server_default="es-ES"),
        sa.Column("settings", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_telegram_user_id", "users", ["telegram_user_id"])

    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("meta", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_conversation_messages_user_id", "conversation_messages", ["user_id"])
    op.create_index("ix_conversation_messages_role", "conversation_messages", ["role"])
    op.create_index("ix_conversation_messages_created_at", "conversation_messages", ["created_at"])

    op.create_table(
        "pending_confirmations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(200), nullable=False),
        sa.Column("payload", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("proposed_reply", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="open",
        ),  # open|confirmed|cancelled|expired
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pending_confirmations_user_id", "pending_confirmations", ["user_id"])
    op.create_index("ix_pending_confirmations_status", "pending_confirmations", ["status"])
    op.create_index("ix_pending_confirmations_expires_at", "pending_confirmations", ["expires_at"])

    op.create_table(
        "memory_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB, nullable=False, server_default="{}"),
        sa.Column("scope", sa.String(200), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="proposed"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_memory_rules_user_id", "memory_rules", ["user_id"])
    op.create_index("ix_memory_rules_user_active", "memory_rules", ["user_id", "active"])

    op.create_table(
        "notes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_notes_user_id", table_name="notes")
    op.drop_table("notes")
    op.drop_index("ix_memory_rules_user_active", table_name="memory_rules")
    op.drop_index("ix_memory_rules_user_id", table_name="memory_rules")
    op.drop_table("memory_rules")
    op.drop_index("ix_pending_confirmations_expires_at", table_name="pending_confirmations")
    op.drop_index("ix_pending_confirmations_status", table_name="pending_confirmations")
    op.drop_index("ix_pending_confirmations_user_id", table_name="pending_confirmations")
    op.drop_table("pending_confirmations")
    op.drop_index("ix_conversation_messages_created_at", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_role", table_name="conversation_messages")
    op.drop_index("ix_conversation_messages_user_id", table_name="conversation_messages")
    op.drop_table("conversation_messages")
    op.drop_index("ix_users_telegram_user_id", table_name="users")
    op.drop_table("users")
