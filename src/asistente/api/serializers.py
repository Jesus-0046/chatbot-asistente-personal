"""Serializadores explícitos de la API Web (Fase 10.0).

- `serialize(obj)`: dict ordenado de las columnas del modelo (mapeo del
  esquema, no atributos arbitrarios), con `deep_json` aplicado a los valores.
- `deep_json(value)`: normaliza recursivamente Decimal → str y datetime/date →
  ISO 8601, y serializa objetos ORM anidados en dicts compuestos por los
  servicios de dominio.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from asistente.persistence.models import User


def deep_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): deep_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [deep_json(v) for v in value]
    if hasattr(value, "__table__"):
        return serialize(value)
    return value


def serialize(obj: Any) -> dict[str, Any]:
    """Serializa una fila ORM a dict plano con sus columnas reales."""
    if obj is None:
        return {}
    table = obj.__table__
    return {column.name: deep_json(getattr(obj, column.name)) for column in table.columns}


def serialize_many(objs: list[Any]) -> list[dict[str, Any]]:
    return [serialize(obj) for obj in objs]


def user_to_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "name": user.name,
        "username": user.username,
        "timezone": user.timezone,
        "lang": user.lang,
        "created_at": deep_json(user.created_at),
    }
