"""Tests unitarios del webhook de WhatsApp (Fase 9).

Cubre la verificación de suscripción (GET /webhook) y la autenticación de los
POST (`X-Hub-Signature-256`). La firma se calcula con el body CRUDO; nada de
esto depende de PostgreSQL.
"""

from __future__ import annotations

import hashlib
import hmac
import json

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from tests.unit.helpers import make_settings

from asistente.adapters.whatsapp.webhook import _signature_ok, build_webhook_app
from asistente.config import Settings

APP_SECRET = "app_secret_de_prueba"
VERIFY_TOKEN = "mi_token_de_verificacion"


def _settings() -> Settings:
    return make_settings(
        whatsapp_enabled=True,
        whatsapp_verify_token=VERIFY_TOKEN,
        whatsapp_app_secret=APP_SECRET,
    )


def _sign(raw: bytes, secret: str = APP_SECRET) -> str:
    expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return f"sha256={expected}"


def _app(channel: object) -> web.Application:
    return build_webhook_app(channel, _settings())  # type: ignore[arg-type]


def _client(app: web.Application):
    return TestClient(TestServer(app))


# --------------------------------------------------------------------------- #
# Firma (helper puro)                                                         #
# --------------------------------------------------------------------------- #
class _FakeHeadersRequest:
    """Stub de `web.Request`: `_signature_ok` solo lee `headers`."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_firma_correcta_se_valida() -> None:
    raw = b'{"hola": true}'
    settings = _settings()
    request = _FakeHeadersRequest({"X-Hub-Signature-256": _sign(raw)})
    assert _signature_ok(request, raw, settings)  # type: ignore[arg-type]


def test_firma_ausente_o_incorrecta_se_rechaza() -> None:
    raw = b'{"hola": true}'
    settings = _settings()

    assert not _signature_ok(_FakeHeadersRequest({}), raw, settings)  # type: ignore[arg-type]
    assert not _signature_ok(  # type: ignore[arg-type]
        _FakeHeadersRequest({"X-Hub-Signature-256": "sha256=0123456789"}),
        raw,
        settings,
    )


def test_firma_con_prefijo_invalido_se_rechaza() -> None:
    raw = b"{}"
    settings = _settings()
    request = _FakeHeadersRequest({"X-Hub-Signature-256": "md5=" + _sign(raw).split("=", 1)[1]})
    assert not _signature_ok(request, raw, settings)  # type: ignore[arg-type]


def test_firma_no_coincide_con_otro_body() -> None:
    settings = _settings()
    request = _FakeHeadersRequest({"X-Hub-Signature-256": _sign(b'{"a":1}')})
    assert not _signature_ok(request, b'{"a":2}', settings)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# GET /webhook: verificación de suscripción                                   #
# --------------------------------------------------------------------------- #
async def test_verify_ok_devuelve_el_challenge() -> None:
    calls: list[str] = []

    class FakeChannel:
        def verify_token(self, token: str | None) -> bool:
            calls.append(token or "")
            return token == VERIFY_TOKEN

    app = _app(FakeChannel())
    async with _client(app) as client:
        resp = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "123456"},
        )
        assert resp.status == 200
        assert (await resp.text()) == "123456"
    assert calls == [VERIFY_TOKEN]


async def test_verify_con_token_incorrecto_devuelve_403() -> None:
    app = _app(FakeChannelIncorrecto())
    async with _client(app) as client:
        resp = await client.get(
            "/webhook",
            params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "x"},
        )
        assert resp.status == 403


class FakeChannelIncorrecto:
    def verify_token(self, token: str | None) -> bool:
        return token == VERIFY_TOKEN


async def test_verify_sin_mode_devuelve_403() -> None:
    app = _app(FakeChannelIncorrecto())
    async with _client(app) as client:
        resp = await client.get("/webhook", params={"hub.verify_token": "otro"})
        assert resp.status == 403


# --------------------------------------------------------------------------- #
# POST /webhook: autenticación y despacho                                    #
# --------------------------------------------------------------------------- #
class RecordingChannel:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def handle_payload(self, payload: dict) -> None:
        self.payloads.append(payload)


async def test_post_con_firma_valida_entrega_payload_y_devuelve_200() -> None:
    channel = RecordingChannel()
    app = _app(channel)
    payload = {"object": "whatsapp_business_account", "entry": [{"id": "e", "changes": []}]}
    body = json.dumps(payload).encode("utf-8")
    async with _client(app) as client:
        resp = await client.post("/webhook", data=body, headers={"X-Hub-Signature-256": _sign(body)})
        assert resp.status == 200
    assert channel.payloads == [payload]


async def test_post_sin_firma_devuelve_401_y_no_procesa() -> None:
    channel = RecordingChannel()
    app = _app(channel)
    async with _client(app) as client:
        resp = await client.post(
            "/webhook",
            data=b'{"entry": []}',
            headers={"X-Hub-Signature-256": "sha256=000000"},
        )
        assert resp.status == 401
    assert channel.payloads == []


async def test_post_con_firma_valida_pero_json_invalido_devuelve_400() -> None:
    channel = RecordingChannel()
    app = _app(channel)
    body = b"{esto no es json"
    async with _client(app) as client:
        resp = await client.post("/webhook", data=body, headers={"X-Hub-Signature-256": _sign(body)})
        assert resp.status == 400
    assert channel.payloads == []


async def test_post_payload_no_dict_devuelve_200_sin_procesar() -> None:
    channel = RecordingChannel()
    app = _app(channel)
    body = b"[1, 2, 3]"
    async with _client(app) as client:
        resp = await client.post("/webhook", data=body, headers={"X-Hub-Signature-256": _sign(body)})
        assert resp.status == 200
    assert channel.payloads == []
