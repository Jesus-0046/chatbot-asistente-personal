"""Confirmaciones (Fase 10.0): /api/v1/confirmations.

El endpoint lee la confirmación abierta y permite confirmarla/cancelarla; la
EJECUCIÓN del payload asociado sigue siendo responsabilidad del orquestador
(proceso del chat), no de esta capa.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json

router = APIRouter(tags=["confirmations"])


@router.get("/confirmations")
async def get_open_confirmation(current: CurrentUserDeps, services: ServicesDeps) -> dict:
    found = await services.confirmations.find_open(current.user_id)
    if found is None:
        return {"confirmation": None}
    return dict(deep_json({"confirmation": found}))


@router.post("/confirmations/{confirmation_id}/confirm")
async def confirm_confirmation(confirmation_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    result = await services.confirmations.confirm(current.user_id, confirmation_id)
    if result is None:
        raise HTTPException(status_code=404, detail="La confirmación no existe o ya no está abierta.")
    return dict(deep_json(result))


@router.post("/confirmations/{confirmation_id}/cancel")
async def cancel_confirmation(confirmation_id: int, current: CurrentUserDeps, services: ServicesDeps) -> dict:
    cancelled = await services.confirmations.cancel(current.user_id, confirmation_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="La confirmación no existe o ya no está abierta.")
    return {"status": "cancelled"}
