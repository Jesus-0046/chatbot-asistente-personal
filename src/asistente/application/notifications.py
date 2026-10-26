"""Canal de notificaciones del asistente (capa application).

`NotificationSender` es el contrato que consume el `ReminderPoller` para
entregar recordatorios fuera de una conversación activa. El canal concreto
(Telegram de momento) se inyecta desde el adaptador.
"""

from __future__ import annotations

from typing import Protocol


class NotificationSender(Protocol):
    async def send(self, user_id: int, message: str) -> None:
        """Entrega `message` al usuario identificado por su `id` interno.

        El envío bloquea (awaits) y propaga errores: el poller reintenta los
        recordatorios pendientes si el canal falla.
        """
        ...
