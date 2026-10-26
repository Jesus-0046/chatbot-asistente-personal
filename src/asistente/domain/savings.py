"""Servicio de dominio de objetivos de ahorro (Fase 3.2).

El ahorro REAL son los saldos de cuentas type=savings. Los objetivos
de ahorro son metas independientes cuyo progreso se calcula a partir
de sus contribuciones.

Política de contribuciones:
- Objetivo activo: contribuciones permitidas.
- Objetivo completado (contributed >= target): contribuciones permitidas
  (remaining = 0, progress puede superar 100%).
- Objetivo cancelado: NO se aceptan nuevas contribuciones.

Una contribución NO crea automáticamente gasto ni transferencia. Si se
vincula a una transferencia (transfer_id), la transferencia es el
movimiento financiero real y la contribución es metadato.

Reglas de lectura (Fase 3.2/3.3.1):
- `get_goal_status` es UNA LECTURA: NO escribe la BD. El estado reportado es
  el DETERMINISTA calculado a partir de `status` persistido, el target y el
  total contribuido (active | completed | cancelled). El `status` persistido
  solo cambia mediante una operación de dominio explícita (`cancel_goal`).

Reglas de vinculación de contribuciones a transferencias:
- La transferencia debe existir y pertenecer al usuario.
- Debe estar en estado válido (`posted`), no cancelada.
- Las cuentas origen y destino deben existir y pertenecer al usuario.
- La cuenta DESTINO debe ser de tipo `savings` (el movimiento va hacia una
  cuenta de ahorro). Rechaza `bank → cash`, `bank → bank`, etc.
- El importe debe coincidir EXACTAMENTE con el de la transferencia (Decimal).
- Una transferencia solo se puede vincular a una UNICA contribución.
"""

from __future__ import annotations

from decimal import Decimal

from asistente.domain.currencies import normalize_currency
from asistente.persistence.models import SavingsContribution, SavingsGoal
from asistente.persistence.repositories import (
    AccountRepository,
    SavingsContributionRepository,
    SavingsGoalRepository,
    TransferRepository,
)
from asistente.shared.errors import AuthorizationError, NotFoundError
from asistente.shared.timeutil import utc_now

VALID_TRANSFER_STATUS = "posted"


class SavingsGoalService:
    def __init__(
        self,
        goal_repo: SavingsGoalRepository,
        contribution_repo: SavingsContributionRepository,
        transfer_repo: TransferRepository,
        account_repo: AccountRepository,
    ) -> None:
        self._goals = goal_repo
        self._contributions = contribution_repo
        self._transfers = transfer_repo
        self._accounts = account_repo

    async def create_goal(
        self,
        *,
        user_id: int,
        name: str,
        target_amount: Decimal,
        deadline=None,
    ) -> SavingsGoal:
        if target_amount <= 0:
            raise ValueError("El importe del objetivo debe ser mayor que 0.")
        currency = normalize_currency(None)
        return await self._goals.create(
            user_id=user_id,
            name=name.strip()[:200],
            target_amount=target_amount,
            currency=currency,
            deadline=deadline,
        )

    async def cancel_goal(self, user_id: int, goal_id: int) -> SavingsGoal:
        goal = await self._goals.find_one(user_id, goal_id)
        if goal is None:
            raise NotFoundError("El objetivo de ahorro no existe o no te pertenece.")
        if goal.status == "cancelled":
            raise ValueError("El objetivo ya está cancelado.")
        updated = await self._goals.update(user_id, goal_id, status="cancelled")
        assert updated is not None
        return updated

    async def list_goals(self, user_id: int, *, status: str | None = None) -> list[SavingsGoal]:
        return await self._goals.list_for_user(user_id, status=status or "active")

    async def get_goal_status(self, user_id: int, goal_id: int) -> dict:
        """Estado DETERMINISTA calculado. LECTURA pura (no escribe la BD)."""
        goal = await self._goals.find_one(user_id, goal_id)
        if goal is None:
            raise NotFoundError("El objetivo de ahorro no existe o no te pertenece.")
        contributed = await self._contributions.sum_for_goal(user_id, goal_id)
        target = Decimal(str(goal.target_amount))
        remaining = max(target - contributed, Decimal("0.00"))
        progress = (contributed / target * 100) if target > 0 else Decimal("0.00")
        if goal.status == "cancelled":
            status = "cancelled"
        elif contributed >= target:
            status = "completed"
        else:
            status = "active"
        return {
            "goal": goal,
            "contributed": contributed,
            "remaining": remaining,
            "progress": progress,
            "status": status,
        }

    async def record_contribution(
        self,
        *,
        user_id: int,
        goal_id: int,
        amount: Decimal,
        contributed_at=None,
        note: str | None = None,
        transfer_id: int | None = None,
    ) -> SavingsContribution:
        if amount <= 0:
            raise ValueError("El importe de la contribución debe ser mayor que 0.")
        goal = await self._goals.find_one(user_id, goal_id)
        if goal is None:
            raise NotFoundError("El objetivo de ahorro no existe o no te pertenece.")
        if goal.status == "cancelled":
            raise AuthorizationError("No se pueden registrar contribuciones a un objetivo cancelado.")
        currency = normalize_currency(None)
        if transfer_id is not None:
            await self._validate_transfer_link(user_id, transfer_id, amount)
        return await self._contributions.create(
            user_id=user_id,
            goal_id=goal_id,
            amount=amount,
            currency=currency,
            contributed_at=contributed_at or utc_now(),
            note=note,
            transfer_id=transfer_id,
        )

    async def _validate_transfer_link(self, user_id: int, transfer_id: int, amount: Decimal) -> None:
        transfer = await self._transfers.find_one(user_id, transfer_id)
        if transfer is None:
            raise NotFoundError("La transferencia no existe o no te pertenece.")
        if transfer.status != VALID_TRANSFER_STATUS:
            raise ValueError("La transferencia vinculada no está en estado 'posted'.")
        transfer_amount = Decimal(str(transfer.amount))
        if transfer_amount != Decimal(str(amount)):
            raise ValueError(
                f"El importe de la contribución ({amount}) no coincide con el de la transferencia ({transfer_amount})."
            )
        from_account = await self._accounts.find_one(user_id, transfer.from_account_id)
        if from_account is None:
            raise NotFoundError("La cuenta de origen de la transferencia no existe o no te pertenece.")
        to_account = await self._accounts.find_one(user_id, transfer.to_account_id)
        if to_account is None:
            raise NotFoundError("La cuenta de destino de la transferencia no existe o no te pertenece.")
        if from_account.id == to_account.id:
            raise ValueError("La transferencia vinculada no mueve dinero entre cuentas distintas.")
        if to_account.type != "savings":
            raise ValueError("La transferencia vinculada debe llegar a una cuenta de tipo ahorro (savings).")
        existing = await self._contributions.find_by_transfer(user_id, transfer_id)
        if existing is not None:
            raise ValueError(
                f"La transferencia {transfer_id} ya está vinculada a otra contribución "
                "(cada transferencia solo puede vincularse una vez)."
            )

    async def list_contributions(self, user_id: int, goal_id: int) -> list[SavingsContribution]:
        goal = await self._goals.find_one(user_id, goal_id)
        if goal is None:
            raise NotFoundError("El objetivo de ahorro no existe o no te pertenece.")
        return await self._contributions.list_for_goal(user_id, goal_id)
