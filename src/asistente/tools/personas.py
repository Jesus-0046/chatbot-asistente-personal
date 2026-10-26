"""Herramientas de personas, deudas y gastos compartidos (Fase 3.4).

Tools (10):

Personas:
    add_person / list_people / update_person / deactivate_person
Deudas:
    record_debt / settle_debt / get_debts_status
Gastos compartidos:
    create_shared_expense / get_shared_expense_status / cancel_shared_expense

Reglas de aislamiento y representación:
- Todos los ids provienen de tools de QUERY (list_people/debts/gastos); el LLM
  NUNCA inventa un `person_id`/`debt_id`/`expense_id`.
- Los nombres de persona se resuelven con `list_people` cuando hace falta el id;
  una búsqueda por nombre la resuelve `PersonService.resolve` (1 → usa; >1 →
  pide el id; 0 → error).
- Una deuda NO es un gasto: no se registra movimiento de cuenta. Un gasto
  compartido tampoco: es un reparto separado. Si el usuario pagó, el gasto
  compartido captura el total y quién/cómo se pagó, sin duplicar en `debts`.
- Liquidación de deudas: SOLO a cero (pago completo). Pago parcial NO está
  soportado y se responde con error explicando que una parte no se puede
  registrar parcialmente todavía.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from asistente.domain.amounts import fmt_amount
from asistente.tools.agenda import _resolve_period, _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money, _resolve_occurred
from asistente.tools.registry import ToolDefinition, ToolResult

# --------------------------------------------------------------------------- #
# Esquemas de argumentos (validación Pydantic)                                #
# --------------------------------------------------------------------------- #
PersonRef = int | str


class PersonCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class PersonRefArgs(BaseModel):
    person_id: int = Field(gt=0)


class PersonUpdateArgs(BaseModel):
    person_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)


class DebtRecordArgs(BaseModel):
    person: PersonRef = Field(description="person_id (entero) o nombre ('Juan')")
    direction: str = Field(description="'owed_to_user' = me deben | 'user_owes' = yo debo")
    amount: str = Field(min_length=1, max_length=40, description='"28,80", "100", "1500"')
    reason: str | None = Field(default=None, max_length=300)
    due_at: str | None = Field(default=None, max_length=200, description="Cuándo vence (natural o ISO)")

    @field_validator("direction")
    @classmethod
    def _check_direction(cls, v: str) -> str:
        if v not in {"owed_to_user", "user_owes"}:
            raise ValueError("direction debe ser 'owed_to_user' (me deben) o 'user_owes' (yo debo).")
        return v


class DebtSettleArgs(BaseModel):
    debt_id: int = Field(gt=0)


class DebtsStatusArgs(BaseModel):
    person: PersonRef | None = Field(default=None, description="Filtrar por persona (opcional)")


class DebtIdArgs(BaseModel):
    debt_id: int = Field(gt=0)


class ParticipantItem(BaseModel):
    is_user: bool | None = Field(default=None, description="True si el participante es el usuario")
    person_id: int | None = Field(default=None, gt=0)
    person_name: str | None = Field(default=None, min_length=1, max_length=200, description="Nombre de la persona")
    share: str | None = Field(
        default=None, min_length=1, max_length=40, description="Parte manual (solo reparto manual)"
    )


class SharedExpensePaymentItem(BaseModel):
    who: PersonRef = Field(description="Quién pagó: 'user'/'yo', person_id o nombre")
    amount: str = Field(min_length=1, max_length=40)


class SharedExpenseCreateArgs(BaseModel):
    title: str = Field(min_length=1, max_length=300, description="Qué fue, p. ej. 'Cena con Juan'")
    total_amount: str = Field(min_length=1, max_length=40, description='"84", "28,80"')
    participant: list[ParticipantItem] = Field(min_length=1, description="Quiénes se reparten el gasto")
    paid_by: PersonRef = Field(description="Quién pagó: 'user'/'yo', person_id o nombre")
    payments: list[SharedExpensePaymentItem] | None = Field(
        default=None,
        description="Si pagaron VARIAS personas, lista {who, amount} cuya suma == total",
    )
    occurred_at: str | None = Field(default=None, max_length=200)
    note: str | None = Field(default=None, max_length=1000)


class SharedExpenseStatusArgs(BaseModel):
    period: str = Field(default="este mes", max_length=200, description="hoy, esta semana, este mes...")
    person: PersonRef | None = Field(default=None, description="Filtrar por persona (opcional)")


class SharedExpenseRefArgs(BaseModel):
    expense_id: int = Field(gt=0)


# --------------------------------------------------------------------------- #
# Personas                                                                    #
# --------------------------------------------------------------------------- #
async def _add_person(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PersonCreateArgs(**kwargs)
    try:
        person = await ctx.services.people.create(user_id=ctx.user_id, name=args.name)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": person.id,
            "name": person.name,
            "active": person.active,
            "description": f"Persona '{person.name}' (ID {person.id}) creada.",
        },
    )


async def _list_people(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    people = await ctx.services.people.list(ctx.user_id)
    return ToolResult(
        ok=True,
        data={"people": [{"id": p.id, "name": p.name, "active": p.active} for p in people]},
    )


async def _update_person(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PersonUpdateArgs(**kwargs)
    try:
        person = await ctx.services.people.update(ctx.user_id, args.person_id, name=args.name)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": person.id,
            "name": person.name,
            "active": person.active,
            "description": f"Nombre actualizado: '{person.name}' (ID {person.id}).",
        },
    )


async def _deactivate_person(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PersonRefArgs(**kwargs)
    try:
        person = await ctx.services.people.deactivate(ctx.user_id, args.person_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "id": person.id,
            "name": person.name,
            "active": False,
            "description": f"Persona '{person.name}' desactivada (su historial se conserva literalmente).",
        },
    )


# --------------------------------------------------------------------------- #
# Deudas / préstamos                                                          #
# --------------------------------------------------------------------------- #
async def _record_debt(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = DebtRecordArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    try:
        person = await ctx.services.people.resolve(ctx.user_id, args.person)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    due = _resolve_occurred(ctx, args.due_at, user_tz)
    if isinstance(due, ToolResult):
        return due
    try:
        debt = await ctx.services.debts.create(
            user_id=ctx.user_id,
            person_id=person.id,
            direction=args.direction,
            amount=amount,
            reason=args.reason,
            due_at=due,
        )
    except Exception as exc:
        return _ko(exc)
    direction_text = "te debe " if debt.direction == "owed_to_user" else "le debes "
    return ToolResult(
        ok=True,
        data={
            "debt_id": debt.id,
            "person": person.name,
            "person_id": person.id,
            "direction": debt.direction,
            "amount": fmt_amount(debt.amount),
            "currency": debt.currency,
            "reason": debt.reason,
            "due_at": debt.due_at,
            "description": f"Registrado: {person.name} {direction_text}{fmt_amount(debt.amount)} € (ID {debt.id}).",
        },
    )


async def _settle_debt(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = DebtSettleArgs(**kwargs)
    try:
        debt = await ctx.services.debts.settle(ctx.user_id, args.debt_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "debt_id": debt.id,
            "status": debt.status,
            "remaining_amount": fmt_amount(debt.remaining_amount),
            "description": f"Deuda #{debt.id} liquidada completamente.",
        },
    )


async def _get_debts_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = DebtsStatusArgs(**kwargs)
    person_id = None
    if args.person is not None:
        try:
            person = await ctx.services.people.resolve(ctx.user_id, args.person)
        except Exception as exc:
            return _ko(exc)
        person_id = person.id
    try:
        status = await ctx.services.debts.get_status(ctx.user_id, person_id=person_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(ok=True, data=status)


# --------------------------------------------------------------------------- #
# Gastos compartidos                                                          #
# --------------------------------------------------------------------------- #
async def _create_shared_expense(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SharedExpenseCreateArgs(**kwargs)
    amount = _parse_money(args.total_amount)
    if isinstance(amount, ToolResult):
        return amount
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    try:
        created = await ctx.services.shared_expenses.create(
            user_id=ctx.user_id,
            title=args.title,
            total_amount=amount,
            participants=[p.model_dump() for p in args.participant],
            paid_by=args.paid_by,
            payments=([{"who": p.who, "amount": p.amount} for p in args.payments] if args.payments else None),
            occurred_at=occurred,
            note=args.note,
        )
    except Exception as exc:
        return _ko(exc)
    expense = created["expense"]
    return ToolResult(
        ok=True,
        data={
            "expense_id": expense.id,
            "title": expense.title,
            "total_amount": fmt_amount(expense.total_amount),
            "currency": expense.currency,
            "occurred_at": expense.occurred_at,
            "description": (
                f"Gasto compartido '{expense.title}' ({fmt_amount(expense.total_amount)} €) "
                f"registrado (ID {expense.id})."
            ),
        },
    )


async def _get_shared_expense_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SharedExpenseStatusArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    person_id = None
    if args.person is not None:
        try:
            person = await ctx.services.people.resolve(ctx.user_id, args.person)
        except Exception as exc:
            return _ko(exc)
        person_id = person.id
    try:
        status = await ctx.services.shared_expenses.get_status(
            ctx.user_id,
            person_id=person_id,
            occurred_from=start_utc,
            occurred_until=end_utc,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "period": args.period,
            **status,
        },
    )


async def _cancel_shared_expense(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = SharedExpenseRefArgs(**kwargs)
    try:
        expense = await ctx.services.shared_expenses.cancel(ctx.user_id, args.expense_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "expense_id": expense.id,
            "title": expense.title,
            "status": expense.status,
            "description": f"Gasto compartido '{expense.title}' cancelado.",
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
PERSONA_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="add_person",
        description="Da de alta una persona con la que el usuario comparte deudas o gastos ('Juan', 'María'). "
        "No se crea una persona automáticamente al mencionar un nombre: primero hay que darla de alta (o reutilizar el id de list_people).",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre, p. ej. 'Juan'"},
            },
            "required": ["name"],
        },
        handler=_add_person,
        args_model=PersonCreateArgs,
    ),
    ToolDefinition(
        name="list_people",
        description="Lista las personas dadas de alta (idas y nombres). Úsala antes de registrar una deuda o gasto compartido "
        "para obtener el person_id; nunca inventes ids.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_people,
    ),
    ToolDefinition(
        name="update_person",
        description="Renombra una persona existente (p. ej. corregir un nombre).",
        input_schema={
            "type": "object",
            "properties": {
                "person_id": {"type": "integer"},
                "name": {"type": "string"},
            },
            "required": ["person_id", "name"],
        },
        handler=_update_person,
        args_model=PersonUpdateArgs,
    ),
    ToolDefinition(
        name="deactivate_person",
        description="Desactiva una persona (ya no se sugiere ni se usa en gastos nuevos). Su historial NO se borra.",
        input_schema={
            "type": "object",
            "properties": {"person_id": {"type": "integer"}},
            "required": ["person_id"],
        },
        handler=_deactivate_person,
        args_model=PersonRefArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="record_debt",
        description="Registra una deuda/préstamo PERSONAL entre el usuario y una persona ('Juan me debe 50' → me debe; "
        "'le debo 30 a María' → yo debo). NO es un gasto ni un ingreso: no toca cuentas ni saldos. "
        "La persona debe existir (add_person/list_people). Importe en lenguaje natural.",
        input_schema={
            "type": "object",
            "properties": {
                "person": {
                    "type": ["integer", "string"],
                    "description": "person_id (de list_people) o nombre ('Juan')",
                },
                "direction": {
                    "type": "string",
                    "enum": ["owed_to_user", "user_owes"],
                    "description": "owed_to_user = esa persona te debe; user_owes = tú le debes",
                },
                "amount": {"type": "string", "description": "Importe, p. ej. '50'"},
                "reason": {"type": "string", "default": None, "description": "Motivo, p. ej. 'parte del alquiler'"},
                "due_at": {"type": "string", "default": None, "description": "Cuándo vence (por defecto sin fecha)"},
            },
            "required": ["person", "direction", "amount"],
        },
        handler=_record_debt,
        args_model=DebtRecordArgs,
    ),
    ToolDefinition(
        name="settle_debt",
        description="Liquida UNA deuda completa (pasa a saldada). Solo admite liquidar la deuda entera a cero; "
        "los pagos parciales NO están soportados todavía (si te piden una parte, indícalo al usuario).",
        input_schema={
            "type": "object",
            "properties": {"debt_id": {"type": "integer", "description": "Id de una deuda abierta"}},
            "required": ["debt_id"],
        },
        handler=_settle_debt,
        args_model=DebtSettleArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="get_debts_status",
        description="Estado de todas las deudas abiertas del usuario: quién le debe qué (y a quién), con totales "
        "'me deben' y 'yo debo'. '¿Quién me debe dinero?', '¿qué deudas tengo?' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "person": {
                    "type": ["integer", "string"],
                    "default": None,
                    "description": "Filtrar por persona (opcional)",
                },
            },
            "required": [],
        },
        handler=_get_debts_status,
        args_model=DebtsStatusArgs,
    ),
    ToolDefinition(
        name="create_shared_expense",
        description="Registra un GASTO COMPARTIDO (real, ya pagado) entre personas: total, quién(es) lo pagó(aron) y "
        "cómo se reparte. NO es una deuda ni un gasto de cuenta: es el reparto en sí. Si alguien trae 'share', es reparto "
        "manual y TODOS los participantes llevan share (suma == total); si nadie la trae, reparto igualitario exacto "
        "('Cena 84 € que pagué yo para los tres' → share igualitario 28 cada uno)."
        "Los nombres/persona deben resolverse antes con list_people/add_person.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Qué fue, p. ej. 'Cena con Juan'"},
                "total_amount": {"type": "string", "description": "Importe total ya pagado, p. ej. '84'"},
                "participant": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "is_user": {
                                "type": "boolean",
                                "default": False,
                                "description": "True si el participante es el usuario",
                            },
                            "person_id": {
                                "type": "integer",
                                "default": None,
                                "description": "Id de la persona (de list_people)",
                            },
                            "person_name": {
                                "type": "string",
                                "default": None,
                                "description": "Nombre de la persona (se resuelve)",
                            },
                            "share": {
                                "type": "string",
                                "default": None,
                                "description": "Parte en reparto MANUAL, p. ej. '27,50'",
                            },
                        },
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                },
                "paid_by": {
                    "type": ["integer", "string"],
                    "description": "Quién pagó TODO: 'user'/'yo', person_id o nombre",
                },
                "payments": {
                    "type": "array",
                    "default": None,
                    "description": "Si pagaron VARIOS, lista {who, amount} con suma == total",
                    "items": {
                        "type": "object",
                        "properties": {
                            "who": {"type": ["integer", "string"], "description": "'user'/'yo', person_id o nombre"},
                            "amount": {"type": "string"},
                        },
                        "required": ["who", "amount"],
                        "additionalProperties": False,
                    },
                },
                "occurred_at": {"type": "string", "default": None, "description": "Cuándo fue (por defecto hoy)"},
                "note": {"type": "string", "default": None},
            },
            "required": ["title", "total_amount", "participant", "paid_by"],
        },
        handler=_create_shared_expense,
        args_model=SharedExpenseCreateArgs,
    ),
    ToolDefinition(
        name="get_shared_expense_status",
        description="Estado de los gastos compartidos de un periodo: quién pagó, cuánto aportó cada participante y "
        "cuánto debe/o le deben netamente a cada uno ('¿Quién me debe de la cena del sábado?' → esta tool). "
        "Solo gastos ACTIVOS; el usuario no debe pagar más de una vez por el mismo gasto (si ya pagó el total, no pagará de nuevo).",
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes", "description": "hoy, esta semana, este mes..."},
                "person": {
                    "type": ["integer", "string"],
                    "default": None,
                    "description": "Filtrar por persona (opcional)",
                },
            },
            "required": [],
        },
        handler=_get_shared_expense_status,
        args_model=SharedExpenseStatusArgs,
    ),
    ToolDefinition(
        name="cancel_shared_expense",
        description="Cancela un gasto compartido activo (equivocado, duplicado...). Las posiciones de ese gasto dejan "
        "de mostrarse como pendientes.",
        input_schema={
            "type": "object",
            "properties": {"expense_id": {"type": "integer"}},
            "required": ["expense_id"],
        },
        handler=_cancel_shared_expense,
        args_model=SharedExpenseRefArgs,
        requires_confirmation=True,
    ),
]
