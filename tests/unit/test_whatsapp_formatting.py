"""Tests de división de texto para envío por WhatsApp (Fase 9)."""

from __future__ import annotations

from asistente.adapters.whatsapp.formatting import WHATSAPP_MAX_TEXT_CHARS, split_for_send


def test_texto_corto_se_devuelve_tal_cual() -> None:
    assert split_for_send("hola") == ["hola"]


def test_texto_vacio_devuelve_un_trozo_vacio() -> None:
    assert split_for_send("") == [""]


def test_texto_de_exactamente_el_limite() -> None:
    text = "a" * WHATSAPP_MAX_TEXT_CHARS
    assert split_for_send(text) == [text]


def test_texto_largo_se_parte_en_trozos_que_respetan_el_limite() -> None:
    text = " ".join(f"palabra{i}" for i in range(2000))
    chunks = split_for_send(text)
    assert len(chunks) > 1
    assert all(len(c) <= WHATSAPP_MAX_TEXT_CHARS for c in chunks)
    assert "".join(chunks).replace(" ", "") == text.replace(" ", "")


def test_no_se_parte_una_url_o_numero_normal() -> None:
    url = "https://example.com/ruta/ultralarga/" + "x" * 200
    number = "12345678901234567890"
    filler = " ".join(f"palabra-{i}" for i in range(700))
    text = f"{url} {number} {filler} fin"
    chunks = split_for_send(text)
    assert len(chunks) > 1
    assert all(len(c) <= WHATSAPP_MAX_TEXT_CHARS for c in chunks)
    assert any(url in c for c in chunks)
    assert any(number in c for c in chunks)


def test_token_mas_largo_que_el_limite_se_parte_por_caracteres() -> None:
    giant = "z" * (2 * WHATSAPP_MAX_TEXT_CHARS + 5)
    text = f"inicio {giant} final"
    chunks = split_for_send(text)
    assert all(len(c) <= WHATSAPP_MAX_TEXT_CHARS for c in chunks)
    assert chunks[0] == "inicio"
    assert chunks[1] == "z" * WHATSAPP_MAX_TEXT_CHARS
    assert chunks[2] == "z" * WHATSAPP_MAX_TEXT_CHARS
    assert chunks[-2] == "z" * 5
    assert chunks[-1] == "final"
    assert "".join(chunks) == text.replace(" ", "")


def test_los_saltos_de_linea_se_conservan_dentro_del_trozo() -> None:
    lines = [f"línea {i}\n" for i in range(50)]
    text = "".join(lines)
    chunks = split_for_send(text)
    joined = "".join(chunks)
    assert joined == text
    assert all(len(c) <= WHATSAPP_MAX_TEXT_CHARS for c in chunks)
