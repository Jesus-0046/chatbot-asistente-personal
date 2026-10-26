"""Cliente de la WhatsApp Cloud API (Meta) y puerta de acceso al proveedor.

`WhatsAppGateway` es el contrato mínimo que `WhatsAppChannel` necesita (enviar
texto y descargar media); `WhatsAppClient` lo implementa con aiohttp y las
tests E2E lo sustituyen por un proveedor fake.

Errores normalizados (4xx/5xx/timeout/red/payload): el webhook nunca reintenta
envíos (reintentar un timeout tras la aceptación de Meta puede DUPLICAR el
mensaje; queda documentado). Los detalles internos jamás llegan al usuario.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import aiohttp

from asistente.config import Settings
from asistente.logging_setup import get_logger

_log = get_logger("whatsapp.client")


@dataclass(frozen=True, slots=True)
class WhatsAppMedia:
    data: bytes
    mime_type: str
    width: int | None = None
    height: int | None = None


class WhatsAppGatewayError(RuntimeError):
    """Error de la API de WhatsApp (4xx/5xx/rate limit/timeout/red/payload)."""

    kind: str = "gateway"


class WhatsAppRateLimitedError(WhatsAppGatewayError):
    kind = "rate_limit"


class WhatsAppTimeoutError(WhatsAppGatewayError):
    kind = "timeout"


class WhatsAppConnectionError(WhatsAppGatewayError):
    kind = "connection"


class WhatsAppInvalidResponseError(WhatsAppGatewayError):
    kind = "invalid_response"


class WhatsAppMediaError(WhatsAppGatewayError):
    kind = "media"


class WhatsAppGateway(Protocol):
    async def send_text(self, to: str, text: str) -> None: ...

    async def download_media(self, media_id: str) -> WhatsAppMedia: ...


_RATE_LIMIT_ERROR_CODES = {4, 130429, 131026}  # 4 = rate limit general de Graph API


class WhatsAppClient:
    """Implementación real con aiohttp contra la Graph API de Meta."""

    def __init__(self, settings: Settings, gateway_base_url: str | None = None) -> None:
        self._settings = settings
        self._gateway_base_url = gateway_base_url  # tests E2E apuntan a un fake HTTP
        self._session: aiohttp.ClientSession | None = None

    @property
    def _base_url(self) -> str:
        return self._gateway_base_url or f"{self._settings.whatsapp_api_base_url}/{self._settings.whatsapp_api_version}"

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _session_client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.whatsapp_access_token.get_secret_value()}",
            "Content-Type": "application/json",
        }

    def _timeout(self) -> aiohttp.ClientTimeout:
        return aiohttp.ClientTimeout(total=self._settings.whatsapp_http_timeout_seconds)

    async def send_text(self, to: str, text: str) -> None:
        url = f"{self._base_url}/{self._settings.whatsapp_phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        session = await self._session_client()
        try:
            async with session.post(url, json=payload, headers=self._headers(), timeout=self._timeout()) as resp:
                body = await resp.json(content_type=None)
        except TimeoutError as exc:
            _log.warning("whatsapp_send_timeout")
            raise WhatsAppTimeoutError("whatsapp timeout") from exc
        except aiohttp.ClientError as exc:
            _log.warning("whatsapp_send_connection_error")
            raise WhatsAppConnectionError("whatsapp connection error") from exc

        if resp.status < 200 or resp.status >= 300:
            error = (body or {}).get("error") or {}
            code = error.get("code")
            if resp.status == 429 or code in _RATE_LIMIT_ERROR_CODES:
                raise WhatsAppRateLimitedError("whatsapp rate limited")
            _log.warning("whatsapp_send_rejected", status=resp.status, error_code=code)
            raise WhatsAppGatewayError("whatsapp rejected message")
        if not (body or {}).get("messages"):
            _log.warning("whatsapp_send_unexpected_response", status=resp.status)
            raise WhatsAppInvalidResponseError("whatsapp unexpected response")

    async def download_media(self, media_id: str) -> WhatsAppMedia:
        session = await self._session_client()
        url = f"{self._base_url}/{media_id}"
        try:
            async with session.get(url, headers=self._headers(), timeout=self._timeout()) as resp:
                if resp.status < 200 or resp.status >= 300:
                    _log.warning("whatsapp_media_rejected", status=resp.status)
                    raise WhatsAppMediaError("media not available")
                data = await resp.read()
                mime = resp.headers.get("Content-Type", "application/octet-stream").split(";")[0].strip()
        except TimeoutError as exc:
            raise WhatsAppMediaError("media download timeout") from exc
        except aiohttp.ClientError as exc:
            raise WhatsAppMediaError("media download connection error") from exc

        max_bytes = self._settings.image_max_size_bytes
        if len(data) > max_bytes:
            raise WhatsAppMediaError("media too large")
        if mime not in self._settings.image_allowed_mime_types:
            raise WhatsAppMediaError("media type not allowed")
        return WhatsAppMedia(data=data, mime_type=mime)
