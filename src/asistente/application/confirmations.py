"""Servicio de aplicación: confirmaciones genéricas reutilizables.

Flujo:
1. LLM usa una tool que requiere confirmación → el orquestador crea una
   confirmación pendiente y responde al usuario con la propuesta.
2. El usuario dice "sí" → `confirm()` ejecuta el payload sobre la tool y
   marca la confirmación como confirmada.
3. El usuario dice "no" → `cancel()` la descarta.
4. Si expira antes → `expire_overdue()` la marca como expirada.
"""

from __future__ import annotations

from asistente.persistence.repositories import ConfirmationRepository
from asistente.shared.timeutil import utc_now

OPEN = "open"
CONFIRMED = "confirmed"
CANCELLED = "cancelled"
EXPIRED = "expired"

_YES = {"sí", "si", "yes", "vale", "ok", "de acuerdo", "adelante", "confirmo", "confirmado", "por favor"}
_NO = {"no", "cancelar", "cancel", "cancelado", "nada", "ni modo", "oops"}


def classify_user_intent(text: str) -> str | None:
    """Clasifica texto corto del usuario como 'sí', 'no' o None (inconcluso)."""
    t = text.strip().lower()
    if t in _YES:
        return "yes"
    if t in _NO:
        return "no"
    return None


class ConfirmationService:
    def __init__(self, repo: ConfirmationRepository, *, ttl_minutes: int = 10) -> None:
        self._repo = repo
        self._ttl_minutes = ttl_minutes

    async def create_pending(
        self,
        *,
        user_id: int,
        topic: str,
        payload: dict,
        proposed_reply: str,
    ) -> dict:
        """Persiste la confirmación y devuelve un dict serializable para el LLM."""
        now = utc_now()
        from datetime import timedelta

        expires_at = now + timedelta(minutes=self._ttl_minutes)
        row = await self._repo.create(
            user_id=user_id,
            topic=topic,
            payload=payload,
            proposed_reply=proposed_reply,
            expires_at=expires_at,
        )
        await self._repo.expire_overdue(user_id, now)
        return {"id": row.id, "topic": row.topic, "expires_at": expires_at.isoformat()}

    async def find_open(self, user_id: int) -> dict | None:
        now = utc_now()
        await self._repo.expire_overdue(user_id, now)
        row = await self._repo.find_open(user_id)
        if row is None:
            return None
        return {
            "id": row.id,
            "topic": row.topic,
            "payload": row.payload,
            "proposed_reply": row.proposed_reply,
            "expires_at": row.expires_at.isoformat(),
        }

    async def confirm(self, user_id: int, confirmation_id: int) -> dict | None:
        now = utc_now()
        ok = await self._repo.set_status(user_id, confirmation_id, CONFIRMED, now)
        if not ok:
            return None
        return {"status": CONFIRMED}

    async def cancel(self, user_id: int, confirmation_id: int) -> bool:
        now = utc_now()
        return await self._repo.set_status(user_id, confirmation_id, CANCELLED, now)
