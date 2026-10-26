"""Adaptadores: interfaz de canal aislada del núcleo de negocio.

El orquestador (Fase 1) solo entenderá `InboundMessage`/`OutboundMessage`;
cualquier canal (Telegram, WhatsApp, web, móvil) implementa este contrato.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from asistente.application.context import OutboundMessage


class ChannelAdapter(ABC):
    """Contrato común para los canales externos."""

    @abstractmethod
    async def start(self) -> None:
        """Arranca el canal (polling/webhook/API)."""

    @abstractmethod
    async def stop(self) -> None:
        """Detiene el canal de forma limpia."""

    @abstractmethod
    async def send(self, message: OutboundMessage) -> None:
        """Envía un mensaje saliente a través del canal."""
        raise NotImplementedError
