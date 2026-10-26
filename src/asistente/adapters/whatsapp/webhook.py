"""Webhook HTTP de la WhatsApp Cloud API (Fase 9).

- `GET /webhook`  verificación de suscripción: `hub.mode`, `hub.verify_token`
  y `hub.challenge`. El token jamás se registra en logs.
- `POST /webhook` mensajes autenticados con `X-Hub-Signature-256`
  (`sha256=<HMAC-SHA256 del body crudo con WHATSAPP_APP_SECRET>`). Si falta o
  no coincide → 401 sin procesar. Se responde SIEMPRE 200 a Meta (no se pide
  retransmisión): la idempotencia la garantiza `processed_inbound_events`.

El cuerpo se lee en crudo (nunca `request.json()`) para poder verificarlo
antes de parsearlo.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from aiohttp import web

from asistente.adapters.whatsapp.channel import WhatsAppChannel
from asistente.config import Settings
from asistente.logging_setup import get_logger

_log = get_logger("whatsapp.webhook")

WEBHOOK_PATH = "/webhook"


def _signature_ok(request: web.Request, raw: bytes, settings: Settings) -> bool:
    provided = request.headers.get("X-Hub-Signature-256", "")
    if not provided.startswith("sha256="):
        return False
    secret = settings.whatsapp_app_secret.get_secret_value().encode("utf-8")
    expected = hmac.new(secret, raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, f"sha256={expected}")


async def _verify_handler(request: web.Request) -> web.Response:
    channel: WhatsAppChannel = request.app["whatsapp_channel"]
    params = request.query
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")
    if mode == "subscribe" and channel.verify_token(token):
        _log.info("webhook_verification_succeeded")
        return web.Response(text=challenge or "ok")
    _log.warning("webhook_verification_rejected", mode=mode)
    return web.Response(status=403, text="Forbidden")


async def _post_handler(request: web.Request) -> web.Response:
    channel: WhatsAppChannel = request.app["whatsapp_channel"]
    settings: Settings = request.app["settings"]
    raw = await request.read()
    if not _signature_ok(request, raw, settings):
        _log.warning("webhook_signature_invalid")
        return web.Response(status=401, text="Invalid signature")
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        _log.warning("webhook_invalid_json")
        return web.Response(status=400, text="Invalid JSON")
    if not isinstance(payload, dict):
        return web.Response(status=200, text="OK")
    await channel.handle_payload(payload)
    return web.Response(status=200, text="OK")


def build_webhook_app(channel: WhatsAppChannel, settings: Settings) -> web.Application:
    """Construye la aplicación aiohttp que sirve los endpoints del webhook."""
    app = web.Application()
    app["whatsapp_channel"] = channel
    app["settings"] = settings
    app.router.add_get(WEBHOOK_PATH, _verify_handler)
    app.router.add_post(WEBHOOK_PATH, _post_handler)
    return app
