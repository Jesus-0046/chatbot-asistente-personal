"""Servicio de dominio de suscripciones (Fase 3.3).

La suscripción NO es un motor independiente: se apoya SIEMPRE en una
recurrencia (`recurring_transaction_id`) que genera los pagos planificados.
La suscripción aporta la semántica de facturación (nombre, periodo, próxima
cuota) sobre esa recurrencia.

- `billing_period` usa la misma semántica que la frecuencia de la recurrencia
  vinculada (daily | weekly | monthly | yearly).
- `next_billing_date` ES un espejo de `recurring_transactions.next_due_at`
  (se re-sincroniza en create/update/list).
- Cancelar la suscripción desactiva la recurrencia exclusiva, cancela los
  planificados futuros y conserva el histórico (transactions convertidas).
- El `contexto` de una suscripción NO deja de generar pagos por sí solo: los
  generadores perezosos del motor de recurrencias materializan las ocurrencias.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from asistente.domain.categories import normalize_category
from asistente.domain.recurring import RecurringTransactionService, first_occurrence_after
from asistente.persistence.models import Subscription
from asistente.persistence.repositories import SubscriptionRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.recurrence import ALL_FREQS, FREQ_MONTHLY, FREQ_YEARLY
from asistente.shared.timeutil import now_in_timezone

DEFAULT_SUBSCRIPTION_CATEGORY = "Suscripciones"


class SubscriptionService:
    def __init__(
        self, subscription_repo: SubscriptionRepository, recurring_service: RecurringTransactionService
    ) -> None:
        self._subscriptions = subscription_repo
        self._recurring = recurring_service

    async def create(
        self,
        *,
        user_id: int,
        name: str,
        amount: Decimal,
        currency: str = "EUR",
        billing_period: str,
        account_id: int | None = None,
        category: str | None = None,
        next_billing_date: datetime | None = None,
        timezone: str = "Europe/Madrid",
    ) -> Subscription:
        name = (name or "").strip()
        if not name:
            raise ValueError("El nombre de la suscripción no puede estar vacío.")
        if billing_period not in ALL_FREQS:
            raise ValueError(f"Periodo de facturación no válido: {billing_period}. Válidos: {', '.join(ALL_FREQS)}.")
        if next_billing_date is not None and next_billing_date.tzinfo is None:
            raise ValueError("La próxima fecha de facturación debe incluir zona horaria (aware).")

        today = now_in_timezone(timezone).date()
        # Atomicidad (Fase 3.2/3.3.1): la recurrencia y la suscripción se crean
        # en UNA única transacción. Si algo falla, no queda ninguna huérfana.
        async with self._subscriptions.transaction() as session:
            recurring = await self._recurring.create(
                user_id=user_id,
                type="expense",
                title=name,
                amount=amount,
                currency=currency,
                account_id=account_id,
                merchant=name,
                category=(normalize_category(category) if category else DEFAULT_SUBSCRIPTION_CATEGORY),
                frequency=billing_period,
                interval=1,
                day_of_month=(
                    next_billing_date.day
                    if billing_period in (FREQ_MONTHLY, FREQ_YEARLY) and next_billing_date
                    else (today.day if billing_period in (FREQ_MONTHLY, FREQ_YEARLY) else None)
                ),
                month_of_year=(
                    next_billing_date.month
                    if billing_period == FREQ_YEARLY and next_billing_date
                    else (today.month if billing_period == FREQ_YEARLY else None)
                ),
                start_date=(next_billing_date.date() if next_billing_date else None),
                next_due_at=next_billing_date,
                timezone=timezone,
                session=session,
            )
            return await self._subscriptions.create(
                user_id=user_id,
                name=name,
                amount=recurring.amount,
                currency=recurring.currency,
                billing_period=billing_period,
                next_billing_date=recurring.next_due_at,
                account_id=recurring.account_id,
                category=recurring.category,
                recurring_transaction_id=recurring.id,
                active=True,
                session=session,
            )

    async def update(self, user_id: int, subscription_id: int, **fields) -> Subscription:
        async with self._subscriptions.transaction() as session:
            return await self._update_in_tx(user_id, subscription_id, session, fields)

    async def _update_in_tx(self, user_id: int, subscription_id: int, session, fields: dict) -> Subscription:
        sub = await self._subscriptions.find_one(user_id, subscription_id)
        if sub is None:
            raise NotFoundError("La suscripción no existe o no te pertenece.")
        if sub.recurring_transaction_id is None:
            raise ValueError("La suscripción no tiene recurrencia asociada y no se puede actualizar.")
        rec = await self._recurring.get(user_id, sub.recurring_transaction_id)

        rec_fields: dict = {}
        sub_fields: dict = {}
        if "name" in fields:
            name = (fields["name"] or "").strip()
            if not name:
                raise ValueError("El nombre de la suscripción no puede estar vacío.")
            rec_fields["title"] = name
            rec_fields["merchant"] = name
            sub_fields["name"] = name
        if "amount" in fields:
            rec_fields["amount"] = fields["amount"]
            sub_fields["amount"] = fields["amount"]
        if "account_id" in fields:
            rec_fields["account_id"] = fields["account_id"]
            sub_fields["account_id"] = fields["account_id"]
        if "category" in fields:
            category = normalize_category(fields["category"])
            rec_fields["category"] = category
            sub_fields["category"] = category
        period_changed = False
        if "billing_period" in fields:
            period_changed = True
            rec_fields["frequency"] = fields["billing_period"]
            sub_fields["billing_period"] = fields["billing_period"]
        explicit_next = "next_billing_date" in fields
        if explicit_next:
            next_at = fields["next_billing_date"]
            if next_at is not None and next_at.tzinfo is None:
                raise ValueError("La próxima fecha de facturación debe incluir zona horaria (aware).")
            rec_fields["next_due_at"] = next_at
            rec_fields["start_date"] = next_at.date() if next_at is not None else None
            rec_fields["day_of_month"] = (
                next_at.day
                if next_at is not None and rec_fields.get("frequency", rec.frequency) in (FREQ_MONTHLY, FREQ_YEARLY)
                else rec.day_of_month
            )
            rec_fields["month_of_year"] = (
                next_at.month
                if next_at is not None and rec_fields.get("frequency", rec.frequency) == FREQ_YEARLY
                else rec.month_of_year
            )
            sub_fields["next_billing_date"] = next_at

        if rec_fields:
            rec = await self._recurring.update(user_id, rec.id, session=session, **rec_fields)

        if period_changed and not explicit_next:
            # Recalcular la próxima cuota desde hoy con la nueva cadencia.
            next_due = first_occurrence_after(
                now_in_timezone(rec.timezone),
                frequency=rec.frequency,
                interval=rec.interval,
                day_of_month=rec.day_of_month,
                month_of_year=rec.month_of_year,
                start_date=rec.start_date,
                end_date=rec.end_date,
                timezone=rec.timezone,
            )
            rec = await self._recurring.update(user_id, rec.id, session=session, next_due_at=next_due)

        if sub_fields:
            updated = await self._subscriptions.update(user_id, subscription_id, session=session, **sub_fields)
            if updated is not None:
                sub = updated

        if sub.next_billing_date != rec.next_due_at:
            updated = await self._subscriptions.update(
                user_id, subscription_id, session=session, next_billing_date=rec.next_due_at
            )
            if updated is not None:
                sub = updated
        return sub

    async def cancel(self, user_id: int, subscription_id: int) -> Subscription:
        async with self._subscriptions.transaction() as session:
            sub = await self._subscriptions.find_one(user_id, subscription_id)
            if sub is None:
                raise NotFoundError("La suscripción no existe o no te pertenece.")
            if sub.recurring_transaction_id is not None:
                await self._recurring.cancel(user_id, sub.recurring_transaction_id, session=session)
            cancelled = await self._subscriptions.deactivate(user_id, subscription_id, session=session)
            assert cancelled is not None
            return cancelled

    async def list(self, user_id: int, *, active_only: bool | None = None) -> list[Subscription]:
        # Generación perezosa: antes de listar, materializa lo pendiente.
        await self._recurring.generate_upcoming_planned_transactions(user_id)
        subs = await self._subscriptions.list_for_user(user_id, active_only=active_only)
        for index, sub in enumerate(subs):
            if sub.recurring_transaction_id is None:
                continue
            rec = await self._recurring.get(user_id, sub.recurring_transaction_id)
            if rec.next_due_at != sub.next_billing_date or rec.active != sub.active:
                freshed = await self._subscriptions.update(
                    user_id,
                    sub.id,
                    next_billing_date=rec.next_due_at,
                    active=rec.active,
                )
                if freshed is not None:
                    subs[index] = freshed
        return subs
