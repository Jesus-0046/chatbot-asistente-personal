"""Secreto y hashing del acceso Web (Fase 10.0).

- Contraseñas: Argon2id (argon2-cffi), hash PHC almacenable tal cual.
- Emails: se normalizan (trim + minúsculas) antes de comparar/guardar.
- Tokens de sesión y códigos de enlace: NUNCA se persiste el valor crudo; solo
  su SHA-256 (64 caracteres hex). El valor crudo se entrega una sola vez.
"""

from __future__ import annotations

import hashlib
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_PH = PasswordHasher()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Alfabeto legible (sin 0/O/1/I) para códigos de enlace pegables por humanos.
_BIND_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def normalize_email(email: str) -> str:
    """Devuelve el email normalizado para comparar/almacenar."""
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(_EMAIL_RE.match(normalize_email(email)))


def hash_password(password: str) -> str:
    return _PH.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verifica un password contra un hash PHC sin filtrar el motivo del fallo."""
    try:
        return _PH.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def generate_token() -> str:
    """Token Bearer crudo (solo se persistirá su SHA-256)."""
    return secrets.token_urlsafe(32)


def generate_binding_code() -> str:
    """Código de enlace de 8 caracteres legibles (single-use, hash-only)."""
    return "".join(secrets.choice(_BIND_ALPHABET) for _ in range(8))


def sha256_hex(value: str) -> str:
    """Digest SHA-256 (64 hex) usado como `*_hash` en BD."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
