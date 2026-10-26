"""Moneda única soportada (Fase 3.1.1).

El sistema trabaja únicamente con EUR. El campo `currency` se mantiene en los
modelos, preparado para una futura fase de multi-moneda real, pero ahora mismo
CUALQUIER otra moneda se rechaza de forma explícita en la capa de
dominio/servicio: sin conversiones, sin cambios silenciosos.

`SUPPORTED_CURRENCY` es la fuente única del valor "EUR" (evita duplicar el
literal por todo el código).
"""

from __future__ import annotations

SUPPORTED_CURRENCY = "EUR"


def normalize_currency(value: str | None) -> str:
    """Normaliza y valida una moneda.

    Devuelve `SUPPORTED_CURRENCY` si el valor (sin importar mayúsculas o
    espacios: "eur", "EUR", " eur ") coincide con la moneda soportada, o si es
    nulo/vacío (equivale al default). En cualquier otro caso lanza un
    `ValueError` con mensaje claro, sin convertir ni adivinar.
    """
    if value is None or not value.strip():
        return SUPPORTED_CURRENCY
    cleaned = value.strip().upper()[:8]
    if cleaned == SUPPORTED_CURRENCY:
        return SUPPORTED_CURRENCY
    raise ValueError(
        f"Moneda no soportada: '{value.strip()}'. Actualmente el asistente trabaja únicamente con {SUPPORTED_CURRENCY}."
    )
