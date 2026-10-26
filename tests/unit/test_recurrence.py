"""Tests del motor de recurrencias (Fase 2).

Casos: diaria, semanal con días concretos, mensual (saneo de fin de mes),
anual (dia bisciesto), fin incluido, `next_occurrence` tras una fecha y
límites de ventana.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from asistente.shared.recurrence import RecurrenceSpec, iter_occurrences, next_occurrence
from asistente.shared.timeutil import to_utc

TZ_MAD = ZoneInfo("Europe/Madrid")


def local(*, year=2026, month=9, day=14, hour=0, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=TZ_MAD)


def test_daily_genera_ocurrencias():
    spec = RecurrenceSpec(
        id=1, freq="daily", interval=1, start_at_utc=to_utc(local(day=1, hour=9)), timezone="Europe/Madrid"
    )
    start, end = local(day=1), local(day=10)
    got = iter_occurrences(spec, start, end + timedelta(days=1))
    assert [d.day for d in got] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def test_daily_intervalo_2():
    spec = RecurrenceSpec(
        id=1, freq="daily", interval=2, start_at_utc=to_utc(local(day=1, hour=9)), timezone="Europe/Madrid"
    )
    got = iter_occurrences(spec, local(day=1), local(day=14) + timedelta(days=1))
    assert [d.day for d in got] == [1, 3, 5, 7, 9, 11, 13]


def test_weekly_con_dias_concretos():
    # Empieza lunes 14 sep (weekday 0). Semanal lunes(0)+miércoles(2)+viernes(4).
    spec = RecurrenceSpec(
        id=1,
        freq="weekly",
        interval=1,
        days_of_week=(0, 2, 4),
        start_at_utc=to_utc(local(hour=9)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(), local(day=27) + timedelta(days=1))
    # 14(lun),16(mie),18(vie),21(lun),23(mie),25(vie)
    assert [d.day for d in got] == [14, 16, 18, 21, 23, 25]


def test_weekly_con_dias_sin_orden():
    spec = RecurrenceSpec(
        id=1,
        freq="weekly",
        interval=1,
        days_of_week=(4, 0, 2),
        start_at_utc=to_utc(local(hour=9)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(), local(day=20) + timedelta(days=1))
    assert [d.day for d in got] == [14, 16, 18]


def test_weekly_por_defecto_dia_de_inicio():
    spec = RecurrenceSpec(id=1, freq="weekly", interval=1, start_at_utc=to_utc(local(hour=9)), timezone="Europe/Madrid")
    got = iter_occurrences(spec, local(), local(day=28) + timedelta(days=1))
    # Solo lunes (14, 21, 28)
    assert [d.day for d in got] == [14, 21, 28]


def test_monthly_saneo_fin_de_mes():
    # 31 de enero -> 28 de febrero, 31 de marzo ...
    spec = RecurrenceSpec(
        id=1,
        freq="monthly",
        interval=1,
        start_at_utc=to_utc(datetime(2026, 1, 31, 9, 0, tzinfo=TZ_MAD)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(year=2026, month=1), local(year=2026, month=4, day=1))
    assert [(d.month, d.day) for d in got] == [(1, 31), (2, 28), (3, 31)]


def test_monthly_intervalo_2():
    spec = RecurrenceSpec(
        id=1,
        freq="monthly",
        interval=2,
        start_at_utc=to_utc(datetime(2026, 1, 15, 9, 0, tzinfo=TZ_MAD)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(year=2026, month=1), local(year=2026, month=7, day=1))
    assert [(d.month, d.day) for d in got] == [(1, 15), (3, 15), (5, 15)]


def test_yearly_dia_bisiesto():
    spec = RecurrenceSpec(
        id=1,
        freq="yearly",
        interval=1,
        start_at_utc=to_utc(datetime(2024, 2, 29, 9, 0, tzinfo=TZ_MAD)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(year=2024, month=2), local(year=2027, month=3, day=1))
    assert [(d.year, d.month, d.day) for d in got] == [
        (2024, 2, 29),
        (2025, 2, 28),
        (2026, 2, 28),
        (2027, 2, 28),
    ]


def test_end_at_acota_ocurrencias():
    spec = RecurrenceSpec(
        id=1,
        freq="daily",
        interval=1,
        start_at_utc=to_utc(local(day=1, hour=9)),
        end_at_utc=to_utc(local(day=4, hour=23, minute=59)),
        timezone="Europe/Madrid",
    )
    got = iter_occurrences(spec, local(day=1), local(day=10) + timedelta(days=1))
    assert [d.day for d in got] == [1, 2, 3, 4]


def test_no_genera_antes_de_ventana():
    spec = RecurrenceSpec(
        id=1, freq="daily", interval=1, start_at_utc=to_utc(local(day=1, hour=9)), timezone="Europe/Madrid"
    )
    got = iter_occurrences(spec, local(day=6, hour=9, minute=5), local(day=7, hour=8, minute=59))
    assert [d.day for d in got] == []


def test_next_occurrence():
    spec = RecurrenceSpec(
        id=1, freq="daily", interval=1, start_at_utc=to_utc(local(day=1, hour=9)), timezone="Europe/Madrid"
    )
    nxt = next_occurrence(spec, to_utc(local(day=5, hour=10)))
    assert nxt is not None
    assert nxt.day == 6


def test_next_occurrence_sin_siguiente_por_end():
    spec = RecurrenceSpec(
        id=1,
        freq="daily",
        interval=1,
        start_at_utc=to_utc(local(day=1, hour=9)),
        end_at_utc=to_utc(local(day=3, hour=9)),
        timezone="Europe/Madrid",
    )
    assert next_occurrence(spec, to_utc(local(day=3, hour=10))) is None


def test_frecuencia_invalida():
    try:
        RecurrenceSpec(id=1, freq="cada-x", start_at_utc=to_utc(local(day=1)))
        raise AssertionError("Debía fallar")
    except ValueError:
        pass


def test_intervalo_invalido():
    try:
        RecurrenceSpec(id=1, freq="daily", interval=0, start_at_utc=to_utc(local(day=1)))
        raise AssertionError("Debía fallar")
    except ValueError:
        pass


def test_next_occurrence_respeta_hora_local():
    # 9:00 en Madrid = 07:00 UTC (verano). Preguntar a las 6:30 UTC (8:30 local).
    spec = RecurrenceSpec(
        id=1, freq="daily", interval=1, start_at_utc=to_utc(local(day=1, hour=9)), timezone="Europe/Madrid"
    )
    after = datetime(2026, 9, 1, 6, 30, tzinfo=UTC)  # 8:30 local
    nxt = next_occurrence(spec, after)
    assert nxt.hour == 9
    assert nxt.day == 1
