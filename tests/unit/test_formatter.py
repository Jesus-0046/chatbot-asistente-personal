"""Tests unitarios: formateo HTML para Telegram."""

from __future__ import annotations

from asistente.adapters.telegram.formatter import escape_html


def test_escape_html_escapa_caracteres_especiales() -> None:
    assert escape_html("<b>hello & world</b>") == "&lt;b&gt;hello &amp; world&lt;/b&gt;"


def test_escape_html_ultraligero_sin_caracteres() -> None:
    assert escape_html("texto plano 123") == "texto plano 123"


def test_escape_html_none() -> None:
    assert escape_html(None) == ""
