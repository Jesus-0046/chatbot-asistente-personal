"""Flujos E2E por el orquestador con dobles deterministas del LLM (Fase 8).

Se ejercitan flujos COMPLETOS con tools y servicios reales (BD SQLite en
memoria conectada): el LLM pide una tool → se ejecuta (o pide confirmación) →
persiste. Cubre los flujos señalados en Fase 8: mantenimiento de vehículo con
coste (mueve dinero) y viaje con confirmación (nada se cancela hasta el "sí").
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from tests.unit.helpers import make_connected_services

from asistente.ai.llm import LLMResponse, LLMToolCall
from asistente.application.context import InboundMessage
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.tools import create_tool_registry

TELEGRAM = 7001


def text_response(content: str = "Hecho.") -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[])


def tool_response(name: str, arguments: dict[str, Any] | None = None) -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[LLMToolCall(id="call_1", function_name=name, arguments=arguments or {})],
    )


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls = 0

    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            return text_response()
        return self._responses.pop(0)


def _inbound(text: str = "") -> InboundMessage:
    return InboundMessage(
        user_id=TELEGRAM,
        text=text,
        display_name="Test",
        metadata={"username": "test_user", "name": None},
    )


async def _env() -> tuple[Services, Orchestrator, int]:
    services = await make_connected_services()
    user = await services.users.ensure_user(
        telegram_user_id=TELEGRAM, name="Test", username="test", timezone="Europe/Madrid", lang="es-ES"
    )
    orchestrator = Orchestrator(
        services=services,
        llm=ScriptedLLM([]),
        tools=create_tool_registry(),
    )
    return services, orchestrator, user.id


@pytest.mark.asyncio
async def test_e2e_mantenimiento_vehiculo_con_coste_persiste_gasto_y_mantenimiento() -> None:
    services, orchestrator, user_id = await _env()
    orchestrator._llm = ScriptedLLM(
        [
            tool_response(
                "record_vehicle_maintenance",
                {"vehicle": "Coche", "maintenance_type": "revision", "description": "Pastillas", "cost": "80,50"},
            ),
            text_response("Revisión registrada."),
        ]
    )
    account = await services.accounts.create(
        user_id=user_id, name="Banco", type="bank", initial_balance=Decimal("1000")
    )
    vehicle = await services.vehicles.create(user_id=user_id, name="Coche")

    out = await orchestrator.handle(_inbound("he hecho una revisión de 80,50 euros al coche"))

    assert out.text == "Revisión registrada."
    maintenance = await services.vehicles.list_maintenance(user_id, vehicle)
    assert len(maintenance) == 1
    assert maintenance[0].cost == Decimal("80.50")
    txn = await services.transactions.list(user_id, account_id=account.id)
    assert len(txn) == 1
    assert txn[0].amount == Decimal("80.50")
    assert txn[0].type == "expense"


@pytest.mark.asyncio
async def test_e2e_viaje_no_se_cancela_hasta_confirmar_y_con_si_se_cancela() -> None:
    services, orchestrator, user_id = await _env()
    trip = await services.trips.create(user_id=user_id, name="Málaga")

    orchestrator._llm = ScriptedLLM([tool_response("cancel_trip", {"trip_id": trip.id})])
    first = await orchestrator.handle(_inbound("cancela el viaje a Málaga"))

    assert "¿Confirmas" in first.text or "¿confirmo" in first.text
    trip_after = await services.trips.get(user_id, trip.id)
    assert trip_after.status == "planned"  # nada cancelado aún

    orchestrator._llm = ScriptedLLM([text_response("Viaje cancelado.")])
    second = await orchestrator.handle(_inbound("sí"))

    assert second.text == "Viaje cancelado."
    trip_done = await services.trips.get(user_id, trip.id)
    assert trip_done.status == "cancelled"


@pytest.mark.asyncio
async def test_e2e_viaje_se_rechaza_con_no_y_sigue_planeado() -> None:
    services, orchestrator, user_id = await _env()
    trip = await services.trips.create(user_id=user_id, name="Buenos Aires")

    orchestrator._llm = ScriptedLLM([tool_response("cancel_trip", {"trip_id": trip.id})])
    first = await orchestrator.handle(_inbound("cancela el viaje"))
    assert "¿Confirmas" in first.text or "¿confirmo" in first.text

    second = await orchestrator.handle(_inbound("no"))
    assert "cancelado" in second.text

    trip_after = await services.trips.get(user_id, trip.id)
    assert trip_after.status == "planned"
