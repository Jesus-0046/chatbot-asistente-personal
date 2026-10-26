"""Tests del parser de fechas naturales (es-ES). Fase 2.

Cubre las expresiones requeridas: hoy, mañana, pasado mañana, días de la
semana, próximos, dentro de N horas/días, día del mes, "N de M", horas
explícitas y bandas del día, con `now` controlado.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from asistente.shared.dateparse import DateParseError, NaturalDateParser

TZ_MAD = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 9, 14, 10, 0, tzinfo=TZ_MAD)  # lunes 14 sep 2026 10:00


def parse(expr: str, *, now: datetime = NOW, hour: int = 9) -> datetime:
    return NaturalDateParser(default_hour=hour).parse(expr, now_local=now).value


def as_local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=TZ_MAD)


def test_hoy():
    assert parse("hoy") == as_local("2026-09-14T09:00")


def test_hoy_con_hora():
    assert parse("hoy a las 10") == as_local("2026-09-14T10:00")


def test_hoy_hora_pasada_va_a_manana():
    later = NOW + timedelta(minutes=30)  # 10:30
    assert parse("a las 10", now=later) == as_local("2026-09-15T10:00")


def test_hora_que_no_ha_pasado_se_queda_hoy():
    assert parse("a las 15") == as_local("2026-09-14T15:00")


def test_manana():
    assert parse("mañana") == as_local("2026-09-15T09:00")


def test_manana_a_las_10():
    assert parse("mañana a las 10") == as_local("2026-09-15T10:00")


def test_pasado_manana():
    assert parse("pasado mañana") == as_local("2026-09-16T09:00")


def test_viernes_esta_semana():
    assert parse("el viernes") == as_local("2026-09-18T09:00")


def test_viernes_con_hora():
    assert parse("el viernes a las 11:30") == as_local("2026-09-18T11:30")


def test_proximo_viernes_semana_siguiente():
    assert parse("el próximo viernes") == as_local("2026-09-25T09:00")


def test_lunes_que_viene():
    assert parse("el lunes que viene") == as_local("2026-09-21T09:00")


def test_miercoles_por_la_tarde():
    assert parse("el miércoles por la tarde") == as_local("2026-09-16T16:00")


def test_hoy_es_el_dia_candidato_semana_proxima():
    r = NaturalDateParser().parse("el lunes", now_local=NOW)
    assert r.value == as_local("2026-09-14T09:00")
    assert [c.date() for c in r.candidates] == [as_local("2026-09-21T09:00").date()]


def test_dentro_de_2_horas():
    assert parse("dentro de 2 horas") == as_local("2026-09-14T12:00")


def test_dentro_de_3_dias():
    assert parse("dentro de 3 días") == as_local("2026-09-17T10:00")


def test_dentro_de_20_minutos():
    assert parse("dentro de 20 minutos") == as_local("2026-09-14T10:20")


def test_el_dia_20():
    assert parse("el día 20") == as_local("2026-09-20T09:00")


def test_el_dia_que_ya_paso_va_al_mes_siguiente():
    # 5 de septiembre ya pasó (hoy es 14), y 14 tampoco, asumamos día 5
    assert parse("el día 5") == as_local("2026-10-05T09:00")


def test_n_de_m_mismo_ano():
    assert parse("el 25 de octubre") == as_local("2026-10-25T09:00")


def test_n_de_m_pasado_va_ano_siguiente():
    assert parse("el 2 de marzo") == as_local("2027-03-02T09:00")


def test_n_de_m_con_hora():
    assert parse("el 2 de octubre a las 10") == as_local("2026-10-02T10:00")


def test_15_junio_tarde():
    assert parse("15 de junio a las 4 de la tarde") == as_local("2027-06-15T16:00")


def test_semana_que_viene_lunes_temp():
    assert parse("la semana que viene") == as_local("2026-09-21T09:00")


def test_proximo_mes():
    assert parse("el próximo mes") == as_local("2026-10-14T09:00")


def test_all_day_flag():
    assert NaturalDateParser().parse("mañana", now_local=NOW).all_day is True
    assert NaturalDateParser().parse("mañana a las 10", now_local=NOW).all_day is False


def test_ambiguo_el_lunes_emite_candidato():
    r = NaturalDateParser().parse("el lunes", now_local=NOW)
    assert len(r.candidates) == 1


def test_fecha_incomprensible_levanta_error():
    with pytest.raises(DateParseError):
        parse("la semana próxima")


def test_dia_inexistente_levanta_error():
    with pytest.raises(DateParseError):
        parse("el día 31 de febrero")


def test_el_dia_31_valido_en_mes_con_31():
    assert parse("el día 31", now=datetime(2026, 10, 10, 12, 0, tzinfo=TZ_MAD)) == as_local("2026-10-31T09:00")


def test_el_dia_31_en_mes_de_30_dias_levanta_error():
    with pytest.raises(DateParseError):
        parse("el día 31", now=datetime(2026, 4, 10, 12, 0, tzinfo=TZ_MAD))  # abril


def test_el_dia_30_de_febrero_levanta_error_sin_ajuste_silencioso():
    """Regresión Fase 2.1: 'el día 30' en febrero NO debe resolverse al 28/29
    en silencio ni romper con un ValueError: debe ser un DateParseError claro."""
    with pytest.raises(DateParseError):
        parse("el día 30", now=datetime(2026, 2, 1, 12, 0, tzinfo=TZ_MAD))


def test_el_dia_29_de_febrero_bisiesto():
    value = NaturalDateParser().parse("el día 29", now_local=datetime(2028, 2, 1, 12, 0, tzinfo=TZ_MAD)).value
    assert value == as_local("2028-02-29T09:00")


def test_el_dia_29_de_febrero_no_bisiesto_levanta_error():
    with pytest.raises(DateParseError):
        parse("el día 29", now=datetime(2026, 2, 1, 12, 0, tzinfo=TZ_MAD))


def test_el_dia_5_al_final_de_ano_va_al_ano_siguiente():
    value = parse("el día 5", now=datetime(2026, 12, 25, 10, 0, tzinfo=TZ_MAD))
    assert value == as_local("2027-01-05T09:00")


def test_el_dia_31_al_final_de_mes_con_31_se_queda_en_el_mes():
    assert parse("el día 31", now=datetime(2026, 1, 10, 10, 0, tzinfo=TZ_MAD)) == as_local("2026-01-31T09:00")


def test_no_acepta_naive_now():
    with pytest.raises(DateParseError):
        NaturalDateParser().parse("mañana", now_local=datetime(2026, 9, 14, 10, 0))
