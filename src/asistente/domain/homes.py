"""Servicio de dominio del módulo de casa (Fase 4).

Una vivienda pertenece a UN usuario. Su historial de mantenimientos nunca se
borra; un mantenimiento sin abordar es `pending` y con fecha prevista aparece
en "próximas". Cuando tiene `occurred_at` o `cost` pasa a `done`.

Mantenimientos con coste: se crea EXACTAMENTE UNA transacción de gasto
(categoría 'Vivienda') en la MISMA transacción atómica que el detalle. La
cuenta destino se resuelve ANTES de abrir la transacción (patrón del proyecto).
"""

from __future__ import annotations

import builtins
from datetime import datetime, timedelta
from decimal import Decimal

from asistente.domain.people import normalize_name
from asistente.domain.transactions import TransactionService
from asistente.persistence.models import Home, HomeMaintenance
from asistente.persistence.repositories import HomeMaintenanceRepository, HomeRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import utc_now

HOME_MAINTENANCE_TYPES = ("revision", "reparacion", "otro")
_NOTE_MAX = 2000


class HomeService:
    def __init__(
        self,
        home_repo: HomeRepository,
        maintenance_repo: HomeMaintenanceRepository,
        transactions: TransactionService,
    ) -> None:
        self._homes = home_repo
        self._maintenance = maintenance_repo
        self._transactions = transactions

    # ------------------------------------------------------------------ #
    # CRUD de vivienda                                                     #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        home_type: str | None = None,
        address: str | None = None,
        notes: str | None = None,
    ) -> Home:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre de la vivienda no puede estar vacío.")
        if home_type is not None and home_type not in ("piso", "casa", "garaje", "local", "otro"):
            raise ValueError("Tipo de vivienda no válido. Válidos: piso, casa, garaje, local, otro.")
        return await self._homes.create(
            user_id=user_id,
            name=(name or "").strip()[:200],
            normalized_name=normalized[:200],
            home_type=(home_type or "").strip()[:32] or None,
            address=(address or "").strip()[:300] or None,
            notes=(notes or "").strip()[:_NOTE_MAX] or None,
        )

    async def list(self, user_id: int, *, only_active: bool = True) -> list[Home]:
        return await self._homes.list_for_user(user_id, only_active=only_active)

    async def get(self, user_id: int, home_id: int) -> Home:
        home = await self._homes.find_one(user_id, home_id)
        if home is None:
            raise NotFoundError("La vivienda no existe o no te pertenece.")
        return home

    async def resolve(self, user_id: int, name_or_id) -> Home:
        if isinstance(name_or_id, int):
            return await self.get(user_id, name_or_id)
        normalized = normalize_name(str(name_or_id))
        if not normalized:
            raise ValueError("El nombre de la vivienda está vacío.")
        matches = await self._homes.find_by_normalized_name(user_id, normalized)
        if len(matches) == 0:
            raise NotFoundError(f"No se encontró ninguna vivienda activa llamada '{name_or_id.strip()}'.")
        if len(matches) > 1:
            ids = ", ".join(str(h.id) for h in matches)
            raise ValueError(f"Hay varias viviendas llamadas '{name_or_id.strip()}'. Indica su ID (opciones: {ids}).")
        return matches[0]

    async def update(self, user_id: int, home_id: int, *, name=None, home_type=None, address=None, notes=None) -> Home:
        await self.get(user_id, home_id)
        fields: dict = {}
        if name is not None:
            normalized = normalize_name(name)
            if not normalized:
                raise ValueError("El nombre de la vivienda no puede estar vacío.")
            fields["name"] = (name or "").strip()[:200]
            fields["normalized_name"] = normalized[:200]
        if home_type is not None:
            if home_type not in ("piso", "casa", "garaje", "local", "otro"):
                raise ValueError("Tipo de vivienda no válido. Válidos: piso, casa, garaje, local, otro.")
            fields["home_type"] = (home_type or "").strip()[:32] or None
        if address is not None:
            fields["address"] = (address or "").strip()[:300] or None
        if notes is not None:
            fields["notes"] = (notes or "").strip()[:_NOTE_MAX] or None
        fields["updated_at"] = utc_now()
        updated = await self._homes.update(user_id, home_id, **fields)
        assert updated is not None
        return updated

    async def deactivate(self, user_id: int, home_id: int) -> Home:
        home = await self.get(user_id, home_id)
        if not home.active:
            raise ValueError("La vivienda ya está desactivada.")
        updated = await self._homes.update(user_id, home_id, active=False, updated_at=utc_now())
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Mantenimientos                                                       #
    # ------------------------------------------------------------------ #
    async def record_maintenance(
        self,
        *,
        user_id: int,
        home: Home,
        title: str,
        maintenance_type: str = "revision",
        occurred_at: datetime | None = None,
        cost: Decimal | None = None,
        next_due_at: datetime | None = None,
        note: str | None = None,
        account_id: int | None = None,
    ) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("El mantenimiento necesita un título/descripción.")
        if maintenance_type not in HOME_MAINTENANCE_TYPES:
            raise ValueError(
                f"Tipo de mantenimiento no válido: {maintenance_type}. Válidos: {', '.join(HOME_MAINTENANCE_TYPES)}."
            )
        if cost is not None and Decimal(str(cost)) <= 0:
            raise ValueError("El coste del mantenimiento debe ser mayor que cero.")

        cost_dec = Decimal(str(cost)) if cost is not None else None
        # Un mantenimiento con fechas de ejecución: hecho. Solo previsto: pendiente.
        status = "done" if (occurred_at is not None or cost_dec is not None) else "pending"

        if cost_dec is None:
            maintenance = await self._maintenance.create(
                user_id=user_id,
                home_id=home.id,
                title=title[:300],
                maintenance_type=maintenance_type,
                occurred_at=occurred_at,
                cost=None,
                status=status,
                next_due_at=next_due_at,
                note=(note or "").strip()[:_NOTE_MAX] or None,
                transaction_id=None,
            )
            return {
                "home_id": home.id,
                "maintenance_id": maintenance.id,
                "title": maintenance.title,
                "maintenance_type": maintenance.maintenance_type,
                "occurred_at": maintenance.occurred_at,
                "cost": maintenance.cost,
                "status": maintenance.status,
                "next_due_at": maintenance.next_due_at,
                "transaction_id": None,
            }

        account = await self._transactions.resolve_account(user_id, account_id)
        async with self._homes.transaction() as session:
            transaction = await self._transactions.create_in_session(
                user_id=user_id,
                type="expense",
                amount=cost_dec,
                occurred_at=occurred_at or utc_now(),
                account=account,
                merchant="Mantenimiento casa",
                category="Vivienda",
                note=(note or "").strip()[:_NOTE_MAX] or None,
                source="manual",
                session=session,
            )
            maintenance = await self._maintenance.create(
                user_id=user_id,
                home_id=home.id,
                title=title[:300],
                maintenance_type=maintenance_type,
                occurred_at=occurred_at or transaction.occurred_at,
                cost=cost_dec,
                status=status,
                next_due_at=next_due_at,
                note=(note or "").strip()[:_NOTE_MAX] or None,
                transaction_id=transaction.id,
                session=session,
            )

        return {
            "home_id": home.id,
            "maintenance_id": maintenance.id,
            "title": maintenance.title,
            "maintenance_type": maintenance.maintenance_type,
            "occurred_at": maintenance.occurred_at,
            "cost": maintenance.cost,
            "status": maintenance.status,
            "next_due_at": maintenance.next_due_at,
            "transaction_id": transaction.id,
        }

    async def list_maintenance(self, user_id: int, home: Home, *, limit: int = 30) -> builtins.list[HomeMaintenance]:
        return await self._maintenance.list_for_home(user_id, home.id, limit=limit)

    # ------------------------------------------------------------------ #
    # Estado                                                               #
    # ------------------------------------------------------------------ #
    async def get_status(self, user_id: int, home_id: int) -> dict:
        home = await self.get(user_id, home_id)
        now = utc_now()
        week_ago = now - timedelta(days=30)

        recent = await self._maintenance.list_for_home(user_id, home_id, limit=5)
        all_items = await self._maintenance.list_for_home(user_id, home_id, limit=100)
        pending = [m for m in all_items if m.status == "pending"]
        upcoming = [m for m in pending if m.next_due_at is not None and m.next_due_at >= now]
        upcoming.sort(key=lambda m: m.next_due_at or now)

        spend_years = await self._maintenance.list_for_home(user_id, home_id, limit=100)
        spend_total = sum(
            (
                Decimal(str(m.cost))
                for m in spend_years
                if m.cost is not None and m.occurred_at is not None and m.occurred_at >= week_ago
            ),
            Decimal("0.00"),
        )

        return {
            "home": home,
            "recent": recent,
            "pending": pending[:5],
            "upcoming": upcoming[:5],
            "spend_last_30_days": {"total": spend_total, "currency": "EUR"},
        }

    async def get_upcoming(self, user_id: int) -> dict:
        now = utc_now()
        pending = await self._maintenance.list_for_user(user_id, status="pending", limit=200)
        upcoming = [m for m in pending if m.next_due_at is not None]
        upcoming.sort(key=lambda m: m.next_due_at or now)
        return {"upcoming": upcoming[:20]}
