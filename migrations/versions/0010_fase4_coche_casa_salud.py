"""Fase 4: coche, casa y salud y hábitos.

Tablas creadas:
- vehicles:                vehículo del usuario (nombre normalizado, matrícula,
                           odómetro actual en km). Nunca se borra físicamente
                           si tiene histórico: `active=False`.
- fuel_entries:            repostajes. `amount` (€) es OBLIGATORIO y cada
                           repostaje crea EXACTAMENTE UNA transacción de gasto
                           (FK `transaction_id`). `liters`/`odometer` opcionales.
- vehicle_maintenances:    mantenimiento/revisión/ITV (la ITV se registra con
                           `maintenance_type='itv'` y próximas fechas). `cost`
                           opcional: si existe, crea una transacción vinculada.
- vehicle_insurances:      seguro del vehículo: registro INFORMATIVO. No es un
                           movimiento financiero (sin `transaction_id`).
- homes:                   vivienda del usuario.
- home_maintenances:       mantenimiento de vivienda (revisiones, reparaciones).
                           `occurred_at`/`cost` → `done`; en otro caso `pending`
                           (aparece en próximas). Con `cost` crea una transacción
                           vinculada.
- habits:                  hábito (agua, ejercicio...) con objetivo por periodo.
- habit_logs:              registros de hábito (contado de cumplimiento).

Borrado:
- `fuel_entries.vehicle_id`, `vehicle_maintenances.vehicle_id`,
  `vehicle_insurances.vehicle_id`, `home_maintenances.home_id` y
  `habit_logs.habit_id`: ON DELETE RESTRICT (nunca se borra histórico).
- `*_transaction_id`: ON DELETE SET NULL hacia `transactions` (la transacción
  vinculada se desliga, no se arrastra).
- `user_id`: ON DELETE CASCADE en todas las tablas.

Otros cambios:
- `events.category` (String 32 nullable): etiqueta libre de evento (Fase 4 usa
  'salud' para citas médicas; un cambio NO destructivo sobre una tabla existente).

Revision ID: 0010_fase4_coche_casa_salud
Revises: 0009_fase3_4_personas_deudas_gastos_compartidos
Create Date: 2026-09-17

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_fase4_coche_casa_salud"
down_revision: str | None = "0009_fase3_4_personas_deudas_gastos_compartidos"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("category", sa.String(32), nullable=True))

    op.create_table(
        "vehicles",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("brand", sa.String(200), nullable=True),
        sa.Column("model", sa.String(200), nullable=True),
        sa.Column("plate", sa.String(20), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("color", sa.String(50), nullable=True),
        sa.Column("fuel_type", sa.String(32), nullable=True),
        sa.Column("current_odometer", sa.Numeric(12, 1), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "year IS NULL OR year BETWEEN 1900 AND 2100",
            name="ck_vehicles_year_range",
        ),
        sa.CheckConstraint(
            "current_odometer IS NULL OR current_odometer >= 0",
            name="ck_vehicles_odometer",
        ),
    )
    op.create_index("ix_vehicles_user_id", "vehicles", ["user_id"])
    op.create_index("ix_vehicles_user_normalized", "vehicles", ["user_id", "normalized_name"])
    op.create_index("ix_vehicles_user_active", "vehicles", ["user_id", "active"])

    op.create_table(
        "fuel_entries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("liters", sa.Numeric(10, 2), nullable=True),
        sa.Column("odometer", sa.Numeric(12, 1), nullable=True),
        sa.Column("fuel_type", sa.String(32), nullable=True),
        sa.Column("full_tank", sa.Boolean(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "transaction_id",
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
        sa.CheckConstraint("amount > 0", name="ck_fuel_entries_amount_positive"),
        sa.CheckConstraint(
            "liters IS NULL OR liters > 0",
            name="ck_fuel_entries_liters_positive",
        ),
        sa.CheckConstraint(
            "odometer IS NULL OR odometer >= 0",
            name="ck_fuel_entries_odometer",
        ),
    )
    op.create_index("ix_fuel_entries_user_id", "fuel_entries", ["user_id"])
    op.create_index("ix_fuel_entries_vehicle_id", "fuel_entries", ["vehicle_id"])
    op.create_index("ix_fuel_entries_occurred_at", "fuel_entries", ["occurred_at"])
    op.create_index("ix_fuel_entries_transaction_id", "fuel_entries", ["transaction_id"])
    op.create_index("ix_fuel_entries_user_occurred", "fuel_entries", ["user_id", "occurred_at"])

    op.create_table(
        "vehicle_maintenances",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("maintenance_type", sa.String(32), nullable=False, server_default="revision"),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("odometer", sa.Numeric(12, 1), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_due_odometer", sa.Numeric(12, 1), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "transaction_id",
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
        sa.CheckConstraint(
            "cost IS NULL OR cost > 0",
            name="ck_vehicle_maintenances_cost_positive",
        ),
        sa.CheckConstraint(
            "odometer IS NULL OR odometer >= 0",
            name="ck_vehicle_maintenances_odometer",
        ),
        sa.CheckConstraint(
            "maintenance_type IN ('revision', 'itv', 'reparacion', 'otro')",
            name="ck_vehicle_maintenances_type",
        ),
    )
    op.create_index("ix_vehicle_maintenances_user_id", "vehicle_maintenances", ["user_id"])
    op.create_index("ix_vehicle_maintenances_vehicle_id", "vehicle_maintenances", ["vehicle_id"])
    op.create_index("ix_vehicle_maintenances_occurred_at", "vehicle_maintenances", ["occurred_at"])
    op.create_index("ix_vehicle_maintenances_next_due_at", "vehicle_maintenances", ["next_due_at"])
    op.create_index("ix_vehicle_maintenances_transaction_id", "vehicle_maintenances", ["transaction_id"])
    op.create_index(
        "ix_vehicle_maintenances_user_occurred",
        "vehicle_maintenances",
        ["user_id", "occurred_at"],
    )

    op.create_table(
        "vehicle_insurances",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "vehicle_id",
            sa.BigInteger(),
            sa.ForeignKey("vehicles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("company", sa.String(200), nullable=True),
        sa.Column("policy_number", sa.String(100), nullable=True),
        sa.Column("insurance_type", sa.String(64), nullable=True),
        sa.Column("coverage", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("billing_period", sa.String(16), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "amount IS NULL OR amount > 0",
            name="ck_vehicle_insurances_amount_positive",
        ),
        sa.CheckConstraint(
            "billing_period IS NULL OR "
            "billing_period IN ('one_time', 'monthly', 'quarter_year', 'half_year', 'yearly')",
            name="ck_vehicle_insurances_billing_period",
        ),
    )
    op.create_index("ix_vehicle_insurances_user_id", "vehicle_insurances", ["user_id"])
    op.create_index("ix_vehicle_insurances_vehicle_id", "vehicle_insurances", ["vehicle_id"])
    op.create_index("ix_vehicle_insurances_end_date", "vehicle_insurances", ["end_date"])
    op.create_index("ix_vehicle_insurances_user_active", "vehicle_insurances", ["user_id", "active"])

    op.create_table(
        "homes",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("home_type", sa.String(32), nullable=True),
        sa.Column("address", sa.String(300), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
    op.create_index("ix_homes_user_id", "homes", ["user_id"])
    op.create_index("ix_homes_user_normalized", "homes", ["user_id", "normalized_name"])
    op.create_index("ix_homes_user_active", "homes", ["user_id", "active"])

    op.create_table(
        "home_maintenances",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "home_id",
            sa.BigInteger(),
            sa.ForeignKey("homes.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("maintenance_type", sa.String(32), nullable=False, server_default="revision"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost", sa.Numeric(12, 2), nullable=True),
        sa.Column("currency", sa.String(8), nullable=False, server_default="EUR"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "transaction_id",
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
        sa.CheckConstraint(
            "cost IS NULL OR cost > 0",
            name="ck_home_maintenances_cost_positive",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'done')",
            name="ck_home_maintenances_status",
        ),
        sa.CheckConstraint(
            "maintenance_type IN ('revision', 'reparacion', 'otro')",
            name="ck_home_maintenances_type",
        ),
    )
    op.create_index("ix_home_maintenances_user_id", "home_maintenances", ["user_id"])
    op.create_index("ix_home_maintenances_home_id", "home_maintenances", ["home_id"])
    op.create_index("ix_home_maintenances_occurred_at", "home_maintenances", ["occurred_at"])
    op.create_index("ix_home_maintenances_next_due_at", "home_maintenances", ["next_due_at"])
    op.create_index("ix_home_maintenances_transaction_id", "home_maintenances", ["transaction_id"])
    op.create_index(
        "ix_home_maintenances_user_occurred",
        "home_maintenances",
        ["user_id", "occurred_at"],
    )

    op.create_table(
        "habits",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("normalized_name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=True),
        sa.Column("target_per_period", sa.Numeric(10, 2), nullable=True),
        sa.Column("period", sa.String(16), nullable=False, server_default="week"),
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
        sa.CheckConstraint(
            "target_per_period IS NULL OR target_per_period > 0",
            name="ck_habits_target_positive",
        ),
        sa.CheckConstraint("period IN ('day', 'week', 'month', 'year')", name="ck_habits_period"),
    )
    op.create_index("ix_habits_user_id", "habits", ["user_id"])
    op.create_index("ix_habits_user_normalized", "habits", ["user_id", "normalized_name"])
    op.create_index("ix_habits_user_active", "habits", ["user_id", "active"])

    op.create_table(
        "habit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "habit_id",
            sa.BigInteger(),
            sa.ForeignKey("habits.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("value", sa.Numeric(10, 2), nullable=True),
        sa.Column("logged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("value IS NULL OR value > 0", name="ck_habit_logs_value_positive"),
    )
    op.create_index("ix_habit_logs_user_id", "habit_logs", ["user_id"])
    op.create_index("ix_habit_logs_habit_id", "habit_logs", ["habit_id"])
    op.create_index("ix_habit_logs_logged_at", "habit_logs", ["logged_at"])
    op.create_index("ix_habit_logs_user_logged", "habit_logs", ["user_id", "logged_at"])


def downgrade() -> None:
    op.drop_index("ix_habit_logs_user_logged", table_name="habit_logs")
    op.drop_index("ix_habit_logs_logged_at", table_name="habit_logs")
    op.drop_index("ix_habit_logs_habit_id", table_name="habit_logs")
    op.drop_index("ix_habit_logs_user_id", table_name="habit_logs")
    op.drop_table("habit_logs")

    op.drop_index("ix_habits_user_active", table_name="habits")
    op.drop_index("ix_habits_user_normalized", table_name="habits")
    op.drop_index("ix_habits_user_id", table_name="habits")
    op.drop_table("habits")

    op.drop_index("ix_home_maintenances_user_occurred", table_name="home_maintenances")
    op.drop_index("ix_home_maintenances_transaction_id", table_name="home_maintenances")
    op.drop_index("ix_home_maintenances_next_due_at", table_name="home_maintenances")
    op.drop_index("ix_home_maintenances_occurred_at", table_name="home_maintenances")
    op.drop_index("ix_home_maintenances_home_id", table_name="home_maintenances")
    op.drop_index("ix_home_maintenances_user_id", table_name="home_maintenances")
    op.drop_table("home_maintenances")

    op.drop_index("ix_homes_user_active", table_name="homes")
    op.drop_index("ix_homes_user_normalized", table_name="homes")
    op.drop_index("ix_homes_user_id", table_name="homes")
    op.drop_table("homes")

    op.drop_index("ix_vehicle_insurances_user_active", table_name="vehicle_insurances")
    op.drop_index("ix_vehicle_insurances_end_date", table_name="vehicle_insurances")
    op.drop_index("ix_vehicle_insurances_vehicle_id", table_name="vehicle_insurances")
    op.drop_index("ix_vehicle_insurances_user_id", table_name="vehicle_insurances")
    op.drop_table("vehicle_insurances")

    op.drop_index(
        "ix_vehicle_maintenances_user_occurred",
        table_name="vehicle_maintenances",
    )
    op.drop_index("ix_vehicle_maintenances_transaction_id", table_name="vehicle_maintenances")
    op.drop_index("ix_vehicle_maintenances_next_due_at", table_name="vehicle_maintenances")
    op.drop_index("ix_vehicle_maintenances_occurred_at", table_name="vehicle_maintenances")
    op.drop_index("ix_vehicle_maintenances_vehicle_id", table_name="vehicle_maintenances")
    op.drop_index("ix_vehicle_maintenances_user_id", table_name="vehicle_maintenances")
    op.drop_table("vehicle_maintenances")

    op.drop_index("ix_fuel_entries_user_occurred", table_name="fuel_entries")
    op.drop_index("ix_fuel_entries_transaction_id", table_name="fuel_entries")
    op.drop_index("ix_fuel_entries_occurred_at", table_name="fuel_entries")
    op.drop_index("ix_fuel_entries_vehicle_id", table_name="fuel_entries")
    op.drop_index("ix_fuel_entries_user_id", table_name="fuel_entries")
    op.drop_table("fuel_entries")

    op.drop_index("ix_vehicles_user_active", table_name="vehicles")
    op.drop_index("ix_vehicles_user_normalized", table_name="vehicles")
    op.drop_index("ix_vehicles_user_id", table_name="vehicles")
    op.drop_table("vehicles")

    op.drop_column("events", "category")
