"""Adaptador de WhatsApp (Fase 9): webhook HTTP + cliente y canal."""

from __future__ import annotations

from asistente.adapters.whatsapp.channel import WhatsAppChannel
from asistente.adapters.whatsapp.client import (
    WhatsAppClient,
    WhatsAppGateway,
    WhatsAppGatewayError,
    WhatsAppMedia,
    WhatsAppMediaError,
)
from asistente.adapters.whatsapp.formatting import split_for_send
from asistente.adapters.whatsapp.webhook import build_webhook_app

__all__ = [
    "WhatsAppChannel",
    "WhatsAppClient",
    "WhatsAppGateway",
    "WhatsAppGatewayError",
    "WhatsAppMedia",
    "WhatsAppMediaError",
    "build_webhook_app",
    "split_for_send",
]
