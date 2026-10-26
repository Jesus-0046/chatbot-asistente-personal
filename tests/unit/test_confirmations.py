"""Tests unitarios: clasificación de intención y ciclo de vida de confirmaciones."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from asistente.application.confirmations import (
    ConfirmationService,
    classify_user_intent,
)
from asistente.shared.timeutil import utc_now


@dataclass(frozen=True)
class FakeConfirmationRecord:
    id: int
    user_id: int
    topic: str
    payload: dict
    proposed_reply: str
    expires_at: datetime
    status: str
    resolved_at: datetime | None = None


class FakeConfirmationRepo:
    """Réplica en memoria de `ConfirmationRepository` (sin BD)."""

    def __init__(self) -> None:
        self._records: list[FakeConfirmationRecord] = []
        self._next_id = 1

    async def find_open(self, user_id: int) -> FakeConfirmationRecord | None:
        open_rows = [r for r in self._records if r.user_id == user_id and r.status == "open"]
        if not open_rows:
            return None
        return max(open_rows, key=lambda r: r.id)

    async def create(
        self,
        *,
        user_id: int,
        topic: str,
        payload: dict,
        proposed_reply: str,
        expires_at: datetime,
    ) -> FakeConfirmationRecord:
        record = FakeConfirmationRecord(
            id=self._next_id,
            user_id=user_id,
            topic=topic,
            payload=payload,
            proposed_reply=proposed_reply,
            expires_at=expires_at,
            status="open",
        )
        self._records.append(record)
        self._next_id += 1
        return record

    async def expire_overdue(self, user_id: int, now: datetime) -> int:
        affected = 0
        for i, record in enumerate(self._records):
            if record.user_id == user_id and record.status == "open" and record.expires_at <= now:
                self._replace(i, status="expired", resolved_at=now)
                affected += 1
        return affected

    async def set_status(self, user_id: int, confirmation_id: int, status: str, now: datetime) -> bool:
        # Réplica del CAS del repositorio real: solo transiciona desde "open".
        for i, record in enumerate(self._records):
            if record.id == confirmation_id and record.user_id == user_id and record.status == "open":
                self._replace(i, status=status, resolved_at=now)
                return True
        return False

    def _replace(self, index: int, **changes: object) -> None:
        current = self._records[index]
        self._records[index] = FakeConfirmationRecord(
            id=current.id,
            user_id=current.user_id,
            topic=current.topic,
            payload=current.payload,
            proposed_reply=current.proposed_reply,
            expires_at=current.expires_at,
            status=str(changes.get("status", current.status)),
            resolved_at=changes.get("resolved_at", current.resolved_at),  # type: ignore[arg-type]
        )


def make_service(repo: FakeConfirmationRepo, *, ttl_minutes: int = 10) -> ConfirmationService:
    return ConfirmationService(repo, ttl_minutes=ttl_minutes)


# ---------------------------------------------------------------------------
# Clasificación de intención
# ---------------------------------------------------------------------------


def test_clasifica_respuestas_afirmativas() -> None:
    for text in ("sí", "si", "yes", "SI", " Sí ", "vale", "ok", "adelante"):
        assert classify_user_intent(text) == "yes"


def test_clasifica_respuestas_negativas() -> None:
    for text in ("no", "No", "cancelar", "cancelado", "NO "):
        assert classify_user_intent(text) == "no"


def test_texto_inconcluso_devuelve_none() -> None:
    for text in ("¿qué hora es?", "quizá", "", "sí, cuéntame", "no te entiendo"):
        assert classify_user_intent(text) is None


# ---------------------------------------------------------------------------
# Ciclo de vida: crear → confirmar
# ---------------------------------------------------------------------------


async def test_create_pending_guarda_y_es_findable() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo, ttl_minutes=10)

    created = await service.create_pending(
        user_id=7,
        topic="delete_note",
        payload={"note_id": 3},
        proposed_reply="¿Elimino la nota?",
    )
    assert created["id"] == 1
    assert created["topic"] == "delete_note"
    assert "expires_at" in created

    pending = await service.find_open(7)
    assert pending is not None
    assert pending["id"] == 1
    assert pending["payload"] == {"note_id": 3}


async def test_confirm_marca_como_confirmada() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    created = await service.create_pending(
        user_id=7,
        topic="delete_note",
        payload={"note_id": 3},
        proposed_reply="¿Elimino la nota?",
    )

    result = await service.confirm(7, created["id"])
    assert result == {"status": "confirmed"}
    assert await service.find_open(7) is None


async def test_confirm_de_otro_usuario_es_rechazado() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    created = await service.create_pending(
        user_id=7,
        topic="delete_note",
        payload={"note_id": 3},
        proposed_reply="¿Elimino la nota?",
    )

    assert await service.confirm(99, created["id"]) is None
    assert await service.find_open(7) is not None


async def test_confirm_inexistente_devuelve_none() -> None:
    service = make_service(FakeConfirmationRepo())
    assert await service.confirm(7, 4242) is None


async def test_cancel_descarta_la_confirmacion() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    created = await service.create_pending(
        user_id=7,
        topic="delete_note",
        payload={"note_id": 3},
        proposed_reply="¿Elimino la nota?",
    )

    assert await service.cancel(7, created["id"]) is True
    assert await service.find_open(7) is None


async def test_aislamiento_entre_usuarios() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    await service.create_pending(
        user_id=1,
        topic="activate_memory_rule",
        payload={"rule_id": 2},
        proposed_reply="¿Activo la regla?",
    )

    assert await service.find_open(2) is None
    assert await service.find_open(1) is not None


# ---------------------------------------------------------------------------
# Expiración (TTL)
# ---------------------------------------------------------------------------


async def test_confirmacion_vencida_no_es_findable() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo, ttl_minutes=-1)
    await service.create_pending(
        user_id=7,
        topic="delete_note",
        payload={"note_id": 3},
        proposed_reply="¿Elimino la nota?",
    )

    pending = await service.find_open(7)
    assert pending is None


async def test_todas_las_confirmaciones_tienen_expiry_futuro_con_ttl_positivo() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo, ttl_minutes=30)
    created = await service.create_pending(
        user_id=7,
        topic="act",
        payload={},
        proposed_reply="¿ok?",
    )

    record = repo._records[0]
    assert record.status == "open"
    assert record.expires_at > utc_now()
    assert datetime.fromisoformat(created["expires_at"]) == record.expires_at


# ---------------------------------------------------------------------------
# Compare-and-set: una confirmación resuelta nunca se re-ejecuta
# ---------------------------------------------------------------------------


async def test_confirmar_dos_veces_la_segunda_es_rechazada() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    created = await service.create_pending(
        user_id=7,
        topic="apply_extracted_data",
        payload={"kind": "ticket", "expense": {}},
        proposed_reply="¿Registro este gasto?",
    )

    first = await service.confirm(7, created["id"])
    second = await service.confirm(7, created["id"])

    assert first == {"status": "confirmed"}
    assert second is None  # la doble ejecución no ocurre


async def test_cancelar_impide_confirmar_despues() -> None:
    repo = FakeConfirmationRepo()
    service = make_service(repo)
    created = await service.create_pending(user_id=7, topic="act", payload={}, proposed_reply="¿ok?")

    assert await service.cancel(7, created["id"]) is True
    assert await service.confirm(7, created["id"]) is None


async def test_repo_real_cas_contra_confirmaciones_ya_resueltas() -> None:
    from tests.unit.helpers import make_connected_services

    from asistente.persistence.repositories import ConfirmationRepository

    services = await make_connected_services()
    repo = ConfirmationRepository(services.conversations.db)
    row = await repo.create(
        user_id=7,
        topic="apply_extracted_data",
        payload={"kind": "ticket", "expense": {}},
        proposed_reply="¿Registro este gasto?",
        expires_at=utc_now(),
    )

    assert await repo.set_status(7, row.id, "confirmed", utc_now()) is True
    # CAS: ya no está "open" → la segunda transición no afecta a ninguna fila.
    assert await repo.set_status(7, row.id, "confirmed", utc_now()) is False
    assert await repo.set_status(7, row.id, "cancelled", utc_now()) is False
