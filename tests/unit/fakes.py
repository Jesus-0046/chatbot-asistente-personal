"""Fakes de repositorios (en memoria) compartidos por los tests de dominio (Fase 2/2.1/3 y 3.3).

Reflejan el CONTRATO de los repositorios que consumen los servicios de dominio.
Mantienen un estado real (mutable) para que los servicios puedan razonar sobre
él como lo harían sobre una BD.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

from asistente.shared.errors import AlreadyConvertedError, NotFoundError


class _Fake:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


class FakeRuleRepo:
    def __init__(self) -> None:
        self._store: dict[tuple[int, int], _Fake] = {}
        self._next = 1

    async def create(self, *, user_id, freq, interval, days_of_week, start_at, timezone, end_at=None):
        rule = _Fake(
            id=self._next,
            user_id=user_id,
            freq=freq,
            interval=interval,
            days_of_week={"days": list(days_of_week)} if days_of_week else None,
            start_at=start_at,
            timezone=timezone,
            end_at=end_at,
        )
        self._store[(user_id, rule.id)] = rule
        self._next += 1
        return rule

    async def find_one(self, user_id, rule_id):
        return self._store.get((user_id, rule_id))

    async def update(self, user_id, rule_id, **fields):
        rule = self._store.get((user_id, rule_id))
        if rule is None:
            return None
        for k, v in fields.items():
            setattr(rule, k, v)
        return rule

    async def delete(self, user_id, rule_id):
        return self._store.pop((user_id, rule_id), None) is not None


class FakeEventRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, title, start_at, **kwargs):
        base = {
            "description": None,
            "kind": "event",
            "end_at": None,
            "all_day": False,
            "timezone": "Europe/Madrid",
            "location": None,
            "status": "scheduled",
            "priority": "medium",
            "recurrence_id": None,
            "trip_id": None,
            "person": None,
        }
        base.update(kwargs)
        event = _Fake(id=self._next, user_id=user_id, title=title, start_at=start_at, **base)
        self._store.append(event)
        self._next += 1
        return event

    async def find_one(self, user_id, event_id):
        return next((e for e in self._store if e.id == event_id and e.user_id == user_id), None)

    async def list_in_range(self, user_id, end_utc, *, include_cancelled=False, limit=500):
        rows = [e for e in self._store if e.user_id == user_id and e.start_at < end_utc]
        if not include_cancelled:
            rows = [e for e in rows if e.status != "cancelled"]
        return sorted(rows, key=lambda e: e.start_at)[:limit]

    async def list_for_user(self, user_id, *, limit=50):
        rows = [e for e in self._store if e.user_id == user_id and e.status != "cancelled"]
        return sorted(rows, key=lambda e: e.start_at, reverse=True)[:limit]

    async def update(self, user_id, event_id, **fields):
        event = await self.find_one(user_id, event_id)
        if event is None:
            return None
        for k, v in fields.items():
            setattr(event, k, v)
        return event

    async def delete(self, user_id, event_id):
        idx = next((i for i, e in enumerate(self._store) if e.id == event_id and e.user_id == user_id), None)
        if idx is None:
            return False
        self._store.pop(idx)
        return True

    async def list_for_trip(self, user_id, trip_id, *, status="scheduled", limit=500):
        return [
            e
            for e in self._store
            if e.user_id == user_id and getattr(e, "trip_id", None) == trip_id and e.status == status
        ][:limit]


class FakeTaskRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, title, **kwargs):
        base = {
            "description": None,
            "due_at": None,
            "timezone": "Europe/Madrid",
            "status": "pending",
            "priority": "medium",
            "recurrence_id": None,
            "trip_id": None,
            "completed_at": None,
        }
        base.update(kwargs)
        task = _Fake(id=self._next, user_id=user_id, title=title, **base)
        self._store.append(task)
        self._next += 1
        return task

    async def find_one(self, user_id, task_id):
        return next((t for t in self._store if t.id == task_id and t.user_id == user_id), None)

    async def list_for_user(self, user_id, *, status=None, limit=100):
        rows = [t for t in self._store if t.user_id == user_id]
        if status:
            rows = [t for t in rows if t.status == status]
        return rows[:limit]

    async def list_pending(self, user_id, *, limit=100):
        return [t for t in self._store if t.user_id == user_id and t.status == "pending"][:limit]

    async def list_due_in_range(self, user_id, start_utc, end_utc, *, limit=100):
        return [
            t
            for t in self._store
            if t.user_id == user_id
            and t.status == "pending"
            and t.due_at is not None
            and start_utc <= t.due_at < end_utc
        ][:limit]

    async def update(self, user_id, task_id, **fields):
        task = await self.find_one(user_id, task_id)
        if task is None:
            return None
        for k, v in fields.items():
            setattr(task, k, v)
        return task

    async def complete(self, user_id, task_id, completed_at):
        task = await self.find_one(user_id, task_id)
        if task is None or task.status != "pending":
            return None
        task.status = "completed"
        task.completed_at = completed_at
        return task

    async def delete(self, user_id, task_id):
        idx = next((i for i, t in enumerate(self._store) if t.id == task_id and t.user_id == user_id), None)
        if idx is None:
            return False
        self._store.pop(idx)
        return True

    async def list_for_trip(self, user_id, trip_id, *, status="pending", limit=500):
        return [
            t
            for t in self._store
            if t.user_id == user_id and getattr(t, "trip_id", None) == trip_id and t.status == status
        ][:limit]


class FakeReminderRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        message,
        remind_at,
        event_id=None,
        task_id=None,
        trip_id=None,
        timezone="Europe/Madrid",
        offset_minutes=None,
    ):
        reminder = _Fake(
            id=self._next,
            user_id=user_id,
            message=message,
            remind_at=remind_at,
            event_id=event_id,
            task_id=task_id,
            trip_id=trip_id,
            timezone=timezone,
            offset_minutes=offset_minutes,
            status="pending",
            claimed_at=None,
            sent_at=None,
        )
        self._store.append(reminder)
        self._next += 1
        return reminder

    async def find_one(self, user_id, reminder_id):
        return next((r for r in self._store if r.id == reminder_id and r.user_id == user_id), None)

    async def list_for_user(self, user_id, *, status=None, limit=100):
        rows = [r for r in self._store if r.user_id == user_id]
        if status:
            rows = [r for r in rows if r.status == status]
        return rows[:limit]

    async def list_for_event(self, user_id, event_id, *, status=None):
        rows = [r for r in self._store if r.user_id == user_id and r.event_id == event_id]
        if status:
            rows = [r for r in rows if r.status == status]
        return rows

    async def list_for_task(self, user_id, task_id, *, status=None):
        rows = [r for r in self._store if r.user_id == user_id and r.task_id == task_id]
        if status:
            rows = [r for r in rows if r.status == status]
        return rows

    async def claim_due(self, now_utc, limit, *, lease):
        due = [r for r in self._store if r.status == "pending" and r.remind_at <= now_utc][:limit]
        for r in due:
            r.status = "processing"
            r.claimed_at = now_utc
        return due

    async def advance(self, user_id, reminder_id, new_remind_at, *, claimed_at):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None or reminder.status != "processing":
            return None
        reminder.remind_at = new_remind_at
        reminder.status = "pending"
        reminder.claimed_at = None
        return reminder

    async def mark_sent(self, user_id, reminder_id, sent_at, *, claimed_at):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None or reminder.status != "processing":
            return None
        reminder.status = "sent"
        reminder.sent_at = sent_at
        reminder.claimed_at = None
        return reminder

    async def cancel_claimed(self, user_id, reminder_id, *, claimed_at):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None or reminder.status != "processing":
            return None
        reminder.status = "cancelled"
        reminder.claimed_at = None
        return reminder

    async def release(self, user_id, reminder_id, *, claimed_at):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None or reminder.status != "processing":
            return None
        reminder.status = "pending"
        reminder.claimed_at = None
        return reminder

    async def cancel(self, user_id, reminder_id):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None or reminder.status not in ("pending", "processing"):
            return None
        reminder.status = "cancelled"
        reminder.claimed_at = None
        return reminder

    async def resync_task_pending(self, user_id, task_id, new_due_utc, default_offset):
        rows = [r for r in self._store if r.user_id == user_id and r.task_id == task_id and r.status == "pending"]
        for r in rows:
            offset = r.offset_minutes if r.offset_minutes is not None else default_offset
            r.remind_at = new_due_utc - timedelta(minutes=offset)
        return len(rows)

    async def resync_event_pending(self, user_id, event_id, new_start_utc, default_offset):
        rows = [r for r in self._store if r.user_id == user_id and r.event_id == event_id and r.status == "pending"]
        for r in rows:
            offset = r.offset_minutes if r.offset_minutes is not None else default_offset
            r.remind_at = new_start_utc - timedelta(minutes=offset)
        return len(rows)

    async def cancel_task_pending(self, user_id, task_id):
        rows = [r for r in self._store if r.user_id == user_id and r.task_id == task_id and r.status == "pending"]
        for r in rows:
            r.status = "cancelled"
        return len(rows)

    async def cancel_event_pending(self, user_id, event_id):
        rows = [r for r in self._store if r.user_id == user_id and r.event_id == event_id and r.status == "pending"]
        for r in rows:
            r.status = "cancelled"
        return len(rows)

    async def set_trip(self, user_id, reminder_id, trip_id):
        reminder = await self.find_one(user_id, reminder_id)
        if reminder is None:
            return None
        reminder.trip_id = trip_id
        return reminder

    async def delete(self, user_id, reminder_id):
        idx = next((i for i, r in enumerate(self._store) if r.id == reminder_id and r.user_id == user_id), None)
        if idx is None:
            return False
        self._store.pop(idx)
        return True

    async def list_for_trip(self, user_id, trip_id, *, status="pending", limit=500):
        return [
            r
            for r in self._store
            if r.user_id == user_id and getattr(r, "trip_id", None) == trip_id and r.status == status
        ][:limit]


class FakeAccountRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self, *, user_id, name, type, currency="EUR", initial_balance=0, is_default=False, active=True, sort_order=0
    ):
        account = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            type=type,
            currency=currency,
            initial_balance=initial_balance,
            is_default=is_default,
            active=active,
            sort_order=sort_order,
        )
        self._store.append(account)
        self._next += 1
        return account

    async def find_one(self, user_id, account_id):
        return next((a for a in self._store if a.id == account_id and a.user_id == user_id), None)

    async def list_for_user(self, user_id, *, only_active=False):
        rows = [a for a in self._store if a.user_id == user_id]
        if only_active:
            rows = [a for a in rows if a.active]
        return sorted(rows, key=lambda a: (a.sort_order, a.id))

    async def update(self, user_id, account_id, **fields):
        account = await self.find_one(user_id, account_id)
        if account is None:
            return None
        for k, v in fields.items():
            setattr(account, k, v)
        return account

    async def set_default(self, user_id, account_id):
        target = await self.find_one(user_id, account_id)
        if target is None:
            return None
        for a in self._store:
            if a.user_id == user_id:
                a.is_default = False
        target.is_default = True
        return target

    async def clear_default(self, user_id):
        for a in self._store:
            if a.user_id == user_id:
                a.is_default = False


class FakeTransactionRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        type,
        amount,
        occurred_at,
        currency="EUR",
        account_id=None,
        merchant=None,
        category="Otros",
        note=None,
        source="manual",
        status="posted",
        trip_id=None,
        session=None,
    ):
        txn = _Fake(
            id=self._next,
            user_id=user_id,
            type=type,
            amount=amount,
            occurred_at=occurred_at,
            currency=currency,
            account_id=account_id,
            merchant=merchant,
            category=category,
            note=note,
            source=source,
            status=status,
            trip_id=trip_id,
        )
        self._store.append(txn)
        self._next += 1
        return txn

    async def find_one(self, user_id, transaction_id):
        return next((t for t in self._store if t.id == transaction_id and t.user_id == user_id), None)

    async def list_for_trip(self, user_id, trip_id):
        return [t for t in self._store if t.user_id == user_id and t.trip_id == trip_id]

    async def set_trip(self, user_id, transaction_ids, trip_id):
        targets = set(transaction_ids)
        count = 0
        for txn in self._store:
            if txn.user_id == user_id and txn.id in targets:
                txn.trip_id = trip_id
                count += 1
        return count

    async def list_for_user(
        self,
        user_id,
        *,
        start_utc=None,
        end_utc=None,
        account_id=None,
        transaction_type=None,
        category=None,
        merchant=None,
        limit=50,
        offset=0,
    ):
        rows = [t for t in self._store if t.user_id == user_id and t.status == "posted"]
        if start_utc is not None:
            rows = [t for t in rows if t.occurred_at >= start_utc]
        if end_utc is not None:
            rows = [t for t in rows if t.occurred_at < end_utc]
        if account_id is not None:
            rows = [t for t in rows if t.account_id == account_id]
        if transaction_type is not None:
            rows = [t for t in rows if t.type == transaction_type]
        if category is not None:
            rows = [t for t in rows if t.category == category]
        if merchant is not None:
            folded = merchant.lower()
            rows = [t for t in rows if t.merchant and folded in t.merchant.lower()]
        rows = sorted(rows, key=lambda t: (t.occurred_at, t.id), reverse=True)
        return rows[offset : offset + limit]


class FakeTransferRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        from_account_id,
        to_account_id,
        amount,
        occurred_at,
        currency="EUR",
        note=None,
        status="posted",
    ):
        transfer = _Fake(
            id=self._next,
            user_id=user_id,
            from_account_id=from_account_id,
            to_account_id=to_account_id,
            amount=amount,
            occurred_at=occurred_at,
            currency=currency,
            note=note,
            status=status,
        )
        self._store.append(transfer)
        self._next += 1
        return transfer

    async def find_one(self, user_id, transfer_id):
        return next((t for t in self._store if t.id == transfer_id and t.user_id == user_id), None)

    async def list_for_user(
        self,
        user_id,
        *,
        start_utc=None,
        end_utc=None,
        account_id=None,
        from_account_id=None,
        to_account_id=None,
        limit=50,
        offset=0,
    ):
        rows = [t for t in self._store if t.user_id == user_id and t.status == "posted"]
        if start_utc is not None:
            rows = [t for t in rows if t.occurred_at >= start_utc]
        if end_utc is not None:
            rows = [t for t in rows if t.occurred_at < end_utc]
        if account_id is not None:
            rows = [t for t in rows if t.from_account_id == account_id or t.to_account_id == account_id]
        if from_account_id is not None:
            rows = [t for t in rows if t.from_account_id == from_account_id]
        if to_account_id is not None:
            rows = [t for t in rows if t.to_account_id == to_account_id]
        rows = sorted(rows, key=lambda t: (t.occurred_at, t.id), reverse=True)
        return rows[offset : offset + limit]


class FakeBudgetRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, name, amount, currency="EUR", year, month, category=None):
        budget = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            amount=amount,
            currency=currency,
            year=year,
            month=month,
            category=category,
        )
        self._store.append(budget)
        self._next += 1
        return budget

    async def find_one(self, user_id, budget_id):
        return next((b for b in self._store if b.id == budget_id and b.user_id == user_id), None)

    async def find_by_period(self, user_id, year, month, category=None):
        return next(
            (
                b
                for b in self._store
                if b.user_id == user_id and b.year == year and b.month == month and b.category == category
            ),
            None,
        )

    async def list_for_user(self, user_id, *, year=None, month=None, limit=100):
        rows = [b for b in self._store if b.user_id == user_id]
        if year is not None:
            rows = [b for b in rows if b.year == year]
        if month is not None:
            rows = [b for b in rows if b.month == month]
        return sorted(rows, key=lambda b: (b.year, b.month, b.id), reverse=True)[:limit]

    async def update(self, user_id, budget_id, **fields):
        budget = await self.find_one(user_id, budget_id)
        if budget is None:
            return None
        for k, v in fields.items():
            setattr(budget, k, v)
        return budget

    async def delete(self, user_id, budget_id):
        idx = next((i for i, b in enumerate(self._store) if b.id == budget_id and b.user_id == user_id), None)
        if idx is None:
            return False
        self._store.pop(idx)
        return True


class FakeSavingsGoalRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, name, target_amount, currency="EUR", deadline=None, status="active"):
        goal = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            target_amount=target_amount,
            currency=currency,
            deadline=deadline,
            status=status,
        )
        self._store.append(goal)
        self._next += 1
        return goal

    async def find_one(self, user_id, goal_id):
        return next((g for g in self._store if g.id == goal_id and g.user_id == user_id), None)

    async def list_for_user(self, user_id, *, status=None, limit=100):
        rows = [g for g in self._store if g.user_id == user_id]
        if status is not None:
            rows = [g for g in rows if g.status == status]
        return rows[:limit]

    async def update(self, user_id, goal_id, **fields):
        goal = await self.find_one(user_id, goal_id)
        if goal is None:
            return None
        for k, v in fields.items():
            setattr(goal, k, v)
        return goal


class FakeSavingsContributionRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, goal_id, amount, currency="EUR", contributed_at, note=None, transfer_id=None):
        if transfer_id is not None:
            existing = next((c for c in self._store if c.transfer_id == transfer_id), None)
            if existing is not None:
                raise ValueError(
                    "La transferencia ya está vinculada a otra contribución "
                    "(cada transferencia solo puede vincularse una vez)."
                )
        contrib = _Fake(
            id=self._next,
            user_id=user_id,
            goal_id=goal_id,
            amount=amount,
            currency=currency,
            contributed_at=contributed_at,
            note=note,
            transfer_id=transfer_id,
        )
        self._store.append(contrib)
        self._next += 1
        return contrib

    async def find_one(self, user_id, contribution_id):
        return next(
            (c for c in self._store if c.id == contribution_id and c.user_id == user_id),
            None,
        )

    async def find_by_transfer(self, user_id, transfer_id):
        return next(
            (c for c in self._store if c.user_id == user_id and c.transfer_id == transfer_id),
            None,
        )

    async def list_for_user(self, user_id, *, limit=100):
        rows = [c for c in self._store if c.user_id == user_id]
        return sorted(rows, key=lambda c: c.contributed_at, reverse=True)[:limit]

    async def list_for_goal(self, user_id, goal_id, *, limit=100):
        rows = [c for c in self._store if c.user_id == user_id and c.goal_id == goal_id]
        return sorted(rows, key=lambda c: c.contributed_at, reverse=True)[:limit]

    async def sum_for_goal(self, user_id, goal_id):
        return sum(
            (c.amount for c in self._store if c.user_id == user_id and c.goal_id == goal_id),
            Decimal("0.00"),
        )


class FakeRecurringTransactionRepo:
    def __init__(self, *, recurring_store: list, planned_store: list) -> None:
        self._store = recurring_store
        self._planned_store = planned_store
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        type,
        title,
        amount,
        currency="EUR",
        account_id=None,
        merchant=None,
        category="Otros",
        frequency,
        interval=1,
        day_of_month=None,
        month_of_year=None,
        start_date=None,
        next_due_at=None,
        end_date=None,
        timezone="Europe/Madrid",
        active=True,
        paused=False,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            type=type,
            title=title,
            amount=amount,
            currency=currency,
            account_id=account_id,
            merchant=merchant,
            category=category,
            frequency=frequency,
            interval=interval,
            day_of_month=day_of_month,
            month_of_year=month_of_year,
            start_date=start_date,
            next_due_at=next_due_at,
            end_date=end_date,
            timezone=timezone,
            active=active,
            paused=paused,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, recurring_id):
        return next((r for r in self._store if r.id == recurring_id and r.user_id == user_id), None)

    async def list_for_user(self, user_id, *, active_only=False, include_paused=True, limit=100):
        rows = [r for r in self._store if r.user_id == user_id]
        if active_only:
            rows = [r for r in rows if r.active]
        if not include_paused:
            rows = [r for r in rows if not r.paused]
        return sorted(rows, key=lambda r: r.id)[:limit]

    async def list_due(self, user_id, now_utc, *, limit=500):
        rows = [
            r
            for r in self._store
            if r.user_id == user_id
            and r.active
            and not r.paused
            and r.next_due_at is not None
            and r.next_due_at <= now_utc
        ]
        return sorted(rows, key=lambda r: r.next_due_at)[:limit]

    async def update(self, user_id, recurring_id, *, session=None, **fields):
        row = await self.find_one(user_id, recurring_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    async def create_occurrence_and_advance(self, recurring, *, planned_at_utc, next_due_utc):
        existing = next(
            (
                p
                for p in self._planned_store
                if p.user_id == recurring.user_id
                and p.recurring_transaction_id == recurring.id
                and p.planned_at == planned_at_utc
            ),
            None,
        )
        if existing is not None:
            row = next(r for r in self._store if r.id == recurring.id)
            row.next_due_at = next_due_utc
            return None
        planned = _Fake(
            id=self._next,
            user_id=recurring.user_id,
            type=recurring.type,
            title=recurring.title,
            amount=recurring.amount,
            currency=recurring.currency,
            account_id=recurring.account_id,
            merchant=recurring.merchant,
            category=recurring.category,
            planned_at=planned_at_utc,
            recurring_transaction_id=recurring.id,
            status="planned",
            converted_transaction_id=None,
            note=None,
            created_at=None,
            updated_at=None,
        )
        self._planned_store.append(planned)
        self._next += 1
        row = next(r for r in self._store if r.id == recurring.id)
        row.next_due_at = next_due_utc
        return planned

    async def deactivate(self, user_id, recurring_id):
        row = await self.find_one(user_id, recurring_id)
        if row is None:
            return False
        row.active = False
        row.paused = False
        return True


class FakePlannedTransactionRepo:
    def __init__(self, *, planned_store: list, tx_store: list) -> None:
        self._store = planned_store
        self._tx_store = tx_store
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        type,
        title=None,
        amount,
        currency="EUR",
        account_id=None,
        merchant=None,
        category="Otros",
        planned_at,
        note=None,
        recurring_transaction_id=None,
        status="planned",
        converted_transaction_id=None,
    ):
        if recurring_transaction_id is not None:
            existing = next(
                (
                    p
                    for p in self._store
                    if p.recurring_transaction_id == recurring_transaction_id and p.planned_at == planned_at
                ),
                None,
            )
            if existing is not None:
                raise ValueError("Ya existe una ocurrencia planificada para esa fecha.")
        row = _Fake(
            id=self._next,
            user_id=user_id,
            type=type,
            title=title,
            amount=amount,
            currency=currency,
            account_id=account_id,
            merchant=merchant,
            category=category,
            planned_at=planned_at,
            note=note,
            recurring_transaction_id=recurring_transaction_id,
            status=status,
            converted_transaction_id=converted_transaction_id,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, planned_id):
        return next((p for p in self._store if p.id == planned_id and p.user_id == user_id), None)

    async def list_for_user(
        self, user_id, *, status=None, transaction_type=None, start_utc=None, end_utc=None, limit=100
    ):
        rows = [p for p in self._store if p.user_id == user_id]
        if status is not None:
            rows = [p for p in rows if p.status == status]
        if transaction_type is not None:
            rows = [p for p in rows if p.type == transaction_type]
        if start_utc is not None:
            rows = [p for p in rows if p.planned_at >= start_utc]
        if end_utc is not None:
            rows = [p for p in rows if p.planned_at <= end_utc]
        return sorted(rows, key=lambda p: (p.planned_at, p.id))[:limit]

    async def cancel(self, user_id, planned_id):
        row = await self.find_one(user_id, planned_id)
        if row is None:
            return None
        if row.status != "planned":
            raise ValueError(
                "El pago planificado ya no está pendiente (estado: "
                f"{row.status}); solo se pueden cancelar pagos planificados."
            )
        row.status = "cancelled"
        return row

    async def cancel_for_recurring(self, user_id, recurring_id, *, session=None):
        count = 0
        for p in self._store:
            if p.user_id == user_id and p.recurring_transaction_id == recurring_id and p.status == "planned":
                p.status = "cancelled"
                count += 1
        return count

    async def convert(self, planned, *, occurred_at, merchant=None, note=None, source="manual"):
        row = next((p for p in self._store if p.id == planned.id and p.user_id == planned.user_id), None)
        if row is None:
            raise NotFoundError("Pago planificado no encontrado.")
        if row.status != "planned":
            if row.status == "converted":
                raise AlreadyConvertedError("El pago planificado ya fue convertido en transacción.")
            raise ValueError("El pago planificado ya no está pendiente; no se puede convertir.")
        txn = _Fake(
            id=len(self._tx_store) + 1,
            user_id=row.user_id,
            type=row.type,
            amount=row.amount,
            currency=row.currency,
            account_id=row.account_id,
            merchant=merchant if merchant is not None else row.merchant,
            category=row.category,
            occurred_at=occurred_at,
            note=note if note is not None else row.note,
            source=source,
            status="posted",
        )
        self._tx_store.append(txn)
        row.status = "converted"
        row.converted_transaction_id = txn.id
        return row, txn


class FakeSubscriptionRepo:
    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    @asynccontextmanager
    async def transaction(self):
        """Contrato de Repository.transaction: contexto con sesión compartida.

        Los fakes son "transaccionales por operación" (cada mutación es un
        paso de lista); la sesión compartida se ignora. Proporciona la misma
        forma para que los servicios puedan ejecutar flujos compuestos.
        """
        yield None

    async def create(
        self,
        *,
        user_id,
        name,
        amount,
        currency="EUR",
        billing_period,
        next_billing_date=None,
        account_id=None,
        category="Suscripciones",
        recurring_transaction_id=None,
        active=True,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            amount=amount,
            currency=currency,
            billing_period=billing_period,
            next_billing_date=next_billing_date,
            account_id=account_id,
            category=category,
            recurring_transaction_id=recurring_transaction_id,
            active=active,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, subscription_id):
        return next((s for s in self._store if s.id == subscription_id and s.user_id == user_id), None)

    async def list_for_user(self, user_id, *, active_only=None, limit=100):
        rows = [s for s in self._store if s.user_id == user_id]
        if active_only is not None:
            rows = [s for s in rows if s.active == active_only]
        return sorted(rows, key=lambda s: s.id)[:limit]

    async def update(self, user_id, subscription_id, *, session=None, **fields):
        row = await self.find_one(user_id, subscription_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    async def deactivate(self, user_id, subscription_id, *, session=None):
        row = await self.find_one(user_id, subscription_id)
        if row is None:
            return None
        row.active = False
        return row

    async def deactivate_for_recurring(self, user_id, recurring_id, *, session=None):
        count = 0
        for s in self._store:
            if s.user_id == user_id and s.recurring_transaction_id == recurring_id and s.active:
                s.active = False
                count += 1
        return count


class FakePersonRepo:
    """Contrato de PersonRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, name, normalized_name, active=True):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            normalized_name=normalized_name,
            active=active,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, person_id):
        return next(
            (p for p in self._store if p.id == person_id and p.user_id == user_id),
            None,
        )

    async def find_by_normalized_name(self, user_id, normalized_name):
        return [p for p in self._store if p.user_id == user_id and p.normalized_name == normalized_name and p.active]

    async def list_for_user(self, user_id, *, only_active=True, limit=100):
        rows = [p for p in self._store if p.user_id == user_id]
        if only_active:
            rows = [p for p in rows if p.active]
        return sorted(rows, key=lambda p: p.name)[:limit]

    async def update(self, user_id, person_id, **fields):
        row = await self.find_one(user_id, person_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row


class FakeDebtRepo:
    """Contrato de DebtRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        person_id,
        direction,
        amount,
        remaining_amount,
        currency="EUR",
        reason=None,
        due_at=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            person_id=person_id,
            direction=direction,
            amount=amount,
            remaining_amount=remaining_amount,
            currency=currency,
            reason=reason,
            due_at=due_at,
            status="open",
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, debt_id):
        return next(
            (d for d in self._store if d.id == debt_id and d.user_id == user_id),
            None,
        )

    async def list_for_user(
        self,
        user_id,
        *,
        status=None,
        person_id=None,
        direction=None,
        limit=200,
    ):
        rows = [d for d in self._store if d.user_id == user_id]
        if status is not None:
            rows = [d for d in rows if d.status == status]
        if person_id is not None:
            rows = [d for d in rows if d.person_id == person_id]
        if direction is not None:
            rows = [d for d in rows if d.direction == direction]
        return sorted(rows, key=lambda d: d.id, reverse=True)[:limit]

    async def settle(self, user_id, debt_id):
        row = await self.find_one(user_id, debt_id)
        if row is None or row.status != "open":
            return row
        row.status = "settled"
        row.remaining_amount = Decimal("0.00")
        return row


class FakeSharedExpenseRepo:
    """Contrato de SharedExpenseRepository (en memoria)."""

    def __init__(self, share_store: dict | None = None, payment_store: dict | None = None) -> None:
        self._store: list[_Fake] = []
        self._next = 1
        self.share_store = share_store if share_store is not None else {}
        self.payment_store = payment_store if payment_store is not None else {}

    @asynccontextmanager
    async def transaction(self):
        yield None

    async def create(
        self,
        *,
        user_id,
        title,
        total_amount,
        currency="EUR",
        paid_by_person_id=None,
        paid_by_user=False,
        occurred_at,
        note=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            title=title,
            total_amount=total_amount,
            currency=currency,
            paid_by_person_id=paid_by_person_id,
            paid_by_user=paid_by_user,
            occurred_at=occurred_at,
            note=note,
            status="active",
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, expense_id):
        return next(
            (e for e in self._store if e.id == expense_id and e.user_id == user_id),
            None,
        )

    async def list_for_user(
        self,
        user_id,
        *,
        status=None,
        occurred_from=None,
        occurred_until=None,
        person_id=None,
        limit=200,
    ):
        rows = [e for e in self._store if e.user_id == user_id]
        if status is not None:
            rows = [e for e in rows if e.status == status]
        if occurred_from is not None:
            rows = [e for e in rows if e.occurred_at >= occurred_from]
        if occurred_until is not None:
            rows = [e for e in rows if e.occurred_at < occurred_until]
        if person_id is not None:
            rows = [
                e
                for e in rows
                if e.paid_by_person_id == person_id
                or any(s.person_id == person_id for s in self._shares(e.id))
                or any(p.person_id == person_id for p in self._payments(e.id))
            ]
        return sorted(rows, key=lambda e: e.occurred_at, reverse=True)[:limit]

    def _shares(self, expense_id):
        return self.share_store.get(expense_id, [])

    def _payments(self, expense_id):
        return self.payment_store.get(expense_id, [])

    async def settle_cancel(self, user_id, expense_id):
        row = await self.find_one(user_id, expense_id)
        if row is None or row.status != "active":
            return row
        row.status = "cancelled"
        return row


class FakeSharedExpenseShareRepo:
    """Contrato de SharedExpenseShareRepository (en memoria)."""

    def __init__(self, store: dict) -> None:
        self._store = store
        self._next = 1

    async def create(
        self,
        *,
        shared_expense_id,
        person_id=None,
        is_user=False,
        share_amount,
        session=None,
    ):
        rows = self._store.setdefault(shared_expense_id, [])
        if is_user:
            if any(s.is_user for s in rows):
                raise ValueError("El usuario solo puede aparecer UNA vez como participante.")
        else:
            if any(s.person_id == person_id for s in rows):
                raise ValueError("La persona solo puede aparecer UNA vez como participante.")
        row = _Fake(
            id=self._next,
            shared_expense_id=shared_expense_id,
            person_id=person_id,
            is_user=is_user,
            share_amount=share_amount,
            created_at=None,
        )
        rows.append(row)
        self._next += 1
        return row

    async def list_for_expense(self, shared_expense_id, *, session=None):
        return sorted(
            self._store.get(shared_expense_id, []),
            key=lambda s: s.id,
        )


class FakeSharedExpensePaymentRepo:
    """Contrato de SharedExpensePaymentRepository (en memoria)."""

    def __init__(self, store: dict) -> None:
        self._store = store
        self._next = 1

    async def create(
        self,
        *,
        shared_expense_id,
        person_id=None,
        is_user=False,
        amount,
        session=None,
    ):
        rows = self._store.setdefault(shared_expense_id, [])
        if is_user:
            if any(p.is_user for p in rows):
                raise ValueError("El usuario solo puede pagar UNA vez por gasto.")
        else:
            if any(p.person_id == person_id for p in rows):
                raise ValueError("La persona solo puede pagar UNA vez por gasto.")
        row = _Fake(
            id=self._next,
            shared_expense_id=shared_expense_id,
            person_id=person_id,
            is_user=is_user,
            amount=amount,
            created_at=None,
        )
        rows.append(row)
        self._next += 1
        return row

    async def list_for_expense(self, shared_expense_id, *, session=None):
        return sorted(
            self._store.get(shared_expense_id, []),
            key=lambda p: p.id,
        )


# --------------------------------------------------------------------------- #
# Fase 4 — vehículos, casa y hábitos                                          #
# --------------------------------------------------------------------------- #
class FakeVehicleRepo:
    """Contrato de VehicleRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    @asynccontextmanager
    async def transaction(self):
        yield None

    async def create(
        self,
        *,
        user_id,
        name,
        normalized_name,
        brand=None,
        model=None,
        plate=None,
        year=None,
        color=None,
        fuel_type=None,
        current_odometer=None,
        notes=None,
        active=True,
        session=None,
    ):
        row = _Fake(
            id=self._next,
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
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, vehicle_id):
        return next(
            (v for v in self._store if v.id == vehicle_id and v.user_id == user_id),
            None,
        )

    async def find_by_normalized_name(self, user_id, normalized_name):
        return [v for v in self._store if v.user_id == user_id and v.normalized_name == normalized_name and v.active]

    async def list_for_user(self, user_id, *, only_active=True, limit=100):
        rows = [v for v in self._store if v.user_id == user_id]
        if only_active:
            rows = [v for v in rows if v.active]
        return sorted(rows, key=lambda v: v.name)[:limit]

    async def update(self, user_id, vehicle_id, *, session=None, **fields):
        row = await self.find_one(user_id, vehicle_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row

    async def advance_odometer(self, user_id, vehicle_id, odometer, *, session=None):
        row = await self.find_one(user_id, vehicle_id)
        if row is None:
            return False
        if row.current_odometer is None or row.current_odometer < odometer:
            row.current_odometer = odometer
            return True
        return False


class FakeFuelEntryRepo:
    """Contrato de FuelEntryRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        vehicle_id,
        occurred_at,
        amount,
        currency="EUR",
        liters=None,
        odometer=None,
        fuel_type=None,
        full_tank=None,
        note=None,
        transaction_id=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
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
            created_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, entry_id):
        return next(
            (f for f in self._store if f.id == entry_id and f.user_id == user_id),
            None,
        )

    async def list_for_vehicle(self, user_id, vehicle_id, *, limit=20):
        rows = [f for f in self._store if f.user_id == user_id and f.vehicle_id == vehicle_id]
        return sorted(rows, key=lambda f: (f.occurred_at, f.id), reverse=True)[:limit]

    async def list_for_user(self, user_id, *, start_utc=None, end_utc=None, limit=500):
        rows = [f for f in self._store if f.user_id == user_id]
        if start_utc is not None:
            rows = [f for f in rows if f.occurred_at >= start_utc]
        if end_utc is not None:
            rows = [f for f in rows if f.occurred_at < end_utc]
        return sorted(rows, key=lambda f: (f.occurred_at, f.id), reverse=True)[:limit]


class FakeVehicleMaintenanceRepo:
    """Contrato de VehicleMaintenanceRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        vehicle_id,
        maintenance_type="revision",
        description=None,
        occurred_at,
        odometer=None,
        cost=None,
        currency="EUR",
        next_due_at=None,
        next_due_odometer=None,
        note=None,
        transaction_id=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
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
            created_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, maintenance_id):
        return next(
            (m for m in self._store if m.id == maintenance_id and m.user_id == user_id),
            None,
        )

    async def list_for_vehicle(self, user_id, vehicle_id, *, limit=20):
        rows = [m for m in self._store if m.user_id == user_id and m.vehicle_id == vehicle_id]
        return sorted(rows, key=lambda m: (m.occurred_at, m.id), reverse=True)[:limit]

    async def list_for_user(self, user_id, *, start_utc=None, end_utc=None, limit=500):
        rows = [m for m in self._store if m.user_id == user_id]
        if start_utc is not None:
            rows = [m for m in rows if m.occurred_at >= start_utc]
        if end_utc is not None:
            rows = [m for m in rows if m.occurred_at < end_utc]
        return sorted(rows, key=lambda m: (m.occurred_at, m.id), reverse=True)[:limit]


class FakeVehicleInsuranceRepo:
    """Contrato de VehicleInsuranceRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        vehicle_id,
        company=None,
        policy_number=None,
        insurance_type=None,
        coverage=None,
        start_date=None,
        end_date=None,
        amount=None,
        currency="EUR",
        billing_period=None,
        active=True,
        notes=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
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
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, insurance_id):
        return next(
            (i for i in self._store if i.id == insurance_id and i.user_id == user_id),
            None,
        )

    async def list_for_vehicle(self, user_id, vehicle_id, *, only_active=None, limit=20):
        rows = [i for i in self._store if i.user_id == user_id and i.vehicle_id == vehicle_id]
        if only_active is not None:
            rows = [i for i in rows if i.active == only_active]
        return sorted(rows, key=lambda i: (i.end_date is not None, i.end_date or i.id), reverse=False)[:limit]

    async def update(self, user_id, insurance_id, *, session=None, **fields):
        row = await self.find_one(user_id, insurance_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row


class FakeHomeRepo:
    """Contrato de HomeRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    @asynccontextmanager
    async def transaction(self):
        yield None

    async def create(
        self, *, user_id, name, normalized_name, home_type=None, address=None, notes=None, active=True, session=None
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            normalized_name=normalized_name,
            home_type=home_type,
            address=address,
            notes=notes,
            active=active,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, home_id):
        return next(
            (h for h in self._store if h.id == home_id and h.user_id == user_id),
            None,
        )

    async def find_by_normalized_name(self, user_id, normalized_name):
        return [h for h in self._store if h.user_id == user_id and h.normalized_name == normalized_name and h.active]

    async def list_for_user(self, user_id, *, only_active=True, limit=100):
        rows = [h for h in self._store if h.user_id == user_id]
        if only_active:
            rows = [h for h in rows if h.active]
        return sorted(rows, key=lambda h: h.name)[:limit]

    async def update(self, user_id, home_id, *, session=None, **fields):
        row = await self.find_one(user_id, home_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row


class FakeHomeMaintenanceRepo:
    """Contrato de HomeMaintenanceRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        home_id,
        title,
        maintenance_type="revision",
        occurred_at=None,
        cost=None,
        currency="EUR",
        status="pending",
        next_due_at=None,
        note=None,
        transaction_id=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            home_id=home_id,
            title=title,
            maintenance_type=maintenance_type,
            occurred_at=occurred_at,
            cost=cost,
            currency=currency,
            status=status,
            next_due_at=next_due_at,
            note=note,
            transaction_id=transaction_id,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, maintenance_id):
        return next(
            (m for m in self._store if m.id == maintenance_id and m.user_id == user_id),
            None,
        )

    async def list_for_home(self, user_id, home_id, *, limit=30):
        rows = [m for m in self._store if m.user_id == user_id and m.home_id == home_id]
        return sorted(
            rows,
            key=lambda m: (m.occurred_at is not None, m.occurred_at or m.created_at or m.id),
            reverse=True,
        )[:limit]

    async def list_for_user(self, user_id, *, status=None, limit=100):
        rows = [m for m in self._store if m.user_id == user_id]
        if status is not None:
            rows = [m for m in rows if m.status == status]
        return sorted(
            rows, key=lambda m: (m.next_due_at is not None, m.next_due_at or m.created_at or m.id), reverse=True
        )[:limit]

    async def update(self, user_id, maintenance_id, *, session=None, **fields):
        row = await self.find_one(user_id, maintenance_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row


class FakeHabitRepo:
    """Contrato de HabitRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(
        self,
        *,
        user_id,
        name,
        normalized_name,
        description=None,
        unit=None,
        target_per_period=None,
        period="week",
        active=True,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            normalized_name=normalized_name,
            description=description,
            unit=unit,
            target_per_period=target_per_period,
            period=period,
            active=active,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, habit_id):
        return next(
            (h for h in self._store if h.id == habit_id and h.user_id == user_id),
            None,
        )

    async def find_by_normalized_name(self, user_id, normalized_name):
        return [h for h in self._store if h.user_id == user_id and h.normalized_name == normalized_name and h.active]

    async def list_for_user(self, user_id, *, only_active=True, limit=100):
        rows = [h for h in self._store if h.user_id == user_id]
        if only_active:
            rows = [h for h in rows if h.active]
        return sorted(rows, key=lambda h: h.name)[:limit]

    async def update(self, user_id, habit_id, *, session=None, **fields):
        row = await self.find_one(user_id, habit_id)
        if row is None:
            return None
        for k, v in fields.items():
            setattr(row, k, v)
        return row


class FakeHabitLogRepo:
    """Contrato de HabitLogRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, habit_id, value=None, logged_at, note=None, session=None):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            habit_id=habit_id,
            value=value,
            logged_at=logged_at,
            note=note,
            created_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, log_id):
        return next(
            (row for row in self._store if row.id == log_id and row.user_id == user_id),
            None,
        )

    def _in_window(self, user_id, habit_id, start_utc, end_utc):
        return [
            row
            for row in self._store
            if row.user_id == user_id
            and row.habit_id == habit_id
            and row.logged_at >= start_utc
            and row.logged_at < end_utc
        ]

    async def count_in_window(self, user_id, habit_id, *, start_utc, end_utc):
        return len(self._in_window(user_id, habit_id, start_utc, end_utc))

    async def sum_in_window(self, user_id, habit_id, *, start_utc, end_utc):
        from decimal import Decimal as _Decimal

        return sum(
            (row.value for row in self._in_window(user_id, habit_id, start_utc, end_utc) if row.value is not None),
            _Decimal("0.00"),
        )

    async def list_for_habit(self, user_id, habit_id, *, limit=20):
        rows = [row for row in self._store if row.user_id == user_id and row.habit_id == habit_id]
        return sorted(rows, key=lambda row: (row.logged_at, row.id), reverse=True)[:limit]


# --------------------------------------------------------------------------- #
# Fase 6 — viajes, resúmenes y proactividad                                    #
# --------------------------------------------------------------------------- #
class FakeTripRepo:
    """Contrato de TripRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    @asynccontextmanager
    async def transaction(self):
        yield None

    async def create(
        self,
        *,
        user_id,
        name,
        normalized_name,
        destination=None,
        start_at=None,
        end_at=None,
        status="planned",
        trip_type="other",
        timezone="Europe/Madrid",
        budget=None,
        notes=None,
        session=None,
    ):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            name=name,
            normalized_name=normalized_name,
            destination=destination,
            start_at=start_at,
            end_at=end_at,
            status=status,
            trip_type=trip_type,
            timezone=timezone,
            budget=budget,
            notes=notes,
            created_at=None,
            updated_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, trip_id):
        return next((t for t in self._store if t.id == trip_id and t.user_id == user_id), None)

    async def find_by_normalized_name(self, user_id, normalized_name):
        return [t for t in self._store if t.user_id == user_id and t.normalized_name == normalized_name]

    async def list_for_user(self, user_id, *, status=None, limit=100):
        rows = [t for t in self._store if t.user_id == user_id]
        if status:
            rows = [t for t in rows if t.status == status]
        return rows[:limit]

    async def list_in_window(self, user_id, start_utc, end_utc, *, statuses=("planned", "active"), limit=100):
        rows = [
            t
            for t in self._store
            if t.user_id == user_id
            and t.status in statuses
            and t.start_at is not None
            and start_utc <= t.start_at < end_utc
        ]
        return sorted(rows, key=lambda t: t.start_at)[:limit]

    async def update(self, user_id, trip_id, *, session=None, **fields):
        row = await self.find_one(user_id, trip_id)
        if row is None:
            return None
        allowed = {
            "name",
            "normalized_name",
            "destination",
            "start_at",
            "end_at",
            "status",
            "trip_type",
            "timezone",
            "budget",
            "notes",
        }
        for k, v in fields.items():
            if k in allowed:
                setattr(row, k, v)
        return row


class FakeTripChecklistItemRepo:
    """Contrato de TripChecklistItemRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1

    async def create(self, *, user_id, trip_id, text, is_done=False, session=None):
        row = _Fake(
            id=self._next,
            user_id=user_id,
            trip_id=trip_id,
            text=text,
            is_done=is_done,
            created_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def find_one(self, user_id, item_id):
        return next((i for i in self._store if i.id == item_id and i.user_id == user_id), None)

    async def list_for_trip(self, user_id, trip_id, *, limit=200):
        return [i for i in self._store if i.user_id == user_id and i.trip_id == trip_id][:limit]

    async def set_done(self, user_id, item_id, is_done):
        row = await self.find_one(user_id, item_id)
        if row is None:
            return None
        row.is_done = is_done
        return row

    async def count_open(self, user_id, trip_id):
        return len([i for i in self._store if i.user_id == user_id and i.trip_id == trip_id and not i.is_done])

    async def delete(self, user_id, item_id):
        idx = next((i for i, it in enumerate(self._store) if it.id == item_id and it.user_id == user_id), None)
        if idx is None:
            return False
        self._store.pop(idx)
        return True


class FakeUserProactivitySettingsRepo:
    """Contrato de UserProactivitySettingsRepository (en memoria)."""

    def __init__(self) -> None:
        self._store: dict[int, _Fake] = {}
        self._next = 1

    async def find_one(self, user_id):
        return self._store.get(user_id)

    async def get_or_create(self, user_id, *, timezone="Europe/Madrid"):
        existing = self._store.get(user_id)
        if existing is not None:
            return existing
        row = _Fake(
            id=self._next,
            user_id=user_id,
            enabled=False,
            quiet_hours_start="22:00",
            quiet_hours_end="08:00",
            daily_limit=3,
            importance_threshold=3,
            timezone=timezone,
        )
        self._store[user_id] = row
        self._next += 1
        return row

    async def upsert(
        self,
        user_id,
        *,
        enabled=None,
        quiet_hours_start=None,
        quiet_hours_end=None,
        daily_limit=None,
        importance_threshold=None,
        timezone=None,
    ):
        row = await self.get_or_create(user_id, timezone=timezone or "Europe/Madrid")
        if enabled is not None:
            row.enabled = enabled
        if quiet_hours_start is not None:
            row.quiet_hours_start = quiet_hours_start
        if quiet_hours_end is not None:
            row.quiet_hours_end = quiet_hours_end
        if daily_limit is not None:
            row.daily_limit = daily_limit
        if importance_threshold is not None:
            row.importance_threshold = importance_threshold
        if timezone is not None:
            row.timezone = timezone
        return row

    async def list_enabled(self, *, limit=500):
        return [row for row in self._store.values() if row.enabled][:limit]


class FakeProactiveNotificationRepo:
    """Contrato de ProactiveNotificationRepository (en memoria).

    Simula el claim/lease de Fase 6.2 y la reserva de presupuesto EN EL ENVÍO
    de Fase 6.2.1: `claim_pending` NO toca el contador (solo lo lee como hint
    best-effort para el cap de fresh); `reserve_send` consume una unidad justo
    antes de cada envío, `release_send` la devuelve si el envío falla y
    `unclaim` libera a `pending` una fila reclamada sin presupuesto.
    `fail_unique_create` emula la carrera de dedup levantando IntegrityError
    sobre el índice único.
    """

    def __init__(self) -> None:
        self._store: list[_Fake] = []
        self._next = 1
        self._counters: dict[tuple[int, object], int] = {}
        self.fail_unique_create = False
        self.find_any_miss = False

    async def find_existing(self, user_id, kind, dedup_key):
        return next(
            (
                n
                for n in self._store
                if n.user_id == user_id and n.kind == kind and n.dedup_key == dedup_key and n.status != "skipped"
            ),
            None,
        )

    async def find_any(self, user_id, kind, dedup_key):
        if self.find_any_miss:
            # Simula la carrera de dedup: el SELECT del worker perdedor se
            # ejecutó antes del INSERT/commit del ganador y "vio" nada.
            self.find_any_miss = False
            return None
        return next(
            (n for n in self._store if n.user_id == user_id and n.kind == kind and n.dedup_key == dedup_key),
            None,
        )

    async def create(self, *, user_id, kind, dedup_key, title, body, importance=2, status="pending"):
        if self.fail_unique_create:
            dup = next(
                (
                    n
                    for n in self._store
                    if n.user_id == user_id and n.kind == kind and n.dedup_key == dedup_key and n.status != "skipped"
                ),
                None,
            )
            if dup is not None:
                from sqlalchemy.exc import IntegrityError

                raise IntegrityError(
                    "INSERT",
                    {},
                    Exception(
                        "UNIQUE constraint failed: proactive_notifications.user_id, "
                        "proactive_notifications.kind, proactive_notifications.dedup_key"
                    ),
                )
        row = _Fake(
            id=self._next,
            user_id=user_id,
            kind=kind,
            dedup_key=dedup_key,
            title=title,
            body=body,
            importance=importance,
            status=status,
            sent_at=None,
            claimed_at=None,
            lease_until=None,
            worker_id=None,
            created_at=None,
        )
        self._store.append(row)
        self._next += 1
        return row

    async def count_delivered_since(self, user_id, since_utc):
        return len(
            [
                n
                for n in self._store
                if n.user_id == user_id and n.status == "sent" and n.sent_at is not None and n.sent_at >= since_utc
            ]
        )

    async def mark_sent(self, user_id, notification_id, *, worker_id, sent_at):
        row = next((n for n in self._store if n.id == notification_id and n.user_id == user_id), None)
        if row is None:
            return None
        if row.status != "processing" or row.worker_id != worker_id:
            return None
        if row.lease_until is None or row.lease_until <= sent_at:
            return None
        row.status = "sent"
        row.sent_at = sent_at
        row.claimed_at = None
        row.lease_until = None
        row.worker_id = None
        return row

    async def mark_skipped(self, user_id, notification_id, *, worker_id, now_utc):
        row = next((n for n in self._store if n.id == notification_id and n.user_id == user_id), None)
        if row is None:
            return None
        if row.status != "processing" or row.worker_id != worker_id:
            return None
        if row.lease_until is None or row.lease_until <= now_utc:
            return None
        row.status = "skipped"
        row.sent_at = None
        row.claimed_at = None
        row.lease_until = None
        row.worker_id = None
        return row

    async def mark_pending(self, notification_id, *, title, body, importance):
        row = next((n for n in self._store if n.id == notification_id), None)
        if row is None:
            return None
        row.status = "pending"
        row.sent_at = None
        row.claimed_at = None
        row.lease_until = None
        row.worker_id = None
        row.title = title
        row.body = body
        row.importance = importance
        return row

    async def claim_pending(self, user_id, *, worker_id, now_utc, day_local, daily_limit, limit=10, lease_seconds=600):
        reclaims = [
            n
            for n in self._store
            if n.user_id == user_id
            and n.status == "processing"
            and n.lease_until is not None
            and n.lease_until < now_utc
        ][:limit]
        # Fase 6.2.1: el contador solo se LEE como hint para el cap de fresh; el
        # claim no consume presupuesto (eso ocurre en reserve_send).
        key = (user_id, day_local)
        counted = self._counters.get(key, 0)
        remaining = max(0, daily_limit - counted)
        fresh_room = max(0, limit - len(reclaims))
        fresh = [n for n in self._store if n.user_id == user_id and n.status == "pending"][: min(fresh_room, remaining)]
        claimed = reclaims + fresh
        lease = now_utc + timedelta(seconds=max(1, int(lease_seconds)))
        for n in claimed:
            n.status = "processing"
            n.worker_id = worker_id
            n.claimed_at = now_utc
            n.lease_until = lease
        return claimed

    async def reserve_send(self, user_id, *, day_local, daily_limit):
        if daily_limit <= 0:
            return False
        key = (user_id, day_local)
        counted = self._counters.get(key, 0)
        if counted >= daily_limit:
            return False
        self._counters[key] = counted + 1
        return True

    async def release_send(self, user_id, *, day_local):
        key = (user_id, day_local)
        counted = self._counters.get(key, 0)
        if counted > 0:
            self._counters[key] = counted - 1

    async def unclaim(self, user_id, notification_id, *, worker_id, now_utc):
        row = next((n for n in self._store if n.id == notification_id and n.user_id == user_id), None)
        if row is None:
            return False
        if row.status != "processing" or row.worker_id != worker_id:
            return False
        if row.lease_until is None or row.lease_until <= now_utc:
            return False
        row.status = "pending"
        row.sent_at = None
        row.claimed_at = None
        row.lease_until = None
        row.worker_id = None
        return True
