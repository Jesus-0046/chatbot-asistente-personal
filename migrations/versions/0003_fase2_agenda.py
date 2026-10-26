"""Fase 2: tablas de agenda y recordatorios.

Tablas creadas:
- recurrence_rules:  reglas de recurrencia reutilizables (eventos/tareas)
- events:            citas/reuniones/cumpleaños/fechas importantes
- tasks:             tareas independientes con estado
- reminders:         recordatorios persistentes (a evento/tarea o sueltos)

Aislamiento por usuario: todas las tablas llevan `user_id` FK a `users.id`.
No hay entidades financieras (llegan en Fase 3+).

Revision ID: 0003_fase2_agenda
Revises: 0002_fase1_conversation
Create Date: 2026-09-14

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_fase2_agenda"
down_revision: str | None = "0002_fase1_conversation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurrence_rules",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("freq", sa.String(16), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("days_of_week", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_recurrence_rules_user_id", "recurrence_rules", ["user_id"])

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False, server_default="event"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("all_day", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("location", sa.String(300), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="scheduled"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("person", sa.String(200), nullable=True),
        sa.Column(
            "recurrence_id",
            sa.BigInteger(),
            sa.ForeignKey("recurrence_rules.id", ondelete="SET NULL"),
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
    )
    op.create_index("ix_events_user_id", "events", ["user_id"])
    op.create_index("ix_events_start_at", "events", ["start_at"])
    op.create_index("ix_events_recurrence_id", "events", ["recurrence_id"])
    op.create_index("ix_events_user_start", "events", ["user_id", "start_at"])

    op.create_table(
        "tasks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column(
            "recurrence_id",
            sa.BigInteger(),
            sa.ForeignKey("recurrence_rules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
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
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])
    op.create_index("ix_tasks_due_at", "tasks", ["due_at"])
    op.create_index("ix_tasks_recurrence_id", "tasks", ["recurrence_id"])
    op.create_index("ix_tasks_user_status", "tasks", ["user_id", "status"])

    op.create_table(
        "reminders",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            sa.ForeignKey("events.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "task_id",
            sa.BigInteger(),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False, server_default="Europe/Madrid"),
        sa.Column("offset_minutes", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_reminders_user_id", "reminders", ["user_id"])
    op.create_index("ix_reminders_event_id", "reminders", ["event_id"])
    op.create_index("ix_reminders_task_id", "reminders", ["task_id"])
    op.create_index("ix_reminders_remind_at", "reminders", ["remind_at"])
    op.create_index("ix_reminders_user_status", "reminders", ["user_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_reminders_user_status", table_name="reminders")
    op.drop_index("ix_reminders_remind_at", table_name="reminders")
    op.drop_index("ix_reminders_task_id", table_name="reminders")
    op.drop_index("ix_reminders_event_id", table_name="reminders")
    op.drop_index("ix_reminders_user_id", table_name="reminders")
    op.drop_table("reminders")
    op.drop_index("ix_tasks_user_status", table_name="tasks")
    op.drop_index("ix_tasks_recurrence_id", table_name="tasks")
    op.drop_index("ix_tasks_due_at", table_name="tasks")
    op.drop_index("ix_tasks_user_id", table_name="tasks")
    op.drop_table("tasks")
    op.drop_index("ix_events_user_start", table_name="events")
    op.drop_index("ix_events_recurrence_id", table_name="events")
    op.drop_index("ix_events_start_at", table_name="events")
    op.drop_index("ix_events_user_id", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_recurrence_rules_user_id", table_name="recurrence_rules")
    op.drop_table("recurrence_rules")
