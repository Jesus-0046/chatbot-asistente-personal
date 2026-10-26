"""Fase 2.1: claim atómico de recordatorios.

Añade a `reminders`:
- `claimed_at`: momento en que el poller reclama el recordatorio (lease).
  Con el estado `processing` permite que dos workers nunca envíen el mismo
  recordatorio y que un lease expirado (proceso muerto) se recupere por otro
  worker sin depender solo del lock SQL de la transacción (`SKIP LOCKED`).

Migración aditiva: no toca filas existentes (`claimed_at` es NULL → el
primer `claim_due` que las reclame les pone el valor).

Revision ID: 0004_fase2_reminder_claim
Revises: 0003_fase2_agenda
Create Date: 2026-09-15

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_fase2_reminder_claim"
down_revision: str | None = "0003_fase2_agenda"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reminders",
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_reminders_claimed_at", "reminders", ["claimed_at"])


def downgrade() -> None:
    op.drop_index("ix_reminders_claimed_at", table_name="reminders")
    op.drop_column("reminders", "claimed_at")
