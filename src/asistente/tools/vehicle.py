"""Herramientas de vehículos (Fase 4).

Tools (10): add_vehicle / list_vehicles / update_vehicle / deactivate_vehicle /
record_fuel / add_vehicle_insurance / update_vehicle_insurance /
record_vehicle_maintenance / get_vehicle_status / get_vehicle_expirations.

Semántica:
- Los importes viajan como texto y se convierten a `Decimal`.
- Repostajes y mantenimientos con coste crean SU única transacción de gasto
  (categoría 'Transporte') en la misma transacción atómica que el detalle; la
  cuenta se resuelve automáticamente (regla 1-2-3 del módulo financiero) salvo
  que el usuario indique `account_id`.
- El seguro del vehículo NO es un movimiento financiero: solo se registra.
- Los ids se obtienen SIEMPRE de herramientas de consulta (list_vehicles /
  get_vehicle_status); el LLM nunca inventa ids.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from asistente.domain.amounts import fmt_amount
from asistente.shared.timeutil import to_local
from asistente.tools.agenda import _resolve_datetime, _ResolvedNode, _user_timezone
from asistente.tools.context import ToolContext
from asistente.tools.finance import _ko, _parse_money, _resolve_occurred
from asistente.tools.registry import ToolDefinition, ToolResult

VehicleRef = int | str


class VehicleCreateArgs(BaseModel):
    name: str = Field(min_length=1, max_length=200, description="Nombre: 'mi coche', 'Auris'...")
    brand: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    plate: str | None = Field(default=None, max_length=20)
    year: int | None = Field(default=None, ge=1900, le=2100)
    color: str | None = Field(default=None, max_length=50)
    fuel_type: str | None = Field(default=None, max_length=32, description="gasolina, diésel, eléctrico...")
    current_odometer: str | None = Field(
        default=None, max_length=40, description="Kilómetros actuales, p. ej. '182000'"
    )
    notes: str | None = Field(default=None, max_length=1000)


class VehicleRefArgs(BaseModel):
    vehicle_id: int = Field(gt=0)


class VehicleUpdateArgs(BaseModel):
    vehicle_id: int = Field(gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand: str | None = Field(default=None, max_length=200)
    model: str | None = Field(default=None, max_length=200)
    plate: str | None = Field(default=None, max_length=20)
    year: int | None = Field(default=None, ge=1900, le=2100)
    color: str | None = Field(default=None, max_length=50)
    fuel_type: str | None = Field(default=None, max_length=32)
    current_odometer: str | None = Field(default=None, max_length=40)
    notes: str | None = Field(default=None, max_length=1000)


class FuelRecordArgs(BaseModel):
    vehicle: VehicleRef = Field(description="vehicle_id (entero) o nombre ('mi coche')")
    amount: str = Field(min_length=1, max_length=40, description="Importe, p. ej. '45,80'")
    occurred_at: str | None = Field(default=None, max_length=200, description="Cuándo (por defecto ahora)")
    liters: str | None = Field(default=None, max_length=40, description="Litros, p. ej. '42'")
    odometer: str | None = Field(default=None, max_length=40, description="Kilómetros del cuadro al repostar")
    fuel_type: str | None = Field(default=None, max_length=32)
    full_tank: bool | None = Field(default=None, description="True si llenó el depósito")
    note: str | None = Field(default=None, max_length=1000)
    account_id: int | None = Field(default=None, gt=0, description="Cuenta del gasto (si se conoce)")


class InsuranceCreateArgs(BaseModel):
    vehicle: VehicleRef = Field(description="vehicle_id (entero) o nombre")
    company: str | None = Field(default=None, max_length=200, description="Compañía aseguradora")
    policy_number: str | None = Field(default=None, max_length=100)
    insurance_type: str | None = Field(default=None, max_length=64, description="a todo riesgo, terceros...")
    coverage: str | None = Field(default=None, max_length=1000)
    start_date: str | None = Field(default=None, max_length=200, description="Inicio de la póliza")
    end_date: str | None = Field(default=None, max_length=200, description="Vencimiento de la póliza")
    amount: str | None = Field(default=None, max_length=40, description="Importe (informativo)")
    billing_period: str | None = Field(
        default=None, max_length=16, description="yearly, half_year, quarter_year, monthly, one_time"
    )
    notes: str | None = Field(default=None, max_length=1000)


class InsuranceUpdateArgs(BaseModel):
    insurance_id: int = Field(gt=0)
    company: str | None = Field(default=None, max_length=200)
    policy_number: str | None = Field(default=None, max_length=100)
    insurance_type: str | None = Field(default=None, max_length=64)
    coverage: str | None = Field(default=None, max_length=1000)
    start_date: str | None = Field(default=None, max_length=200)
    end_date: str | None = Field(default=None, max_length=200)
    amount: str | None = Field(default=None, max_length=40)
    billing_period: str | None = Field(default=None, max_length=16)
    notes: str | None = Field(default=None, max_length=1000)


class MaintenanceRecordArgs(BaseModel):
    vehicle: VehicleRef = Field(description="vehicle_id (entero) o nombre")
    maintenance_type: str = Field(default="revision", description="revision, itv, reparacion, otro")
    description: str | None = Field(default=None, max_length=300, description="Qué se hizo, p. ej. 'cambio de aceite'")
    occurred_at: str | None = Field(default=None, max_length=200, description="Cuándo (por defecto hoy)")
    odometer: str | None = Field(default=None, max_length=40, description="Km en el momento")
    cost: str | None = Field(default=None, max_length=40, description="Coste (opcional; si existe crea el gasto)")
    next_due_at: str | None = Field(default=None, max_length=200, description="Próxima revisión (fecha o ISO)")
    next_due_odometer: str | None = Field(default=None, max_length=40, description="Km de la próxima revisión")
    note: str | None = Field(default=None, max_length=1000)
    account_id: int | None = Field(default=None, gt=0, description="Cuenta del gasto (si se conoce)")


class VehicleStatusArgs(BaseModel):
    vehicle: VehicleRef = Field(description="vehicle_id (entero) o nombre de un vehículo dado de alta")


# --------------------------------------------------------------------------- #
# Handlers                                                                    #
# --------------------------------------------------------------------------- #
def _fmt_dt(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _resolve_date(ctx: ToolContext, expression: str | None, user_tz: str) -> ToolResult | date | None:
    if not expression:
        return None
    node = _resolve_datetime(ctx, expression, user_tz)
    if not isinstance(node, _ResolvedNode):
        return node
    return node.value.date()


async def _add_vehicle(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = VehicleCreateArgs(**kwargs)
    odometer = None
    if args.current_odometer:
        try:
            odometer = float(args.current_odometer)
        except (TypeError, ValueError):
            return _ko(Exception("El odómetro debe ser un número."))
    try:
        vehicle = await ctx.services.vehicles.create(
            user_id=ctx.user_id,
            name=args.name,
            brand=args.brand,
            model=args.model,
            plate=args.plate,
            year=args.year,
            color=args.color,
            fuel_type=args.fuel_type,
            current_odometer=odometer,
            notes=args.notes,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle_id": vehicle.id,
            "name": vehicle.name,
            "plate": vehicle.plate,
            "current_odometer": float(vehicle.current_odometer) if vehicle.current_odometer is not None else None,
            "description": f"Vehículo '{vehicle.name}' dado de alta (ID {vehicle.id}).",
        },
    )


async def _list_vehicles(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    vehicles = await ctx.services.vehicles.list(ctx.user_id)
    return ToolResult(
        ok=True,
        data={
            "vehicles": [
                {
                    "id": v.id,
                    "name": v.name,
                    "brand": v.brand,
                    "model": v.model,
                    "plate": v.plate,
                    "fuel_type": v.fuel_type,
                    "current_odometer": float(v.current_odometer) if v.current_odometer is not None else None,
                    "active": v.active,
                }
                for v in vehicles
            ]
        },
    )


async def _update_vehicle(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = VehicleUpdateArgs(**kwargs)
    odometer = None
    if args.current_odometer is not None:
        try:
            odometer = float(args.current_odometer)
        except (TypeError, ValueError):
            return _ko(Exception("El odómetro debe ser un número."))
    try:
        vehicle = await ctx.services.vehicles.update(
            ctx.user_id,
            args.vehicle_id,
            name=args.name,
            brand=args.brand,
            model=args.model,
            plate=args.plate,
            year=args.year,
            color=args.color,
            fuel_type=args.fuel_type,
            current_odometer=odometer,
            notes=args.notes,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle_id": vehicle.id,
            "name": vehicle.name,
            "description": f"Vehículo '{vehicle.name}' actualizado.",
        },
    )


async def _deactivate_vehicle(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = VehicleRefArgs(**kwargs)
    try:
        vehicle = await ctx.services.vehicles.deactivate(ctx.user_id, args.vehicle_id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle_id": vehicle.id,
            "name": vehicle.name,
            "active": False,
            "description": f"Vehículo '{vehicle.name}' desactivado (su histórico se conserva literalmente).",
        },
    )


async def _record_fuel(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = FuelRecordArgs(**kwargs)
    amount = _parse_money(args.amount)
    if isinstance(amount, ToolResult):
        return amount
    try:
        vehicle = await ctx.services.vehicles.resolve(ctx.user_id, args.vehicle)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    liters = None
    if args.liters:
        liters = _parse_money(args.liters)
        if isinstance(liters, ToolResult):
            return liters
    odometer = None
    if args.odometer:
        try:
            odometer = float(args.odometer)
        except (TypeError, ValueError):
            return _ko(Exception("El odómetro debe ser un número."))
    try:
        result = await ctx.services.vehicles.record_fuel(
            user_id=ctx.user_id,
            vehicle=vehicle,
            amount=amount,
            occurred_at=occurred,
            liters=liters,
            odometer=odometer,
            fuel_type=args.fuel_type,
            full_tank=args.full_tank,
            note=args.note,
            account_id=args.account_id,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle_id": result["vehicle_id"],
            "fuel_entry_id": result["fuel_entry_id"],
            "transaction_id": result["transaction_id"],
            "amount": fmt_amount(result["amount"]),
            "currency": result["currency"],
            "occurred_at": _fmt_dt(result["occurred_at"]),
            "liters": float(result["liters"]) if result["liters"] is not None else None,
            "odometer": float(result["odometer"]) if result["odometer"] is not None else None,
            "odometer_advanced": result["odometer_advanced"],
            "description": (
                f"Repostaje de {fmt_amount(result['amount'])} € en cuentas (categoría Transporte, "
                f"ID {result['fuel_entry_id']}, movimiento {result['transaction_id']})."
            ),
        },
    )


async def _add_vehicle_insurance(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = InsuranceCreateArgs(**kwargs)
    try:
        vehicle = await ctx.services.vehicles.resolve(ctx.user_id, args.vehicle)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    start_date = _resolve_date(ctx, args.start_date, user_tz)
    if isinstance(start_date, ToolResult):
        return start_date
    end_date = _resolve_date(ctx, args.end_date, user_tz)
    if isinstance(end_date, ToolResult):
        return end_date
    amount = None
    if args.amount:
        amount = _parse_money(args.amount)
        if isinstance(amount, ToolResult):
            return amount
    try:
        insurance = await ctx.services.vehicles.add_insurance(
            user_id=ctx.user_id,
            vehicle=vehicle,
            company=args.company,
            policy_number=args.policy_number,
            insurance_type=args.insurance_type,
            coverage=args.coverage,
            start_date=start_date,
            end_date=end_date,
            amount=amount,
            billing_period=args.billing_period,
            notes=args.notes,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "insurance_id": insurance.id,
            "vehicle_id": vehicle.id,
            "company": insurance.company,
            "end_date": insurance.end_date.isoformat() if insurance.end_date else None,
            "amount": fmt_amount(insurance.amount) if insurance.amount is not None else None,
            "description": (
                f"Seguro de '{vehicle.name}' registrado (ID {insurance.id}). "
                "No se registra como gasto automáticamente: es un registro informativo."
            ),
        },
    )


async def _update_vehicle_insurance(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = InsuranceUpdateArgs(**kwargs)
    params: dict = {}
    for key in ("company", "policy_number", "insurance_type", "coverage", "billing_period", "notes"):
        value = getattr(args, key)
        if value is not None:
            params[key] = value
    user_tz = await _user_timezone(ctx)
    if args.start_date is not None:
        sd = _resolve_date(ctx, args.start_date, user_tz)
        if isinstance(sd, ToolResult):
            return sd
        params["start_date"] = sd
    if args.end_date is not None:
        ed = _resolve_date(ctx, args.end_date, user_tz)
        if isinstance(ed, ToolResult):
            return ed
        params["end_date"] = ed
    if args.amount is not None:
        amount = _parse_money(args.amount)
        if isinstance(amount, ToolResult):
            return amount
        params["amount"] = amount
    try:
        insurance = await ctx.services.vehicles.update_insurance(ctx.user_id, args.insurance_id, **params)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "insurance_id": insurance.id,
            "company": insurance.company,
            "end_date": insurance.end_date.isoformat() if insurance.end_date else None,
            "description": f"Seguro #{insurance.id} actualizado.",
        },
    )


async def _record_vehicle_maintenance(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = MaintenanceRecordArgs(**kwargs)
    try:
        vehicle = await ctx.services.vehicles.resolve(ctx.user_id, args.vehicle)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    occurred = _resolve_occurred(ctx, args.occurred_at, user_tz)
    if isinstance(occurred, ToolResult):
        return occurred
    next_due = _resolve_occurred(ctx, args.next_due_at, user_tz)
    if isinstance(next_due, ToolResult):
        return next_due
    cost = None
    if args.cost:
        cost = _parse_money(args.cost)
    if isinstance(cost, ToolResult):
        return cost
    odometer = None
    if args.odometer:
        try:
            odometer = float(args.odometer)
        except (TypeError, ValueError):
            return _ko(Exception("El odómetro debe ser un número."))
    next_due_km = None
    if args.next_due_odometer:
        try:
            next_due_km = float(args.next_due_odometer)
        except (TypeError, ValueError):
            return _ko(Exception("Los kilómetros de la próxima revisión deben ser un número."))
    try:
        result = await ctx.services.vehicles.record_maintenance(
            user_id=ctx.user_id,
            vehicle=vehicle,
            maintenance_type=args.maintenance_type,
            description=args.description,
            occurred_at=occurred,
            odometer=odometer,
            cost=cost,
            next_due_at=next_due,
            next_due_odometer=next_due_km,
            note=args.note,
            account_id=args.account_id,
        )
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle_id": result["vehicle_id"],
            "maintenance_id": result["maintenance_id"],
            "maintenance_type": result["maintenance_type"],
            "description": result["description"],
            "cost": fmt_amount(result["cost"]) if result["cost"] is not None else None,
            "transaction_id": result["transaction_id"],
            "occurred_at": _fmt_dt(result["occurred_at"]),
            "description_text": (
                f"Mantenimiento '{result['description'] or result['maintenance_type']}' de '{vehicle.name}' registrado"
                + (
                    f" con coste {fmt_amount(result['cost'])} € (movimiento {result['transaction_id']})."
                    if result["cost"] is not None
                    else "."
                )
            ),
        },
    )


async def _get_vehicle_status(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    args = VehicleStatusArgs(**kwargs)
    user_tz = await _user_timezone(ctx)
    try:
        vehicle = await ctx.services.vehicles.resolve(ctx.user_id, args.vehicle)
        status = await ctx.services.vehicles.get_status(ctx.user_id, vehicle.id)
    except Exception as exc:
        return _ko(exc)
    return ToolResult(
        ok=True,
        data={
            "vehicle": {
                "id": status["vehicle"].id,
                "name": status["vehicle"].name,
                "brand": status["vehicle"].brand,
                "model": status["vehicle"].model,
                "plate": status["vehicle"].plate,
                "year": status["vehicle"].year,
                "fuel_type": status["vehicle"].fuel_type,
                "current_odometer": float(status["vehicle"].current_odometer)
                if status["vehicle"].current_odometer
                else None,
            },
            "last_fuel_entries": [
                {
                    "id": f.id,
                    "occurred_at": to_local(f.occurred_at, user_tz).isoformat(),
                    "amount": fmt_amount(f.amount),
                    "liters": float(f.liters) if f.liters is not None else None,
                    "odometer": float(f.odometer) if f.odometer is not None else None,
                }
                for f in status["fuel_entries"]
            ],
            "last_maintenances": [
                {
                    "id": m.id,
                    "maintenance_type": m.maintenance_type,
                    "description": m.description,
                    "occurred_at": to_local(m.occurred_at, user_tz).isoformat(),
                    "cost": fmt_amount(m.cost) if m.cost is not None else None,
                }
                for m in status["maintenances"]
            ],
            "upcoming": [
                {
                    "id": m.id,
                    "maintenance_type": m.maintenance_type,
                    "description": m.description,
                    "next_due_at": to_local(m.next_due_at, user_tz).isoformat() if m.next_due_at else None,
                    "next_due_odometer": float(m.next_due_odometer) if m.next_due_odometer is not None else None,
                }
                for m in status["upcoming"]
            ],
            "insurance": [
                {
                    "id": i.id,
                    "company": i.company,
                    "insurance_type": i.insurance_type,
                    "start_date": i.start_date.isoformat() if i.start_date else None,
                    "end_date": i.end_date.isoformat() if i.end_date else None,
                    "active": i.active,
                }
                for i in status["insurances"]
            ],
            "spend_last_30_days": {
                "fuel": fmt_amount(status["spend_last_30_days"]["fuel"]),
                "maintenance": fmt_amount(status["spend_last_30_days"]["maintenance"]),
                "total": fmt_amount(status["spend_last_30_days"]["total"]),
                "currency": status["spend_last_30_days"]["currency"],
            },
        },
    )


async def _get_vehicle_expirations(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    try:
        data = await ctx.services.vehicles.get_expirations(ctx.user_id)
    except Exception as exc:
        return _ko(exc)
    user_tz = await _user_timezone(ctx)
    return ToolResult(
        ok=True,
        data={
            "horizon_days": data["horizon_days"],
            "expirations": [
                {
                    "vehicle": {
                        "id": item["vehicle"].id,
                        "name": item["vehicle"].name,
                        "plate": item["vehicle"].plate,
                    },
                    "insurance": [
                        {
                            "id": i.id,
                            "company": i.company,
                            "end_date": i.end_date.isoformat() if i.end_date else None,
                        }
                        for i in item["insurance"]
                    ],
                    "itv_due": [
                        {
                            "id": m.id,
                            "description": m.description,
                            "next_due_at": to_local(m.next_due_at, user_tz).isoformat() if m.next_due_at else None,
                        }
                        for m in item["itv"]
                    ],
                }
                for item in data["expirations"]
            ],
        },
    )


# --------------------------------------------------------------------------- #
# Registro de tools                                                           #
# --------------------------------------------------------------------------- #
VEHICLE_TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        name="add_vehicle",
        description="Da de alta un vehículo del usuario (coche/moto): 'Añade mi Auris 2019'. "
        "El odómetro (current_odometer) es opcional y solo informativo.",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Nombre: 'mi coche', 'Auris'..."},
                "brand": {"type": "string", "default": None},
                "model": {"type": "string", "default": None},
                "plate": {"type": "string", "default": None},
                "year": {"type": "integer", "default": None},
                "color": {"type": "string", "default": None},
                "fuel_type": {"type": "string", "default": None, "description": "gasolina, diésel, eléctrico..."},
                "current_odometer": {"type": "string", "default": None, "description": "Km actuales, p. ej. '182000'"},
                "notes": {"type": "string", "default": None},
            },
            "required": ["name"],
        },
        handler=_add_vehicle,
        args_model=VehicleCreateArgs,
    ),
    ToolDefinition(
        name="list_vehicles",
        description="Lista los vehículos dados de alta (ids, nombre, matrícula, combustible, odómetro). "
        "Úsala antes de repostar o registrar un mantenimiento para conocer los vehicle_id; nunca inventes ids.",
        input_schema={"type": "object", "properties": {}},
        handler=_list_vehicles,
    ),
    ToolDefinition(
        name="update_vehicle",
        description="Actualiza un vehículo existente (nombre, matrícula, año, odómetro...).",
        input_schema={
            "type": "object",
            "properties": {
                "vehicle_id": {"type": "integer"},
                "name": {"type": "string", "default": None},
                "brand": {"type": "string", "default": None},
                "model": {"type": "string", "default": None},
                "plate": {"type": "string", "default": None},
                "year": {"type": "integer", "default": None},
                "color": {"type": "string", "default": None},
                "fuel_type": {"type": "string", "default": None},
                "current_odometer": {"type": "string", "default": None},
                "notes": {"type": "string", "default": None},
            },
            "required": ["vehicle_id"],
        },
        handler=_update_vehicle,
        args_model=VehicleUpdateArgs,
    ),
    ToolDefinition(
        name="deactivate_vehicle",
        description="Desactiva un vehículo (ya no se listará en consultas). Su histórico de gastos NO se borra.",
        input_schema={
            "type": "object",
            "properties": {"vehicle_id": {"type": "integer"}},
            "required": ["vehicle_id"],
        },
        handler=_deactivate_vehicle,
        args_model=VehicleRefArgs,
        requires_confirmation=True,
    ),
    ToolDefinition(
        name="record_fuel",
        description="Registra un repostaje: importe SIEMPRE obligatorio (crea un gasto de categoría Transporte en la "
        "cuenta correspondiente de forma automática) y opcionalmente litros, odómetro y tipo de combustible. "
        "'He echado 45 € de gasolina en el coche' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "vehicle": {
                    "type": ["integer", "string"],
                    "description": "vehicle_id (de list_vehicles) o nombre ('mi coche')",
                },
                "amount": {"type": "string", "description": "Importe, p. ej. '45,80'"},
                "occurred_at": {"type": "string", "default": None, "description": "Cuándo (por defecto ahora)"},
                "liters": {"type": "string", "default": None, "description": "Litros, p. ej. '42'"},
                "odometer": {"type": "string", "default": None, "description": "Km del cuadro al repostar"},
                "fuel_type": {"type": "string", "default": None},
                "full_tank": {"type": "boolean", "default": None, "description": "True si llenó el depósito"},
                "note": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None, "description": "Cuenta del gasto (si se conoce)"},
            },
            "required": ["vehicle", "amount"],
        },
        handler=_record_fuel,
        args_model=FuelRecordArgs,
    ),
    ToolDefinition(
        name="add_vehicle_insurance",
        description="Registra el seguro de un vehículo (compañía, póliza, cobertura, vencimiento). Es un registro "
        "INFORMATIVO: NO crea ningún movimiento en las cuentas. Para planificar su pago usa las tools de suscripciones "
        "o gastos recurrentes.",
        input_schema={
            "type": "object",
            "properties": {
                "vehicle": {
                    "type": ["integer", "string"],
                    "description": "vehicle_id (de list_vehicles) o nombre",
                },
                "company": {"type": "string", "default": None},
                "policy_number": {"type": "string", "default": None},
                "insurance_type": {"type": "string", "default": None, "description": "'a todo riesgo', 'terceros'..."},
                "coverage": {"type": "string", "default": None},
                "start_date": {"type": "string", "default": None, "description": "Inicio de la póliza"},
                "end_date": {"type": "string", "default": None, "description": "Vencimiento, p. ej. 'el 15/03/2027'"},
                "amount": {"type": "string", "default": None, "description": "Importe anual (informativo)"},
                "billing_period": {
                    "type": "string",
                    "default": None,
                    "enum": ["yearly", "half_year", "quarter_year", "monthly", "one_time"],
                },
                "notes": {"type": "string", "default": None},
            },
            "required": ["vehicle"],
        },
        handler=_add_vehicle_insurance,
        args_model=InsuranceCreateArgs,
    ),
    ToolDefinition(
        name="update_vehicle_insurance",
        description="Actualiza un seguro de vehículo existente (vencimiento, compañía, importe...).",
        input_schema={
            "type": "object",
            "properties": {
                "insurance_id": {"type": "integer"},
                "company": {"type": "string", "default": None},
                "policy_number": {"type": "string", "default": None},
                "insurance_type": {"type": "string", "default": None},
                "coverage": {"type": "string", "default": None},
                "start_date": {"type": "string", "default": None},
                "end_date": {"type": "string", "default": None},
                "amount": {"type": "string", "default": None},
                "billing_period": {"type": "string", "default": None},
                "notes": {"type": "string", "default": None},
            },
            "required": ["insurance_id"],
        },
        handler=_update_vehicle_insurance,
        args_model=InsuranceUpdateArgs,
    ),
    ToolDefinition(
        name="record_vehicle_maintenance",
        description="Registra un mantenimiento/revisión/ITV del vehículo. Si tiene coste, crea un gasto de categoría "
        "Transporte en la cuenta correspondiente de forma automática (si no, solo se anota). Puede indicar la próxima "
        "revisión (fecha y/o km) para avisar con antelación. 'Le toca pasar la ITV en marzo' → maintenance_type='itv'.",
        input_schema={
            "type": "object",
            "properties": {
                "vehicle": {
                    "type": ["integer", "string"],
                    "description": "vehicle_id (de list_vehicles) o nombre",
                },
                "maintenance_type": {
                    "type": "string",
                    "default": "revision",
                    "enum": ["revision", "itv", "reparacion", "otro"],
                },
                "description": {
                    "type": "string",
                    "default": None,
                    "description": "Qué se hizo, p. ej. 'cambio de aceite'",
                },
                "occurred_at": {"type": "string", "default": None, "description": "Cuándo (por defecto hoy)"},
                "odometer": {"type": "string", "default": None, "description": "Km en el momento"},
                "cost": {"type": "string", "default": None, "description": "Coste (opcional; si existe crea el gasto)"},
                "next_due_at": {
                    "type": "string",
                    "default": None,
                    "description": "Próxima revisión: 'en 3 meses' o ISO",
                },
                "next_due_odometer": {"type": "string", "default": None, "description": "Km de la próxima revisión"},
                "note": {"type": "string", "default": None},
                "account_id": {"type": "integer", "default": None, "description": "Cuenta del gasto (si se conoce)"},
            },
            "required": ["vehicle"],
        },
        handler=_record_vehicle_maintenance,
        args_model=MaintenanceRecordArgs,
    ),
    ToolDefinition(
        name="get_vehicle_status",
        description="Estado completo de UN vehículo: odómetro, últimos repostajes, últimos mantenimientos, próximas "
        "revisiones/ITV, seguros activos y gasto de los últimos 30 días en ese vehículo. '¿Cómo va mi coche?', "
        "'¿cuándo le toca la ITV?' → esta tool.",
        input_schema={
            "type": "object",
            "properties": {
                "vehicle": {
                    "type": ["integer", "string"],
                    "description": "vehicle_id (de list_vehicles) o nombre",
                },
            },
            "required": ["vehicle"],
        },
        handler=_get_vehicle_status,
        args_model=VehicleStatusArgs,
    ),
    ToolDefinition(
        name="get_vehicle_expirations",
        description="Vehículos con seguro o ITV próximos a caducar (próximos 30 días) o ya vencidos. "
        "'¿Qué se me va a caducar?' → esta tool.",
        input_schema={"type": "object", "properties": {}},
        handler=_get_vehicle_expirations,
    ),
]
