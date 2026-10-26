"""E2E de Fase 9: webhook → canal WhatsApp → orquestador real → gateway fake.

Cinco flujos end-to-end con la pila completa (BD SQLite en memoria, servicios
reales, registro de tools real, orquestador real y canal de WhatsApp), sin red
externa ni credenciales. El LLM (respuestas guionizadas) y el proveedor de
visión son los únicos elementos simulados.

Flujos:
 1. Texto de un usuario vinculado → respuesta.
 2. Gasto financiero: el mismo wamid entregado dos veces se ejecuta UNA sola
    vez (no doble ejecución financiera).
 3. Confirmación vía WhatsApp: delete_note se confirma con "sí" y no se repite.
 4. Imagen: se descarga la media, se propone, se confirma y se persiste la nota.
 5. Pairing completo: número desconocido → código → tool link_whatsapp (desde
    Telegram) → el siguiente mensaje ya entra al orquestador.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from tests.unit.helpers import make_in_memory_db, make_services

from asistente.adapters.whatsapp.channel import WhatsAppChannel
from asistente.adapters.whatsapp.client import WhatsAppMedia
from asistente.ai.llm import LLMResponse, LLMToolCall
from asistente.ai.vision import ImageAttachment
from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.application.orchestrator import Orchestrator
from asistente.persistence.db import Base
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext

WA_ID = "34600000000"
TG_ID = "111"


class FakeGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.media: dict[str, WhatsAppMedia] = {}

    async def send_text(self, to: str, text: str) -> None:
        self.sent.append((to, text))

    async def download_media(self, media_id: str) -> WhatsAppMedia:
        return self.media[media_id]


class ScriptedLLM:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self._responses = list(responses or [])
        self.calls = 0

    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        self.calls += 1
        if not self._responses:
            return LLMResponse(content="ok", tool_calls=[])
        return self._responses.pop(0)


def _text_response(content: str) -> LLMResponse:
    return LLMResponse(content=content, tool_calls=[])


def _tool_response(name: str, arguments: dict[str, Any]) -> LLMResponse:
    return LLMResponse(content=None, tool_calls=[LLMToolCall(id="call_1", function_name=name, arguments=arguments)])


class FakeVision:
    def understand(self, *, image: ImageAttachment, hint: str | None = None) -> dict[str, Any]:
        return {
            "kind": "structured_note",
            "structured_note": {
                "title": {"value": "Lista de la compra", "confidence": "high"},
                "content": {"value": "Pan, leche y huevos", "confidence": "high"},
            },
        }


def _payload(*messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "entr", "changes": [{"field": "messages", "value": {"messages": list(messages)}}]}],
    }


def _text(message_id: str, body: str) -> dict[str, Any]:
    return {"from": WA_ID, "id": message_id, "type": "text", "text": {"body": body}}


def _image(message_id: str, media_id: str) -> dict[str, Any]:
    return {
        "from": WA_ID,
        "id": message_id,
        "type": "image",
        "image": {"id": media_id, "mime_type": "image/jpeg", "caption": "mi nota"},
    }


async def _setup() -> tuple[Any, Any, FakeGateway, WhatsAppChannel, Any]:
    """Pila real completa sobre SQLite en memoria y channel de WhatsApp."""
    db = make_in_memory_db()
    db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    services = make_services(db)
    gateway = FakeGateway()
    registry = create_tool_registry()
    orchestrator = Orchestrator(services=services, llm=ScriptedLLM(), tools=registry, vision=FakeVision())
    channel = WhatsAppChannel(services=services, orchestrator=orchestrator, gateway=gateway)
    return services, db, gateway, channel, registry


def _orchestrator(channel: WhatsAppChannel) -> Orchestrator:
    return channel._orchestrator  # noqa: SLF001 - acceso de test


async def _link(services: Any, wa_id: str = WA_ID) -> int:
    """Crea el usuario Telegram y vincula el número de WhatsApp; devuelve user.id."""
    user = await services.users.ensure_user(
        channel="telegram",
        external_user_id=TG_ID,
        telegram_user_id=int(TG_ID),
        name="Dueño",
        username=None,
        timezone="Europe/Madrid",
        lang="es-ES",
    )
    _, code = await services.channel_identities.ensure_pending_pair(CHANNEL_WHATSAPP, wa_id)
    assert code is not None
    linked = await services.channel_identities.link_with_code(code=code, user_id=user.id)
    assert linked is not None
    return user.id


async def _count(db: Any, model: Any, **filters: Any) -> int:
    stmt = select(func.count()).select_from(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    async with db.session() as session:
        return (await session.execute(stmt)).scalar_one()


async def test_flujo_1_texto_vinculado_responde() -> None:
    services, db, gateway, channel, _ = await _setup()
    await _link(services)
    _orchestrator(channel)._llm = ScriptedLLM([_text_response("¡Hola desde WhatsApp!")])

    await channel.handle_payload(_payload(_text("w1", "hola")))

    assert gateway.sent == [(WA_ID, "¡Hola desde WhatsApp!")]


async def test_flujo_2_gasto_no_se_duplica_con_wamid_repetido() -> None:
    from asistente.persistence.models import Transaction

    services, db, gateway, channel, _ = await _setup()
    user_id = await _link(services)
    await services.accounts.create(
        user_id=user_id,
        name="Efectivo",
        type="cash",
        currency="EUR",
        initial_balance=Decimal("1000.00"),
    )
    llm = ScriptedLLM(
        [
            _tool_response("create_expense", {"amount": "20,50", "merchant": "Gasolinera"}),
            _text_response("Anotado."),
        ]
    )
    _orchestrator(channel)._llm = llm

    # El MISMO wamid se entrega dos veces (las tandas de Meta lo hacen).
    await channel.handle_payload(_payload(_text("w2", "gasto 20,50 en gasolina")))
    await channel.handle_payload(_payload(_text("w2", "gasto 20,50 en gasolina")))

    assert llm.calls == 2, "el segundo envío del mismo wamid no debe volver a orquestar"
    assert len(gateway.sent) == 1
    assert await _count(db, Transaction, user_id=user_id) == 1


async def test_flujo_3_confirmacion_via_whatsapp_no_duplica() -> None:
    from asistente.persistence.models import Note, PendingConfirmation

    services, db, gateway, channel, _ = await _setup()
    user_id = await _link(services)
    note = await services.notes.create(user_id=user_id, title="Temporal", body="borrar")

    llm = ScriptedLLM([_tool_response("delete_note", {"note_id": note.id}), _text_response("Nota eliminada.")])
    _orchestrator(channel)._llm = llm

    await channel.handle_payload(_payload(_text("w3", "borra la nota")))
    await channel.handle_payload(_payload(_text("w3s", "sí")))
    # Duplicados de ambos mensajes: no deben re-ejecutar nada.
    await channel.handle_payload(_payload(_text("w3s", "sí")))
    await channel.handle_payload(_payload(_text("w3", "borra la nota")))

    assert await _count(db, Note, id=note.id) == 0
    assert await _count(db, PendingConfirmation, status="open") == 0


async def test_flujo_4_imagen_propone_y_se_confirma() -> None:
    from asistente.persistence.models import Note, PendingConfirmation

    services, db, gateway, channel, _ = await _setup()
    user_id = await _link(services)
    gateway.media["media-42"] = WhatsAppMedia(data=b"\xff\xd8\xff", mime_type="image/jpeg", width=8, height=8)
    _orchestrator(channel)._llm = ScriptedLLM([_text_response("Nota guardada.")])

    await channel.handle_payload(_payload(_image("w4", "media-42")))

    assert any("He leído tu nota" in text for _, text in gateway.sent)
    assert await _count(db, PendingConfirmation, user_id=user_id) == 1

    await channel.handle_payload(_payload(_text("w4s", "sí")))

    assert await _count(db, Note, user_id=user_id) == 1
    assert await _count(db, PendingConfirmation, user_id=user_id, status="open") == 0


async def test_flujo_5_pairing_completo_hasta_orquestador() -> None:
    services, db, gateway, channel, registry = await _setup()
    user = await services.users.ensure_user(
        channel="telegram",
        external_user_id=TG_ID,
        telegram_user_id=int(TG_ID),
        name="Dueño",
        username=None,
        timezone="Europe/Madrid",
        lang="es-ES",
    )

    # 1) Número desconocido → pairing con código.
    await channel.handle_payload(_payload(_text("w5", "hola")))
    assert len(gateway.sent) == 1
    to, reply = gateway.sent[0]
    assert to == WA_ID
    assert "vincular" in reply

    # 2) La tool (desde Telegram) consume el código de una sola vez.
    code = reply.split("código: ")[1].split("\n")[0].strip()
    assert len(code) == 8

    from asistente.application.pairing import looks_like_pair_code

    assert looks_like_pair_code(code)
    result = await registry.execute(ToolContext(user_id=user.id, services=services), "link_whatsapp", {"code": code})
    assert result.ok is True

    # 3) El siguiente mensaje ya entra al orquestador y se responde.
    _orchestrator(channel)._llm = ScriptedLLM([_text_response("¡Enlazado!")])
    await channel.handle_payload(_payload(_text("w6", "ya estoy aquí")))
    assert gateway.sent[-1] == (WA_ID, "¡Enlazado!")

    # 4) La identidad ya se resuelve en BD.
    assert await services.channel_identities.resolve_user(CHANNEL_WHATSAPP, WA_ID) is not None
    # 5) El código es de un solo uso.
    result2 = await registry.execute(ToolContext(user_id=user.id, services=services), "link_whatsapp", {"code": code})
    assert result2.ok is False
