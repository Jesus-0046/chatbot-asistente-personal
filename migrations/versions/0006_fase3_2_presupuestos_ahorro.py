"""Fase 3.2: presupuestos y objetivos de ahorro.

Tablas creadas:
- budgets:              presupuestos mensuales (generales o por categoría).
- savings_goals:        objetivos de ahorro del usuario.
- savings_contributions: contribuciones a objetivos de ahorro.

Reglas de datos:
- Importes en Numeric(12,2), nunca floats.
- Todas las tablas llevan user_id FK a users.id (aislamiento por usuario).
- Un único presupuesto general por usuario/mes (category IS NULL) garantizado
  con índice parcial único en PostgreSQL.
- Un único presupuesto por categoría por usuario/mes con otro índice parcial.
- savings_goals.target_amount > 0; savings_contributions.amount > 0.
- ON DELETE para savings_goals.id en contributions: RESTRICT (no borrar
  objetivo con contribuciones).

Revision ID: 0006_fase3_2_presupuestos_ahorro
Revises: 0005_fase3_finanzas
Create Date: 2026-09-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_fase3_2_presupuestos_ahorro"
down_revision: str | None = "0005_fase3_finanzas"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "budgets",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(64), nullable=True),
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
        sa.CheckConstraint("amount > 0", name="ck_budgets_amount_positive"),
        sa.CheckConstraint("month >= 1 AND month <= 12", name="ck_budgets_month_range"),
    )
    op.create_index("ix_budgets_user_id", "budgets", ["user_id"])
    op.create_index("ix_budgets_category", "budgets", ["category"])
    op.create_index(
        "ix_budgets_user_year_month_null",
        "budgets",
        ["user_id", "year", "month"],
        unique=True,
        postgresql_where=sa.text("category IS NULL"),
    )
    op.create_index(
        "ix_budgets_user_year_month_category",
        "budgets",
        ["user_id", "year", "month", "category"],
        unique=True,
        postgresql_where=sa.text("category IS NOT NULL"),
    )

    op.create_table(
        "savings_goals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("target_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("deadline", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
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
        sa.CheckConstraint("target_amount > 0", name="ck_savings_goals_target_positive"),
    )
    op.create_index("ix_savings_goals_user_id", "savings_goals", ["user_id"])

    op.create_table(
        "savings_contributions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "goal_id",
            sa.BigInteger(),
            sa.ForeignKey("savings_goals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("contributed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "transfer_id",
            sa.BigInteger(),
            sa.ForeignKey("transfers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount > 0", name="ck_savings_contributions_amount_positive"),
    )
    op.create_index("ix_savings_contributions_user_id", "savings_contributions", ["user_id"])
    op.create_index("ix_savings_contributions_goal_id", "savings_contributions", ["goal_id"])
    op.create_index("ix_savings_contributions_transfer_id", "savings_contributions", ["transfer_id"])


def downgrade() -> None:
    op.drop_index("ix_savings_contributions_transfer_id", table_name="savings_contributions")
    op.drop_index("ix_savings_contributions_goal_id", table_name="savings_contributions")
    op.drop_index("ix_savings_contributions_user_id", table_name="savings_contributions")
    op.drop_table("savings_contributions")

    op.drop_index("ix_savings_goals_user_id", table_name="savings_goals")
    op.drop_table("savings_goals")

    op.drop_index("ix_budgets_user_year_month_category", table_name="budgets")
    op.drop_index("ix_budgets_user_year_month_null", table_name="budgets")
    op.drop_index("ix_budgets_category", table_name="budgets")
    op.drop_index("ix_budgets_user_id", table_name="budgets")
    op.drop_table("budgets")
