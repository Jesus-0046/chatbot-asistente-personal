"""Tool de aplicación de extracción de imágenes (Fase 7).

`apply_extracted_data` es la ÚNICA puerta de escritura para lo que se extrae
de una imagen: convierte una PROPUESTA validada (construida por
`domain.images.ImageAnalysisService`) en filas reales reutilizando los
servicios de dominio existentes (transacciones, tareas y notas) — la misma
capa que usan `create_expense`, `create_task` y `create_note`. No existe aquí
ninguna segunda implementación de escritura.

La tool está marcada `requires_confirmation`: el orquestador crea la
confirmación Y el usuario tiene que confirmar antes de que se persista nada.
Ninguna imagen ni binario viaja en los argumentos: solo la propuesta de texto
estructurado.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from asistente.domain.amounts import fmt_amount
from asistente.shared.language import parse_priority
from asistente.shared.timeutil import to_utc
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult

NOTE_MAX_CHARS = 10000


# --------------------------------------------------------------------------- #
# Esquemas de la propuesta (validación Pydantic al ejecutar)                  #
# --------------------------------------------------------------------------- #
class ExpenseProposal(BaseModel):
    amount: str = Field(min_length=1, max_length=40)
    merchant: str | None = Field(default=None, max_length=300)
    occurred_at: str | None = Field(default=None, max_length=100)
    category: str | None = Field(default=None, max_length=64)
    note: str | None = Field(default=None, max_length=1000)


class TaskProposal(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    due_date: str | None = Field(default=None, max_length=50)
    priority: str | None = Field(default=None, max_length=50)


class ShoppingItemProposal(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    quantity: str = Field(default="", max_length=50)
    notes: str = Field(default="", max_length=300)


class ShoppingListProposal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    items: list[ShoppingItemProposal] = Field(min_length=1)


class StructuredNoteProposal(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=20000)


class ApplyExtractedArgs(BaseModel):
    kind: Literal["ticket", "tasks", "shopping_list", "structured_note"]
    expense: ExpenseProposal | None = None
    tasks: list[TaskProposal] | None = None
    shopping_list: ShoppingListProposal | None = None
    structured_note: StructuredNoteProposal | None = None
    confidence: str = Field(default="high", max_length=10)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_kind_section(self) -> ApplyExtractedArgs:
        sections = {
            "ticket": self.expense,
            "tasks": self.tasks,
            "shopping_list": self.shopping_list,
            "structured_note": self.structured_note,
        }
        if sections[self.kind] is None:
            raise ValueError(f"La propuesta de tipo '{self.kind}' no trae su bloque de datos.")
        return self


def _ko(exc: Exception) -> ToolResult:
    return ToolResult(ok=False, error=str(exc))


def _parse_dt_utc(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return to_utc(datetime.fromisoformat(raw))


async def _apply_ticket(ctx: ToolContext, expense: ExpenseProposal) -> ToolResult:
    if not expense.occurred_at:
        return ToolResult(
            ok=False,
            error="El ticket no indica una fecha completa fiable; confírmala antes de registrar el gasto.",
        )
    try:
        amount = Decimal(expense.amount)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, error=f"Importe no válido en la propuesta: {exc}")
    if amount <= 0:
        return ToolResult(ok=False, error="El importe del gasto debe ser mayor que cero.")
    try:
        occurred = _parse_dt_utc(expense.occurred_at)
    except ValueError as exc:
        return ToolResult(ok=False, error=f"Fecha del ticket no válida: {exc}")

    try:
        txn = await ctx.services.transactions.create(
            user_id=ctx.user_id,
            type="expense",
            amount=amount,
            merchant=expense.merchant,
            category=expense.category,
            occurred_at=occurred,
            note=expense.note,
        )
    except Exception as exc:  # noqa: BLE001
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "created": "expense",
            "transaction_id": txn.id,
            "amount": fmt_amount(txn.amount),
            "merchant": txn.merchant,
        },
    )


async def _apply_tasks(ctx: ToolContext, tasks: list[TaskProposal]) -> ToolResult:
    items: list[dict[str, Any]] = []
    for task in tasks:
        due = _parse_dt_utc(task.due_date)
        items.append(
            {
                "title": task.title,
                "due_at": due,
                "priority": parse_priority(task.priority),
            }
        )
    try:
        created = await ctx.services.tasks.create_batch(user_id=ctx.user_id, tasks=items)
    except Exception as exc:  # noqa: BLE001
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "created": "tasks",
            "count": len(created),
            "tasks": [{"task_id": row.id, "title": row.title} for row in created],
        },
    )


async def _apply_shopping_list(ctx: ToolContext, shopping_list: ShoppingListProposal) -> ToolResult:
    lines = [f"- {item.name}" + (f" ({item.quantity})" if item.quantity else "") for item in shopping_list.items]
    body = "\n".join(lines)[:NOTE_MAX_CHARS]
    try:
        note = await ctx.services.notes.create(user_id=ctx.user_id, title=shopping_list.title, body=body)
    except Exception as exc:  # noqa: BLE001
        return _ko(exc)
    return ToolResult(ok=True, data={"created": "shopping_list", "note_id": note.id, "title": note.title})


async def _apply_structured_note(ctx: ToolContext, structured_note: StructuredNoteProposal) -> ToolResult:
    try:
        note = await ctx.services.notes.create(
            user_id=ctx.user_id, title=structured_note.title, body=structured_note.content[:NOTE_MAX_CHARS]
        )
    except Exception as exc:  # noqa: BLE001
        return _ko(exc)
    return ToolResult(ok=True, data={"created": "structured_note", "note_id": note.id, "title": note.title})


async def _apply_extracted_data(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = ApplyExtractedArgs(**kwargs)
    if args.kind == "ticket":
        return await _apply_ticket(ctx, args.expense)  # type: ignore[arg-type]
    if args.kind == "tasks":
        return await _apply_tasks(ctx, args.tasks or [])
    if args.kind == "shopping_list":
        return await _apply_shopping_list(ctx, args.shopping_list)  # type: ignore[arg-type]
    return await _apply_structured_note(ctx, args.structured_note)  # type: ignore[arg-type]


IMAGE_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="apply_extracted_data",
        description=(
            "Persiste una propuesta extraída de una imagen YA confirmada por el usuario "
            "(ticket → gasto; lista de la compra → nota; lista de tareas → tareas; nota → nota). "
            "Solo la invoca el flujo de confirmación: nunca ejecutar sin confirmación explícita."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["ticket", "tasks", "shopping_list", "structured_note"]},
                "expense": {
                    "type": "object",
                    "description": "Bloque gasto (kind=ticket): amount, merchant, occurred_at, category, note",
                },
                "tasks": {"type": "array", "description": "Tareas (kind=tasks): title, due_date, priority"},
                "shopping_list": {
                    "type": "object",
                    "description": "Lista de la compra (kind=shopping_list): title + items",
                },
                "structured_note": {
                    "type": "object",
                    "description": "Nota estructurada (kind=structured_note): title + content",
                },
                "confidence": {"type": "string", "default": "high"},
                "warnings": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["kind"],
        },
        handler=_apply_extracted_data,
        requires_confirmation=True,
        args_model=ApplyExtractedArgs,
    ),
]
