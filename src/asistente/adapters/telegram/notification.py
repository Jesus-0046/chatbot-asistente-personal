"""Envío de notificaciones por Telegram (requiere chat privado iniciado).

El `user_id` interno se traduce al `telegram_user_id` del usuario, que en un
chat privado de PTB coincide con el `chat_id` al que responder.
"""

from __future__ import annotations

from telegram.ext import Application

from asistente.domain.users import UserService
from asistente.logging_setup import get_logger

logger = get_logger("telegram.notification")


class TelegramNotificationSender:
    def __init__(self, app: Application, users: UserService) -> None:
        self._app = app
        self._users = users

    async def send(self, user_id: int, message: str) -> None:
        telegram_id = await self._users.telegram_id_for(user_id)
        if telegram_id is None:
            raise RuntimeError(f"Usuario {user_id} sin telegram_user_id para notificaciones.")
        await self._app.bot.send_message(chat_id=telegram_id, text=message)
        logger.info("reminder_sent", user_id=user_id, chat_id=telegram_id)
