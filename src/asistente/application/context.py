"""Objeto de contexto de conversación (canal-agnóstico).

Los campos `InboundMessage` y `OutboundMessage` son los únicos puntos de
contacto entre el adaptador del canal (Telegram, etc.) y el orquestador.
El payload `metadata` debe contener la información mínima que el orquestador
necesita para construir el contexto del LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from asistente.application.channels import CHANNEL_TELEGRAM


@dataclass(frozen=True, slots=True)
class ImageAttachment:
    """Imagen en memoria (Fase 7): se analiza, NUNCA se persiste.

    El adaptador del canal descarga el archivo y entrega los bytes aquí; la
    extracción los usa solo para la llamada al proveedor de visión. No se
    guarda el binario en BD ni en logs.
    """

    file_id: str
    mime_type: str
    data: bytes
    size: int
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """Mensaje normalizado de entrada (canal-agnóstico).

    `user_id` es el identificador ESTABLE del canal (nunca un nombre):
    el `telegram_user_id` como texto o el `wa_id` de WhatsApp. El adaptador
    entrega siempre `str`. `channel` indica el canal de origen y
    `external_message_id` (p. ej. wamid) alimenta la idempotencia en el webhook.
    """

    user_id: str
    text: str
    display_name: str
    channel: str = CHANNEL_TELEGRAM
    external_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[ImageAttachment] = field(default_factory=list)


@dataclass(slots=True)
class OutboundMessage:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OrchestratorContext:
    user: Any  # persistence.models.User
    history_limit: int = 40
    llm_limit: int = 5
