"""Herramientas de inteligencia financiera (Fase 5).

Capa de LECTURA sobre `FinancialIntelligenceService`: previsión, patrones y
análisis del dinero del usuario. Ninguna tool escribe en la BD (no crea
movimientos, planificados, presupuestos, objetivos, deudas ni suscripciones).

Tools (12):
- get_financial_overview / get_estimated_available / get_spending_capacity
- analyze_spending / get_spending_changes / detect_unusual_expenses
- get_largest_expenses / get_recurring_spending_summary / get_savings_overview
- get_financial_budget_overview / get_month_end_projection / compare_financial_periods

Responsabilidades del LLM: elegir la ventana (`period`) en lenguaje natural
("este mes", "este fin de semana", "los pr\u00f3ximos 7 d\u00edas") y explicar los
resultados; nunca calcular las cifras cr\u00edticas por su cuenta.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.domain.categories import normalize_category
from asistente.domain.intelligence import FinancialIntelligenceService, previous_window
from asistente.shared.timeutil import to_utc
from asistente.tools.agenda import _now_local, _resolve_period, _tz, _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolDefinition, ToolResult


class PeriodArgs(BaseModel):
    period: str = Field(
        default="este mes",
        max_length=200,
        description='Ventana deseada: "este mes", "este fin de semana", "los pr\u00f3ximos 7 d\u00edas"',
    )


class FilterArgs(PeriodArgs):
    category: str | None = Field(default=None, max_length=64, description="Filtra por categor\u00eda")
    merchant: str | None = Field(default=None, max_length=300, description="Filtra por comercio")
    account_id: int | None = Field(default=None, gt=0, description="Filtra por cuenta")


class LargestArgs(FilterArgs):
    limit: int = Field(default=5, ge=1, le=50, description="N\u00famero m\u00e1ximo de gastos (m\u00e1x. 50)")


class BudgetOverviewArgs(BaseModel):
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)


def _resolve_finance_period(ctx: ToolContext, period_text: str, user_tz: str) -> ToolResult | tuple[datetime, datetime]:
    """Resuelve la ventana; a\u00f1ade soporte de 'este fin de semana' (s\u00e1bado 00:00 local)."""
    text = (period_text or "").strip().lower()
    if text in ("este fin de semana", "el fin de semana", "fin de semana", "este finde", "el finde", "finde"):
        now = _now_local(user_tz)
        saturday = now + timedelta(days=(5 - now.weekday()))
        start = saturday.replace(hour=0, minute=0, second=0, microsecond=0)
        return to_utc(start), to_utc(start + timedelta(days=3))
    return _resolve_period(ctx, period_text, user_tz)


def _format(value: Any, user_tz: str) -> Any:
    """Convierte en cascada Decimal→texto e ISO→hora local para el LLM."""
    if isinstance(value, dict):
        return {k: _format(v, user_tz) for k, v in value.items()}
    if isinstance(value, list):
        return [_format(v, user_tz) for v in value]
    if isinstance(value, Decimal):
        return fmt_amount(value)
    if isinstance(value, datetime):
        return value.astimezone(_tz(user_tz)).isoformat()
    return value


def _services(ctx: ToolContext) -> FinancialIntelligenceService:
    return ctx.services.intelligence


# --------------------------------------------------------------------------- #
# Implementaciones                                                             #
# --------------------------------------------------------------------------- #
async def _get_financial_overview(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    user_tz = await _user_timezone(ctx)
    data = await _services(ctx).financial_overview(ctx.user_id, timezone=user_tz)
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_estimated_available(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PeriodArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    data = await _services(ctx).estimated_available(ctx.user_id, start_utc=start_utc, end_utc=end_utc, timezone=user_tz)
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_spending_capacity(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PeriodArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    data = await _services(ctx).spending_capacity(ctx.user_id, start_utc=start_utc, end_utc=end_utc, timezone=user_tz)
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _analyze_spending(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = FilterArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    category = normalize_category(args.category) if args.category else None
    data = await _services(ctx).analyze_spending(
        ctx.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        category=category,
        merchant=args.merchant,
        account_id=args.account_id,
    )
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_spending_changes(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PeriodArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    prev_start, prev_end = previous_window(start_utc, end_utc)
    data = await _services(ctx).spending_changes(
        ctx.user_id,
        current_start=start_utc,
        current_end=end_utc,
        previous_start=prev_start,
        previous_end=prev_end,
    )
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _detect_unusual_expenses(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PeriodArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    data = await _services(ctx).detect_unusual_expenses(ctx.user_id, start_utc=start_utc, end_utc=end_utc)
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_largest_expenses(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = LargestArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    category = normalize_category(args.category) if args.category else None
    data = await _services(ctx).largest_expenses(
        ctx.user_id,
        start_utc=start_utc,
        end_utc=end_utc,
        category=category,
        merchant=args.merchant,
        account_id=args.account_id,
        limit=args.limit,
    )
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_recurring_spending_summary(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    user_tz = await _user_timezone(ctx)
    data = await _services(ctx).recurring_spending_summary(ctx.user_id, timezone=user_tz)
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_savings_overview(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    data = await _services(ctx).savings_overview(ctx.user_id)
    user_tz = await _user_timezone(ctx)
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_financial_budget_overview(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = BudgetOverviewArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    local_now = _now_local(user_tz)
    year = args.year or local_now.year
    month = args.month or local_now.month
    data = await _services(ctx).financial_budget_overview(ctx.user_id, year=year, month=month, timezone=user_tz)
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _get_month_end_projection(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    user_tz = await _user_timezone(ctx)
    data = await _services(ctx).month_end_projection(ctx.user_id, timezone=user_tz)
    return ToolResult(ok=True, data=_format(data, user_tz))


async def _compare_financial_periods(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = PeriodArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    period = _resolve_finance_period(ctx, args.period, user_tz)
    if not isinstance(period, tuple):
        return period
    start_utc, end_utc = period
    prev_start, prev_end = previous_window(start_utc, end_utc)
    data = await _services(ctx).compare_financial_periods(
        ctx.user_id,
        current_start=start_utc,
        current_end=end_utc,
        previous_start=prev_start,
        previous_end=prev_end,
    )
    data["periodo_consultado"] = args.period
    return ToolResult(ok=True, data=_format(data, user_tz))


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
INTELLIGENCE_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="get_financial_overview",
        description=(
            "PANORAMA financiero actual: patrimonio, dinero liquido, deuda de tarjetas, ahorro real, "
            "ingresos/gastos del mes, gastos pendientes, deudas con personas, objetivos de ahorro, "
            "presupuestos del mes y disponible estimado. 'Cuanto dinero tengo en total?', "
            "'como estan mis finanzas?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_financial_overview,
    ),
    ToolDefinition(
        name="get_estimated_available",
        description=(
            "Disponible estimado para un periodo: liquido actual + ingresos (realizados y planificados) "
            "- gastos (realizados y planificados) - cuota de ahorro objetivo - deudas que vencen. "
            "'Cuanto me queda para gastar este mes?', 'cuanto tendre disponible este fin de semana?' "
            "-> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": 'Ventana: "este mes", "este fin de semana", "esta semana", "los pr\u00f3ximos 7 d\u00edas"',
                }
            },
            "required": [],
        },
        handler=_get_estimated_available,
        args_model=PeriodArgs,
    ),
    ToolDefinition(
        name="get_spending_capacity",
        description=(
            "Cuanto puede gastar el usuario en un periodo sin tocar el ahorro: mismo calculo que el "
            "disponible estimado, con el ahorro excluido. 'Cuanto puedo gastar este fin de semana?' "
            "-> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": 'Ventana: "este fin de semana", "este mes", "esta semana"',
                }
            },
            "required": [],
        },
        handler=_get_spending_capacity,
        args_model=PeriodArgs,
    ),
    ToolDefinition(
        name="analyze_spending",
        description=(
            "Analiza el gasto de un periodo: total, numero de gastos, gasto medio, desglose por categoria "
            "con porcentajes, por comercio, mayores gastos y comparacion automatica con el periodo "
            "equivalente anterior. 'Como estoy gastando este mes?', 'donde se me va el dinero?' "
            "-> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes"},
                "category": {"type": "string", "default": None, "description": "Filtra por categoria"},
                "merchant": {"type": "string", "default": None, "description": "Filtra por comercio"},
                "account_id": {"type": "integer", "default": None, "description": "Filtra por cuenta"},
            },
            "required": [],
        },
        handler=_analyze_spending,
        args_model=FilterArgs,
    ),
    ToolDefinition(
        name="get_spending_changes",
        description=(
            "Evolucion del gasto por categoria entre dos periodos equivalentes (actual vs el inmediatamente "
            "anterior de la misma duracion). 'Ha subido o bajado mi gasto?', 'en que categorias estoy "
            "gastando mas que el mes pasado?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": 'Periodo actual (el anterior se deduce): "este mes", "este fin de semana"',
                }
            },
            "required": [],
        },
        handler=_get_spending_changes,
        args_model=PeriodArgs,
    ),
    ToolDefinition(
        name="detect_unusual_expenses",
        description=(
            "Detecta gastos INUSUALES del periodo sobre el historico de los ultimos 90 dias (reglas "
            "deterministas, sin IA): un gasto muy por encima de la mediana de su categoria o comercio, o "
            "una categoria muy por encima de periodos equivalentes previos. Cada resultado explica el "
            "criterio. NUNCA interpreta los resultados como fraude ni como certeza: son avisos. "
            "'Hay algun gasto raro este mes?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": 'Periodo a analizar: "este mes", "esta semana"',
                }
            },
            "required": [],
        },
        handler=_detect_unusual_expenses,
        args_model=PeriodArgs,
    ),
    ToolDefinition(
        name="get_largest_expenses",
        description=(
            "Lista los mayores gastos de un periodo (por importe, de mayor a menor). "
            "'Cuales han sido mis gastos mas grandes este mes?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": "string", "default": "este mes"},
                "category": {"type": "string", "default": None},
                "merchant": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None},
                "limit": {"type": "integer", "default": 5},
            },
            "required": [],
        },
        handler=_get_largest_expenses,
        args_model=LargestArgs,
    ),
    ToolDefinition(
        name="get_recurring_spending_summary",
        description=(
            "Resumen de recurrencias y suscripciones activas con su equivalente mensual y anual, y los "
            "proximos cobros planificados (30 dias). 'Cuales son mis gastos fijos?', 'cuanto pago al "
            "mes entre suscripciones y recurrencias?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_recurring_spending_summary,
    ),
    ToolDefinition(
        name="get_savings_overview",
        description=(
            "Estado de ahorro: saldo real de cuentas de tipo ahorro (separado) y progreso de objetivos de "
            "ahorro (aportado, restante, porcentaje). 'Cuanto llevo ahorrado?', 'como voy con mis "
            "objetivos de ahorro?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_savings_overview,
    ),
    ToolDefinition(
        name="get_financial_budget_overview",
        description=(
            "Estado de los presupuestos de un mes (por defecto el actual): limite, gastado, restante, "
            "porcentaje usado y estado (normal <80% / warning 80-99,99% / exceeded >=100%). "
            "'Como voy con mis presupuestos este mes?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "year": {"type": "integer", "default": None},
                "month": {"type": "integer", "default": None, "description": "1-12"},
            },
            "required": [],
        },
        handler=_get_financial_budget_overview,
        args_model=BudgetOverviewArgs,
    ),
    ToolDefinition(
        name="get_month_end_projection",
        description=(
            "Prevision de fin de mes: realizado hasta hoy, compromisos planificados restantes y, si hay "
            "datos suficientes (>=10 dias de mes con movimientos), extrapolacion de gastos variables por "
            "dia. La respuesta indica el metodo usado. 'Cuanto habre gastado a fin de mes?', 'como "
            "terminare el mes?' -> esta tool."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_get_month_end_projection,
    ),
    ToolDefinition(
        name="compare_financial_periods",
        description=(
            "Compara dos periodos equivalentes (actual vs el inmediatamente anterior): ingresos, gastos y "
            "ahorro (ingresos - gastos), con variacion absoluta y porcentual. 'Como fue este mes respecto "
            "al anterior?' -> esta tool."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "default": "este mes",
                    "description": 'Periodo actual a comparar contra el equivalente anterior: "este mes"',
                }
            },
            "required": [],
        },
        handler=_compare_financial_periods,
        args_model=PeriodArgs,
    ),
]
