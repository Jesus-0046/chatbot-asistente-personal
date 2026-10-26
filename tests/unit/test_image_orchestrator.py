"""Tests de extremo a extremo del flujo de imágenes en el orquestador (Fase 7).

Verifica la regla fundamental de la fase con servicios reales (BD SQLite en
memoria): imagen → propuesta → confirmación (o cancelación) → `apply_extracted_data`
(persistencia única). La imagen JAMÁS escribe nada por sí sola.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from tests.unit.helpers import make_connected_services
from tests.unit.image_fakes import FakeVisionProvider, make_image

from asistente.ai.llm import LLMResponse
from asistente.ai.vision import ImageExtractionError
from asistente.application.context import InboundMessage
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.tools import create_tool_registry

TELEGRAM = 7001


class FakeLLM:
    def __init__(self, content: str = "Hecho.") -> None:
        self.calls = 0
        self._content = content

    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        self.calls += 1
        return LLMResponse(content=self._content, tool_calls=[])


def _inbound(text: str = "", *, attachments: list | None = None) -> InboundMessage:
    return InboundMessage(
        user_id=TELEGRAM,
        text=text,
        display_name="Test",
        metadata={"username": "test_user", "name": None},
        attachments=attachments or [],
    )


async def _env(
    *, payload: dict[str, Any] | None = None, vision_error: Exception | None = None
) -> tuple[Services, Orchestrator, int]:
    services = await make_connected_services()
    user = await services.users.ensure_user(
        telegram_user_id=TELEGRAM, name="Test", username="test", timezone="Europe/Madrid", lang="es-ES"
    )
    await services.accounts.create(
        user_id=user.id,
        name="Cuenta",
        type="bank",
        currency="EUR",
        initial_balance=Decimal("1000.00"),
    )
    orchestrator = Orchestrator(
        services=services,
        llm=FakeLLM(),
        tools=create_tool_registry(),
        vision=FakeVisionProvider(payload=payload, error=vision_error),
    )
    return services, orchestrator, user.id


async def test_imagen_genera_propuesta_y_nada_se_escribe_hasta_confirmar() -> None:
    services, orch, uid = await _env()

    out = await orch.handle(_inbound(attachments=[make_image()]))

    assert "Mercadona" in out.text
    assert "28,80" in out.text
    assert "¿Registro este gasto" in out.text
    assert await services.transactions.list(uid) == []
    assert await services.notes.list(uid) == []
    assert await services.tasks.list(uid) == []

    pending = await services.confirmations.find_open(uid)
    assert pending is not None
    payload = pending["payload"]
    assert payload["tool"] == "apply_extracted_data"
    assert payload["arguments"]["kind"] == "ticket"

    out2 = await orch.handle(_inbound("sí"))
    assert out2.text == "Hecho."
    txns = await services.transactions.list(uid, limit=10)
    assert len(txns) == 1  # una única transacción al confirmar
    assert isinstance(txns[0].amount, Decimal)
    assert txns[0].amount == Decimal("28.80")
    assert txns[0].merchant == "Mercadona"


async def test_confirmacion_del_payload_no_contiene_imagen() -> None:
    services, orch, uid = await _env()
    await orch.handle(_inbound(attachments=[make_image()]))
    pending = await services.confirmations.find_open(uid)
    dumped = str(pending["payload"])
    assert "file_id" not in dumped
    assert "base64" not in dumped.lower()
    assert "file_1" not in dumped


async def test_cancelacion_no_crea_absolutamente_nada() -> None:
    services, orch, uid = await _env()
    out = await orch.handle(_inbound(attachments=[make_image()]))
    assert "¿Registro este gasto" in out.text

    out2 = await orch.handle(_inbound("no"))
    assert "cancelado" in out2.text
    assert await services.transactions.list(uid) == []
    assert await services.confirmations.find_open(uid) is None


async def test_error_del_proveedor_devuelve_texto_amigable() -> None:
    services, orch, _uid = await _env(vision_error=ImageExtractionError("timeout", "x"))
    out = await orch.handle(_inbound(attachments=[make_image()]))
    assert "tardado demasiado" in out.text
    assert await services.confirmations.find_open(_uid) is None


async def test_imagen_ilegible_devuelve_texto_amigable() -> None:
    services, orch, uid = await _env(payload={"kind": "unknown"})
    out = await orch.handle(_inbound(attachments=[make_image()]))
    assert "No he podido leer nada útil" in out.text
    assert await services.confirmations.find_open(uid) is None


async def test_contexto_pendiente_se_resuelve_antes_del_flujo_imagen() -> None:
    services, orch, uid = await _env()
    out = await orch.handle(_inbound(attachments=[make_image()]))
    assert "¿Registro este gasto" in out.text

    # El usuario responde "sí" ENVIANDO otra foto: primero se resuelve la pendiente.
    out2 = await orch.handle(_inbound("sí", attachments=[make_image()]))
    assert out2.text == "Hecho."
    assert len(await services.transactions.list(uid, limit=10)) == 1
    # No se creó una segunda propuesta mientras ya había una pendiente resuelta.
    assert await services.confirmations.find_open(uid) is None


async def test_posible_duplicado_se_avisa_pero_no_bloquea() -> None:
    services, orch, uid = await _env()
    today = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    await services.transactions.create(
        user_id=uid, type="expense", amount=Decimal("28.80"), merchant="Mercadona", occurred_at=today
    )

    out = await orch.handle(_inbound(attachments=[make_image()]))
    assert "Posible duplicado" in out.text

    out2 = await orch.handle(_inbound("sí"))
    assert out2.text == "Hecho."
    assert len(await services.transactions.list(uid, limit=10)) == 2  # no se bloquea


async def test_sin_imagen_el_flujo_imagen_no_se_dispara() -> None:
    services, orch, uid = await _env()
    out = await orch.handle(_inbound("hola"))
    assert out.text == "Hecho."
    assert await services.confirmations.find_open(uid) is None
    assert await services.transactions.list(uid) == []


async def test_el_historial_guarda_la_interaccion_de_la_imagen() -> None:
    services, orch, uid = await _env()
    await orch.handle(_inbound(attachments=[make_image()]))
    recent = await services.conversations.recent(uid, 20)
    roles = {row.role for row in recent}
    assert "user" in roles and "assistant" in roles
