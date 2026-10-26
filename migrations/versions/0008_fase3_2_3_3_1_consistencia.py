"""Fase 3.2/3.3.1: consistencia de la vinculación transferencia -> contribución.

Cambio aplicado SOLO a la tabla `savings_contributions` de la Fase 3.2:

- ÍNDICE ÚNICO PARCIAL sobre `transfer_id`: una transferencia real solo puede
  vincularse a UNA contribución en toda la historia del usuario. El predicado
  `transfer_id IS NOT NULL` deja fuera las contribuciones sin vínculo (la gran
  mayoría) y garantiza que las filas vinculadas no colisionen por los multiple
  NULL que permite un índice único normal.

La comprobación en dominio (`SavingsGoalService._validate_transfer_link`) es la
defensa blanda (mensaje claro); este índice es el cinturón de seguridad a nivel
de base de datos. Si se detectan duplicados preexistentes, la migración falla
y hay que reconciliarlos antes (nunca se borra histórico automáticamente).

Revision ID: 0008_fase3_2_3_3_1_consistencia
Revises: 0007_fase3_3_recurrencias_planificados_suscripciones
Create Date: 2026-09-16

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_fase3_2_3_3_1_consistencia"
down_revision: str | None = "0007_fase3_3_recurrencias_planificados_suscripciones"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ux_savings_contributions_transfer_id",
        "savings_contributions",
        ["transfer_id"],
        unique=True,
        postgresql_where=sa.text("transfer_id IS NOT NULL"),
        sqlite_where=sa.text("transfer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ux_savings_contributions_transfer_id", table_name="savings_contributions")
