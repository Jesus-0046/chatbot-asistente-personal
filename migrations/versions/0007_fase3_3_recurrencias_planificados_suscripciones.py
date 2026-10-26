"""Fase 3.3: recurrencias financieras, pagos planificados y suscripciones.

Tablas creadas:
- recurring_transactions:  regla de recurrencia financiera (la REGLA, no un
                           movimiento): frecuencia + intervalo + ancla de mes.
- planned_transactions:    ocurrencia futura planificada (NO mueve dinero hasta
                           confirmarse; status planned|converted|cancelled).
- subscriptions:           suscripción vinculada a una recurrencia (la
                           recurrencia es el motor; la suscripción su facturación).

Reglas de datos:
- Importes en Numeric(12,2), nunca floats; todas las tablas llevan user_id FK a
  users.id (aislamiento por usuario).
- recurring_transactions: amount > 0; type en (expense, income); frequency en
  (daily, weekly, monthly, yearly); day_of_month 1-31; month_of_year 1-12.
  `day_of_month` guarda el día ANCLA (31 ene → 28/29 feb → 31 mar → 30 abr →
  31 may) sin desviación permanente.
- planned_transactions: amount > 0; type/status con checks. La unicidad
  (recurring_transaction_id, planned_at) impide materializar dos veces la misma
  ocurrencia (idempotencia). El vínculo a la transacción real convertida
  (converted_transaction_id) es SET NULL si se borra (no se borra histórico).
- subscriptions: amount > 0; billing_period en (daily, weekly, monthly, yearly).

ON DELETE:
- user_id: CASCADE (borrar usuario borra sus recurrencias/planificados/suscripciones).
- account_id: SET NULL (desactivar/borrar cuenta no rompe la recurrencia).
- recurring_transaction_id / converted_transaction_id: SET NULL (no perder trazabilidad
  del host).

Revision ID: 0007_fase3_3_recurrencias_planificados_suscripciones
Revises: 0006_fase3_2_presupuestos_ahorro
Create Date: 2026-09-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_fase3_3_recurrencias_planificados_suscripciones"
down_revision: str | None = "0006_fase3_2_presupuestos_ahorro"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("merchant", sa.String(300), nullable=True),
        sa.Column("category", sa.String(64), nullable=False, server_default="Otros"),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("month_of_year", sa.Integer(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.false()),
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
        sa.CheckConstraint("amount > 0", name="ck_recurring_transactions_amount_positive"),
        sa.CheckConstraint("type IN ('expense', 'income')", name="ck_recurring_transactions_type"),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_recurring_transactions_frequency",
        ),
        sa.CheckConstraint("day_of_month >= 1 AND day_of_month <= 31", name="ck_recurring_day_of_month"),
        sa.CheckConstraint("month_of_year >= 1 AND month_of_year <= 12", name="ck_recurring_month_of_year"),
    )
    op.create_index("ix_recurring_transactions_user_id", "recurring_transactions", ["user_id"])
    op.create_index("ix_recurring_transactions_account_id", "recurring_transactions", ["account_id"])
    op.create_index("ix_recurring_transactions_next_due_at", "recurring_transactions", ["next_due_at"])

    op.create_table(
        "planned_transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(300), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("merchant", sa.String(300), nullable=True),
        sa.Column("category", sa.String(64), nullable=False, server_default="Otros"),
        sa.Column("planned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "recurring_transaction_id",
            sa.BigInteger(),
            sa.ForeignKey("recurring_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
        sa.Column(
            "converted_transaction_id",
            sa.BigInteger(),
            sa.ForeignKey("transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
        sa.CheckConstraint("amount > 0", name="ck_planned_transactions_amount_positive"),
        sa.CheckConstraint("type IN ('expense', 'income')", name="ck_planned_transactions_type"),
        sa.CheckConstraint(
            "status IN ('planned', 'converted', 'cancelled')",
            name="ck_planned_transactions_status",
        ),
    )
    op.create_index("ix_planned_transactions_user_id", "planned_transactions", ["user_id"])
    op.create_index("ix_planned_transactions_account_id", "planned_transactions", ["account_id"])
    op.create_index(
        "ix_planned_transactions_planned_at",
        "planned_transactions",
        ["planned_at"],
    )
    op.create_index("ix_planned_user_planned", "planned_transactions", ["user_id", "planned_at"])
    op.create_index(
        "ix_planned_transactions_recurring_id",
        "planned_transactions",
        ["recurring_transaction_id"],
    )
    op.create_index(
        "ix_planned_transactions_converted_id",
        "planned_transactions",
        ["converted_transaction_id"],
    )
    # Idempotencia: una sola fila por (recurrencia, ocurrencia). Las filas
    # manuales (recurring_transaction_id NULL) no colisionan (NULL != NULL).
    op.create_index(
        "ix_planned_recurring_occurrence",
        "planned_transactions",
        ["recurring_transaction_id", "planned_at"],
        unique=True,
    )

    op.create_table(
        "subscriptions",
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
        sa.Column("billing_period", sa.String(16), nullable=False),
        sa.Column("next_billing_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("category", sa.String(64), nullable=False, server_default="Suscripciones"),
        sa.Column(
            "recurring_transaction_id",
            sa.BigInteger(),
            sa.ForeignKey("recurring_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
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
        sa.CheckConstraint("amount > 0", name="ck_subscriptions_amount_positive"),
        sa.CheckConstraint(
            "billing_period IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_subscriptions_billing_period",
        ),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_next_billing_date", "subscriptions", ["next_billing_date"])
    op.create_index("ix_subscriptions_account_id", "subscriptions", ["account_id"])
    op.create_index("ix_subscriptions_recurring_id", "subscriptions", ["recurring_transaction_id"])


def downgrade() -> None:
    op.drop_index("ix_subscriptions_recurring_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_account_id", table_name="subscriptions")
    op.drop_index("ix_subscriptions_next_billing_date", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_planned_recurring_occurrence", table_name="planned_transactions")
    op.drop_index("ix_planned_transactions_converted_id", table_name="planned_transactions")
    op.drop_index("ix_planned_transactions_recurring_id", table_name="planned_transactions")
    op.drop_index("ix_planned_user_planned", table_name="planned_transactions")
    op.drop_index("ix_planned_transactions_planned_at", table_name="planned_transactions")
    op.drop_index("ix_planned_transactions_account_id", table_name="planned_transactions")
    op.drop_index("ix_planned_transactions_user_id", table_name="planned_transactions")
    op.drop_table("planned_transactions")

    op.drop_index("ix_recurring_transactions_next_due_at", table_name="recurring_transactions")
    op.drop_index("ix_recurring_transactions_account_id", table_name="recurring_transactions")
    op.drop_index("ix_recurring_transactions_user_id", table_name="recurring_transactions")
    op.drop_table("recurring_transactions")
