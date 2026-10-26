"""Integración PG: flujo de imágenes completo (omitido sin TEST_DATABASE_URL).

Uso:
  TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/integration -m db

Verifica el invariante de la Fase 7 sobre PostgreSQL real: la imagen genera
una propuesta que NO escribe nada; solo al confirmar `apply_extracted_data`
persiste el gasto (una única transacción), en el user_id del autenticado, y
la confirmación pendiente nunca contiene la imagen ni binario alguno.
"""

from __future__ import annotations

import os
from decimal import Decimal
from typing import Any

import pytest
import sqlalchemy as sa
from tests.unit.helpers import make_services
from tests.unit.image_fakes import FakeVisionProvider, make_image

from asistente.ai.llm import LLMResponse
from asistente.application.context import InboundMessage
from asistente.application.orchestrator import Orchestrator
from asistente.application.services import Services
from asistente.persistence.db import Database
from asistente.tools import create_tool_registry

pytestmark = pytest.mark.db

TELEGRAM = 919293


class FakeLLM:
    def chat(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> LLMResponse:
        return LLMResponse(content="Registrado en PostgreSQL.", tool_calls=[])


def _inbound(text: str = "", *, attachments: list | None = None) -> InboundMessage:
    return InboundMessage(
        user_id=TELEGRAM,
        text=text,
        display_name="PG",
        metadata={"username": "pg_user", "name": None},
        attachments=attachments or [],
    )


@pytest.mark.asyncio
async def test_flujo_completo_imagen_consulta_en_pg_real() -> None:
    db = Database(os.environ["TEST_DATABASE_URL"])
    db.connect()
    services: Services = make_services(db)
    user = await services.users.ensure_user(
        telegram_user_id=TELEGRAM, name="PG", username="pg_user", timezone="Europe/Madrid", lang="es-ES"
    )
    uid = user.id
    await services.accounts.create(
        user_id=uid,
        name="Cuenta PG",
        type="bank",
        currency="EUR",
        initial_balance=Decimal("1000.00"),
    )
    orchestrator = Orchestrator(
        services=services,
        llm=FakeLLM(),
        tools=create_tool_registry(),
        vision=FakeVisionProvider(),
    )
    try:
        out = await orchestrator.handle(_inbound(attachments=[make_image()]))
        assert "Mercadona" in out.text

        pending = await services.confirmations.find_open(uid)
        assert pending is not None
        dumped = str(pending["payload"])
        assert "file_id" not in dumped and "base64" not in dumped.lower()

        before = len(await services.transactions.list(uid, limit=100))
        assert before == 0  # la imagen por sí sola no escribe

        out2 = await orchestrator.handle(_inbound("sí"))
        assert out2.text == "Registrado en PostgreSQL."

        txns = await services.transactions.list(uid, limit=100)
        assert len(txns) == 1
        txn = txns[0]
        assert isinstance(txn.amount, Decimal)
        assert txn.amount == Decimal("28.80")
        assert txn.merchant == "Mercadona"

        row = await _select_txn(db, uid)
        assert row is not None
        assert row[1] == "Mercadona"
    finally:
        await _cleanup(db, uid)
        await db.close()


async def _select_txn(db: Database, uid: int):
    async with db.session() as session:
        result = await session.execute(
            sa.text(
                "SELECT id, merchant, amount, type FROM transactions WHERE user_id = :uid ORDER BY id DESC LIMIT 1"
            ),
            {"uid": uid},
        )
        return result.first()


async def _cleanup(db: Database, uid: int) -> None:
    async with db.session() as session:
        await session.execute(sa.text("DELETE FROM users WHERE id = :uid"), {"uid": uid})
        await session.commit()
