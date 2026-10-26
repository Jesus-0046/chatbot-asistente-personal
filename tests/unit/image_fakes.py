"""Fakes deterministas para el flujo de imágenes (Fase 7).

Permiten probar el servicio de extracción/validación, las tools y el
orquestador sin red: el `FakeVisionProvider` devuelve extracciones
estructuradas fijas y `FakeTransactionQuery` simula el histórico de
movimientos para la detección de duplicados.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any

from asistente.application.context import ImageAttachment


def leaf(value: Any, confidence: str = "high") -> dict[str, Any]:
    return {"value": value, "confidence": confidence}


def make_image(*, size: int = 1000, mime_type: str = "image/jpeg") -> ImageAttachment:
    return ImageAttachment(
        file_id="file_1",
        mime_type=mime_type,
        data=b"\x00" * size,
        size=size,
        width=100,
        height=200,
    )


def fresh_ticket(
    *,
    merchant: str = "Mercadona",
    total: str = "28,80",
    date_v: str = "20/09/2026",
    time_v: str = "18:42",
    currency: str = "EUR",
    merchant_c: str = "high",
    total_c: str = "high",
    date_c: str = "high",
    items: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "ticket",
        "ticket": {
            "merchant": leaf(merchant, merchant_c),
            "total": leaf(total, total_c),
            "date": leaf(date_v, date_c),
            "time": leaf(time_v),
            "currency": leaf(currency),
            "items": items
            if items is not None
            else [
                {
                    "description": leaf("Pan"),
                    "quantity": leaf("1"),
                    "unit_price": leaf("1,20"),
                    "total_price": leaf("1,20"),
                },
                {
                    "description": leaf("Leche"),
                    "quantity": leaf("2"),
                    "unit_price": leaf("1,10"),
                    "total_price": leaf("2,20"),
                },
                {
                    "description": leaf("Café"),
                    "quantity": leaf("1"),
                    "unit_price": leaf("25,40"),
                    "total_price": leaf("25,40"),
                },
            ],
        },
        "warnings": warnings or [],
    }


def fresh_tasks(
    *,
    tasks: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "task_list",
        "task_list": {
            "tasks": tasks
            or [
                {"title": leaf("Comprar pan"), "due_date": leaf("25/09/2026"), "priority": leaf("alta")},
                {"title": leaf("Llamar al seguro"), "due_date": leaf(""), "priority": leaf("")},
            ]
        },
        "warnings": warnings or [],
    }


def fresh_shopping(
    *,
    title: str = "Lista de la compra",
    items: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "shopping_list",
        "shopping_list": {
            "title": leaf(title),
            "items": list(items)
            if items is not None
            else [
                {"name": leaf("Pan de molde"), "quantity": leaf("2"), "notes": leaf("")},
                {"name": leaf("Tomates"), "quantity": leaf("1 kg"), "notes": leaf("")},
            ],
        },
        "warnings": warnings or [],
    }


def fresh_note(
    *,
    title: str = "Receta de tortilla",
    content: str = "4 huevos, 2 patatas, sal.",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "kind": "structured_note",
        "structured_note": {"title": leaf(title), "content": leaf(content)},
        "warnings": warnings or [],
    }


class FakeVisionProvider:
    """Devuelve una extracción fija (deep-copiada) o lanza un error dado."""

    def __init__(self, payload: dict[str, Any] | None = None, *, error: Exception | None = None) -> None:
        self._payload = payload
        self._error = error
        self.images: list[Any] = []
        self.hints: list[str | None] = []

    def understand(self, *, image: Any, hint: str | None = None) -> dict[str, Any]:
        self.images.append(image)
        self.hints.append(hint)
        if self._error is not None:
            raise self._error
        return deepcopy(self._payload if self._payload is not None else fresh_ticket())


class FakeTransactionRow:
    def __init__(self, *, user_id: int, amount: Decimal, occurred_at: datetime, merchant: str = "Mercadona") -> None:
        self.user_id = user_id
        self.amount = amount
        self.occurred_at = occurred_at
        self.merchant = merchant


class FakeTransactionQuery:
    """Simula el histórico de movimientos para detección de duplicados.

    Respeta user_id, comercio (ilike) y ventana [start_utc, end_utc), como el
    repositorio real, para que los tests de "otro día / otro usuario" sean
    fieles al comportamiento de producción.
    """

    def __init__(self, rows: list[FakeTransactionRow] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[tuple[int, dict[str, Any]]] = []

    async def list(self, user_id: int, **kwargs: Any) -> list[FakeTransactionRow]:
        self.calls.append((user_id, kwargs))
        rows = [row for row in self.rows if row.user_id == user_id]
        merchant = (kwargs.get("merchant") or "").lower()
        if merchant:
            rows = [row for row in rows if merchant in (row.merchant or "").lower()]
        start_utc = kwargs.get("start_utc")
        if start_utc is not None:
            rows = [row for row in rows if row.occurred_at is None or row.occurred_at >= start_utc]
        end_utc = kwargs.get("end_utc")
        if end_utc is not None:
            rows = [row for row in rows if row.occurred_at is None or row.occurred_at < end_utc]
        return rows
