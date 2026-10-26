"""Fase 9: identidad canal-agnóstica, vinculación WhatsApp e idempotencia.

Crea `user_channels`: identidad `(channel, external_user_id)` que resuelve al
`users.id` interno. Estados:
- `linked`:  identidad confirmada y vinculada a un usuario.
- `pending`: identidad vista en WhatsApp que espera su código de enlace
             (confirmado desde Telegram; ver tool `link_whatsapp`).

Backfill: cada `users.telegram_user_id` existente se importa como fila
`('telegram', CAST(telegram_user_id AS VARCHAR), 'linked')`.

`users.telegram_user_id` pasa a NULLABLE (la identidad Telegram ya no es la
única fuente de verdad; se conserva para notificaciones/pollers). El índice
único se mantiene: en PostgreSQL permite múltiples NULL.

Crea `processed_inbound_events`: idempotencia de eventos entrantes con clave
única `(channel, external_message_id)`. Su insert dedup se hace ANTES de tocar
lógica de negocio: un mismo wamid nunca produce dos gastos/tareas/confirmaciones.

Revision ID: 0014_fase9_whatsapp
Revises: 0013_fase6_2_claim_lease
Create Date: 2026-09-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_fase9_whatsapp"
down_revision: str | None = "0013_fase6_2_claim_lease"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- user_channels: identidad por canal ---
    op.create_table(
        "user_channels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="linked"),
        sa.Column("pair_code_hash", sa.String(64), nullable=True),
        sa.Column("pair_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "external_user_id", name="uq_user_channels_channel_external"),
    )
    op.create_check_constraint("ck_user_channels_status", "user_channels", "status IN ('linked', 'pending')")
    op.create_index("ix_user_channels_user_id", "user_channels", ["user_id"])

    # --- backfill: identidades Telegram existentes ---
    op.execute(
        """
        INSERT INTO user_channels (user_id, channel, external_user_id, status)
        SELECT id, 'telegram', CAST(telegram_user_id AS VARCHAR(255)), 'linked'
        FROM users
        WHERE telegram_user_id IS NOT NULL
        """
    )

    # --- users.telegram_user_id pasa a nullable ---
    op.alter_column("users", "telegram_user_id", existing_type=sa.BigInteger(), nullable=True)

    # --- processed_inbound_events: idempotencia de eventos entrantes ---
    op.create_table(
        "processed_inbound_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("external_message_id", sa.String(255), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "external_message_id", name="uq_processed_events_channel_message"),
    )


def downgrade() -> None:
    # --- processed_inbound_events ---
    op.drop_table("processed_inbound_events")

    # Restaura NOT NULL en users.telegram_user_id. Si existen usuarios creados
    # solo por WhatsApp (sin telegram_user_id), el downgrade FALLA a propósito:
    # devolver la BD a NOT NULL destruiría identidades vinculadas.
    op.alter_column("users", "telegram_user_id", existing_type=sa.BigInteger(), nullable=False)

    # --- user_channels ---
    op.drop_index("ix_user_channels_user_id", table_name="user_channels")
    op.drop_constraint("ck_user_channels_status", "user_channels", type_="check")
    op.drop_table("user_channels")
