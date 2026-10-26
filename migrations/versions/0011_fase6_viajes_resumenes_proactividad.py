"""Fase 6: viajes, resúmenes y proactividad controlada.

Tablas creadas:
- trips:                     viaje del usuario (destino, fechas, estado
                             planned|active|completed|cancelled).
- trip_checklist_items:      ítems de checklist del viaje (FK RESTRICT:
                             los ítems no sobreviven a un borrado físico).
- user_proactivity_settings: preferencias de proactividad (opt-in, horas de
                             silencio "HH:MM" en la zona del usuario, límite
                             diario de notificaciones).
- proactive_notifications:   notificación proactiva generada por el poller.
                             La clave única (user_id, kind, dedup_key)
                             impide duplicados; `importance` (1..3) puntúa.

Columnas añadidas y FKs:
- `transactions.trip_id` existía como columna reservada SIN FK (Fase 3). Ahora
  se crea la FK → `trips.id` (ON DELETE SET NULL) y un índice.
- `events.trip_id`, `tasks.trip_id`, `reminders.trip_id`: nueva columna FK →
  `trips.id` (ON DELETE SET NULL) con índice. Así un viaje puede agrupar
  eventos, tareas, recordatorios y movimientos; borrar/cancelar un viaje
  nunca arrastra el histórico (SOLO se desvincula).

Ninguna de las tablas se borra físicamente en la app: viajes se cancelan o
completan (conservan histórico).

Revision ID: 0011_fase6_viajes_resumenes_proactividad
Revises: 0010_fase4_coche_casa_salud
Create Date: 2026-09-18

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_fase6_viajes_resumenes_proactividad"
down_revision: str | None = "0010_fase4_coche_casa_salud"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trips",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("normalized_name", sa.String(300), nullable=False),
        sa.Column("destination", sa.String(300), nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="planned"),
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
            "start_at IS NULL OR end_at IS NULL OR end_at >= start_at",
            name="ck_trips_dates_ordered",
        ),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'completed', 'cancelled')",
            name="ck_trips_status",
        ),
    )
    op.create_index("ix_trips_user_id", "trips", ["user_id"])
    op.create_index("ix_trips_user_normalized", "trips", ["user_id", "normalized_name"])
    op.create_index("ix_trips_user_status", "trips", ["user_id", "status"])
    op.create_index("ix_trips_start_at", "trips", ["start_at"])
    op.create_index("ix_trips_end_at", "trips", ["end_at"])

    op.add_column("events", sa.Column("trip_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_events_trip_id", "events", ["trip_id"])
    op.create_foreign_key("fk_events_trip_id", "events", "trips", ["trip_id"], ["id"], ondelete="SET NULL")

    op.add_column("tasks", sa.Column("trip_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_tasks_trip_id", "tasks", ["trip_id"])
    op.create_foreign_key("fk_tasks_trip_id", "tasks", "trips", ["trip_id"], ["id"], ondelete="SET NULL")

    op.add_column("reminders", sa.Column("trip_id", sa.BigInteger(), nullable=True))
    op.create_index("ix_reminders_trip_id", "reminders", ["trip_id"])
    op.create_foreign_key(
        "fk_reminders_trip_id",
        "reminders",
        "trips",
        ["trip_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_index("ix_transactions_trip_id", "transactions", ["trip_id"])
    op.create_foreign_key(
        "fk_transactions_trip_id",
        "transactions",
        "trips",
        ["trip_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "trip_checklist_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trip_id",
            sa.BigInteger(),
            sa.ForeignKey("trips.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("text", sa.String(500), nullable=False),
        sa.Column("is_done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_trip_checklist_items_user_id", "trip_checklist_items", ["user_id"])
    op.create_index("ix_trip_checklist_items_trip_id", "trip_checklist_items", ["trip_id"])
    op.create_index(
        "ix_trip_checklist_items_user_trip",
        "trip_checklist_items",
        ["user_id", "trip_id"],
    )

    op.create_table(
        "user_proactivity_settings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("quiet_hours_start", sa.String(5), nullable=False, server_default="22:00"),
        sa.Column("quiet_hours_end", sa.String(5), nullable=False, server_default="08:00"),
        sa.Column("daily_limit", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
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
            "daily_limit >= 0 AND daily_limit <= 50",
            name="ck_proactivity_daily_limit",
        ),
    )
    op.create_index("ix_user_proactivity_settings_user_id", "user_proactivity_settings", ["user_id"], unique=True)

    op.create_table(
        "proactive_notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("dedup_key", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "importance >= 1 AND importance <= 3",
            name="ck_proactive_notifications_importance",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'sent', 'skipped')",
            name="ck_proactive_notifications_status",
        ),
    )
    op.create_index(
        "ix_proactive_notifications_user_id",
        "proactive_notifications",
        ["user_id"],
    )
    op.create_index(
        "ix_proactive_notifications_user_kind_dedup",
        "proactive_notifications",
        ["user_id", "kind", "dedup_key"],
        unique=True,
        postgresql_where=sa.text("status != 'skipped'"),
    )
    op.create_index(
        "ix_proactive_notifications_user_status",
        "proactive_notifications",
        ["user_id", "status"],
    )
    op.create_index(
        "ix_proactive_notifications_user_sent_at",
        "proactive_notifications",
        ["user_id", "sent_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_proactive_notifications_user_sent_at", table_name="proactive_notifications")
    op.drop_index("ix_proactive_notifications_user_status", table_name="proactive_notifications")
    op.drop_index(
        "ix_proactive_notifications_user_kind_dedup",
        table_name="proactive_notifications",
    )
    op.drop_index("ix_proactive_notifications_user_id", table_name="proactive_notifications")
    op.drop_table("proactive_notifications")

    op.drop_index("ix_user_proactivity_settings_user_id", table_name="user_proactivity_settings")
    op.drop_table("user_proactivity_settings")

    op.drop_index("ix_trip_checklist_items_user_trip", table_name="trip_checklist_items")
    op.drop_index("ix_trip_checklist_items_trip_id", table_name="trip_checklist_items")
    op.drop_index("ix_trip_checklist_items_user_id", table_name="trip_checklist_items")
    op.drop_table("trip_checklist_items")

    op.drop_constraint("fk_transactions_trip_id", "transactions", type_="foreignkey")
    op.drop_index("ix_transactions_trip_id", table_name="transactions")

    op.drop_constraint("fk_reminders_trip_id", "reminders", type_="foreignkey")
    op.drop_index("ix_reminders_trip_id", table_name="reminders")
    op.drop_column("reminders", "trip_id")

    op.drop_constraint("fk_tasks_trip_id", "tasks", type_="foreignkey")
    op.drop_index("ix_tasks_trip_id", table_name="tasks")
    op.drop_column("tasks", "trip_id")

    op.drop_constraint("fk_events_trip_id", "events", type_="foreignkey")
    op.drop_index("ix_events_trip_id", table_name="events")
    op.drop_column("events", "trip_id")

    op.drop_index("ix_trips_end_at", table_name="trips")
    op.drop_index("ix_trips_start_at", table_name="trips")
    op.drop_index("ix_trips_user_status", table_name="trips")
    op.drop_index("ix_trips_user_normalized", table_name="trips")
    op.drop_index("ix_trips_user_id", table_name="trips")
    op.drop_table("trips")
