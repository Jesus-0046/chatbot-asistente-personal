"""Fase 3: núcleo financiero (cuentas, movimientos y traspasos).

Tablas creadas:
- accounts:     cuentas del usuario (bank/savings/cash/credit_card/other).
                El saldo es DERIVADO (initial_balance + ingresos - gastos
                +- traspasos); la tabla NO guarda el saldo actual.
- transactions: movimiento de gasto (expense) o ingreso (income). `amount`
                siempre positivo; el signo lo da `type`.
- transfers:    traspaso entre dos cuentas del mismo usuario. NO es gasto ni
                ingreso: no cuenta en los totales de gastos.

Reglas de datos:
- Importes en `Numeric(12,2)`, nunca floats.
- Todas las tablas llevan `user_id` FK a `users.id` (aislamiento por usuario).
- `accounts.is_default` admite como máximo UNA fila por usuario -> índice
  único parcial en PostgreSQL `WHERE is_default`. El servicio además la
  mantiene (clear_default/set_default), de modo que el invariante no depende
  solo de la BD.
- Las cuentas con histórico NO se borran: `active=False` (deactivate).
- `transactions.account_id` y los FKs de traspaso van a `accounts.id` con
  `ON DELETE SET NULL` y son NULL-ables: la cuenta puede desactivarse/borrarse
  sin destruir el histórico de movimientos.

Revision ID: 0005_fase3_finanzas
Revises: 0004_fase2_reminder_claim
Create Date: 2026-09-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_fase3_finanzas"
down_revision: str | None = "0004_fase2_reminder_claim"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("initial_balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
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
    op.create_index("ix_accounts_user_id", "accounts", ["user_id"])
    op.create_index("ix_accounts_type", "accounts", ["type"])
    op.create_index(
        "ix_accounts_user_default",
        "accounts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.String(16), nullable=False),
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
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(16), nullable=False, server_default="posted"),
        sa.Column("trip_id", sa.Integer(), nullable=True),
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
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_type", "transactions", ["type"])
    op.create_index("ix_transactions_account_id", "transactions", ["account_id"])
    op.create_index("ix_transactions_merchant", "transactions", ["merchant"])
    op.create_index("ix_transactions_category", "transactions", ["category"])
    op.create_index("ix_transactions_occurred_at", "transactions", ["occurred_at"])
    op.create_index("ix_transactions_user_occurred", "transactions", ["user_id", "occurred_at"])

    op.create_table(
        "transfers",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "from_account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "to_account_id",
            sa.BigInteger(),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="posted"),
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
    op.create_index("ix_transfers_user_id", "transfers", ["user_id"])
    op.create_index("ix_transfers_from_account_id", "transfers", ["from_account_id"])
    op.create_index("ix_transfers_to_account_id", "transfers", ["to_account_id"])
    op.create_index("ix_transfers_occurred_at", "transfers", ["occurred_at"])
    op.create_index("ix_transfers_user_occurred", "transfers", ["user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_transfers_user_occurred", table_name="transfers")
    op.drop_index("ix_transfers_occurred_at", table_name="transfers")
    op.drop_index("ix_transfers_to_account_id", table_name="transfers")
    op.drop_index("ix_transfers_from_account_id", table_name="transfers")
    op.drop_index("ix_transfers_user_id", table_name="transfers")
    op.drop_table("transfers")

    op.drop_index("ix_transactions_user_occurred", table_name="transactions")
    op.drop_index("ix_transactions_occurred_at", table_name="transactions")
    op.drop_index("ix_transactions_category", table_name="transactions")
    op.drop_index("ix_transactions_merchant", table_name="transactions")
    op.drop_index("ix_transactions_account_id", table_name="transactions")
    op.drop_index("ix_transactions_type", table_name="transactions")
    op.drop_index("ix_transactions_user_id", table_name="transactions")
    op.drop_table("transactions")

    op.drop_index("ix_accounts_user_default", table_name="accounts")
    op.drop_index("ix_accounts_type", table_name="accounts")
    op.drop_index("ix_accounts_user_id", table_name="accounts")
    op.drop_table("accounts")
