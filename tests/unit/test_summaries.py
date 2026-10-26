"""Tests de dominio: SummaryService (Fase 6). Resúmenes de solo lectura."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

NOW = datetime(2026, 8, 3, 9, 0, tzinfo=UTC)


async def test_daily_incluye_eventos_tareas_pagos_y_viajes(services) -> None:
    summaries = services["summaries"]
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services["events"].create(user_id=1, title="Reunión", start_at_utc=datetime(2026, 8, 3, 11, tzinfo=UTC))
    await services["tasks"].create(user_id=1, title="Pagar luz", due_at_utc=datetime(2026, 8, 3, 18, tzinfo=UTC))
    await services["tasks"].create(user_id=1, title="Vieja", due_at_utc=datetime(2026, 8, 1, 10, tzinfo=UTC))
    await services["planned"].create(
        user_id=1,
        type="expense",
        title="Gimnasio",
        amount=Decimal("30"),
        planned_at=datetime(2026, 8, 3, 8, tzinfo=UTC),
    )
    trip = await services["trips"].create(
        user_id=1, name="Lisboa", destination="Lisboa", start_at=datetime(2026, 8, 3, 20, tzinfo=UTC)
    )
    await services["trips"].add_checklist_item(1, trip.id, "Pasaporte")

    data = await summaries.daily(1, when_utc=NOW, timezone="UTC")
    assert data["date"] == "2026-08-03"
    assert len(data["events"]) == 1
    assert [t["title"] for t in data["tasks_due_today"]] == ["Pagar luz"]
    assert len(data["planned_due_today"]) == 1
    assert data["trips_starting_today"][0]["name"] == "Lisboa"
    assert data["trips_starting_today"][0]["pending_checklist"] == 1
    assert data["overdue_task_count"] == 1


async def test_weekly_cuenta_y_lista(services) -> None:
    summaries = services["summaries"]
    await services["events"].create(user_id=1, title="Médico", start_at_utc=datetime(2026, 8, 5, 9, tzinfo=UTC))
    await services["tasks"].create(user_id=1, title="Comprar", due_at_utc=datetime(2026, 8, 6, 12, tzinfo=UTC))
    await services["trips"].create(user_id=1, name="Cádiz", start_at=datetime(2026, 8, 7, 8, tzinfo=UTC))

    data = await summaries.weekly(1, when_utc=NOW, timezone="UTC")
    assert data["event_count"] == 1
    assert data["task_count"] == 1
    assert data["trip_count"] == 1


async def test_upcoming_ordena_por_fecha(services) -> None:
    summaries = services["summaries"]
    await services["tasks"].create(user_id=1, title="Tarde", due_at_utc=datetime(2026, 8, 10, 12, tzinfo=UTC))
    await services["events"].create(user_id=1, title="Pronto", start_at_utc=datetime(2026, 8, 4, 9, tzinfo=UTC))

    data = await summaries.upcoming(1, days=30, when_utc=NOW, timezone="UTC")
    assert [i["title"] for i in data["items"]] == ["Pronto", "Tarde"]
    assert data["count"] == 2


async def test_pending_agrega_atrasos_e_importes(services) -> None:
    summaries = services["summaries"]
    await services["accounts"].create(user_id=1, name="Banco", type="bank", initial_balance=Decimal("1000"))
    await services["tasks"].create(user_id=1, title="Atrasada", due_at_utc=datetime(2026, 8, 1, 10, tzinfo=UTC))
    await services["tasks"].create(user_id=1, title="Futura", due_at_utc=datetime(2026, 8, 9, 10, tzinfo=UTC))
    await services["planned"].create(
        user_id=1, type="expense", title="Cuota", amount=Decimal("45.50"), planned_at=datetime(2026, 8, 20, tzinfo=UTC)
    )

    data = await summaries.pending(1, when_utc=NOW, timezone="UTC")
    assert data["task_count"] == 2
    assert data["overdue_task_count"] == 1
    assert data["planned_payment_count"] == 1
    assert data["planned_total_amount"] == "45.50"


async def test_resumenes_no_escriben(services) -> None:
    summaries = services["summaries"]
    await summaries.daily(1, when_utc=NOW, timezone="UTC")
    await summaries.weekly(1, when_utc=NOW, timezone="UTC")
    await summaries.upcoming(1, when_utc=NOW, timezone="UTC")
    await summaries.pending(1, when_utc=NOW, timezone="UTC")
    assert services["transactions"]._tx._store == []
