"""Servicio de dominio del módulo de vehículos (Fase 4).

Un vehículo es un bien de UN usuario (coche/moto); pertenece a `user_id` y
nunca se borra si tiene histórico: se desactiva (`active=False`).

Repostajes y mantenimientos con coste se integrarán con el módulo de finanzas:
CADA repostaje y CADA mantenimiento con `cost` crea EXACTAMENTE UNA transacción
de gasto (`transaction_id`) insertada en la MISMA transacción atómica que el
detalle (patrón `transaction()` + `session=` del código base). El seguro NO es
un movimiento financiero: es un registro informativo sin `transaction_id`.

La cuenta destino se resuelve ANTES de abrir la transacción (regla 1-2-3 de
`TransactionService.resolve_account`), igual que en `SharedExpenseService`.
"""

from __future__ import annotations

import builtins
from datetime import date, datetime, timedelta
from decimal import Decimal

from asistente.domain.people import normalize_name
from asistente.domain.transactions import TransactionService
from asistente.persistence.models import (
    Vehicle,
    VehicleInsurance,
    VehicleMaintenance,
)
from asistente.persistence.repositories import (
    FuelEntryRepository,
    VehicleInsuranceRepository,
    VehicleMaintenanceRepository,
    VehicleRepository,
)
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import utc_now

VEHICLE_MAINTENANCE_TYPES = ("revision", "itv", "reparacion", "otro")
VEHICLE_BILLING_PERIODS = ("one_time", "monthly", "quarter_year", "half_year", "yearly")
_NOTE_MAX = 2000


class VehicleService:
    def __init__(
        self,
        vehicle_repo: VehicleRepository,
        fuel_repo: FuelEntryRepository,
        maintenance_repo: VehicleMaintenanceRepository,
        insurance_repo: VehicleInsuranceRepository,
        transactions: TransactionService,
    ) -> None:
        self._vehicles = vehicle_repo
        self._fuel = fuel_repo
        self._maintenance = maintenance_repo
        self._insurance = insurance_repo
        self._transactions = transactions

    # ------------------------------------------------------------------ #
    # CRUD de vehículo                                                     #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        brand: str | None = None,
        model: str | None = None,
        plate: str | None = None,
        year: int | None = None,
        color: str | None = None,
        fuel_type: str | None = None,
        current_odometer=None,
        notes: str | None = None,
    ) -> Vehicle:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre del vehículo no puede estar vacío.")
        if year is not None and not (1900 <= int(year) <= 2100):
            raise ValueError("El año del vehículo debe estar entre 1900 y 2100.")
        if current_odometer is not None and Decimal(str(current_odometer)) < 0:
            raise ValueError("El odómetro no puede ser negativo.")
        return await self._vehicles.create(
            user_id=user_id,
            name=(name or "").strip()[:200],
            normalized_name=normalized[:200],
            brand=(brand or "").strip()[:200] or None,
            model=(model or "").strip()[:200] or None,
            plate=(plate or "").strip()[:20] or None,
            year=int(year) if year is not None else None,
            color=(color or "").strip()[:50] or None,
            fuel_type=(fuel_type or "").strip()[:32] or None,
            current_odometer=Decimal(str(current_odometer)) if current_odometer is not None else None,
            notes=(notes or "").strip()[:_NOTE_MAX] or None,
        )

    async def list(self, user_id: int, *, only_active: bool = True) -> list[Vehicle]:
        return await self._vehicles.list_for_user(user_id, only_active=only_active)

    async def get(self, user_id: int, vehicle_id: int) -> Vehicle:
        vehicle = await self._vehicles.find_one(user_id, vehicle_id)
        if vehicle is None:
            raise NotFoundError("El vehículo no existe o no te pertenece.")
        return vehicle

    async def resolve(self, user_id: int, name_or_id) -> Vehicle:
        """Resuelve id/entero o nombre textual (misma semántica que personas)."""
        if isinstance(name_or_id, int):
            return await self.get(user_id, name_or_id)
        normalized = normalize_name(str(name_or_id))
        if not normalized:
            raise ValueError("El nombre del vehículo está vacío.")
        matches = await self._vehicles.find_by_normalized_name(user_id, normalized)
        if len(matches) == 0:
            raise NotFoundError(f"No se encontró ningún vehículo activo llamado '{name_or_id.strip()}'.")
        if len(matches) > 1:
            ids = ", ".join(str(v.id) for v in matches)
            raise ValueError(f"Hay varios vehículos llamados '{name_or_id.strip()}'. Indica su ID (opciones: {ids}).")
        return matches[0]

    async def update(
        self,
        user_id: int,
        vehicle_id: int,
        *,
        name=None,
        brand=None,
        model=None,
        plate=None,
        year=None,
        color=None,
        fuel_type=None,
        current_odometer=None,
        notes=None,
    ) -> Vehicle:
        await self.get(user_id, vehicle_id)
        fields: dict = {}
        if name is not None:
            normalized = normalize_name(name)
            if not normalized:
                raise ValueError("El nombre del vehículo no puede estar vacío.")
            fields["name"] = (name or "").strip()[:200]
            fields["normalized_name"] = normalized[:200]
        if brand is not None:
            fields["brand"] = (brand or "").strip()[:200] or None
        if model is not None:
            fields["model"] = (model or "").strip()[:200] or None
        if plate is not None:
            fields["plate"] = (plate or "").strip()[:20] or None
        if year is not None:
            if not (1900 <= int(year) <= 2100):
                raise ValueError("El año del vehículo debe estar entre 1900 y 2100.")
            fields["year"] = int(year)
        if color is not None:
            fields["color"] = (color or "").strip()[:50] or None
        if fuel_type is not None:
            fields["fuel_type"] = (fuel_type or "").strip()[:32] or None
        if current_odometer is not None:
            odometer = Decimal(str(current_odometer))
            if odometer < 0:
                raise ValueError("El odómetro no puede ser negativo.")
            fields["current_odometer"] = odometer
        if notes is not None:
            fields["notes"] = (notes or "").strip()[:_NOTE_MAX] or None
        fields["updated_at"] = utc_now()
        updated = await self._vehicles.update(user_id, vehicle_id, **fields)
        assert updated is not None
        return updated

    async def deactivate(self, user_id: int, vehicle_id: int) -> Vehicle:
        vehicle = await self.get(user_id, vehicle_id)
        if not vehicle.active:
            raise ValueError("El vehículo ya está desactivado.")
        updated = await self._vehicles.update(user_id, vehicle_id, active=False, updated_at=utc_now())
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Repostajes                                                           #
    # ------------------------------------------------------------------ #
    async def record_fuel(
        self,
        *,
        user_id: int,
        vehicle: Vehicle,
        amount: Decimal,
        occurred_at: datetime | None = None,
        liters=None,
        odometer=None,
        fuel_type: str | None = None,
        full_tank: bool | None = None,
        note: str | None = None,
        account_id: int | None = None,
    ) -> dict:
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValueError("El importe del repostaje debe ser mayor que cero.")
        if liters is not None and Decimal(str(liters)) <= 0:
            raise ValueError("Los litros deben ser mayores que cero.")
        if odometer is not None and Decimal(str(odometer)) < 0:
            raise ValueError("El odómetro no puede ser negativo.")

        occurred = occurred_at or utc_now()
        # La cuenta se resuelve ANTES de abrir la transacción (patrón existente).
        account = await self._transactions.resolve_account(user_id, account_id)

        async with self._vehicles.transaction() as session:
            transaction = await self._transactions.create_in_session(
                user_id=user_id,
                type="expense",
                amount=amount,
                occurred_at=occurred,
                account=account,
                merchant="Combustible",
                category="Transporte",
                note=(note or "").strip()[:_NOTE_MAX] or None,
                source="manual",
                session=session,
            )
            entry = await self._fuel.create(
                user_id=user_id,
                vehicle_id=vehicle.id,
                occurred_at=occurred,
                amount=amount,
                liters=Decimal(str(liters)) if liters is not None else None,
                odometer=Decimal(str(odometer)) if odometer is not None else None,
                fuel_type=(fuel_type or "").strip()[:32] or None,
                full_tank=full_tank,
                note=(note or "").strip()[:_NOTE_MAX] or None,
                transaction_id=transaction.id,
                session=session,
            )
            advanced = False
            if odometer is not None:
                advanced = await self._vehicles.advance_odometer(
                    user_id, vehicle.id, Decimal(str(odometer)), session=session
                )

        return {
            "vehicle_id": vehicle.id,
            "fuel_entry_id": entry.id,
            "transaction_id": transaction.id,
            "amount": amount,
            "currency": transaction.currency,
            "occurred_at": entry.occurred_at,
            "liters": entry.liters,
            "odometer": entry.odometer,
            "odometer_advanced": advanced,
            "full_tank": entry.full_tank,
            "account_id": transaction.account_id,
        }

    # ------------------------------------------------------------------ #
    # Mantenimiento e ITV                                                  #
    # ------------------------------------------------------------------ #
    async def record_maintenance(
        self,
        *,
        user_id: int,
        vehicle: Vehicle,
        maintenance_type: str = "revision",
        description: str | None = None,
        occurred_at: datetime | None = None,
        odometer=None,
        cost: Decimal | None = None,
        next_due_at: datetime | None = None,
        next_due_odometer=None,
        note: str | None = None,
        account_id: int | None = None,
    ) -> dict:
        if maintenance_type not in VEHICLE_MAINTENANCE_TYPES:
            raise ValueError(
                f"Tipo de mantenimiento no válido: {maintenance_type}. Válidos: {', '.join(VEHICLE_MAINTENANCE_TYPES)}."
            )
        if cost is not None and Decimal(str(cost)) <= 0:
            raise ValueError("El coste del mantenimiento debe ser mayor que cero.")
        if odometer is not None and Decimal(str(odometer)) < 0:
            raise ValueError("El odómetro no puede ser negativo.")
        if next_due_odometer is not None and Decimal(str(next_due_odometer)) < 0:
            raise ValueError("El kilómetro de la próxima revisión no puede ser negativo.")

        occurred = occurred_at or utc_now()
        cost_dec = Decimal(str(cost)) if cost is not None else None

        if cost_dec is None:
            maintenance = await self._maintenance.create(
                user_id=user_id,
                vehicle_id=vehicle.id,
                maintenance_type=maintenance_type,
                description=(description or "").strip()[:500] or None,
                occurred_at=occurred,
                odometer=Decimal(str(odometer)) if odometer is not None else None,
                cost=None,
                next_due_at=next_due_at,
                next_due_odometer=Decimal(str(next_due_odometer)) if next_due_odometer is not None else None,
                note=(note or "").strip()[:_NOTE_MAX] or None,
                transaction_id=None,
            )
            return {
                "vehicle_id": vehicle.id,
                "maintenance_id": maintenance.id,
                "maintenance_type": maintenance.maintenance_type,
                "description": maintenance.description,
                "occurred_at": maintenance.occurred_at,
                "cost": maintenance.cost,
                "transaction_id": None,
            }

        account = await self._transactions.resolve_account(user_id, account_id)
        async with self._vehicles.transaction() as session:
            transaction = await self._transactions.create_in_session(
                user_id=user_id,
                type="expense",
                amount=cost_dec,
                occurred_at=occurred,
                account=account,
                merchant="Mantenimiento coche",
                category="Transporte",
                note=(note or "").strip()[:_NOTE_MAX] or None,
                source="manual",
                session=session,
            )
            maintenance = await self._maintenance.create(
                user_id=user_id,
                vehicle_id=vehicle.id,
                maintenance_type=maintenance_type,
                description=(description or "").strip()[:500] or None,
                occurred_at=occurred,
                odometer=Decimal(str(odometer)) if odometer is not None else None,
                cost=cost_dec,
                next_due_at=next_due_at,
                next_due_odometer=Decimal(str(next_due_odometer)) if next_due_odometer is not None else None,
                note=(note or "").strip()[:_NOTE_MAX] or None,
                transaction_id=transaction.id,
                session=session,
            )

        return {
            "vehicle_id": vehicle.id,
            "maintenance_id": maintenance.id,
            "maintenance_type": maintenance.maintenance_type,
            "description": maintenance.description,
            "occurred_at": maintenance.occurred_at,
            "cost": maintenance.cost,
            "transaction_id": transaction.id,
        }

    async def list_maintenance(
        self, user_id: int, vehicle: Vehicle, *, limit: int = 20
    ) -> builtins.list[VehicleMaintenance]:
        return await self._maintenance.list_for_vehicle(user_id, vehicle.id, limit=limit)

    # ------------------------------------------------------------------ #
    # Seguros                                                              #
    # ------------------------------------------------------------------ #
    async def add_insurance(
        self,
        *,
        user_id: int,
        vehicle: Vehicle,
        company: str | None = None,
        policy_number: str | None = None,
        insurance_type: str | None = None,
        coverage: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        amount=None,
        billing_period: str | None = None,
        notes: str | None = None,
    ) -> VehicleInsurance:
        if amount is not None and Decimal(str(amount)) <= 0:
            raise ValueError("El importe del seguro debe ser mayor que cero.")
        if billing_period not in (None, *VEHICLE_BILLING_PERIODS):
            raise ValueError(
                f"Periodicidad no válida: {billing_period}. Válidas: {', '.join(VEHICLE_BILLING_PERIODS)}."
            )
        if start_date is not None and end_date is not None and end_date < start_date:
            raise ValueError("La fecha de fin del seguro no puede ser anterior a la de inicio.")
        return await self._insurance.create(
            user_id=user_id,
            vehicle_id=vehicle.id,
            company=(company or "").strip()[:200] or None,
            policy_number=(policy_number or "").strip()[:100] or None,
            insurance_type=(insurance_type or "").strip()[:64] or None,
            coverage=(coverage or "").strip()[:_NOTE_MAX] or None,
            start_date=start_date,
            end_date=end_date,
            amount=Decimal(str(amount)) if amount is not None else None,
            currency="EUR",
            billing_period=billing_period,
            active=True,
            notes=(notes or "").strip()[:_NOTE_MAX] or None,
        )

    async def update_insurance(self, user_id: int, insurance_id: int, **params) -> VehicleInsurance:
        insurance = await self._insurance.find_one(user_id, insurance_id)
        if insurance is None:
            raise NotFoundError("El seguro no existe o no te pertenece.")
        fields: dict = {}
        for key in ("company", "policy_number", "insurance_type", "coverage", "billing_period", "active"):
            if key in params:
                value = params[key]
                if key == "billing_period":
                    if value not in (None, *VEHICLE_BILLING_PERIODS):
                        raise ValueError(
                            f"Periodicidad no válida: {value}. Válidas: {', '.join(VEHICLE_BILLING_PERIODS)}."
                        )
                elif key in ("company", "insurance_type"):
                    fields[key] = (value or "").strip()[:200] or None
                elif key == "policy_number":
                    fields[key] = (value or "").strip()[:100] or None
                elif key == "coverage":
                    fields[key] = (value or "").strip()[:_NOTE_MAX] or None
                else:
                    fields[key] = value
        start_date = params.get("start_date", insurance.start_date)
        end_date = params.get("end_date", insurance.end_date)
        if start_date is not None and end_date is not None and end_date < start_date:
            raise ValueError("La fecha de fin del seguro no puede ser anterior a la de inicio.")
        if "start_date" in params:
            fields["start_date"] = params["start_date"]
        if "end_date" in params:
            fields["end_date"] = params["end_date"]
        if "amount" in params:
            amount = params["amount"]
            if amount is not None and Decimal(str(amount)) <= 0:
                raise ValueError("El importe del seguro debe ser mayor que cero.")
            fields["amount"] = Decimal(str(amount)) if amount is not None else None
        if "notes" in params:
            fields["notes"] = (params["notes"] or "").strip()[:_NOTE_MAX] or None
        fields["updated_at"] = utc_now()
        updated = await self._insurance.update(user_id, insurance_id, **fields)
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Estado                                                               #
    # ------------------------------------------------------------------ #
    async def get_status(self, user_id: int, vehicle_id: int) -> dict:
        vehicle = await self.get(user_id, vehicle_id)
        now = utc_now()
        week_ago = now - timedelta(days=30)

        fuel_entries = await self._fuel.list_for_vehicle(user_id, vehicle_id, limit=5)
        maintenances = await self._maintenance.list_for_vehicle(user_id, vehicle_id, limit=5)
        insurances = await self._insurance.list_for_vehicle(user_id, vehicle_id, only_active=True, limit=20)

        upcoming: list[VehicleMaintenance] = []
        for m in await self._maintenance.list_for_vehicle(user_id, vehicle_id, limit=100):
            due = m.next_due_at is not None and m.next_due_at >= now
            due_km = m.next_due_odometer is not None and (
                vehicle.current_odometer is None or m.next_due_odometer <= vehicle.current_odometer
            )
            if due or due_km:
                upcoming.append(m)
        upcoming.sort(key=lambda m: m.next_due_at or m.occurred_at or now)

        insurance_upcoming = [i for i in insurances if i.end_date is not None and i.end_date >= date.today()]
        insurance_upcoming.sort(key=lambda i: i.end_date or date.max)

        spend = await self._spend_in_window(user_id, start_utc=week_ago, end_utc=now)

        return {
            "vehicle": vehicle,
            "fuel_entries": fuel_entries,
            "maintenances": maintenances,
            "upcoming": upcoming[:5],
            "insurances": insurance_upcoming,
            "spend_last_30_days": spend,
        }

    async def get_expirations(self, user_id: int) -> dict:
        """Vehículos con seguro o ITV próxima a caducar (<= 30 días) o vencida."""
        horizon = date.today() + timedelta(days=30)
        now = utc_now()
        result = []
        for vehicle in await self._vehicles.list_for_user(user_id, only_active=True):
            item: dict = {"vehicle": vehicle, "insurance": [], "itv": []}
            for insurance in await self._insurance.list_for_vehicle(user_id, vehicle.id, only_active=True, limit=20):
                if insurance.end_date is None:
                    continue
                if insurance.end_date <= horizon:
                    item["insurance"].append(insurance)
            for m in await self._maintenance.list_for_vehicle(user_id, vehicle.id, limit=100):
                if (
                    m.maintenance_type == "itv"
                    and m.next_due_at is not None
                    and m.next_due_at <= now + timedelta(days=30)
                ):
                    item["itv"].append(m)
            if item["insurance"] or item["itv"]:
                result.append(item)
        return {"expirations": result, "horizon_days": 30}

    async def _spend_in_window(self, user_id: int, *, start_utc: datetime, end_utc: datetime) -> dict:
        fuel_total = sum(
            (
                Decimal(str(f.amount))
                for f in await self._fuel.list_for_user(user_id, start_utc=start_utc, end_utc=end_utc)
            ),
            Decimal("0.00"),
        )
        maintenance_total = sum(
            (
                Decimal(str(m.cost))
                for m in await self._maintenance.list_for_user(user_id, start_utc=start_utc, end_utc=end_utc)
                if m.cost is not None
            ),
            Decimal("0.00"),
        )
        return {
            "fuel": fuel_total,
            "maintenance": maintenance_total,
            "total": fuel_total + maintenance_total,
            "currency": "EUR",
        }
