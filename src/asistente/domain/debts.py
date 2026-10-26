"""Servicio de dominio de deudas/préstamos personales (Fase 3.4).

Una deuda relaciona al usuario con una persona: "Juan me debe 100" o "yo le
debo 50 a María". NO es un gasto/ingreso: no toca cuentas, saldos,
presupuestos, ahorro ni patrimonio. Es una relación financiera interna que se
maneja por separate (el usuario mantiene el historial de quién debe a quién).

Reglas:
- `direction` obligatoria: `owed_to_user` (me deben) o `user_owes` (yo debo).
- `amount` siempre > 0 (Decimal). `remaining_amount` = `amount` al crearse.
- Liquidación: una sola UPDATE → `remaining_amount=0`, `status=settled`.
  - Pago parcial **NO** se implementa: se dice al usuario "solo puedo liquidar
    deudas completas; registra la liquidación como gasto e ingreso por
    separado si es necesario" (especialización pendiente para futuro).
  - Si ya está liquidada → error estructurado (no es idempotente).
- Las personas deben existir y estar activas (ON DELETE RESTRICT en BD).
"""

from __future__ import annotations

from decimal import Decimal

from asistente.domain.currencies import normalize_currency
from asistente.persistence.models import Debt, Person
from asistente.persistence.repositories import DebtRepository, PersonRepository
from asistente.shared.errors import NotFoundError

VALID_DIRECTIONS = {"owed_to_user", "user_owes"}


class DebtService:
    def __init__(self, debt_repo: DebtRepository, person_repo: PersonRepository) -> None:
        self._debts = debt_repo
        self._people = person_repo

    async def _ensure_person(self, user_id: int, person_id: int) -> Person:
        person = await self._people.find_one(user_id, person_id)
        if person is None:
            raise NotFoundError("La persona no existe o no te pertenece.")
        return person

    async def create(
        self,
        *,
        user_id: int,
        person_id: int,
        direction: str,
        amount: Decimal,
        reason: str | None = None,
        due_at=None,
    ) -> Debt:
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"Dirección no válida: '{direction}'. Usa 'owed_to_user' (me deben) o 'user_owes' (yo debo)."
            )
        if amount <= 0:
            raise ValueError("El importe de la deuda debe ser mayor que 0.")
        await self._ensure_person(user_id, person_id)
        currency = normalize_currency(None)
        return await self._debts.create(
            user_id=user_id,
            person_id=person_id,
            direction=direction,
            amount=amount,
            remaining_amount=amount,
            currency=currency,
            reason=(reason or "").strip()[:300] or None,
            due_at=due_at,
        )

    async def settle(self, user_id: int, debt_id: int) -> Debt:
        """Liquida completamente una deuda abierta."""
        debt = await self._debts.find_one(user_id, debt_id)
        if debt is None:
            raise NotFoundError("La deuda no existe o no te pertenece.")
        if debt.status != "open":
            raise ValueError(
                "La deuda ya está liquidada o cancelada."
                if debt.status == "settled"
                else "La deuda está cancelada y no se puede liquidar."
            )
        updated = await self._debts.settle(user_id, debt_id)
        assert updated is not None
        return updated

    async def get_status(self, user_id: int, *, person_id: int | None = None) -> dict:
        """Estado consolidado de las deudas abiertas del usuario.

        - `person_id=None` → TODAS las deudas abiertas del usuario.
        - `person_id` dado  → solo las deudas abiertas de esa persona
          (reutiliza `list_for_person`, que además valida que la persona
          existe y pertenece al usuario).

        Retorna:
        - debts: lista de deudas agrupadas por persona.
        - total_owed_to_me: sum(remaining) de deudas donde direction=owed_to_user.
        - total_i_owe:      sum(remaining) de deudas donde direction=user_owes.
        """
        if person_id is not None:
            all_debts = await self.list_for_person(user_id, person_id, status="open")
        else:
            all_debts = await self._debts.list_for_user(user_id, status="open", limit=200)
        by_person: dict[int, list[Debt]] = {}
        total_owed_to_me = Decimal("0.00")
        total_i_owe = Decimal("0.00")
        for debt in all_debts:
            by_person.setdefault(debt.person_id, []).append(debt)
            remaining = Decimal(str(debt.remaining_amount))
            if debt.direction == "owed_to_user":
                total_owed_to_me += remaining
            else:
                total_i_owe += remaining

        result_people = []
        for person_id, debts in sorted(by_person.items()):
            person = await self._people.find_one(user_id, person_id)
            person_name = person.name if person else f"Persona #{person_id}"
            for d in debts:
                result_people.append(
                    {
                        "debt_id": d.id,
                        "person": person_name,
                        "person_id": person_id,
                        "direction": d.direction,
                        "remaining": Decimal(str(d.remaining_amount)),
                        "currency": d.currency,
                        "reason": d.reason,
                        "due_at": d.due_at,
                    }
                )

        return {
            "debts": result_people,
            "total_owed_to_me": total_owed_to_me,
            "total_i_owe": total_i_owe,
        }

    async def list_for_person(self, user_id: int, person_id: int, *, status: str | None = None) -> list[Debt]:
        await self._ensure_person(user_id, person_id)
        return await self._debts.list_for_user(user_id, person_id=person_id, status=status, limit=200)
