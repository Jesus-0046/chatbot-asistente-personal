"""Fase 3.4: personas, deudas/préstamos y gastos compartidos.

Tablas creadas:
- people:                    personas con las que el usuario comparte deudas o
                             gastos. `normalized_name` (minúsculas, sin acentos,
                             espacios colapsados) facilita búsqueda/`resolution`.
                             Nunca se borran físicamente si tienen histórico:
                             `active=False`.
- debts:                     deuda/préstamo PERSONAL entre el usuario y una
                             persona. `direction` indica quién debe a quién
                             (owed_to_user | user_owes); `remaining_amount`
                             empieza igual a `amount` y cae a 0 al liquidar
                             (settle → status=settled). La suma nunca excede
                             `amount` (CheckConstraints).
- shared_expenses:           gasto compartido (quién o quiénes pagaron, reparto).
- shared_expense_shares:     participación de cada persona/usuario en el gasto.
                             El usuario aparece como mucho UNA vez por gasto y
                             cada persona una vez (índices únicos parciales).
                             SUM(shares) == total garantizado en dominio.
- shared_expense_payments:   aportación de cada pagador (una o varias personas).
                             SUM(payments) == total garantizado en dominio.

Reglas de borrado:
- `people.*_person_id` / `debts.person_id`: ON DELETE RESTRICT: no se borran
  personas con histórico financiero.
- shares y payments: ON DELETE CASCADE desde `shared_expenses` (mueren con su
  gasto); referencia a personas con RESTRICT.

Revision ID: 0009_fase3_4_personas_deudas_gastos_compartidos
Revises: 0008_fase3_2_3_3_1_consistencia
Create Date: 2026-09-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_fase3_4_personas_deudas_gastos_compartidos"
down_revision: str | None = "0008_fase3_2_3_3_1_consistencia"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "people",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
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
    op.create_index("ix_people_user_id", "people", ["user_id"])
    op.create_index("ix_people_user_normalized", "people", ["user_id", "normalized_name"])

    op.create_table(
        "debts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("direction", sa.String(16), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("remaining_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
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
        sa.CheckConstraint("amount > 0", name="ck_debts_amount_positive"),
        sa.CheckConstraint("remaining_amount >= 0", name="ck_debts_remaining_non_negative"),
        sa.CheckConstraint(
            "direction IN ('owed_to_user', 'user_owes')",
            name="ck_debts_direction",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'settled', 'cancelled')",
            name="ck_debts_status",
        ),
    )
    op.create_index("ix_debts_user_id", "debts", ["user_id"])
    op.create_index("ix_debts_person_id", "debts", ["person_id"])
    op.create_index("ix_debts_due_at", "debts", ["due_at"])
    op.create_index("ix_debts_user_status", "debts", ["user_id", "status"])

    op.create_table(
        "shared_expenses",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column(
            "paid_by_person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("paid_by_user", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
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
        sa.CheckConstraint("total_amount > 0", name="ck_shared_expenses_total_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'cancelled')",
            name="ck_shared_expenses_status",
        ),
        # Pagador O el usuario O una persona, nunca ambos.
        sa.CheckConstraint(
            "(paid_by_user AND paid_by_person_id IS NULL) OR (NOT paid_by_user AND paid_by_person_id IS NOT NULL)",
            name="ck_shared_expenses_payer",
        ),
    )
    op.create_index("ix_shared_expenses_user_id", "shared_expenses", ["user_id"])
    op.create_index("ix_shared_expenses_paid_by_person_id", "shared_expenses", ["paid_by_person_id"])
    op.create_index("ix_shared_expenses_occurred_at", "shared_expenses", ["occurred_at"])
    op.create_index("ix_shared_expenses_user_occurred", "shared_expenses", ["user_id", "occurred_at"])

    op.create_table(
        "shared_expense_shares",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "shared_expense_id",
            sa.BigInteger(),
            sa.ForeignKey("shared_expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("is_user", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("share_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("share_amount > 0", name="ck_shares_amount_positive"),
        # Un participante es el usuario O una persona, nunca ambos.
        sa.CheckConstraint(
            "(is_user AND person_id IS NULL) OR (NOT is_user AND person_id IS NOT NULL)",
            name="ck_shares_participant_kind",
        ),
    )
    op.create_index("ix_shares_shared_expense_id", "shared_expense_shares", ["shared_expense_id"])
    op.create_index("ix_shares_person_id", "shared_expense_shares", ["person_id"])
    # El usuario solo una vez por gasto; cada persona solo una vez por gasto.
    op.create_index(
        "ux_shares_user",
        "shared_expense_shares",
        ["shared_expense_id"],
        unique=True,
        postgresql_where=sa.text("is_user"),
        sqlite_where=sa.text("is_user"),
    )
    op.create_index(
        "ux_shares_person",
        "shared_expense_shares",
        ["shared_expense_id", "person_id"],
        unique=True,
        postgresql_where=sa.text("person_id IS NOT NULL"),
        sqlite_where=sa.text("person_id IS NOT NULL"),
    )

    op.create_table(
        "shared_expense_payments",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "shared_expense_id",
            sa.BigInteger(),
            sa.ForeignKey("shared_expenses.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "person_id",
            sa.BigInteger(),
            sa.ForeignKey("people.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("is_user", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        sa.CheckConstraint(
            "(is_user AND person_id IS NULL) OR (NOT is_user AND person_id IS NOT NULL)",
            name="ck_payments_payer_kind",
        ),
    )
    op.create_index("ix_payments_shared_expense_id", "shared_expense_payments", ["shared_expense_id"])
    op.create_index("ix_payments_person_id", "shared_expense_payments", ["person_id"])
    op.create_index(
        "ux_payments_user",
        "shared_expense_payments",
        ["shared_expense_id"],
        unique=True,
        postgresql_where=sa.text("is_user"),
        sqlite_where=sa.text("is_user"),
    )
    op.create_index(
        "ux_payments_person",
        "shared_expense_payments",
        ["shared_expense_id", "person_id"],
        unique=True,
        postgresql_where=sa.text("person_id IS NOT NULL"),
        sqlite_where=sa.text("person_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_payments_person", table_name="shared_expense_payments")
    op.drop_index("ux_payments_user", table_name="shared_expense_payments")
    op.drop_index("ix_payments_person_id", table_name="shared_expense_payments")
    op.drop_index("ix_payments_shared_expense_id", table_name="shared_expense_payments")
    op.drop_table("shared_expense_payments")

    op.drop_index("ux_shares_person", table_name="shared_expense_shares")
    op.drop_index("ux_shares_user", table_name="shared_expense_shares")
    op.drop_index("ix_shares_person_id", table_name="shared_expense_shares")
    op.drop_index("ix_shares_shared_expense_id", table_name="shared_expense_shares")
    op.drop_table("shared_expense_shares")

    op.drop_index("ix_shared_expenses_user_occurred", table_name="shared_expenses")
    op.drop_index("ix_shared_expenses_occurred_at", table_name="shared_expenses")
    op.drop_index("ix_shared_expenses_paid_by_person_id", table_name="shared_expenses")
    op.drop_index("ix_shared_expenses_user_id", table_name="shared_expenses")
    op.drop_table("shared_expenses")

    op.drop_index("ix_debts_user_status", table_name="debts")
    op.drop_index("ix_debts_due_at", table_name="debts")
    op.drop_index("ix_debts_person_id", table_name="debts")
    op.drop_index("ix_debts_user_id", table_name="debts")
    op.drop_table("debts")

    op.drop_index("ix_people_user_normalized", table_name="people")
    op.drop_index("ix_people_user_id", table_name="people")
    op.drop_table("people")
