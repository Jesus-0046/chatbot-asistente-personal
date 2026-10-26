"""Servicio de dominio del módulo de salud y hábitos (Fase 4).

Los hábitos son objetivos de constancia (beber agua, ejercicio, meditar...)
con una unidad opcional y un objetivo POR PERIODO (`day`/`week`/`month`/`year`).
Cada `log` es "una vez cumplido" (o un valor, p. ej. litros/minutos).

Cumplimiento = número de registros del periodo EN CURSO (ventana local del
usuario) frente a `target_per_period`. La ventana se calcula en la zona
horaria del usuario (la pasa el tool) y se consulta en UTC.

Este módulo NO diagnostica ni recomienda tratamientos: es organización y
seguimiento de rutinas.
"""

from __future__ import annotations

import builtins
from datetime import datetime, timedelta
from decimal import Decimal

from asistente.domain.people import normalize_name
from asistente.persistence.models import Habit
from asistente.persistence.repositories import HabitLogRepository, HabitRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import add_months, start_of_day, to_utc, utc_now

HABIT_PERIODS = ("day", "week", "month", "year")
_NOTE_MAX = 2000


def period_window(now_local: datetime, period: str) -> tuple[datetime, datetime]:
    """Ventana (local) del periodo en curso: [start, end).

    - day:   hoy a medianoche → mañana.
    - week:  lunes a medianoche → lunes siguiente.
    - month: día 1 a medianoche → día 1 del mes siguiente.
    - year:  1 de enero → 1 de enero del año siguiente.
    """
    if period not in HABIT_PERIODS:
        raise ValueError(f"Periodo de hábito no válido: {period}. Válidos: {', '.join(HABIT_PERIODS)}.")
    start = start_of_day(now_local)
    if period == "day":
        end = start + timedelta(days=1)
    elif period == "week":
        start = start - timedelta(days=start.weekday())
        end = start + timedelta(days=7)
    elif period == "month":
        start = start.replace(day=1)
        end = add_months(start, 1)
    else:  # year
        start = start.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
    return start, end


class HabitService:
    def __init__(self, habit_repo: HabitRepository, log_repo: HabitLogRepository) -> None:
        self._habits = habit_repo
        self._logs = log_repo

    # ------------------------------------------------------------------ #
    # CRUD de hábito                                                       #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        *,
        user_id: int,
        name: str,
        description: str | None = None,
        unit: str | None = None,
        target_per_period=None,
        period: str = "week",
    ) -> Habit:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre del hábito no puede estar vacío.")
        if period not in HABIT_PERIODS:
            raise ValueError(f"Periodo de hábito no válido: {period}. Válidos: {', '.join(HABIT_PERIODS)}.")
        if target_per_period is not None and Decimal(str(target_per_period)) <= 0:
            raise ValueError("El objetivo por periodo debe ser mayor que cero.")
        return await self._habits.create(
            user_id=user_id,
            name=(name or "").strip()[:200],
            normalized_name=normalized[:200],
            description=(description or "").strip()[:_NOTE_MAX] or None,
            unit=(unit or "").strip()[:32] or None,
            target_per_period=Decimal(str(target_per_period)) if target_per_period is not None else None,
            period=period,
        )

    async def list(self, user_id: int, *, only_active: bool = True) -> list[Habit]:
        return await self._habits.list_for_user(user_id, only_active=only_active)

    async def get(self, user_id: int, habit_id: int) -> Habit:
        habit = await self._habits.find_one(user_id, habit_id)
        if habit is None:
            raise NotFoundError("El hábito no existe o no te pertenece.")
        return habit

    async def resolve(self, user_id: int, name_or_id) -> Habit:
        if isinstance(name_or_id, int):
            return await self.get(user_id, name_or_id)
        normalized = normalize_name(str(name_or_id))
        if not normalized:
            raise ValueError("El nombre del hábito está vacío.")
        matches = await self._habits.find_by_normalized_name(user_id, normalized)
        if len(matches) == 0:
            raise NotFoundError(f"No se encontró ningún hábito activo llamado '{name_or_id.strip()}'.")
        if len(matches) > 1:
            ids = ", ".join(str(h.id) for h in matches)
            raise ValueError(f"Hay varios hábitos llamados '{name_or_id.strip()}'. Indica su ID (opciones: {ids}).")
        return matches[0]

    async def update(
        self,
        user_id: int,
        habit_id: int,
        *,
        name=None,
        description=None,
        unit=None,
        target_per_period=None,
        period=None,
    ) -> Habit:
        await self.get(user_id, habit_id)
        fields: dict = {}
        if name is not None:
            normalized = normalize_name(name)
            if not normalized:
                raise ValueError("El nombre del hábito no puede estar vacío.")
            fields["name"] = (name or "").strip()[:200]
            fields["normalized_name"] = normalized[:200]
        if description is not None:
            fields["description"] = (description or "").strip()[:_NOTE_MAX] or None
        if unit is not None:
            fields["unit"] = (unit or "").strip()[:32] or None
        if target_per_period is not None:
            target = Decimal(str(target_per_period))
            if target <= 0:
                raise ValueError("El objetivo por periodo debe ser mayor que cero.")
            fields["target_per_period"] = target
        if period is not None:
            if period not in HABIT_PERIODS:
                raise ValueError(f"Periodo de hábito no válido: {period}. Válidos: {', '.join(HABIT_PERIODS)}.")
            fields["period"] = period
        fields["updated_at"] = utc_now()
        updated = await self._habits.update(user_id, habit_id, **fields)
        assert updated is not None
        return updated

    async def deactivate(self, user_id: int, habit_id: int) -> Habit:
        habit = await self.get(user_id, habit_id)
        if not habit.active:
            raise ValueError("El hábito ya está desactivado.")
        updated = await self._habits.update(user_id, habit_id, active=False, updated_at=utc_now())
        assert updated is not None
        return updated

    # ------------------------------------------------------------------ #
    # Registros                                                            #
    # ------------------------------------------------------------------ #
    async def log(
        self,
        *,
        user_id: int,
        habit: Habit,
        value=None,
        logged_at: datetime | None = None,
        note: str | None = None,
        timezone: str | None = None,
    ) -> dict:
        if value is not None and Decimal(str(value)) <= 0:
            raise ValueError("El valor del registro debe ser mayor que cero.")
        logged = logged_at or utc_now()

        window_start, window_end = self._window_for_log(logged, habit.period, timezone)
        entry = await self._logs.create(
            user_id=user_id,
            habit_id=habit.id,
            value=Decimal(str(value)) if value is not None else None,
            logged_at=logged,
            note=(note or "").strip()[:_NOTE_MAX] or None,
        )
        return {
            "habit_id": habit.id,
            "log_id": entry.id,
            "value": entry.value,
            "logged_at": entry.logged_at,
            "period": habit.period,
            "count": await self._count_in_window(user_id, habit.id, window_start, window_end),
        }

    async def get_status(self, user_id: int, habit_id: int, *, timezone: str | None = None) -> dict:
        habit = await self.get(user_id, habit_id)
        now = utc_now()
        return {
            "habit": habit,
            **await self._window_stats(user_id, habit, now=now, timezone=timezone),
        }

    async def list_with_progress(self, user_id: int, *, timezone: str | None = None) -> builtins.list[dict]:
        result = []
        now = utc_now()
        for habit in await self._habits.list_for_user(user_id, only_active=True):
            stats = await self._window_stats(user_id, habit, now=now, timezone=timezone)
            result.append({"habit": habit, **stats})
        return result

    # ------------------------------------------------------------------ #
    # Internos                                                             #
    # ------------------------------------------------------------------ #
    async def _window_stats(self, user_id: int, habit: Habit, *, now: datetime, timezone: str | None) -> dict:
        from asistente.shared.timeutil import to_local

        now_local = to_local(now, timezone)
        window_start, window_end = period_window(now_local, habit.period)
        start_utc = to_utc(window_start)
        end_utc = to_utc(window_end)
        count = await self._count_in_window(user_id, habit.id, start_utc, end_utc)
        total = await self._logs.sum_in_window(user_id, habit.id, start_utc=start_utc, end_utc=end_utc)
        last_logs = await self._logs.list_for_habit(user_id, habit.id, limit=5)
        target = Decimal(str(habit.target_per_period)) if habit.target_per_period is not None else None
        achieved: bool | None = None
        if target is not None:
            achieved = count >= target
        return {
            "window": {"start_utc": start_utc, "end_utc": end_utc, "label": habit.period},
            "count": count,
            "total": total,
            "target": target,
            "achieved": achieved,
            "last_logs": last_logs,
        }

    def _window_for_log(self, logged_at: datetime, period: str, timezone: str | None):
        from asistente.shared.timeutil import to_local

        local = to_local(logged_at, timezone)
        window_start, window_end = period_window(local, period)
        return to_utc(window_start), to_utc(window_end)

    async def _count_in_window(self, user_id: int, habit_id: int, start_utc: datetime, end_utc: datetime) -> int:
        return await self._logs.count_in_window(user_id, habit_id, start_utc=start_utc, end_utc=end_utc)
