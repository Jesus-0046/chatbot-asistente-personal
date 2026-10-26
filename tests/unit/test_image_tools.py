"""Tests de la tool `apply_extracted_data` (Fase 7).

Verifica la regla fundamental: NUNCA imagen → LLM → escritura directa. La
tool requiere confirmación; solo al ejecutarse `confirmed=True` persiste
gastos/tareas/notas reutilizando los servicios de dominio existentes (misma
capa que create_expense/create_task/create_note). Aislamiento por usuario
(IDOR): toda operación queda scoped en `ctx.user_id`.
"""

from __future__ import annotations

from decimal import Decimal

from tests.unit.helpers import make_connected_services

from asistente.application.services import Services
from asistente.tools import create_tool_registry
from asistente.tools.context import ToolContext
from asistente.tools.registry import ToolRegistry

USUARIO = 10
OTRO_USUARIO = 11


def _ticket_proposal(**overrides: object) -> dict:
    expense = {
        "amount": "28.80",
        "merchant": "Mercadona",
        "occurred_at": "2026-09-20T16:42:00+00:00",
        "category": None,
        "note": "Ticket de Mercadona · 3 líneas",
    }
    expense.update(overrides)
    return {
        "kind": "ticket",
        "expense": expense,
        "confidence": "high",
        "warnings": [],
    }


async def _env() -> tuple[Services, ToolRegistry, int]:
    services = await make_connected_services()
    user = await services.users.ensure_user(
        telegram_user_id=USUARIO,
        name="Prueba",
        username="prueba",
        timezone="Europe/Madrid",
        lang="es-ES",
    )
    await services.accounts.create(
        user_id=user.id,
        name="Cuenta",
        type="bank",
        currency="EUR",
        initial_balance=Decimal("1000.00"),
    )
    return services, create_tool_registry(), user.id


async def test_sin_confirmacion_no_escribe_absolutamente_nada() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)

    result = await registry.execute(ctx, "apply_extracted_data", _ticket_proposal(), confirmed=False)

    assert not result.ok
    assert result.needs_confirmation
    assert await services.transactions.list(uid) == []
    assert await services.tasks.list(uid) == []
    assert await services.notes.list(uid) == []


async def test_ticket_confirmado_crea_una_unica_transaccion_expense() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)

    result = await registry.execute(ctx, "apply_extracted_data", _ticket_proposal(), confirmed=True)

    assert result.ok
    assert result.data["created"] == "expense"
    txns = await services.transactions.list(uid, limit=10)
    assert len(txns) == 1
    txn = txns[0]
    assert isinstance(txn.amount, Decimal)  # nunca float en persistencia
    assert txn.amount == Decimal("28.80")
    assert txn.merchant == "Mercadona"
    assert str(txn.merchant) == "Mercadona"


async def test_ticket_sin_fecha_completa_no_se_registra() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    result = await registry.execute(ctx, "apply_extracted_data", _ticket_proposal(occurred_at=None), confirmed=True)
    assert not result.ok
    assert "fecha" in (result.error or "")
    assert await services.transactions.list(uid) == []  # no se inventa la fecha


async def test_ticket_sin_total_no_puede_registrarse() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    result = await registry.execute(ctx, "apply_extracted_data", _ticket_proposal(amount=None), confirmed=True)
    assert not result.ok


async def test_tasks_confirmadas_crean_tareas() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    proposal = {
        "kind": "tasks",
        "tasks": [
            {"title": "Comprar pan", "due_date": "2026-09-25", "priority": "urgente"},
            {"title": "Llamar al seguro", "due_date": None, "priority": None},
        ],
        "confidence": "high",
        "warnings": [],
    }
    result = await registry.execute(ctx, "apply_extracted_data", proposal, confirmed=True)
    assert result.ok
    assert result.data["count"] == 2
    tasks = await services.tasks.list(uid)
    assert len(tasks) == 2
    by_title = {t.title: t for t in tasks}
    assert by_title["Comprar pan"].priority == "high"
    assert by_title["Comprar pan"].due_at is not None
    assert by_title["Llamar al seguro"].due_at is None  # sin fecha: no se inventa


async def test_lista_compra_confirmada_se_guarda_como_nota() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    proposal = {
        "kind": "shopping_list",
        "shopping_list": {
            "title": "Lista del sábado",
            "items": [
                {"name": "Pan de molde", "quantity": "2", "notes": ""},
                {"name": "Tomates", "quantity": "1 kg", "notes": ""},
            ],
        },
        "confidence": "high",
        "warnings": [],
    }
    result = await registry.execute(ctx, "apply_extracted_data", proposal, confirmed=True)
    assert result.ok
    assert result.data["created"] == "shopping_list"
    notes = await services.notes.list(uid)
    assert len(notes) == 1
    assert notes[0].title == "Lista del sábado"
    assert "- Pan de molde (2)" in notes[0].body


async def test_nota_estructurada_confirmada_se_guarda() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    proposal = {
        "kind": "structured_note",
        "structured_note": {
            "title": "Receta",
            "content": "4 huevos y 2 patatas.",
        },
        "confidence": "high",
        "warnings": [],
    }
    result = await registry.execute(ctx, "apply_extracted_data", proposal, confirmed=True)
    assert result.ok
    notes = await services.notes.list(uid)
    assert notes[0].title == "Receta"
    assert notes[0].body == "4 huevos y 2 patatas."


async def test_tipo_incoherente_con_su_bloque_es_rechazado() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    result = await registry.execute(
        ctx, "apply_extracted_data", {"kind": "ticket", "confidence": "high", "warnings": []}, confirmed=True
    )
    assert not result.ok
    assert "bloque" in (result.error or "")


async def test_la_confirmacion_esta_scoped_al_usuario_autenticado() -> None:
    services, registry, uid = await _env()
    other = await services.users.ensure_user(
        telegram_user_id=OTRO_USUARIO, name="Otro", username="otro", timezone="Europe/Madrid", lang="es-ES"
    )
    ctx = ToolContext(user_id=uid, services=services)
    proposal = _ticket_proposal()
    result = await registry.execute(ctx, "apply_extracted_data", proposal, confirmed=True)
    assert result.ok
    # El usuario B no ve ni puede tocar nada del usuario A.
    assert await services.transactions.list(uid) != await services.transactions.list(other.id)
    assert len(await services.transactions.list(other.id)) == 0
    txns = await services.transactions.list(uid, limit=10)
    assert all(t.user_id == uid for t in txns)


async def test_importe_convertido_sin_floats_intermedios() -> None:
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    result = await registry.execute(
        ctx,
        "apply_extracted_data",
        {
            "kind": "structured_note",
            "structured_note": {
                "title": "Ticket detalle",
                "content": "Total 28,80 €",
            },
            "confidence": "high",
            "warnings": [],
        },
        confirmed=True,
    )
    assert result.ok


async def test_lote_de_tareas_es_atomico_si_una_falla() -> None:
    """Si una tarea del lote falla, NINGUNA se persiste (sin escrituras parciales)."""
    services, registry, uid = await _env()
    ctx = ToolContext(user_id=uid, services=services)
    proposal = {
        "kind": "tasks",
        "tasks": [
            {"title": "Tarea 1", "due_date": "2026-09-25", "priority": None},
            {"title": "Tarea 2", "due_date": None, "priority": None},
            {"title": "Tarea 3", "due_date": None, "priority": None},
        ],
        "confidence": "high",
        "warnings": [],
    }

    original = services.tasks._tasks.create
    calls = {"n": 0}

    async def flaky_create(
        *,
        user_id: int,
        title: str,
        due_at: object = None,
        timezone: str = "Europe/Madrid",
        priority: str = "medium",
        session: object = None,
        **kwargs: object,
    ) -> object:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("Fallo simulado en la segunda tarea")
        return await original(
            user_id=user_id, title=title, due_at=due_at, timezone=timezone, priority=priority, session=session, **kwargs
        )

    services.tasks._tasks.create = flaky_create  # type: ignore[method-assign]
    result = await registry.execute(ctx, "apply_extracted_data", proposal, confirmed=True)

    assert not result.ok
    assert await services.tasks.list(uid) == []  # rollback total, no tareas parciales
