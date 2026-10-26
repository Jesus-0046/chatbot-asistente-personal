"""baseline vacía

Fase 0: migración inicial vacía que prueba el pipeline de Alembic
(conexión, transacción, `alembic_version`). Los modelos llegan en Fase 1+.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-12

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa  # noqa: F401
from alembic import op  # noqa: F401

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
