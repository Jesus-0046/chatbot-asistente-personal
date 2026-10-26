"""Servicio de dominio de memoria.

Las reglas de memoria nacen SIEMPRE como propuesta (inactivas) y solo se
activan tras confirmación explícita del usuario. Nunca se activan solas.
"""

from __future__ import annotations

from asistente.persistence.models import MemoryRule
from asistente.persistence.repositories import MemoryRuleRepository
from asistente.shared.errors import BusinessError, NotFoundError
from asistente.shared.timeutil import utc_now

PROPOSED_SOURCE = "proposed"
ACTIVE_SOURCE = "ai"  # activada con confirmación
USER_SOURCE = "user"


class MemoryService:
    def __init__(self, memory_repo: MemoryRuleRepository) -> None:
        self._rules = memory_repo

    async def propose(
        self,
        *,
        user_id: int,
        kind: str,
        key: str,
        value: dict,
        scope: str | None,
    ) -> MemoryRule:
        """Crea la regla como propuesta INACTIVA para que el usuario confirme."""
        return await self._rules.create(
            user_id=user_id,
            kind=kind,
            key=key,
            value=value,
            scope=scope,
            source=PROPOSED_SOURCE,
            active=False,
        )

    async def activate(self, user_id: int, rule_id: int) -> MemoryRule:
        rule = await self._rules.set_active(user_id, rule_id, active=True, confirmed_at=utc_now())
        if rule is None:
            raise NotFoundError("La regla de memoria no existe o no te pertenece.")
        return rule

    async def revoke(self, user_id: int, rule_id: int) -> MemoryRule:
        rule = await self._rules.set_active(user_id, rule_id, active=False)
        if rule is None:
            raise NotFoundError("La regla de memoria no existe o no te pertenece.")
        return rule

    async def delete(self, user_id: int, rule_id: int) -> None:
        if not await self._rules.delete(user_id, rule_id):
            raise NotFoundError("La regla de memoria no existe o no te pertenece.")

    async def active_rules(self, user_id: int) -> list[MemoryRule]:
        return await self._rules.list_for_user(user_id, active_only=True)

    async def ensure_activated_from_proposal(self, user_id: int, rule_id: int) -> MemoryRule:
        """Activa SOLO propuestas (source=proposed). Usado por la tool de
        confirmación: evita reactivar reglas ya revocadas."""
        rule = await self._get(user_id, rule_id)
        if rule.source != PROPOSED_SOURCE:
            raise BusinessError("Esa regla ya se gestionó previamente.")
        return await self.activate(user_id, rule_id)

    async def _get(self, user_id: int, rule_id: int) -> MemoryRule:
        for rule in await self._rules.list_for_user(user_id):
            if rule.id == rule_id:
                return rule
        raise NotFoundError("La regla de memoria no existe o no te pertenece.")
