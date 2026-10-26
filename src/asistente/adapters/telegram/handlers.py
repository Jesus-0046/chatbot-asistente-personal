"""Handlers de Telegram (Fase 1).

Solo traducen mensajes del canal a `InboundMessage` y la respuesta del
orquestador a texto. No hay lógica de negocio, ni OpenAI, ni BD aquí.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from asistente.application.channels import CHANNEL_TELEGRAM
from asistente.application.context import ImageAttachment, InboundMessage
from asistente.application.orchestrator import Orchestrator
from asistente.config import Settings
from asistente.logging_setup import get_logger

logger = get_logger("telegram.handlers")


def _is_authorized(user_id: int, settings: Settings) -> bool:
    return user_id in settings.owner_ids_set


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Responde a `/start`. Rechaza (o informa) a usuarios no autorizados."""
    settings: Settings = context.bot_data["settings"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None:
        if message is not None:
            await message.reply_text("No se pudo identificar al usuario.")
        return

    if not _is_authorized(user.id, settings):
        logger.info("unauthorized_user", telegram_user_id=user.id)
        await message.reply_text(
            "No estás autorizado a usar este bot. Si crees que es un error, ponte en contacto con el propietario."
        )
        return

    logger.info("start_command", telegram_user_id=user.id)
    await message.reply_text("Hola, soy tu asistente personal. ¿En qué te ayudo?")


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reenvía un mensaje de texto al orquestador y responde con su salida."""
    settings: Settings = context.bot_data["settings"]
    orchestrator: Orchestrator = context.bot_data["orchestrator"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or message.text is None:
        if message is not None:
            await message.reply_text("No se pudo identificar el mensaje.")
        return

    if not _is_authorized(user.id, settings):
        logger.info("unauthorized_user", telegram_user_id=user.id)
        await message.reply_text(
            "No estás autorizado a usar este bot. Si crees que es un error, ponte en contacto con el propietario."
        )
        return

    inbound = InboundMessage(
        user_id=str(user.id),
        text=message.text,
        display_name=user.first_name or "",
        channel=CHANNEL_TELEGRAM,
        external_message_id=str(message.message_id),
        metadata={
            "username": user.username,
            "name": user.full_name or user.first_name or None,
        },
    )
    logger.info("message_received", telegram_user_id=user.id)
    outbound = await orchestrator.handle(inbound)
    await message.reply_text(outbound.text)


async def photo_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reenvía una foto (Fase 7) al orquestador con la imagen en memoria.

    El adaptador SOLO traduce: descarga la foto a bytes en memoria, construye
    el `ImageAttachment` y delega. No hay lógica de negocio, ni OpenAI, ni BD,
    ni persistencia de la imagen aquí.
    """
    settings: Settings = context.bot_data["settings"]
    orchestrator: Orchestrator = context.bot_data["orchestrator"]
    user = update.effective_user
    message = update.effective_message
    if user is None or message is None or not message.photo:
        if message is not None:
            await message.reply_text("No se pudo identificar la imagen.")
        return

    if not _is_authorized(user.id, settings):
        logger.info("unauthorized_user", telegram_user_id=user.id)
        await message.reply_text(
            "No estás autorizado a usar este bot. Si crees que es un error, ponte en contacto con el propietario."
        )
        return

    photo = max(message.photo, key=lambda p: p.file_size or 0)
    file = await context.bot.get_file(photo.file_id)
    data = await file.download_as_bytearray()

    inbound = InboundMessage(
        user_id=str(user.id),
        text=message.caption or "",
        display_name=user.first_name or "",
        channel=CHANNEL_TELEGRAM,
        external_message_id=str(message.message_id),
        metadata={
            "username": user.username,
            "name": user.full_name or user.first_name or None,
        },
        attachments=[
            ImageAttachment(
                file_id=photo.file_id,
                mime_type="image/jpeg",
                data=bytes(data),
                size=len(data),
                width=photo.width,
                height=photo.height,
            )
        ],
    )
    logger.info("photo_received", telegram_user_id=user.id, size_bytes=len(data))
    outbound = await orchestrator.handle(inbound)
    await message.reply_text(outbound.text)
