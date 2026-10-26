"""Tests del servicio de dominio de personas (Fase 3.4).

Cubre: alta con normalización (acentos/minúsculas), listas activas,
renombrado, desactivación (nunca borrar), aislamiento por usuario y
resolución por nombre/id (incluida la ambigüedad).
"""

from __future__ import annotations

import pytest

from asistente.domain.people import normalize_name
from asistente.shared.errors import NotFoundError


def test_normalize_name_quita_acentos_y_colapsa_espacios() -> None:
    assert normalize_name("  María   García ") == "maria garcia"
    assert normalize_name("José Ángel") == "jose angel"
    assert normalize_name("MIGUEL") == "miguel"


async def test_create_persona_normaliza(services) -> None:
    person = await services["people_svc"].create(user_id=1, name="María García")

    assert person.name == "María García"
    assert person.normalized_name == "maria garcia"
    assert person.active is True


async def test_create_persona_nombre_vacio_rechazado(services) -> None:
    with pytest.raises(ValueError, match="vacío"):
        await services["people_svc"].create(user_id=1, name="   ")


async def test_duplicados_de_nombre_activos_permitidos(services) -> None:
    first = await services["people_svc"].create(user_id=1, name="Juan")
    second = await services["people_svc"].create(user_id=1, name="juan")

    assert first.id != second.id


async def test_list_solo_activas_por_defecto(services) -> None:
    juan = await services["people_svc"].create(user_id=1, name="Juan")
    await services["people_svc"].create(user_id=1, name="María")
    await services["people_svc"].deactivate(1, juan.id)

    listed = await services["people_svc"].list(1)
    assert [p.name for p in listed] == ["María"]


async def test_isolacion_usuarios_no_ven_las_personas_del_otro(services) -> None:
    await services["people_svc"].create(user_id=1, name="Juan")

    listed = await services["people_svc"].list(2)
    assert listed == []


async def test_update_recalcula_normalized_name(services) -> None:
    person = await services["people_svc"].create(user_id=1, name="María")
    updated = await services["people_svc"].update(1, person.id, name="Mariana")

    assert updated.name == "Mariana"
    assert updated.normalized_name == "mariana"


async def test_update_persona_ajena_lanza_not_found(services) -> None:
    await services["people_svc"].create(user_id=1, name="Juan")
    with pytest.raises(NotFoundError):
        await services["people_svc"].update(2, 1, name="X")


async def test_deactivate_persona_y_doblemente_error(services) -> None:
    person = await services["people_svc"].create(user_id=1, name="Juan")
    deactivated = await services["people_svc"].deactivate(1, person.id)
    assert deactivated.active is False

    with pytest.raises(ValueError, match="desactivada"):
        await services["people_svc"].deactivate(1, person.id)


async def test_resolve_por_id(services) -> None:
    person = await services["people_svc"].create(user_id=1, name="Juan")
    resolved = await services["people_svc"].resolve(1, person.id)
    assert resolved.id == person.id


async def test_resolve_por_id_ajeno_not_found(services) -> None:
    with pytest.raises(NotFoundError):
        await services["people_svc"].resolve(1, 999)


async def test_resolve_por_nombre_normalizado_sin_acentos(services) -> None:
    await services["people_svc"].create(user_id=1, name="José")
    resolved = await services["people_svc"].resolve(1, "JOSE")
    assert resolved.name == "José"


async def test_resolve_ambiguo_pide_id(services) -> None:
    await services["people_svc"].create(user_id=1, name="Juan")
    await services["people_svc"].create(user_id=1, name="juan")

    with pytest.raises(ValueError, match="ID"):
        await services["people_svc"].resolve(1, "juan")


async def test_resolve_sin_coincidencias_not_found(services) -> None:
    with pytest.raises(NotFoundError):
        await services["people_svc"].resolve(1, "Inexistente")


async def test_resolve_ignora_personas_desactivadas(services) -> None:
    person = await services["people_svc"].create(user_id=1, name="Juan")
    await services["people_svc"].deactivate(1, person.id)

    with pytest.raises(NotFoundError):
        await services["people_svc"].resolve(1, "Juan")
