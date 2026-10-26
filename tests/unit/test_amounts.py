"""Tests de parseo de importes (Fase 3).

`parse_amount` rechaza 0 y negativos (gastos/ingresos/traspasos siempre > 0);
`parse_amount_non_negative` acepta 0 pero rechaza negativos (saldo inicial de
cuentas no de crédito); `parse_amount_signed` acepta negativos/cero/positivos
(saldo inicial de tarjetas de crédito, ej. "-350").
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from asistente.domain.amounts import (
    fmt_amount,
    parse_amount,
    parse_amount_non_negative,
    parse_amount_signed,
)


def test_parse_amount_acepta_positivos() -> None:
    assert parse_amount("28,80") == Decimal("28.80")
    assert parse_amount("35 euros") == Decimal("35.00")
    assert parse_amount("1.250,50") == Decimal("1250.50")


def test_parse_amount_rechaza_cero() -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        parse_amount("0")


def test_parse_amount_rechaza_cero_varias_formas() -> None:
    for cero in ("0", "0.0", "0,00", "0.00"):
        with pytest.raises(ValueError, match="mayor que cero"):
            parse_amount(cero)


def test_parse_amount_rechaza_negativo() -> None:
    with pytest.raises(ValueError, match="mayor que cero"):
        parse_amount("-5")


def test_parse_amount_non_negative_acepta_cero() -> None:
    assert parse_amount_non_negative("0") == Decimal("0.00")
    assert parse_amount_non_negative("0.0") == Decimal("0.00")
    assert parse_amount_non_negative("0,00") == Decimal("0.00")


def test_parse_amount_non_negative_acepta_positivos() -> None:
    assert parse_amount_non_negative("500") == Decimal("500.00")
    assert parse_amount_non_negative("28,80") == Decimal("28.80")


def test_parse_amount_non_negative_rechaza_negativo() -> None:
    with pytest.raises(ValueError, match="mayor o igual a cero"):
        parse_amount_non_negative("-5")


def test_parse_amount_signed_acepta_negativos() -> None:
    assert parse_amount_signed("-350") == Decimal("-350.00")
    assert parse_amount_signed("-28,80") == Decimal("-28.80")
    assert parse_amount_signed(" -120.50") == Decimal("-120.50")
    assert parse_amount_signed("-350 euros") == Decimal("-350.00")


def test_parse_amount_signed_acepta_cero_y_positivos() -> None:
    assert parse_amount_signed("0") == Decimal("0.00")
    assert parse_amount_signed("0,00") == Decimal("0.00")
    assert parse_amount_signed("350") == Decimal("350.00")


def test_parse_amount_signed_rechaza_no_numerico() -> None:
    with pytest.raises(ValueError, match="importe"):
        parse_amount_signed("abc")


def test_fmt_amount_siempre_dos_decimales() -> None:
    assert fmt_amount(Decimal("0")) == "0.00"
    assert fmt_amount(Decimal("28.8")) == "28.80"
    assert fmt_amount(Decimal("-350")) == "-350.00"
