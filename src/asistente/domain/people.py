"""Servicio de dominio de personas (Fase 3.4).

Una persona es alguien con quien el usuario tiene deudas o gastos compartidos
("Juan", "María"). Pertenecen a UN usuario y nunca se eliminan físicamente si
tienen histórico financiero: se desactivan.

Normalización (`normalize_name`):
    minúsculas → tildes→ascii → colapsar espacios → strip.
    "María García " → "maria garcia"
    Duplicados de nombres activos dentro de un mismo usuario están permitidos
    (dos "Juan"). La resolución por nombre exacto es la única vía de lookup;
    si hay varios → se pregunta al usuario.
"""

from __future__ import annotations

import unicodedata

from asistente.persistence.models import Person
from asistente.persistence.repositories import PersonRepository
from asistente.shared.errors import NotFoundError
from asistente.shared.timeutil import utc_now


def normalize_name(raw: str) -> str:
    """Forma canónica de un nombre de persona: minúsculas, sin acentos,
    espacios colapsados."""
    text = raw.strip().lower()
    # Descomponer y quedarse con ASCII base (quita tildes/diacríticos)
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_base = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    # Colapsar espacios múltiples
    parts = ascii_base.split()
    return " ".join(parts)


class PersonService:
    def __init__(self, person_repo: PersonRepository) -> None:
        self._repo = person_repo

    async def create(self, *, user_id: int, name: str) -> Person:
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre de la persona no puede estar vacío.")
        return await self._repo.create(
            user_id=user_id,
            name=name.strip()[:200],
            normalized_name=normalized[:200],
        )

    async def list(self, user_id: int, *, only_active: bool = True) -> list[Person]:
        return await self._repo.list_for_user(user_id, only_active=only_active)

    async def find_one(self, user_id: int, person_id: int) -> Person | None:
        return await self._repo.find_one(user_id, person_id)

    async def update(self, user_id: int, person_id: int, *, name: str) -> Person:
        person = await self._repo.find_one(user_id, person_id)
        if person is None:
            raise NotFoundError("La persona no existe o no te pertenece.")
        normalized = normalize_name(name)
        if not normalized:
            raise ValueError("El nombre de la persona no puede estar vacío.")
        updated = await self._repo.update(
            user_id,
            person_id,
            name=name.strip()[:200],
            normalized_name=normalized[:200],
            updated_at=utc_now(),
        )
        assert updated is not None
        return updated

    async def deactivate(self, user_id: int, person_id: int) -> Person:
        person = await self._repo.find_one(user_id, person_id)
        if person is None:
            raise NotFoundError("La persona no existe o no te pertenece.")
        if not person.active:
            raise ValueError("La persona ya está desactivada.")
        updated = await self._repo.update(user_id, person_id, active=False, updated_at=utc_now())
        assert updated is not None
        return updated

    async def resolve(self, user_id: int, name_or_id) -> Person:
        """Resuelve un identificador textual.

        - Si es un entero → busca por id (NotFoundError si no existe).
        - Si es texto → normaliza y busca por nombre normalizado:
          * 1 coincidencia → devuelve.
          * 0 coincidencias → NotFoundError.
          * >1 coincidencias → ValueError con "Ambiguous: hay varias personas
            llamadas X; indica el ID." (la herramienta debe preguntar al usuario).
        """
        if isinstance(name_or_id, int):
            person = await self._repo.find_one(user_id, name_or_id)
            if person is None:
                raise NotFoundError("La persona no existe o no te pertenece.")
            return person

        # Nombre textual
        normalized = normalize_name(str(name_or_id))
        if not normalized:
            raise ValueError("El nombre proporcionado está vacío.")
        matches = await self._repo.find_by_normalized_name(user_id, normalized)
        if len(matches) == 0:
            raise NotFoundError(f"No se encontró ninguna persona activa llamada '{name_or_id.strip()}'.")
        if len(matches) > 1:
            ids = ", ".join(str(p.id) for p in matches)
            raise ValueError(f"Hay varias personas llamadas '{name_or_id.strip()}'. Indica su ID (opciones: {ids}).")
        return matches[0]
