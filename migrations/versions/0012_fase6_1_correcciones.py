"""Fase 6.1: correcciones y cierre de Fase 6.

Añade:
- trips: trip_type, timezone, budget
- user_proactivity_settings: importance_threshold

Revision ID: 0012_fase6_1_correcciones
Revises: 0011_fase6_viajes_resumenes_proactividad
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_fase6_1_correcciones"
down_revision: str | None = "0011_fase6_viajes_resumenes_proactividad"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- trips: new columns ---
    op.add_column("trips", sa.Column("trip_type", sa.String(16), nullable=False, server_default="other"))
    op.add_column("trips", sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"))
    op.add_column("trips", sa.Column("budget", sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint(
        "ck_trips_trip_type",
        "trips",
        "trip_type IN ('leisure', 'business', 'family', 'other')",
    )
    op.create_check_constraint(
        "ck_trips_budget_positive",
        "trips",
        "budget IS NULL OR budget >= 0",
    )

    # --- user_proactivity_settings: importance_threshold ---
    op.add_column(
        "user_proactivity_settings",
        sa.Column("importance_threshold", sa.Integer(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_proactivity_importance_threshold",
        "user_proactivity_settings",
        "importance_threshold >= 1 AND importance_threshold <= 3",
    )


def downgrade() -> None:
    op.drop_constraint("ck_proactivity_importance_threshold", "user_proactivity_settings", type_="check")
    op.drop_column("user_proactivity_settings", "importance_threshold")

    op.drop_constraint("ck_trips_budget_positive", "trips", type_="check")
    op.drop_constraint("ck_trips_trip_type", "trips", type_="check")
    op.drop_column("trips", "budget")
    op.drop_column("trips", "timezone")
    op.drop_column("trips", "trip_type")
