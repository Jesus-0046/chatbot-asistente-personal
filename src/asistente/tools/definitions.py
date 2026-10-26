"""Herramientas disponibles para el LLM (Fase 1).

Tools mínimas:
- `get_current_datetime`     devuelve fecha/hora del usuario
- `create_note`              crea una nota
- `get_notes`                lista notas recientes
- `pin_note` / `unpin_note`  fija/desfija nota
- `delete_note`              elimina nota (requiere confirmación)
- `propose_memory_rule`      propone una regla de memoria (inactiva)
- `activate_memory_rule`     activa una regla propuesta (requiere confirmación)
- `list_memory_rules`        lista reglas activas
- `revoke_memory_rule`       revoca una regla activa (requiere confirmación)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class NoArgs(BaseModel):
    pass


class NoteTitle(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(default="", max_length=10000)


class NoteId(BaseModel):
    note_id: int = Field(gt=0)


class NoteList(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)


class MemoryRuleProposal(BaseModel):
    kind: str = Field(pattern=r"^(merchant_category|keyword_category|account_keyword|preference)$")
    key: str = Field(min_length=1, max_length=200)
    value: dict = Field(default_factory=dict)
    scope: str | None = Field(default=None, max_length=200)


class MemoryRuleId(BaseModel):
    rule_id: int = Field(gt=0)


async def _get_current_datetime(ctx: ToolContext) -> ToolResult:
    tz = ctx.services.settings.default_timezone
    from asistente.shared.timeutil import format_datetime, now_in_timezone

    now = now_in_timezone(tz)
    return ToolResult(
        ok=True,
        data={"datetime": format_datetime(now), "timezone": tz},
    )


async def _create_note(ctx: ToolContext, *, title: str, body: str = "") -> ToolResult:
    note = await ctx.services.notes.create(user_id=ctx.user_id, title=title, body=body)
    return ToolResult(ok=True, data={"id": note.id, "title": note.title})


async def _get_notes(ctx: ToolContext, *, limit: int = 20) -> ToolResult:
    notes = await ctx.services.notes.list(ctx.user_id, limit=limit)
    return ToolResult(
        ok=True,
        data={"notes": [{"id": n.id, "title": n.title, "pinned": n.pinned} for n in notes]},
    )


async def _pin_note(ctx: ToolContext, *, note_id: int) -> ToolResult:
    note = await ctx.services.notes.set_pinned(ctx.user_id, note_id, pinned=True)
    return ToolResult(ok=True, data={"id": note.id, "pinned": True})


async def _unpin_note(ctx: ToolContext, *, note_id: int) -> ToolResult:
    note = await ctx.services.notes.set_pinned(ctx.user_id, note_id, pinned=False)
    return ToolResult(ok=True, data={"id": note.id, "pinned": False})


async def _delete_note(ctx: ToolContext, *, note_id: int) -> ToolResult:
    await ctx.services.notes.delete(ctx.user_id, note_id)
    return ToolResult(ok=True, data={"deleted": True})


async def _propose_memory_rule(
    ctx: ToolContext,
    *,
    kind: str,
    key: str,
    value: dict,
    scope: str | None = None,
) -> ToolResult:
    rule = await ctx.services.memory.propose(user_id=ctx.user_id, kind=kind, key=key, value=value, scope=scope)
    return ToolResult(
        ok=True,
        needs_confirmation=True,
        confirmation_id=rule.id,
        proposed_reply=(f"Voy a crear una regla de memoria: {kind} → {key}. ¿Quieres que la active?"),
        data={
            "confirmation": {
                "tool": "activate_memory_rule",
                "arguments": {"rule_id": rule.id},
            }
        },
    )


async def _activate_memory_rule(ctx: ToolContext, *, rule_id: int) -> ToolResult:
    rule = await ctx.services.memory.ensure_activated_from_proposal(ctx.user_id, rule_id)
    return ToolResult(ok=True, data={"rule_id": rule.id, "active": True})


async def _list_memory_rules(ctx: ToolContext) -> ToolResult:
    rules = await ctx.services.memory.active_rules(ctx.user_id)
    return ToolResult(
        ok=True,
        data={"rules": [{"id": r.id, "kind": r.kind, "key": r.key, "value": r.value} for r in rules]},
    )


async def _revoke_memory_rule(ctx: ToolContext, *, rule_id: int) -> ToolResult:
    await ctx.services.memory.revoke(ctx.user_id, rule_id)
    return ToolResult(ok=True, data={"revoked": True})


TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_current_datetime",
        description="Obtiene la fecha y hora actuales del usuario.",
        input_schema={"type": "object", "properties": {}},
        handler=_get_current_datetime,
        args_model=NoArgs,
    ),
    ToolDefinition(
        name="create_note",
        description="Crea una nota con título y contenido.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Título de la nota"},
                "body": {"type": "string", "description": "Contenido de la nota", "default": ""},
            },
            "required": ["title"],
        },
        handler=_create_note,
        args_model=NoteTitle,
    ),
    ToolDefinition(
        name="get_notes",
        description="Lista las notas recientes del usuario.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
        handler=_get_notes,
        args_model=NoteList,
    ),
    ToolDefinition(
        name="pin_note",
        description="Fija una nota.",
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
        handler=_pin_note,
        args_model=NoteId,
    ),
    ToolDefinition(
        name="unpin_note",
        description="Desfija una nota.",
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
        handler=_unpin_note,
        args_model=NoteId,
    ),
    ToolDefinition(
        name="delete_note",
        description="Elimina una nota de forma permanente.",
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
        },
        handler=_delete_note,
        requires_confirmation=True,
        args_model=NoteId,
    ),
    ToolDefinition(
        name="propose_memory_rule",
        description="Propone una regla de memoria para el usuario (queda pendiente de confirmación).",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["merchant_category", "keyword_category", "account_keyword", "preference"],
                },
                "key": {"type": "string", "description": "Clave de la regla"},
                "value": {"type": "object", "description": "Valor de la regla"},
                "scope": {"type": "string", "description": "Alcance de la regla", "default": None},
            },
            "required": ["kind", "key", "value"],
        },
        handler=_propose_memory_rule,
        args_model=MemoryRuleProposal,
    ),
    ToolDefinition(
        name="activate_memory_rule",
        description="Activa una regla de memoria propuesta por el usuario.",
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "integer"}},
            "required": ["rule_id"],
        },
        handler=_activate_memory_rule,
        requires_confirmation=True,
        args_model=MemoryRuleId,
    ),
    ToolDefinition(
        name="list_memory_rules",
        description="Lista las reglas de memoria activas del usuario.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_memory_rules,
        args_model=NoArgs,
    ),
    ToolDefinition(
        name="revoke_memory_rule",
        description="Revoca (desactiva) una regla de memoria activa.",
        input_schema={
            "type": "object",
            "properties": {"rule_id": {"type": "integer"}},
            "required": ["rule_id"],
        },
        handler=_revoke_memory_rule,
        requires_confirmation=True,
        args_model=MemoryRuleId,
    ),
]
