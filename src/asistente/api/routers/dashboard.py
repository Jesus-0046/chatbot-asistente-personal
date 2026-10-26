"""Dashboard (Fase 10.0): /api/v1/dashboard.

Compone en una sola respuesta los datos de las secciones de la app (finanzas,
budgets, ahorro, agenda, hábitos, vehículos, hogar y viajes) reutilizando los
servicios de dominio existentes. El dashboard es tolerante a fallos parciais:
si una sección falla se devuelve `None` con la razón, nunca se rompe el bloque.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query

from asistente.api.dependencies import CurrentUserDeps, ServicesDeps
from asistente.api.serializers import deep_json, serialize_many

router = APIRouter(tags=["dashboard"])


async def _safe(
    section: str,
    fn,
):
    import logging

    logger = logging.getLogger("asistente.api.dashboard")
    try:
        return await fn()
    except Exception as exc:  # noqa: BLE001 - el dashboard no debe romperse por una sección
        logger.warning("dashboard_section_failed section=%s error=%s", section, exc)
        return {"error": f"Sección '{section}' no disponible."}


@router.get("/dashboard")
async def get_dashboard(
    current: CurrentUserDeps,
    services: ServicesDeps,
    timezone: str | None = None,
    days: int = Query(default=30, ge=1, le=90),
) -> dict:
    tz = timezone or (current.user.timezone or "Europe/Madrid")
    svc = services

    async def balance():
        return dict(deep_json(await svc.finance.balance_overview(current.user_id)))

    async def financial_overview():
        return dict(deep_json(await svc.intelligence.financial_overview(current.user_id, timezone=tz)))

    async def daily():
        return dict(deep_json(await svc.summaries.daily(current.user_id, timezone=tz)))

    async def upcoming():
        return dict(deep_json(await svc.summaries.upcoming(current.user_id, days=max(1, days), timezone=tz)))

    async def pending():
        return dict(deep_json(await svc.summaries.pending(current.user_id, timezone=tz)))

    async def budgets():
        items = await svc.budgets.list_with_status(current.user_id, timezone=tz)
        return {"items": [deep_json(item) for item in items]}

    async def savings():
        goals = await svc.savings_goals.list_goals(current.user_id)
        result = []
        for goal in goals:
            status = await svc.savings_goals.get_goal_status(current.user_id, goal.id)
            result.append(
                {
                    "id": goal.id,
                    "name": goal.name,
                    "target": str(goal.target_amount),
                    "deadline": deep_json(goal.deadline),
                    "contributed": str(status["contributed"]),
                    "remaining": str(status["remaining"]),
                    "progress": str(status["progress"]),
                    "status": status["status"],
                }
            )
        return {"items": result}

    async def vehicles():
        return dict(deep_json(await svc.vehicles.get_expirations(current.user_id)))

    async def homes():
        return dict(deep_json(await svc.homes.get_upcoming(current.user_id)))

    async def habits():
        return dict(deep_json(await svc.habits.list_with_progress(current.user_id, timezone=tz)))

    async def trips():
        items = await svc.trips.list_trips(current.user_id, limit=20)
        return {"items": serialize_many(items)}

    sections = {
        "balance": balance,
        "financial_overview": financial_overview,
        "daily": daily,
        "upcoming": upcoming,
        "pending": pending,
        "budgets": budgets,
        "savings": savings,
        "vehicles": vehicles,
        "homes": homes,
        "habits": habits,
        "trips": trips,
    }
    results = {}
    for name, fn in sections.items():
        results[name] = await _safe(name, fn)

    return {
        "timezone": tz,
        "as_of": datetime.now(UTC).isoformat(),
        "user_id": current.user_id,
        **results,
    }
