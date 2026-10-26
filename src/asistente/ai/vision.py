"""Extracción visual de imágenes (Fase 7).

`ImageUnderstandingProvider` es la abstracción que el dominio consume: recibe
la imagen (en memoria, nunca persistida) y devuelve una extracción
ESTRUCTURADA con confianza por campo. El dominio la valida, la normaliza y
construye una propuesta que el usuario debe confirmar antes de que ninguna
tool escriba en la BD.

`OpenAIImageUnderstandingClient` es la implementación real y reutiliza la MISMA
integración OpenAI que el LLM (comparte cliente/modelo/timeout vía
`create_openai_client`). En tests se sustituye por un fake sin tocar el
dominio.

Regla fundamental: imagen → extracción estructurada → validación → propuesta →
confirmación → tool existente → persistencia. El proveedor NUNCA inserta nada.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Literal, Protocol, runtime_checkable

import openai
from structlog import get_logger

from asistente.ai.llm import create_openai_client
from asistente.application.context import ImageAttachment
from asistente.config import Settings

_log = get_logger("ai.vision")

Confidence = Literal["high", "medium", "low"]

VALID_CONFIDENCE: tuple[str, ...] = ("high", "medium", "low")


class ImageExtractionError(Exception):
    """Fallo del análisis de imagen, categorizado para respuesta amigable.

    Categorías (según especificación Fase 7):
    - size_exceeded          imagen demasiado grande
    - unsupported_format     formato no soportado
    - too_many_images        límite de imágenes por mensaje
    - provider_unavailable   proveedor no disponible (auth/api)
    - timeout                tiempo de espera agotado
    - unreadable             imagen sin contenido identificable
    - invalid_json           respuesta visual no parseable
    - invalid_response       respuesta visual inválida/incompleta
    - insufficient_confidence sin confianza suficiente para proponer
    """

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
        self.message = message


def _confidence_of(value: str | None) -> str:
    """Confianza del proveedor: high/medium/low; entrada inválida → low."""
    return value if value in VALID_CONFIDENCE else "low"


# --------------------------------------------------------------------------- #
# Proveedor de visión                                                         #
# --------------------------------------------------------------------------- #
@runtime_checkable
class ImageUnderstandingProvider(Protocol):
    def understand(self, *, image: ImageAttachment, hint: str | None = None) -> dict[str, Any]:
        """Analiza la imagen y devuelve la extracción estructurada en dict.

        El dict sigue el esquema documentado en el prompt del proveedor
        (`kind`, `confidence`, `warnings` y la sección del tipo detectado).
        Puede lanzar `ImageExtractionError`.
        """
        ...


_EXTRACTION_SCHEMA_DOC = """\
Eres un extractor de información de imágenes para un asistente personal (es-ES).
Analiza la imagen y responde SOLO con un JSON (sin texto adicional) con esta forma:

{
  "kind": "ticket" | "shopping_list" | "task_list" | "structured_note" | "unknown",
  "confidence": "high" | "medium" | "low",
  "warnings": ["...", ...],
  <seccion segun kind>
}

Secciones (cada campo SIEMPRE es un objeto {"value": "<texto>", "confidence": "high|medium|low"}):

- kind "ticket":
  "ticket": {
    "merchant":  {"value": "Mercadona", "confidence": "..."},
    "date":      {"value": "20/09/2026", "confidence": "..."},
    "time":      {"value": "18:42", "confidence": "..."},
    "total":     {"value": "28,80", "confidence": "..."},
    "currency":  {"value": "EUR", "confidence": "..."},
    "items": [{"description": {...}, "quantity": {...}, "unit_price": {...}, "total_price": {...}}, ...]
  }

- kind "shopping_list":
  "shopping_list": {
    "title": {"value": "...", "confidence": "..."},
    "items": [{"name": {...}, "quantity": {...}, "notes": {...}}, ...]
  }

- kind "task_list":
  "task_list": {"tasks": [{"title": {...}, "due_date": {...}, "priority": {...}}, ...]}

- kind "structured_note":
  "structured_note": {"title": {...}, "content": {...}}

Reglas:
- Nunca inventes datos. Si un campo no se lee con claridad usa value "" y confidence "low".
- Los importes de dinero SIEMPRE como texto sin símbolos ni separadores de miles ("28,80"; los centavos van tras la coma). Nunca números.
- Las fechas se copian tal como aparecen ("20/09/2026", "20 Sep 2026"). Si no hay año, copia solo lo visible.
- quantity puede ser texto ("1 kg", "2 unidades", "3").
- priority de una tarea: texto corto ("urgente", "normal", "baja") o "".
- kind "unknown" si la imagen no es ninguna de las anteriores. En ese caso warnings debe explicar qué se ve.
- confidence global: "high" si la mayoría de campos se leen con claridad; "medium" si hay campos dudosos; "low" si la imagen es ilegible.
- warnings: lista breve de avisos (importes dudosos, datos ilegibles, fechas sin año...). Nunca menciones el contenido de la imagen fuera del JSON.
"""


class OpenAIImageUnderstandingClient:
    """Extractor de imágenes con la integración OpenAI existente.

    Comparte `create_openai_client` con `OpenAIClient`: misma API key y
    timeout. El modelo usa el configurado (`openai_model`); requiere soporte
    de imágenes (p. ej. gpt-4o-mini).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        model: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._model = model or settings.openai_model
        self._client = client if client is not None else create_openai_client(settings)
        self._max_tokens = settings.openai_max_tokens

    def understand(self, *, image: ImageAttachment, hint: str | None = None) -> dict[str, Any]:
        data_url = f"data:{image.mime_type};base64,{base64.b64encode(image.data).decode('ascii')}"
        user_parts: list[dict[str, Any]] = []
        if hint and hint.strip():
            user_parts.append({"type": "text", "text": hint.strip()})
        user_parts.append({"type": "image_url", "image_url": {"url": data_url}})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _EXTRACTION_SCHEMA_DOC},
                {"role": "user", "content": user_parts},
            ],
            "response_format": {"type": "json_object"},
        }
        if self._max_tokens:
            kwargs["max_tokens"] = self._max_tokens

        try:
            response = self._client.chat.completions.create(**kwargs)
            content: str | None = response.choices[0].message.content
        except openai.AuthenticationError as exc:
            raise ImageExtractionError(
                "provider_unavailable", "No se pudo autenticar con el proveedor visual."
            ) from exc
        except openai.APITimeoutError as exc:
            raise ImageExtractionError("timeout", "El análisis de la imagen tardó demasiado.") from exc
        except openai.APIError as exc:
            # El texto crudo de OpenAI (detalles internos/de red) solo va a los
            # logs; el usuario recibe un mensaje genérico.
            _log.warning("image_provider_error", exc_info=exc)
            raise ImageExtractionError(
                "provider_unavailable", "El proveedor visual no está disponible en este momento."
            ) from exc

        try:
            payload = json.loads(content or "")
        except json.JSONDecodeError as exc:
            raise ImageExtractionError("invalid_json", "El proveedor devolvió una respuesta no válida.") from exc
        if not isinstance(payload, dict) or not payload.get("kind"):
            raise ImageExtractionError("invalid_response", "La extracción de la imagen no es válida.")
        return payload


def anonymized_user(user_id: int) -> str:
    """Identificador de usuario anonimizado para logs de imágenes."""
    import hashlib

    return hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:10]
