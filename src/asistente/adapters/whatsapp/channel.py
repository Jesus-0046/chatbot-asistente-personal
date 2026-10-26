"""Adaptador de WhatsApp (Fase 9).

Es el equivalente a `adapters/telegram/handlers.py`, pero con webhook: traduce
los mensajes de la Cloud API de Meta a `InboundMessage`, reutiliza el
orquestador y envía respuestas por el gateway. Sin lógica de negocio, ni
OpenAI, ni BD aquí. Los nombres impostables (profile_name) nunca participan en
la identidad.

Flujo por mensaje (`message.id` = wamid):
1. Idempotencia: INSERT único en `processed_inbound_events` ANTES de nada.
   Un wamid repetido (tandas del proveedor) se descarta sin efecto.
2. Si la identidad (`wa_id`) no está vinculada → pairing (código por Telegram).
3. Si está vinculada → `InboundMessage` → `Orchestrator.handle` → envío
   (con split por el límite de longitud de WhatsApp).
"""

from __future__ import annotations

import hmac
from typing import Any

from asistente.adapters.whatsapp.client import (
    WhatsAppGateway,
    WhatsAppGatewayError,
    WhatsAppMediaError,
)
from asistente.adapters.whatsapp.formatting import split_for_send
from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.application.context import ImageAttachment, InboundMessage, OutboundMessage
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.logging_setup import get_logger

_log = get_logger("whatsapp.channel")

UNSUPPORTED_REPLY = "Este tipo de mensaje aún no está soportado. Envíame un texto o una imagen."


class WhatsAppChannel:
    def __init__(self, *, services: Services, orchestrator: Orchestrator, gateway: WhatsAppGateway) -> None:
        self._services = services
        self._orchestrator = orchestrator
        self._gateway = gateway
        self._settings = services.settings

    # ------------------------------------------------------------------ #
    # Verificación de suscripción (GET /webhook)                          #
    # ------------------------------------------------------------------ #
    def verify_token(self, token: str | None) -> bool:
        """Compara en tiempo constante con `WHATSAPP_VERIFY_TOKEN`."""
        expected = self._settings.whatsapp_verify_token.get_secret_value()
        if not token or not expected:
            return False
        return hmac.compare_digest(token, expected)

    # ------------------------------------------------------------------ #
    # Payload de Meta: entry[].changes[].value.messages[]                 #
    # ------------------------------------------------------------------ #
    async def handle_payload(self, payload: dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return
        for entry in payload.get("entry", []) or []:
            if not isinstance(entry, dict):
                continue
            for change in entry.get("changes", []) or []:
                if not isinstance(change, dict):
                    continue
                value = change.get("value")
                if not isinstance(value, dict):
                    continue
                for message in value.get("messages", []) or []:
                    await self._handle_message(message)

    async def _handle_message(self, message: dict[str, Any]) -> None:
        from_wa = message.get("from")
        wamid = message.get("id")
        if not from_wa or not wamid:
            _log.debug("whatsapp_message_incomplete")
            return
        if message.get("from_me"):
            _log.debug("whatsapp_echo_ignored")
            return

        identities = self._services.channel_identities
        if identities is None:
            _log.error("whatsapp_channel_identities_missing")
            return

        # 1) Idempotencia ANTES de nada (un wamid solo se procesa una vez).
        if not await identities.mark_processed(CHANNEL_WHATSAPP, str(wamid)):
            _log.info("whatsapp_duplicate_ignored", external_message_id=wamid)
            return

        # 2) Identidad estable del canal (wa_id, nunca profile_name).
        user = await identities.resolve_user(CHANNEL_WHATSAPP, str(from_wa))
        if user is None:
            await self._handle_pairing(from_wa, wamid)
            return

        # 3) Tipos soportados: texto, imagen (con caption) e interactive.
        extracted = await self._extract(message)
        if extracted is None:
            await self._send_text(from_wa, UNSUPPORTED_REPLY)
            return
        text, attachments, media_error = extracted
        if media_error is not None:
            await self._send_text(from_wa, media_error)
            return

        inbound = InboundMessage(
            user_id=str(from_wa),
            text=text or "",
            display_name="",
            channel=CHANNEL_WHATSAPP,
            external_message_id=str(wamid),
            metadata={"profile_name": message.get("profile") or {}},
            attachments=attachments,
        )
        _log.info("whatsapp_message_received", internal_user_id=user.id)
        try:
            outbound = await self._orchestrator.handle(inbound)
        except Exception:  # noqa: BLE001
            _log.exception("orchestrator_failed", internal_user_id=user.id)
            outbound = OutboundMessage(text="Ha ocurrido un error interno. Inténtalo de nuevo pasados unos segundos.")

        for chunk in split_for_send(outbound.text):
            await self._send_text(from_wa, chunk)

    async def _extract(
        self,
        message: dict[str, Any],
    ) -> tuple[str | None, list[ImageAttachment], str | None] | None:
        """Devuelve (texto, adjuntos, error_de_media) o None si no es soportado."""
        mtype = message.get("type")

        if mtype == "text":
            return (message.get("text") or {}).get("body") or "", [], None

        if mtype == "interactive":
            interactive = message.get("interactive") or {}
            title = (interactive.get("button_reply") or {}).get("title") or (interactive.get("list_reply") or {}).get(
                "title"
            )
            if title is None:
                return None
            return str(title), [], None

        if mtype == "image":
            image = message.get("image") or {}
            media_id = image.get("id")
            caption = image.get("caption") or ""
            if not media_id:
                return None
            try:
                media = await self._gateway.download_media(str(media_id))
            except WhatsAppMediaError as exc:
                _log.warning("whatsapp_media_failed", media_id=media_id, reason=exc.kind)
                return (
                    caption,
                    [],
                    "No he podido obtener la imagen (caducó o no está disponible). Envíala de nuevo, por favor.",
                )
            attachment = ImageAttachment(
                file_id=str(media_id),
                mime_type=media.mime_type,
                data=media.data,
                size=len(media.data),
                width=media.width,
                height=media.height,
            )
            return caption or "", [attachment], None

        return None

    # ------------------------------------------------------------------ #
    # Pairing (identidad WhatsApp no vinculada)                           #
    # ------------------------------------------------------------------ #
    async def _handle_pairing(self, from_wa: str, wamid: str) -> None:
        identities = self._services.channel_identities
        if identities is None:
            return
        _, code = await identities.ensure_pending_pair(CHANNEL_WHATSAPP, str(from_wa))
        if code is not None:
            reply = (
                "¡Hola! Para usar el asistente por WhatsApp primero hay que vincular tu número.\n\n"
                f"1. Abre tu chat de Telegram con el bot.\n"
                f"2. Envía el código: {code}\n\n"
                "El código caduca en 30 minutos y es de un solo uso."
            )
        else:
            reply = (
                "Ya tienes un código de vinculación pendiente. Abre tu chat de Telegram "
                "con el bot y envíalo para terminar de vincular este número."
            )
        _log.info("whatsapp_pairing_offered", external_message_id=wamid)
        await self._send_text(from_wa, reply)

    # ------------------------------------------------------------------ #
    # Envío: nunca reintentar (un timeout tras la aceptación duplicaría).  #
    # ------------------------------------------------------------------ #
    async def _send_text(self, to: str, text: str) -> None:
        try:
            await self._gateway.send_text(to, text)
        except WhatsAppGatewayError as exc:
            _log.warning("whatsapp_send_failed", kind=exc.kind, to_masked=_mask(to))
        except Exception:  # noqa: BLE001
            _log.exception("whatsapp_send_unexpected_failure", to_masked=_mask(to))


def _mask(phone: str) -> str:
    """Enmascara el número en logs (solo últimos 3 dígitos)."""
    return f"***{phone[-3:]}" if len(phone) >= 3 else "***"
