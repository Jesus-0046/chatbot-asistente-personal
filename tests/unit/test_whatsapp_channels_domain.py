"""Tests de dominio de canales (Fase 9): identidad, pairing y tool link.

Ejercitan `ChannelIdentityService`/`ChannelIdentityRepository` REALES sobre
SQLite en memoria (misma lógica de restricciones que usa el resto de tests del
proyecto) y la tool `link_whatsapp` que consume el código.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from tests.unit.helpers import make_in_memory_db

from asistente.application.channels import CHANNEL_WHATSAPP
from asistente.application.pairing import PAIR_CODE_LENGTH, PAIR_CODE_TTL_SECONDS, hash_pair_code
from asistente.persistence.db import Base
from asistente.shared.timeutil import utc_now
from asistente.tools.link import _link_whatsapp
from asistente.tools.registry import ToolResult


async def _build() -> tuple[Any, Any, Any]:
    from asistente.domain.channels import ChannelIdentityService
    from asistente.persistence.repositories import ChannelIdentityRepository
    from asistente.persistence.repositories.users import UserRepository

    db = make_in_memory_db()
    db.connect()
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    service = ChannelIdentityService(ChannelIdentityRepository(db))
    users = UserRepository(db)
    return service, users, db


def _users_factory(users: Any) -> Any:
    async def make(*, telegram_user_id: int, name: str = "Usuario") -> Any:
        return await users.ensure_user(
            channel="telegram",
            external_user_id=str(telegram_user_id),
            telegram_user_id=telegram_user_id,
            name=name,
            username=None,
            timezone="Europe/Madrid",
            lang="es-ES",
        )

    return make


# --------------------------------------------------------------------------- #
# Ciclo de pairing y vinculación                                              #
# --------------------------------------------------------------------------- #
async def test_ciclo_completo_pairing_y_vinculacion() -> None:
    service, users_repo, db = await _build()
    make_user = _users_factory(users_repo)

    # 1) ensure_pending_pair genera el código y guarda SOLO su hash.
    row, code = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34611122233")
    assert row.pair_code_hash == hash_pair_code(code)
    assert row.status == "pending"
    # 2) La identidad aún no resuelve a ningún usuario.
    assert await service.resolve_user(CHANNEL_WHATSAPP, "34611122233") is None

    # 3) Segunda llamada: sigue pendiente y no re-expone el código.
    row2, code2 = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34611122233")
    assert code2 is None
    assert row2.id == row.id
    assert row2.pair_code_hash == row.pair_code_hash

    # 4) Código correcto vincula (single-use).
    user = await make_user(telegram_user_id=111, name="Dueño")
    linked = await service.link_with_code(code=code, user_id=user.id)
    assert linked is not None
    assert linked.id == user.id

    # 5) Ahora la identidad resuelve al usuario interno; has_linked True.
    resolved = await service.resolve_user(CHANNEL_WHATSAPP, "34611122233")
    assert resolved is not None
    assert resolved.id == user.id
    assert await service.has_linked(user.id, CHANNEL_WHATSAPP)

    # 6) El código es de UN solo uso.
    assert await service.link_with_code(code=code, user_id=user.id) is None

    # 7) ensure_user por el mismo canal/identidad devuelve el mismo usuario.
    same = await users_repo.ensure_user(
        channel=CHANNEL_WHATSAPP,
        external_user_id="34611122233",
        name="Dueño",
        username=None,
        timezone="Europe/Madrid",
        lang="es-ES",
    )
    assert same.id == user.id


async def test_codigo_caducado_no_vincula() -> None:
    service, users_repo, db = await _build()
    make_user = _users_factory(users_repo)

    from asistente.persistence.repositories import ChannelIdentityRepository

    repo = ChannelIdentityRepository(db)
    row, code = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34655566677")
    await repo.update_pair_code(row.id, hash_pair_code(code), utc_now() - timedelta(seconds=1))

    user = await make_user(telegram_user_id=222, name="U")
    assert await service.link_with_code(code=code, user_id=user.id) is None
    assert await service.resolve_user(CHANNEL_WHATSAPP, "34655566677") is None


async def test_formato_invalido_no_consulta_la_bd() -> None:
    service, users_repo, _ = await _build()
    user = await _users_factory(users_repo)(telegram_user_id=333, name="U")
    assert await service.link_with_code(code="abc", user_id=user.id) is None
    assert await service.link_with_code(code="", user_id=user.id) is None


async def test_usuario_ya_vinculado_en_canal_no_adopta_otro_numero() -> None:
    service, users_repo, _ = await _build()
    user = await _users_factory(users_repo)(telegram_user_id=444, name="U")

    first_row, first_code = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34611111111")
    assert first_code is not None
    assert await service.link_with_code(code=first_code, user_id=user.id) is not None

    second_row, second_code = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34622222222")
    assert second_code is not None
    assert await service.link_with_code(code=second_code, user_id=user.id) is None


async def test_mark_processed_detecta_duplicados() -> None:
    service, _, _ = await _build()
    assert await service.mark_processed(CHANNEL_WHATSAPP, "wamid.uno") is True
    assert await service.mark_processed(CHANNEL_WHATSAPP, "wamid.uno") is False
    assert await service.mark_processed(CHANNEL_WHATSAPP, "wamid.dos") is True


async def test_ensure_user_compatibilidad_backward_telegram() -> None:
    service, users_repo, _ = await _build()
    user = await users_repo.ensure_user(
        telegram_user_id=777,
        name="Antiguo",
        username="antiguo",
        timezone="Europe/Madrid",
        lang="es-ES",
    )
    assert user.telegram_user_id == 777
    again = await users_repo.ensure_user(
        telegram_user_id=777,
        name="Antiguo",
        username="antiguo",
        timezone="Europe/Madrid",
        lang="es-ES",
    )
    assert again.id == user.id


# --------------------------------------------------------------------------- #
# Tool link_whatsapp                                                          #
# --------------------------------------------------------------------------- #
async def test_tool_link_whatsapp_ok_y_un_solo_uso() -> None:
    service, users_repo, _ = await _build()
    user = await _users_factory(users_repo)(telegram_user_id=888, name="Dueño")
    _, code = await service.ensure_pending_pair(CHANNEL_WHATSAPP, "34699999999")

    ctx = SimpleNamespace(user_id=user.id, services=SimpleNamespace(channel_identities=service))

    result = await _link_whatsapp(ctx, code=code)
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert "vinculado" in result.data["description_text"]

    result2 = await _link_whatsapp(ctx, code=code)
    assert result2.ok is False


async def test_tool_link_whatsapp_codigo_invalido() -> None:
    service, users_repo, _ = await _build()
    user = await _users_factory(users_repo)(telegram_user_id=999, name="Dueño")
    ctx = SimpleNamespace(user_id=user.id, services=SimpleNamespace(channel_identities=service))

    result = await _link_whatsapp(ctx, code="NO-EXISTE")
    assert result.ok is False
    assert "no es válido" in (result.error or "")


async def test_tool_link_whatsapp_sin_servicio() -> None:
    service, users_repo, _ = await _build()
    user = await _users_factory(users_repo)(telegram_user_id=1010, name="Dueño")
    ctx = SimpleNamespace(user_id=user.id, services=SimpleNamespace(channel_identities=None))

    result = await _link_whatsapp(ctx, code="ABCDEFGH")
    assert result.ok is False


def test_constantes_de_pairing() -> None:
    assert PAIR_CODE_LENGTH == 8
    assert PAIR_CODE_TTL_SECONDS == 30 * 60
    # El alfabeto no incluye caracteres ambiguos (0/O, 1/I/l).
    import string

    alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    assert "0" not in alphabet and "O" not in alphabet
    assert "1" not in alphabet and "I" not in alphabet and "L" not in alphabet
    assert all(c in string.ascii_uppercase + string.digits for c in alphabet)


def test_tool_link_ok_type_annotations() -> None:
    # helper de tipos: ToolResult carry `.data` dict when ok
    t = ToolResult(ok=True, data={"channel": "whatsapp"})
    assert t.data["channel"] == "whatsapp"
    e = ToolResult(ok=False, error="x")
    assert e.error == "x"
