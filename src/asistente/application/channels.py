"""Nombres de canal: la única enumeración de canales de transporte.

El orquestador, las tools y los servicios de dominio solo conocen estos
identificadores; nunca detalles de la API de un proveedor.
"""

from __future__ import annotations

CHANNEL_TELEGRAM = "telegram"
CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_WEB = "web"

ALL_CHANNELS: tuple[str, ...] = (CHANNEL_TELEGRAM, CHANNEL_WHATSAPP)
