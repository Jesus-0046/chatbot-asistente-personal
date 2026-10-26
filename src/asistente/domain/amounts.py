"""Importes monetarios: SIEMPRE `Decimal`, nunca floats.

`NUMERIC(12, 2)` en BD; aquí se parsean importes en texto libre
("28,80", "35 euros", "1.250,50", "19") y se redondean al céntimo.

Funciones:
- `parse_amount`             gastos/ingresos y traspasos: siempre > 0.
- `parse_amount_non_negative` saldos iniciales de cuentas no de crédito: >= 0.
- `parse_amount_signed`      `initial_balance` genérico: acepta negativos
                             (-350 para una tarjeta de crédito); el signo
                             correcto lo decide `AccountService` según tipo.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

CENTS = Decimal("0.01")


def parse_amount(text: str) -> Decimal:
    """Interpreta un importe de texto natural/ISO.

    Acepta separador decimal con coma o punto y separador de miles con punto o
    coma. Rechaza valores no numéricos o no positivos. Redondea al céntimo.
    """
    return _parse_amount_text(text, allow_zero=False, allow_negative=False)


def parse_amount_non_negative(text: str) -> Decimal:
    """Como `parse_amount` pero acepta 0 y rechaza negativos.

    Se usa para `initial_balance` de cuentas de tipo no crédito (>= 0).
    """
    return _parse_amount_text(text, allow_zero=True, allow_negative=False)


def parse_amount_signed(text: str) -> Decimal:
    """Acepta importes positivos, cero y negativos.

    Permite representar correctamente una deuda inicial de tarjeta de crédito
    ("-350"). El rango válido según el tipo de cuenta lo valida el servicio.
    """
    return _parse_amount_text(text, allow_zero=True, allow_negative=True)


def _parse_amount_text(text: str, *, allow_zero: bool, allow_negative: bool) -> Decimal:
    raw = (text or "").strip().lower().replace("€", "").replace("eur", "").replace("euros", "")
    match = re.search(r"-?\d[\d.,]*", raw)
    if not match:
        raise ValueError(f"No reconozco el importe: '{text}'.")
    digits_raw = match.group(0)
    negative = digits_raw.startswith("-")
    digits = digits_raw[1:] if negative else digits_raw

    amount: Decimal | None = None
    if "," in digits and "." in digits:
        # "1.250,50" → miles con punto, decimal con coma.
        int_part, frac = digits.rsplit(",", 1)
        if 1 <= len(frac) <= 2:
            amount = Decimal(f"{int_part.replace('.', '')}.{frac}")
    if amount is None and "," in digits:
        int_part, frac = digits.rsplit(",", 1)
        if 1 <= len(frac) <= 2:
            amount = Decimal(f"{int_part.replace('.', '')}.{frac}")
        else:
            amount = Decimal(int_part.replace(",", "").replace(".", ""))
    if amount is None and "." in digits:
        int_part, frac = digits.rsplit(".", 1)
        if 1 <= len(frac) <= 2:
            amount = Decimal(f"{int_part.replace(',', '')}.{frac}")
        else:
            amount = Decimal(digits.replace(",", "").replace(".", ""))
    if amount is None:
        amount = Decimal(digits)

    if negative:
        amount = -amount

    if not allow_negative and amount < 0:
        raise ValueError(
            "El importe debe ser mayor o igual a cero." if allow_zero else "El importe debe ser mayor que cero."
        )
    if not allow_zero and amount == 0:
        raise ValueError("El importe debe ser mayor que cero.")
    return amount.quantize(CENTS, rounding=ROUND_HALF_UP)


def fmt_amount(amount: Decimal) -> str:
    """Formato estable para mostrar/almacenar: siempre 2 decimales."""
    return format(amount, ".2f")


def fmt_amount_eu(amount: Decimal) -> str:
    """Formato de presentación en español de España: coma decimal.

    Usado SOLO en texto dirigido al usuario ("28,80 €"); los datos
    estructurados y la persistencia siguen usando `fmt_amount` ("28.80")
    que es directamente parseable por `Decimal`.
    """
    return fmt_amount(amount).replace(".", ",")


def parse_extracted_amount(text: str | None) -> Decimal:
    """Interpreta un importe procedente de OCR/extracción visual (Fase 7).

    Estricto y europeo: la coma SIEMPRE es el separador decimal ("28,80",
    "1.250,50"), el punto SOLO como separador de miles salvo decimal punto en
    casos inequívocos ("28.80", "9.95"). Rechaza valores ambiguos >2 decimales
    ("12,345") en lugar de truncarlos en silencio. Devuelve `Decimal` al
    céntimo; los importes de ticket nunca pueden ser negativos.
    """
    raw = (text or "").strip().replace("\u00a0", " ").replace("\u202f", " ").lower()
    raw = raw.replace("€", "").replace("eur", "").replace("euros", "").replace(" ", "")
    if raw.startswith("+"):
        raw = raw[1:]
    if not raw:
        raise ValueError("No hay importe en la extracción.")
    if raw.startswith("-"):
        raise ValueError("El importe de un ticket no puede ser negativo.")
    if not re.fullmatch(r"\d[\d.,]*", raw):
        raise ValueError(f"No reconozco el importe extraído: '{text}'.")

    amount: Decimal | None = None
    if "," in raw:
        int_part, frac = raw.rsplit(",", 1)
        if not (1 <= len(frac) <= 2):
            raise ValueError(f"Importe extraído ambiguo (más de 2 decimales): '{text}'.")
        if "," in int_part:
            raise ValueError(f"Importe extraído ambiguo (comas repetidas): '{text}'.")
        amount = Decimal(f"{int_part.replace('.', '').replace(',', '')}.{frac}")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts) != 2:
            raise ValueError(f"Importe extraído ambiguo (varios puntos): '{text}'.")
        int_part, frac = parts
        if 1 <= len(frac) <= 2 and len(int_part) <= 3:
            amount = Decimal(f"{int_part}.{frac}")
        elif len(frac) == 3 and int_part.isdigit() and len(int_part) <= 3:
            amount = Decimal(f"{int_part}{frac}")  # "1.250" europeo → miles
        else:
            raise ValueError(f"Importe extraído ambiguo: '{text}'.")
    else:
        amount = Decimal(raw)

    amount = amount.quantize(CENTS, rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ValueError("El importe de un ticket debe ser mayor que cero.")
    return amount
