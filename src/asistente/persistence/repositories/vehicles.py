"""Repositorios del módulo de vehículos (Fase 4), scoped por user_id.

Todos los listados filtran SIEMPRE por `user_id`: un usuario jamás puede leer
los vehículos/repostajes/mantenimientos/seguros de otro. Los métodos
`create`/`update` aceptan `session` para participar en la misma transacción
que otras tablas (p. ej. FuelEntry + Transaction, un solo commit).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select, update

from asistente.persistence.models import FuelEntry, Vehicle, VehicleInsurance, VehicleMaintenance
from asistente.persistence.repositories.base import Repository


class VehicleRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        normalized_name: str,
        brand: str | None = None,
        model: str | None = None,
        plate: str | None = None,
        year: int | None = None,
        color: str | None = None,
        fuel_type: str | None = None,
        current_odometer=None,
        notes: str | None = None,
        active: bool = True,
        session=None,
    ) -> Vehicle:
        async with self._session(session) as s:
            row = Vehicle(
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
                brand=brand,
                model=model,
                plate=plate,
                year=year,
                color=color,
                fuel_type=fuel_type,
                current_odometer=current_odometer,
                notes=notes,
                active=active,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, vehicle_id: int) -> Vehicle | None:
        async with self._session() as session:
            result = await session.execute(select(Vehicle).where(Vehicle.id == vehicle_id, Vehicle.user_id == user_id))
            return result.scalar_one_or_none()

    async def find_by_normalized_name(self, user_id: int, normalized_name: str) -> list[Vehicle]:
        stmt = (
            select(Vehicle)
            .where(Vehicle.user_id == user_id, Vehicle.normalized_name == normalized_name, Vehicle.active.is_(True))
            .order_by(Vehicle.created_at.asc())
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(self, user_id: int, *, only_active: bool = True, limit: int = 100) -> list[Vehicle]:
        stmt = select(Vehicle).where(Vehicle.user_id == user_id)
        if only_active:
            stmt = stmt.where(Vehicle.active.is_(True))
        stmt = stmt.order_by(Vehicle.name.asc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, vehicle_id: int, *, session=None, **fields) -> Vehicle | None:
        async with self._session(session) as s:
            row = await s.get(Vehicle, vehicle_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row

    async def advance_odometer(self, user_id: int, vehicle_id: int, odometer, *, session=None) -> bool:
        """Actualiza el odómetro SÓLO si es mayor que el conocido (o si no hay).

        Devuelve True si se actualizó el vehículo (nunca retrocede). Se ejecuta
        en la sesión dada para formar parte de la transacción del evento.
        """
        async with self._session(session) as s:
            result = await s.execute(
                update(Vehicle)
                .where(
                    Vehicle.id == vehicle_id,
                    Vehicle.user_id == user_id,
                    or_(Vehicle.current_odometer.is_(None), Vehicle.current_odometer < odometer),
                )
                .values(current_odometer=odometer)
                .returning(Vehicle.id)
            )
            updated = result.scalar_one_or_none() is not None
            return updated


class FuelEntryRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        vehicle_id: int,
        occurred_at: datetime,
        amount,
        currency: str = "EUR",
        liters=None,
        odometer=None,
        fuel_type: str | None = None,
        full_tank: bool | None = None,
        note: str | None = None,
        transaction_id: int | None = None,
        session=None,
    ) -> FuelEntry:
        async with self._session(session) as s:
            row = FuelEntry(
                user_id=user_id,
                vehicle_id=vehicle_id,
                occurred_at=occurred_at,
                amount=amount,
                currency=currency,
                liters=liters,
                odometer=odometer,
                fuel_type=fuel_type,
                full_tank=full_tank,
                note=note,
                transaction_id=transaction_id,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, entry_id: int) -> FuelEntry | None:
        async with self._session() as session:
            result = await session.execute(
                select(FuelEntry).where(FuelEntry.id == entry_id, FuelEntry.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_vehicle(self, user_id: int, vehicle_id: int, *, limit: int = 20) -> list[FuelEntry]:
        stmt = (
            select(FuelEntry)
            .where(FuelEntry.user_id == user_id, FuelEntry.vehicle_id == vehicle_id)
            .order_by(FuelEntry.occurred_at.desc(), FuelEntry.id.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        limit: int = 500,
    ) -> list[FuelEntry]:
        stmt = select(FuelEntry).where(FuelEntry.user_id == user_id)
        if start_utc is not None:
            stmt = stmt.where(FuelEntry.occurred_at >= start_utc)
        if end_utc is not None:
            stmt = stmt.where(FuelEntry.occurred_at < end_utc)
        stmt = stmt.order_by(FuelEntry.occurred_at.desc(), FuelEntry.id.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())


class VehicleMaintenanceRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        vehicle_id: int,
        maintenance_type: str = "revision",
        description: str | None = None,
        occurred_at: datetime,
        odometer=None,
        cost=None,
        currency: str = "EUR",
        next_due_at: datetime | None = None,
        next_due_odometer=None,
        note: str | None = None,
        transaction_id: int | None = None,
        session=None,
    ) -> VehicleMaintenance:
        async with self._session(session) as s:
            row = VehicleMaintenance(
                user_id=user_id,
                vehicle_id=vehicle_id,
                maintenance_type=maintenance_type,
                description=description,
                occurred_at=occurred_at,
                odometer=odometer,
                cost=cost,
                currency=currency,
                next_due_at=next_due_at,
                next_due_odometer=next_due_odometer,
                note=note,
                transaction_id=transaction_id,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, maintenance_id: int) -> VehicleMaintenance | None:
        async with self._session() as session:
            result = await session.execute(
                select(VehicleMaintenance).where(
                    VehicleMaintenance.id == maintenance_id, VehicleMaintenance.user_id == user_id
                )
            )
            return result.scalar_one_or_none()

    async def list_for_vehicle(self, user_id: int, vehicle_id: int, *, limit: int = 20) -> list[VehicleMaintenance]:
        stmt = (
            select(VehicleMaintenance)
            .where(VehicleMaintenance.user_id == user_id, VehicleMaintenance.vehicle_id == vehicle_id)
            .order_by(VehicleMaintenance.occurred_at.desc(), VehicleMaintenance.id.desc())
            .limit(limit)
        )
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_for_user(
        self,
        user_id: int,
        *,
        start_utc: datetime | None = None,
        end_utc: datetime | None = None,
        limit: int = 500,
    ) -> list[VehicleMaintenance]:
        stmt = select(VehicleMaintenance).where(VehicleMaintenance.user_id == user_id)
        if start_utc is not None:
            stmt = stmt.where(VehicleMaintenance.occurred_at >= start_utc)
        if end_utc is not None:
            stmt = stmt.where(VehicleMaintenance.occurred_at < end_utc)
        stmt = stmt.order_by(VehicleMaintenance.occurred_at.desc(), VehicleMaintenance.id.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())


class VehicleInsuranceRepository(Repository):
    async def create(
        self,
        *,
        user_id: int,
        vehicle_id: int,
        company: str | None = None,
        policy_number: str | None = None,
        insurance_type: str | None = None,
        coverage: str | None = None,
        start_date=None,
        end_date=None,
        amount=None,
        currency: str = "EUR",
        billing_period: str | None = None,
        active: bool = True,
        notes: str | None = None,
        session=None,
    ) -> VehicleInsurance:
        async with self._session(session) as s:
            row = VehicleInsurance(
                user_id=user_id,
                vehicle_id=vehicle_id,
                company=company,
                policy_number=policy_number,
                insurance_type=insurance_type,
                coverage=coverage,
                start_date=start_date,
                end_date=end_date,
                amount=amount,
                currency=currency,
                billing_period=billing_period,
                active=active,
                notes=notes,
            )
            s.add(row)
            await s.flush()
            await s.refresh(row)
            return row

    async def find_one(self, user_id: int, insurance_id: int) -> VehicleInsurance | None:
        async with self._session() as session:
            result = await session.execute(
                select(VehicleInsurance).where(VehicleInsurance.id == insurance_id, VehicleInsurance.user_id == user_id)
            )
            return result.scalar_one_or_none()

    async def list_for_vehicle(
        self,
        user_id: int,
        vehicle_id: int,
        *,
        only_active: bool | None = None,
        limit: int = 20,
    ) -> list[VehicleInsurance]:
        stmt = select(VehicleInsurance).where(
            VehicleInsurance.user_id == user_id, VehicleInsurance.vehicle_id == vehicle_id
        )
        if only_active is not None:
            stmt = stmt.where(VehicleInsurance.active.is_(only_active))
        stmt = stmt.order_by(VehicleInsurance.end_date.asc().nulls_last(), VehicleInsurance.id.desc()).limit(limit)
        async with self._session() as session:
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def update(self, user_id: int, insurance_id: int, *, session=None, **fields) -> VehicleInsurance | None:
        async with self._session(session) as s:
            row = await s.get(VehicleInsurance, insurance_id)
            if row is None or row.user_id != user_id:
                return None
            for key, value in fields.items():
                setattr(row, key, value)
            await s.flush()
            await s.refresh(row)
            return row
