"""Tests unitarios del servicio de dominio de imágenes (Fase 7).

Cubre: límites configurables, normalización de extracciones (ticket, tareas,
lista de la compra, nota), coherencia suma vs total, confianza por campo,
nunca inventar año, privacidad del payload, y detección de duplicados sin
bloqueo (aislada por usuario).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from tests.unit.helpers import make_settings
from tests.unit.image_fakes import (
    FakeTransactionQuery,
    FakeTransactionRow,
    FakeVisionProvider,
    fresh_note,
    fresh_shopping,
    fresh_tasks,
    fresh_ticket,
    make_image,
)

from asistente.ai.vision import ImageExtractionError
from asistente.domain.images import ImageAnalysisService, friendly_image_error


def make_service(
    *,
    payload: dict[str, Any] | None = None,
    error: Exception | None = None,
    rows: list[FakeTransactionRow] | None = None,
    **settings_overrides: Any,
) -> ImageAnalysisService:
    settings = make_settings(**settings_overrides)
    provider = FakeVisionProvider(payload=payload, error=error)
    return ImageAnalysisService(
        provider=provider,
        settings=settings,
        transactions=FakeTransactionQuery(rows=rows),
    )


def _collect(d: Any, into: list[Any]) -> None:
    if isinstance(d, dict):
        for v in d.values():
            _collect(v, into)
    elif isinstance(d, list):
        for v in d:
            _collect(v, into)
    else:
        into.append(d)


def _assert_no_floats(proposal: dict[str, Any]) -> None:
    values: list[Any] = []
    _collect(proposal, values)
    assert not any(isinstance(v, float) for v in values), "la propuesta lleva floats (debe ser Decimal/texto)"


def _assert_no_image_data(proposal: dict[str, Any]) -> None:
    dumped = json.dumps(proposal)
    assert "base64" not in dumped.lower()
    assert "file_id" not in dumped
    assert "file_1" not in dumped


# --------------------------------------------------------------------------- #
# Ticket feliz                                                                #
# --------------------------------------------------------------------------- #
async def test_ticket_extrae_y_construye_propuesta_completa() -> None:
    service = make_service()
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])

    assert analysis.proposal["kind"] == "ticket"
    expense = analysis.proposal["expense"]
    assert expense["amount"] == "28.80"  # canónico, parseable por Decimal
    assert expense["merchant"] == "Mercadona"
    assert expense["occurred_at"] == "2026-09-20T16:42:00+00:00"  # 18:42 local Europe/Madrid (DST)
    assert expense["category"] is None
    assert analysis.confidence == "high"
    assert analysis.warnings == []
    assert "Mercadona" in analysis.reply and "28,80" in analysis.reply  # coma solo en presentación


async def test_propuesta_sin_floats_ni_datos_de_imagen() -> None:
    analysis = await make_service().analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    _assert_no_floats(analysis.proposal)
    _assert_no_image_data(analysis.proposal)


# --------------------------------------------------------------------------- #
# Límites configurables                                                       #
# --------------------------------------------------------------------------- #
async def test_demasiadas_imagenes_por_mensaje_es_error_categorizado() -> None:
    service = make_service(image_max_per_message=1)
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image(), make_image()])
    assert exc.value.category == "too_many_images"


async def test_imagen_sin_datos_es_unreadable() -> None:
    service = make_service()
    attachment = replace(make_image(size=10), data=b"")
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[attachment])
    assert exc.value.category in ("size_exceeded", "unreadable")


async def test_tamano_maximo_configurable_se_aplica() -> None:
    service = make_service(image_max_size_mb=10)
    big = make_image(size=11 * 1024 * 1024)
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[big])
    assert exc.value.category == "size_exceeded"


async def test_formato_no_soportado_se_rechaza() -> None:
    service = make_service()
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image(mime_type="image/gif")])
    assert exc.value.category == "unsupported_format"


# --------------------------------------------------------------------------- #
# Errores del proveedor                                                      #
# --------------------------------------------------------------------------- #
async def test_error_del_proveedor_se_propaga_con_categoria() -> None:
    service = make_service(error=ImageExtractionError("timeout", "se acabó el tiempo"))
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "timeout"


async def test_excepcion_inesperada_del_proveedor_se_convierte_en_provider_unavailable() -> None:
    service = make_service(error=RuntimeError("boom"))
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "provider_unavailable"


async def test_respuesta_sin_kind_es_invalid_response() -> None:
    service = make_service(payload={"ticket": {}})
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "invalid_response"


async def test_kind_desconocido_es_unreadable() -> None:
    service = make_service(payload={"kind": "unknown"})
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "unreadable"


def test_friendly_image_error_devuelve_texto_claro() -> None:
    assert "tardado demasiado" in friendly_image_error(ImageExtractionError("timeout", "x"))
    assert "solo JPG" in friendly_image_error(ImageExtractionError("unsupported_format", "x"))


# --------------------------------------------------------------------------- #
# Confianza y datos dudosos                                                   #
# --------------------------------------------------------------------------- #
async def test_ticket_con_total_ilegible_degrada_confianza_a_low() -> None:
    service = make_service(payload=fresh_ticket(total="???"))
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert analysis.proposal["expense"]["amount"] is None
    assert analysis.confidence == "low"
    assert any("importe" in w.lower() or "reconozco" in w.lower() for w in analysis.warnings)


async def test_total_negativo_se_rechaza_como_ilegible() -> None:
    service = make_service(payload=fresh_ticket(total="-28,80"))
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert analysis.confidence == "low"
    assert any("negativo" in w.lower() or "total" in w.lower() for w in analysis.warnings)


async def test_ticket_en_otra_moneda_no_propone_nada() -> None:
    service = make_service(payload=fresh_ticket(currency="USD"))
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert exc.value.category == "insufficient_confidence"


async def test_discrepancia_suma_lineas_vs_total_avisa_y_degrada() -> None:
    payload = fresh_ticket(total="30,00")
    service = make_service(payload=payload)
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert any("no coincide" in w for w in analysis.warnings)
    assert analysis.confidence == "low"
    assert "suma de las líneas legibles (28,80 €)" in analysis.reply


async def test_fecha_sin_año_nunca_se_asume() -> None:
    service = make_service(payload=fresh_ticket(date_v="12/03"))
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert analysis.proposal["expense"]["occurred_at"] is None
    assert any("fecha" in w.lower() for w in analysis.warnings)
    assert analysis.confidence == "low"


async def test_incoherencia_en_linea_avisa_sin_romper() -> None:
    items = [
        {
            "description": {"value": "Pan", "confidence": "high"},
            "quantity": {"value": "2", "confidence": "high"},
            "unit_price": {"value": "1,00", "confidence": "high"},
            "total_price": {"value": "3,00", "confidence": "high"},
        },
        {
            "description": {"value": "Café", "confidence": "high"},
            "quantity": {"value": "1", "confidence": "high"},
            "unit_price": {"value": "25,40", "confidence": "high"},
            "total_price": {"value": "25,40", "confidence": "high"},
        },
    ]
    service = make_service(payload=fresh_ticket(items=items, total="28,40"))
    analysis = await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert any("no cuadra" in w for w in analysis.warnings)


async def test_caracteres_extraidos_limitados_por_config() -> None:
    content = "x" * 500
    service = make_service(payload=fresh_note(content=content), image_max_extracted_chars=100)
    analysis = await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert len(analysis.proposal["structured_note"]["content"]) == 100


# --------------------------------------------------------------------------- #
# Tipos de extracción no-ticket                                               #
# --------------------------------------------------------------------------- #
async def test_lista_de_tareas_se_normaliza() -> None:
    analysis = await make_service(payload=fresh_tasks()).analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert analysis.proposal["kind"] == "tasks"
    assert analysis.proposal["tasks"][0]["title"] == "Comprar pan"
    assert analysis.proposal["tasks"][0]["due_date"] == "2026-09-25"
    assert analysis.proposal["tasks"][1]["due_date"] is None  # sin fecha: no se inventa
    assert analysis.confidence in ("high", "medium")


async def test_lista_de_tareas_sin_titulos_es_unreadable() -> None:
    service = make_service(payload=fresh_tasks(tasks=[{"title": {"value": "", "confidence": "low"}}]))
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "unreadable"


async def test_lista_de_la_compra_se_normaliza() -> None:
    analysis = await make_service(payload=fresh_shopping()).analyze(
        user_id=7, user_tz="UTC", attachments=[make_image()]
    )
    assert analysis.proposal["kind"] == "shopping_list"
    assert analysis.proposal["shopping_list"]["items"][0]["name"] == "Pan de molde"
    assert analysis.proposal["shopping_list"]["items"][0]["quantity"] == "2"
    assert "guardo como nota" in analysis.reply


async def test_lista_de_la_compra_sin_lineas_es_unreadable() -> None:
    service = make_service(payload=fresh_shopping(items=[]))
    with pytest.raises(ImageExtractionError) as exc:
        await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert exc.value.category == "unreadable"


async def test_nota_estructurada_se_normaliza() -> None:
    analysis = await make_service(payload=fresh_note()).analyze(user_id=7, user_tz="UTC", attachments=[make_image()])
    assert analysis.proposal["kind"] == "structured_note"
    assert analysis.proposal["structured_note"]["content"] == "4 huevos, 2 patatas, sal."
    assert "¿La guardo?" in analysis.reply


# --------------------------------------------------------------------------- #
# Duplicados: se avisa, nunca se bloquea, y son por-usuario                   #
# --------------------------------------------------------------------------- #
def _row(user_id: int, merchant: str, amount: str, day: datetime) -> FakeTransactionRow:
    return FakeTransactionRow(user_id=user_id, amount=Decimal(amount), occurred_at=day, merchant=merchant)


async def test_duplicado_mismo_comercio_fecha_total_avisa() -> None:
    today = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    rows = [_row(7, "Mercadona", "28.80", today)]
    analysis = await make_service(rows=rows).analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert any("Posible duplicado" in w for w in analysis.warnings)


async def test_importe_distinto_no_es_duplicado() -> None:
    today = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    rows = [_row(7, "Mercadona", "99.99", today)]
    analysis = await make_service(rows=rows).analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert not any("Posible duplicado" in w for w in analysis.warnings)


async def test_duplicado_de_otro_usuario_no_avisa() -> None:
    today = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    rows = [_row(8, "Mercadona", "28.80", today)]
    analysis = await make_service(rows=rows).analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert not any("duplicado" in w.lower() for w in analysis.warnings)


async def test_duplicado_otro_dia_no_avisa() -> None:
    other_day = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    rows = [_row(7, "Mercadona", "28.80", other_day)]
    analysis = await make_service(rows=rows).analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert not any("duplicado" in w.lower() for w in analysis.warnings)


async def test_duplicado_avisa_pero_la_propuesta_sigue_activa() -> None:
    today = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    rows = [_row(7, "Mercadona", "28.80", today)]
    analysis = await make_service(rows=rows).analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    assert analysis.proposal["expense"]["amount"] == "28.80"
    assert analysis.proposal["kind"] == "ticket"


# --------------------------------------------------------------------------- #
# Caption como pista / aislamiento de llamadas                                #
# --------------------------------------------------------------------------- #
async def test_caption_se_pasa_como_pista_al_proveedor() -> None:
    settings = make_settings()
    provider = FakeVisionProvider()
    service = ImageAnalysisService(provider=provider, settings=settings, transactions=FakeTransactionQuery())
    await service.analyze(user_id=7, user_tz="UTC", attachments=[make_image()], caption="ticket del súper")
    assert provider.hints == ["ticket del súper"]


async def test_el_metodo_consulta_historico_con_ventana_del_dia() -> None:
    transactions = FakeTransactionQuery()
    settings = make_settings()
    provider = FakeVisionProvider()
    service = ImageAnalysisService(provider=provider, settings=settings, transactions=transactions)
    await service.analyze(user_id=7, user_tz="Europe/Madrid", attachments=[make_image()])
    user_id, kwargs = transactions.calls[0]
    assert user_id == 7
    assert kwargs["transaction_type"] == "expense"
    assert kwargs["merchant"] == "Mercadona"
    assert kwargs["start_utc"].replace(tzinfo=None) < datetime(2026, 9, 21)
    assert (kwargs["end_utc"] - kwargs["start_utc"]) == timedelta(days=1)
