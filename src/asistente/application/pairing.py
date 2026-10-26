"""Códigos de enlace de WhatsApp (Fase 9).

Un código tiene 8 caracteres (alfabeto sin caracteres ambiguos), caduca a los
30 minutos y es de un solo uso. En BD SOLO se guarda su `sha256`; el texto
plano se muestra una única vez (en la respuesta al mensaje de WhatsApp) y no
se persiste nunca.
"""

from __future__ import annotations

import hashlib
import secrets

PAIR_CODE_LENGTH = 8
PAIR_CODE_TTL_SECONDS = 30 * 60
# Sin I/O/0/1/L para evitar ambigüedades (p. ej. 0-O, 1-I, l-I).
_PAIR_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_pair_code() -> str:
    """Genera un código de enlace de 8 caracteres (entropía ~46 bits)."""
    return "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(PAIR_CODE_LENGTH))


def hash_pair_code(code: str) -> str:
    """Hash (sha256) del código tal y como se guarda en `user_channels`."""
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def looks_like_pair_code(code: str) -> bool:
    """Guardia barata: formato esperado antes de tocar la BD."""
    return len(code) == PAIR_CODE_LENGTH and all(c in _PAIR_ALPHABET for c in code.upper())
