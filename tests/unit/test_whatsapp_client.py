"""Tests del cliente de la WhatsApp Cloud API (Fase 9).

Se monta un servidor HTTP fake (`aiohttp.test_utils`) en un puerto efímero que
simula la Graph API: responde 200/4xx/5xx con retardo controlado y sirve media.
El cliente apunta al fake con `gateway_base_url` (sin credenciales ni red
externa).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from tests.unit.helpers import make_settings

from asistente.adapters.whatsapp.client import (
    WhatsAppClient,
    WhatsAppGatewayError,
    WhatsAppInvalidResponseError,
    WhatsAppMediaError,
    WhatsAppRateLimitedError,
    WhatsAppTimeoutError,
)

PHONE_ID = "123456789"


class FakeGraph:
    def __init__(self) -> None:
        self.send_status = 200
        self.send_body: dict | None = None
        self.latency_seconds = 0.0
        self.messages: list[dict] = []
        self.media_status = 200
        self.media_body = b"\xff\xd8\xff"
        self.media_content_type = "image/jpeg"

    async def handle_send(self, request: web.Request) -> web.Response:
        await self._maybe_latency()
        self.messages.append(await request.json())
        if self.send_status >= 400:
            return web.json_response(
                self.send_body or {"error": {"code": 0, "message": "rejected"}}, status=self.send_status
            )
        if self.send_body is not None:
            return web.json_response(self.send_body, status=self.send_status)
        return web.json_response(
            {
                "messaging_product": "whatsapp",
                "contacts": [{"input": "34600000000", "wa_id": "34600000000"}],
                "messages": [{"id": "wamid.OK"}],
            }
        )

    async def handle_media(self, request: web.Request) -> web.Response:
        await self._maybe_latency()
        if self.media_status >= 400:
            return web.Response(status=self.media_status, text="not found")
        return web.Response(body=self.media_body, headers={"Content-Type": self.media_content_type})

    async def _maybe_latency(self) -> None:
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)


@asynccontextmanager
async def _graph_server(graph: FakeGraph):
    app = web.Application()
    app.router.add_post(f"/{PHONE_ID}/messages", graph.handle_send)
    app.router.add_get("/media-1", graph.handle_media)
    server = TestServer(app)
    await server.start_server()
    try:
        yield f"http://127.0.0.1:{server.port}"
    finally:
        await server.close()


@asynccontextmanager
async def _silent_server():
    """Servidor TCP que acepta la conexión y NUNCA responde (para timeouts)."""

    tasks: list[asyncio.Task[None]] = []

    async def _hold_connection(_reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
        tasks.append(asyncio.current_task())  # type: ignore[arg-type]
        await asyncio.sleep(3600)

    server = await asyncio.start_server(_hold_connection, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _settings(**overrides: object) -> object:
    return make_settings(
        whatsapp_enabled=True,
        whatsapp_phone_number_id=PHONE_ID,
        whatsapp_access_token="tok-prueba",
        whatsapp_app_secret="secret",
        **overrides,
    )


def _client(base: str, **overrides: object) -> WhatsAppClient:
    return WhatsAppClient(_settings(**overrides), gateway_base_url=base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# send_text                                                                   #
# --------------------------------------------------------------------------- #
async def test_send_text_ok_envia_payload_completo() -> None:
    graph = FakeGraph()
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            await client.send_text("34600000000", "hola")
        finally:
            await client.close()
    assert graph.messages == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "34600000000",
            "type": "text",
            "text": {"preview_url": False, "body": "hola"},
        }
    ]


async def test_send_text_rate_limit_lanza_whatsapp_rate_limited() -> None:
    graph = FakeGraph()
    graph.send_status = 429
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            with pytest.raises(WhatsAppRateLimitedError):
                await client.send_text("34600000000", "hola")
        finally:
            await client.close()


async def test_send_text_rechazado_lanza_gateway_error() -> None:
    graph = FakeGraph()
    graph.send_status = 500
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            with pytest.raises(WhatsAppGatewayError):
                await client.send_text("34600000000", "hola")
        finally:
            await client.close()


async def test_send_text_respuesta_inesperada_lanza_invalid_response() -> None:
    graph = FakeGraph()
    graph.send_status = 200
    graph.send_body = {"messaging_product": "whatsapp"}
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            with pytest.raises(WhatsAppInvalidResponseError):
                await client.send_text("34600000000", "hola")
        finally:
            await client.close()


async def test_send_text_timeout_lanza_whatsapp_timeout() -> None:
    async with _silent_server() as base:
        client = _client(base, whatsapp_http_timeout_seconds=0.05)
        try:
            with pytest.raises(WhatsAppTimeoutError):
                await client.send_text("34600000000", "hola")
        finally:
            await client.close()


# --------------------------------------------------------------------------- #
# download_media                                                              #
# --------------------------------------------------------------------------- #
async def test_download_media_ok() -> None:
    graph = FakeGraph()
    graph.media_body = b"\xff\xd8\xff\xe0"
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            media = await client.download_media("media-1")
        finally:
            await client.close()
    assert media.data == b"\xff\xd8\xff\xe0"
    assert media.mime_type == "image/jpeg"


async def test_download_media_no_disponible_lanza_media_error() -> None:
    graph = FakeGraph()
    graph.media_status = 404
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            with pytest.raises(WhatsAppMediaError):
                await client.download_media("media-1")
        finally:
            await client.close()


async def test_download_media_demasiado_grande_lanza_media_error() -> None:
    graph = FakeGraph()
    graph.media_body = b"x" * (1024 * 1024 + 1)
    async with _graph_server(graph) as base:
        client = _client(base, image_max_size_mb=1)
        try:
            with pytest.raises(WhatsAppMediaError):
                await client.download_media("media-1")
        finally:
            await client.close()


async def test_download_media_mime_no_permitido_lanza_media_error() -> None:
    graph = FakeGraph()
    graph.media_content_type = "text/plain"
    async with _graph_server(graph) as base:
        client = _client(base)
        try:
            with pytest.raises(WhatsAppMediaError):
                await client.download_media("media-1")
        finally:
            await client.close()


async def test_download_media_timeout_lanza_media_error() -> None:
    async with _silent_server() as base:
        client = _client(base, whatsapp_http_timeout_seconds=0.05)
        try:
            with pytest.raises(WhatsAppMediaError):
                await client.download_media("media-1")
        finally:
            await client.close()
