"""Fase 6.2: claim/lease concurrente del Proactive Poller.

Añade a `proactive_notifications` los campos de claim/lease:
- claimed_at, lease_until, worker_id
- estado `processing` permitido en el CHECK (reclamado con lease vigente)
- índice (status, lease_until) para localizar reclaims de leases expirados

Crea `proactive_daily_counters` (contador atómico del límite diario local por
usuario) con su clave única (user_id, day) y CHECK count >= 0.

Revision ID: 0013_fase6_2_claim_lease
Revises: 0012_fase6_1_correcciones
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_fase6_2_claim_lease"
down_revision: str | None = "0012_fase6_1_correcciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- proactive_notifications: claim/lease fields ---
    op.add_column("proactive_notifications", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proactive_notifications", sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("proactive_notifications", sa.Column("worker_id", sa.String(64), nullable=True))

    op.drop_constraint("ck_proactive_notifications_status", "proactive_notifications", type_="check")
    op.create_check_constraint(
        "ck_proactive_notifications_status",
        "proactive_notifications",
        "status IN ('pending', 'processing', 'sent', 'skipped')",
    )
    op.create_index(
        "ix_proactive_notifications_status_lease",
        "proactive_notifications",
        ["status", "lease_until"],
    )

    # --- proactive_daily_counters: contador atómico del límite diario ---
    op.create_table(
        "proactive_daily_counters",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "day", name="uq_proactive_daily_counters_user_day"),
    )
    op.create_check_constraint("ck_proactive_daily_counters_count", "proactive_daily_counters", "count >= 0")
    op.create_index("ix_proactive_daily_counters_user_id", "proactive_daily_counters", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_proactive_daily_counters_user_id", table_name="proactive_daily_counters")
    op.drop_constraint("ck_proactive_daily_counters_count", "proactive_daily_counters", type_="check")
    op.drop_table("proactive_daily_counters")

    op.drop_index("ix_proactive_notifications_status_lease", table_name="proactive_notifications")
    op.drop_constraint("ck_proactive_notifications_status", "proactive_notifications", type_="check")
    op.create_check_constraint(
        "ck_proactive_notifications_status",
        "proactive_notifications",
        "status IN ('pending', 'sent', 'skipped')",
    )
    op.drop_column("proactive_notifications", "worker_id")
    op.drop_column("proactive_notifications", "lease_until")
    op.drop_column("proactive_notifications", "claimed_at")
