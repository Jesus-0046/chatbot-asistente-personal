"""Tests unitarios de parsing de campos extraídos de imágenes (Fase 7).

Reglas clave: importes SIEMPRE a Decimal al céntimo (nunca float), las fechas
sin año NO se asumen, y las horas ambiguas no se inventan.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from asistente.domain.amounts import fmt_amount, fmt_amount_eu, parse_extracted_amount
from asistente.domain.images import (
    _safe_date,
    _validate_ticket_items,
    normalize_extracted_date,
    normalize_extracted_time,
    parse_quantity,
)


# --------------------------------------------------------------------------- #
# parse_extracted_amount                                                      #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("28,80", Decimal("28.80")),
        ("28.80", Decimal("28.80")),
        ("1.250,99", Decimal("1250.99")),
        ("1 250,99", Decimal("1250.99")),
        ("€ 28,80", Decimal("28.80")),
        ("28,8", Decimal("28.80")),
        ("1250", Decimal("1250.00")),
        ("0,50", Decimal("0.50")),
        ("1,20 €", Decimal("1.20")),
        ("  " + "12,34" + "  ", Decimal("12.34")),
    ],
)
def test_parse_extracted_amount_ok(raw: str, expected: Decimal) -> None:
    assert parse_extracted_amount(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "abc",
        "28,803",  # 3 decimales: ambiguo
        "12.345,678",
        "0",
        "-28,80",
        "28,,80",
        "1.2.3",
        "28,80,50",
        "28:80",
    ],
)
def test_parse_extracted_amount_rechaza_ambiguos_o_ilegibles(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_extracted_amount(raw)


def test_parse_extracted_amount_devuelve_decimal_nunca_float() -> None:
    value = parse_extracted_amount("28,80")
    assert isinstance(value, Decimal)
    assert fmt_amount(value) == "28.80"  # canónico para datos/persistencia
    assert fmt_amount_eu(value) == "28,80"  # coma solo en presentación


# --------------------------------------------------------------------------- #
# normalize_extracted_date                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-20", date(2026, 9, 20)),
        ("2026/09/20", date(2026, 9, 20)),
        ("20/09/2026", date(2026, 9, 20)),
        ("20-09-2026", date(2026, 9, 20)),
        ("20.09.2026", date(2026, 9, 20)),
        ("20 de septiembre de 2026", date(2026, 9, 20)),
        ("20 sep 2026", date(2026, 9, 20)),
        ("20 septiembre 2026", date(2026, 9, 20)),
    ],
)
def test_normalize_extracted_date_ok(raw: str, expected: date) -> None:
    parsed, warning = normalize_extracted_date(raw)
    assert parsed == expected
    assert warning is None


@pytest.mark.parametrize(
    "raw",
    [
        "12/03",  # sin año
        "20/09/26",  # año ambiguo
        "12 marzo",  # sin año
        "20 sep 26",
        "31/02/2026",  # fecha imposible
        "32/13/2026",
        "1 de 13 de 2026",
        "mes desconocido 2026",
    ],
)
def test_normalize_extracted_date_sin_año_o_ilegible_devuelve_none(raw: str) -> None:
    parsed, warning = normalize_extracted_date(raw)
    assert parsed is None
    assert warning  # siempre explicamos el porqué (regla: no asumir año)


def test_normalize_extracted_date_vacio_devuelve_none_sin_aviso() -> None:
    assert normalize_extracted_date("") == (None, None)
    assert normalize_extracted_date(None) == (None, None)
    assert normalize_extracted_date("   ") is None or normalize_extracted_date("   ")[0] is None


def test_normalize_extracted_date_nunca_inventa_año() -> None:
    parsed, warning = normalize_extracted_date("12/03")
    assert parsed is None
    assert warning  # se explica por qué no se usa (regla: nunca asumir año)


# --------------------------------------------------------------------------- #
# normalize_extracted_time                                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("raw", "hour", "minute"),
    [
        ("18:42", 18, 42),
        ("18.42", 18, 42),
        ("6:30 pm", 18, 30),
        ("6:30 PM", 18, 30),
        ("6:30 am", 6, 30),
        ("12:00 pm", 12, 0),
        ("12:00 am", 0, 0),
        ("09:05:30", 9, 5),
    ],
)
def test_normalize_extracted_time_ok(raw: str, hour: int, minute: int) -> None:
    parsed = normalize_extracted_time(raw)
    assert parsed is not None
    assert parsed.hour == hour
    assert parsed.minute == minute


@pytest.mark.parametrize("raw", ["", None, "sin hora", "25:00", "24:61", "abc"])
def test_normalize_extracted_time_ilegible_devuelve_none(raw: str) -> None:
    assert normalize_extracted_time(raw) is None


# --------------------------------------------------------------------------- #
# parse_quantity                                                             #
# --------------------------------------------------------------------------- #
def test_parse_quantity_extrae_numero() -> None:
    assert parse_quantity("2") == Decimal("2")
    assert parse_quantity("1 kg") == Decimal("1")
    assert parse_quantity("0,5 docenas") == Decimal("0.50")
    assert parse_quantity("") is None
    assert parse_quantity("varios") is None


# --------------------------------------------------------------------------- #
# _validate_ticket_items: coherencia suma líneas vs total                     #
# --------------------------------------------------------------------------- #
def test_validate_items_suma_coherente_sin_avisos() -> None:
    items = [
        {"description": "Pan", "quantity": "1", "unit_price": "1,20", "total_price": "1,20"},
        {"description": "Leche", "quantity": "2", "unit_price": "1,10", "total_price": "2,20"},
        {"description": "Café", "quantity": "1", "unit_price": "25,40", "total_price": "25,40"},
    ]
    total_sum, warnings = _validate_ticket_items(items)
    assert total_sum == Decimal("28.80")
    assert warnings == []


def test_validate_items_detecta_linea_que_no_cuadra() -> None:
    items = [
        {"description": "Pan", "quantity": "2", "unit_price": "1,00", "total_price": "3,00"},  # 2×1 ≠ 3
    ]
    total_sum, warnings = _validate_ticket_items(items)
    assert total_sum == Decimal("3.00")
    assert any("no cuadra" in w for w in warnings)


def test_validate_items_avisa_de_lineas_ilegibles() -> None:
    items = [
        {"description": "Pan", "quantity": "", "unit_price": "", "total_price": ""},
        {"description": "Leche", "quantity": "1", "unit_price": "1,10", "total_price": "1,10"},
    ]
    total_sum, warnings = _validate_ticket_items(items)
    assert total_sum == Decimal("1.10")
    assert any("líneas" in w for w in warnings)


def test_validate_items_calcula_suma_solo_con_cantidad_y_precio_unitario() -> None:
    items = [
        {"description": "Fruta", "quantity": "2", "unit_price": "1,50", "total_price": ""},
    ]
    total_sum, warnings = _validate_ticket_items(items)
    assert total_sum == Decimal("3.00")


def test_safe_date_imposible_devuelve_none() -> None:
    assert _safe_date(2026, 2, 30) is None
    assert _safe_date(2026, 4, 31) is None


def test_fechas_con_hora_se_combinan_en_utc() -> None:
    # Regla de oro: importes y fechas viajan como texto/iso, nunca se pierde Decimal.
    d, _ = normalize_extracted_date("20/09/2026")
    t = normalize_extracted_time("18:42")
    assert d is not None and t is not None
    combined = datetime.combine(d, t, tzinfo=UTC)
    assert combined.isoformat().startswith("2026-09-20T18:42")
