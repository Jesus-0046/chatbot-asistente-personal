"""Tests del canal WhatsApp (Fase 9): traducción, pairing, idempotencia y envío.

Se prueban las ramas del adaptador (`WhatsAppChannel`) con fakes de servicios,
orquestador y gateway; las decisiones de negocio (ciclo del código, repo) se
cubren en `test_whatsapp_channels_domain.py`. Estas pruebas no tocan BD.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.unit.helpers import make_settings

from asistente.adapters.whatsapp.channel import UNSUPPORTED_REPLY, WhatsAppChannel
from asistente.adapters.whatsapp.client import (
    WhatsAppGatewayError,
    WhatsAppMedia,
    WhatsAppMediaError,
)
from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.application.context import OutboundMessage

WA_ID = "34600000000"
CODE = "ABCDEFGH"


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.id = user_id


class FakeIdentities:
    """Mini-versión en memoria de `ChannelIdentityService`."""

    def __init__(self) -> None:
        self.linked: dict[str, FakeUser] = {}
        self.processed: set[str] = set()
        self.pending_codes: dict[str, str] = {}
        self.exposed: list[str] = []

    async def resolve_user(self, channel: str, external_user_id: str) -> FakeUser | None:
        return self.linked.get(external_user_id)

    async def mark_processed(self, channel: str, external_message_id: str) -> bool:
        if external_message_id in self.processed:
            return False
        self.processed.add(external_message_id)
        return True

    async def ensure_pending_pair(self, channel: str, external_user_id: str) -> tuple[object, str | None]:
        if external_user_id in self.pending_codes:
            return (object(), None)
        self.pending_codes[external_user_id] = CODE
        self.exposed.append(CODE)
        return (object(), CODE)

    def link(self, external_user_id: str, user: FakeUser) -> None:
        self.linked[external_user_id] = user


class FakeOrchestrator:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def handle(self, inbound: Any) -> OutboundMessage:
        self.calls.append(inbound)
        return OutboundMessage(text="respuesta del asistente")


class FakeGateway:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.media: dict[str, WhatsAppMedia] = {}
        self.send_error: Exception | None = None
        self.media_error: WhatsAppMediaError | None = None

    async def send_text(self, to: str, text: str) -> None:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append((to, text))

    async def download_media(self, media_id: str) -> WhatsAppMedia:
        if self.media_error is not None:
            raise self.media_error
        return self.media[media_id]


def _text_message(text: str, wamid: str = "wamid.1") -> dict[str, Any]:
    return {
        "from": WA_ID,
        "id": wamid,
        "type": "text",
        "text": {"body": text},
    }


def _payload(*messages: dict[str, Any]) -> dict[str, Any]:
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "entr", "changes": [{"field": "messages", "value": {"messages": list(messages)}}]}],
    }


def _channel(
    *,
    identities: FakeIdentities | None = None,
    orchestrator: FakeOrchestrator | None = None,
    gateway: FakeGateway | None = None,
    settings_override: Any = None,
) -> WhatsAppChannel:
    class _Services:
        settings = settings_override if settings_override is not None else make_settings()
        channel_identities = identities or FakeIdentities()

    return WhatsAppChannel(
        services=_Services(),  # type: ignore[arg-type]
        orchestrator=orchestrator or FakeOrchestrator(),
        gateway=gateway or FakeGateway(),
    )


# --------------------------------------------------------------------------- #
# verify_token                                                                #
# --------------------------------------------------------------------------- #
def test_verify_token_sin_token_configurado_devuelve_false() -> None:
    channel = _channel()
    assert channel.verify_token("cualquier-token") is False
    assert channel.verify_token(None) is False


def test_verify_token_exacto() -> None:
    channel = _channel(settings_override=make_settings(whatsapp_verify_token="token-a"))
    assert channel.verify_token("token-a") is True
    assert channel.verify_token("token-b") is False
    assert channel.verify_token(None) is False
    assert channel.verify_token("") is False


# --------------------------------------------------------------------------- #
# Mensaje de texto vinculado                                                  #
# --------------------------------------------------------------------------- #
async def test_texto_vinculado_llega_al_orquestador_y_se_envia() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    await channel.handle_payload(_payload(_text_message("hola")))

    assert len(orchestrator.calls) == 1
    inbound = orchestrator.calls[0]
    assert inbound.user_id == WA_ID
    assert inbound.channel == CHANNEL_WHATSAPP
    assert inbound.external_message_id == "wamid.1"
    assert inbound.text == "hola"
    assert gateway.sent == [(WA_ID, "respuesta del asistente")]


async def test_mensaje_duplicado_se_ignora() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    await channel.handle_payload(_payload(_text_message("hola")))
    await channel.handle_payload(_payload(_text_message("hola")))

    assert len(orchestrator.calls) == 1
    assert len(gateway.sent) == 1


async def test_mensaje_con_from_me_se_ignora() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = _text_message("hola")
    message["from_me"] = True
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls == []
    assert gateway.sent == []


async def test_mensaje_sin_id_o_sin_from_se_ignora() -> None:
    orchestrator = FakeOrchestrator()
    channel = _channel(orchestrator=orchestrator)

    await channel.handle_payload(_payload({"from": WA_ID, "type": "text", "text": {"body": "x"}}))
    await channel.handle_payload(_payload({"id": "wamid.9", "type": "text", "text": {"body": "x"}}))

    assert orchestrator.calls == []


# --------------------------------------------------------------------------- #
# Pairing: número no vinculado                                                #
# --------------------------------------------------------------------------- #
async def test_numero_no_vinculado_envia_pairing_con_codigo() -> None:
    identities = FakeIdentities()
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    await channel.handle_payload(_payload(_text_message("hola")))

    assert orchestrator.calls == []
    assert len(gateway.sent) == 1
    to, reply = gateway.sent[0]
    assert to == WA_ID
    assert CODE in reply
    assert "vincular" in reply


async def test_pairing_pendiente_no_reexpone_el_codigo() -> None:
    identities = FakeIdentities()
    gateway = FakeGateway()
    channel = _channel(identities=identities, gateway=gateway)

    await channel.handle_payload(_payload(_text_message("primera", wamid="wamid.1")))
    await channel.handle_payload(_payload(_text_message("segunda", wamid="wamid.2")))

    assert len(gateway.sent) == 2
    to, first = gateway.sent[0]
    to2, second = gateway.sent[1]
    assert to == to2 == WA_ID
    assert CODE in first
    assert CODE not in second
    assert "pendiente" in second


# --------------------------------------------------------------------------- #
# Tipos de mensaje                                                            #
# --------------------------------------------------------------------------- #
async def test_interactive_button_reply_se_traduce_a_texto() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    channel = _channel(identities=identities, orchestrator=orchestrator)

    message = {
        "from": WA_ID,
        "id": "wamid.interactive",
        "type": "interactive",
        "interactive": {
            "type": "button_reply",
            "button_reply": {"id": "1", "title": "Sí, confirmo"},
        },
    }
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls[0].text == "Sí, confirmo"
    assert orchestrator.calls[0].external_message_id == "wamid.interactive"


async def test_interactive_sin_eleccion_se_rechaza_como_no_soportado() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = {"from": WA_ID, "id": "wamid.interactive.2", "type": "interactive", "interactive": {"type": "nfm_reply"}}
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls == []
    assert gateway.sent[-1] == (WA_ID, UNSUPPORTED_REPLY)


async def test_imagen_se_descarga_y_llega_como_attachment() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    gateway.media["media-123"] = WhatsAppMedia(data=b"\xff\xd8\xff", mime_type="image/jpeg", width=100, height=50)
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = {
        "from": WA_ID,
        "id": "wamid.image",
        "type": "image",
        "image": {"id": "media-123", "mime_type": "image/jpeg", "caption": "ticket"},
    }
    await channel.handle_payload(_payload(message))

    assert len(orchestrator.calls) == 1
    inbound = orchestrator.calls[0]
    assert inbound.text == "ticket"
    assert len(inbound.attachments) == 1
    attachment = inbound.attachments[0]
    assert attachment.file_id == "media-123"
    assert attachment.mime_type == "image/jpeg"
    assert attachment.data == b"\xff\xd8\xff"
    assert attachment.width == 100
    assert attachment.height == 50


async def test_imagen_sin_media_id_se_rechaza_como_no_soportado() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = {"from": WA_ID, "id": "wamid.image.2", "type": "image", "image": {}}
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls == []
    assert gateway.sent[-1] == (WA_ID, UNSUPPORTED_REPLY)


async def test_error_de_media_responde_mensaje_amigable_sin_orquestar() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    gateway.media_error = WhatsAppMediaError("media caducada")
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = {"from": WA_ID, "id": "wamid.image.3", "type": "image", "image": {"id": "media-999"}}
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls == []
    assert "No he podido obtener la imagen" in gateway.sent[-1][1]


async def test_tipo_no_soportado_responde_mensaje_controlado() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    message = {"from": WA_ID, "id": "wamid.audio", "type": "audio", "audio": {"id": "a1"}}
    await channel.handle_payload(_payload(message))

    assert orchestrator.calls == []
    assert gateway.sent[-1] == (WA_ID, UNSUPPORTED_REPLY)


# --------------------------------------------------------------------------- #
# Robustez del envío                                                          #
# --------------------------------------------------------------------------- #
async def test_error_del_orquestador_devuelve_respuesta_segura() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    gateway = FakeGateway()

    class ExplodingOrchestrator:
        async def handle(self, inbound: Any) -> OutboundMessage:
            raise RuntimeError("boom interno")

    channel = _channel(identities=identities, orchestrator=ExplodingOrchestrator(), gateway=gateway)  # type: ignore[arg-type]

    await channel.handle_payload(_payload(_text_message("hola")))

    assert len(gateway.sent) == 1
    assert "error interno" in gateway.sent[0][1]


async def test_fallo_de_envio_no_rompe_el_webhook() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    orchestrator = FakeOrchestrator()
    gateway = FakeGateway()
    gateway.send_error = WhatsAppGatewayError("rate limit 429")
    channel = _channel(identities=identities, orchestrator=orchestrator, gateway=gateway)

    await channel.handle_payload(_payload(_text_message("hola")))

    assert len(orchestrator.calls) == 1  # la lógica sí se ejecutó
    assert gateway.sent == []


async def test_envio_split_envia_varias_partes() -> None:
    identities = FakeIdentities()
    identities.link(WA_ID, FakeUser(7))
    gateway = FakeGateway()

    class LongOrchestrator:
        async def handle(self, inbound: Any) -> OutboundMessage:
            return OutboundMessage(text="A" * 5000)

    channel = _channel(identities=identities, orchestrator=LongOrchestrator(), gateway=gateway)  # type: ignore[arg-type]

    await channel.handle_payload(_payload(_text_message("hola")))

    assert len(gateway.sent) == 2
    assert sum(len(text) for _, text in gateway.sent) == 5000
    assert all(len(text) <= 4096 for _, text in gateway.sent)


async def test_canal_sin_servicio_de_identidades_no_crashea() -> None:
    class NoIdentities:
        channel_identities = None
        settings = make_settings()

    channel = WhatsAppChannel(
        services=NoIdentities(),  # type: ignore[arg-type]
        orchestrator=FakeOrchestrator(),
        gateway=FakeGateway(),
    )
    await channel.handle_payload(_payload(_text_message("hola")))


@pytest.mark.parametrize("bad_payload", [None, 42, "texto", {}, {"entry": "no-lista"}])
async def test_payloads_malformados_no_crashean(bad_payload: Any) -> None:
    channel = _channel()
    await channel.handle_payload(bad_payload)
