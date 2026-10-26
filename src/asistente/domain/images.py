"""Servicio de dominio de imágenes / extracción estructurada (Fase 7).

Flujo (regla fundamental de la fase):

    imagen → análisis visual/OCR → extracción estructurada → validación →
    normalización → propuesta → confirmación del usuario → tool existente →
    persistencia.

Este servicio es la capa DOMINIO del flujo de estas cuatro veces anteriores a
la persistencia:
- Valida límites configurables (tamaño, nº de imágenes, longitud extraída).
- Llama al `ImageUnderstandingProvider` (abstracción; en tests un fake).
- Valida y NORMALIZA la extracción (importes → Decimal, fechas sin año no se
  asumen, coherencia suma líneas vs total del ticket).
- Detecta posibles duplicados (comercio + fecha + total) sin bloquear jamás.
- Construye la PROPUESTA serializable y el texto que el usuario debe confirmar.

Ninguna imagen ni binario se persiste: la propuesta solo lleva texto
estructurado (es lo que viaja a `PendingConfirmation.payload`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from asistente.ai.vision import ImageExtractionError, ImageUnderstandingProvider
from asistente.application.context import ImageAttachment
from asistente.config import Settings
from asistente.domain.amounts import fmt_amount, fmt_amount_eu, parse_extracted_amount
from asistente.logging_setup import get_logger
from asistente.shared.timeutil import start_of_day, to_local, to_utc, utc_now

logger = get_logger("domain.images")

TICKET_TOTAL_TOLERANCE = Decimal("0.50")
ITEM_TOLERANCE = Decimal("0.02")

VALID_CONFIDENCE = ("high", "medium", "low")


# --------------------------------------------------------------------------- #
# Errores con texto amigable por categoría                                    #
# --------------------------------------------------------------------------- #
_FRIENDLY_ERRORS: dict[str, str] = {
    "size_exceeded": ("La imagen es demasiado grande para analizarla. Envía una foto más ligera, por favor."),
    "unsupported_format": "El formato de la imagen no está soportado (solo JPG, PNG o WebP).",
    "too_many_images": "Solo puedo analizar una imagen por mensaje. Envíame una a una, por favor.",
    "provider_unavailable": "No he podido analizar la imagen ahora mismo; el servicio de visión no está disponible. Inténtalo de nuevo en unos minutos.",
    "timeout": "El análisis de la imagen ha tardado demasiado. Inténtalo de nuevo en unos segundos.",
    "unreadable": "No he podido leer nada útil en la imagen. Envía un ticket, una lista de la compra, una lista de tareas o una nota bien iluminada y enfocada.",
    "invalid_json": "El análisis devolvió una respuesta no válida. Inténtalo de nuevo.",
    "invalid_response": "El análisis de la imagen no devolvió datos estructurados.",
    "insufficient_confidence": "No tengo suficiente confianza para proponerte nada de lo que veo en la imagen. Revisa que sea legible e inténtalo de nuevo.",
}


def friendly_image_error(exc: ImageExtractionError) -> str:
    """Mensaje de usuario para un error de extracción categorizado."""
    return _FRIENDLY_ERRORS.get(exc.category, exc.message)


# --------------------------------------------------------------------------- #
# Resultado del análisis                                                      #
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class ImageAnalysis:
    proposal: dict[str, Any]
    reply: str
    confidence: str
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Normalización de campos extraídos                                           #
# --------------------------------------------------------------------------- #
def _field(section: dict[str, Any] | None, key: str) -> tuple[str, str]:
    """Devuelve (value, confidence) de un campo del proveedor; robusto ante JSON raro."""
    if not isinstance(section, dict):
        return "", "low"
    leaf = section.get(key)
    if not isinstance(leaf, dict):
        return "", "low"
    value = str(leaf.get("value") or "")
    confidence = leaf.get("confidence")
    if confidence not in VALID_CONFIDENCE:
        confidence = "low"
    return value.strip(), confidence  # type: ignore[return-value]


_MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}
_RE_NUMERIC_DATE = re.compile(r"^(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})$")
_RE_ISO_DATE = re.compile(r"^(\d{4})[/-](\d{1,2})[/-](\d{1,2})$")
_RE_TEXT_DATE = re.compile(r"^(\d{1,2})\s+(?:de\s+)?([a-zA-Záéíóúñ]+)\.?\s+(?:de\s+)?(\d{2,4}|)$", re.IGNORECASE)


def normalize_extracted_date(raw: str | None) -> tuple[date | None, str | None]:
    """Normaliza una fecha de ticket a `date`; nunca inventa el año.

    Acepta ISO (2026-09-20), europeas (20/09/2026, 20-09-2026, 20.09.2026) y
    con nombre de mes en español ("20 de septiembre de 2026", "20 sep 2026").
    Si la fecha no trae año (o no se puede interpretar con seguridad) devuelve
    `None` con un aviso: el flujo NO asume un año en silencio.
    """
    if not raw:
        return None, None
    text = raw.strip().lower()
    text = re.sub(r"\s+", " ", text)
    if text.endswith(","):
        text = text[:-1]

    m = _RE_ISO_DATE.match(text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        parsed = _safe_date(year, month, day)
        return parsed, None if parsed else f"Fecha ilegible: {raw}"

    m = _RE_NUMERIC_DATE.match(text)
    if m:
        day, month, year_part = int(m.group(1)), int(m.group(2)), m.group(3)
        if len(year_part) == 2:
            # "20/09/26" → año ambiguo: no se asume una década en silencio.
            return None, f"La fecha '{raw}' no indica el año completo; no la doy por buena."
        year = int(year_part)
        parsed = _safe_date(year, month, day)
        return parsed, None if parsed else f"Fecha ilegible: {raw}"

    m = _RE_TEXT_DATE.match(text)
    if m:
        day = int(m.group(1))
        month_name = m.group(2).lower()
        year_text = m.group(3)
        if month_name not in _MONTHS_ES:
            return None, f"Fecha ilegible: {raw}"
        month = _MONTHS_ES[month_name]
        if not year_text:
            return None, f"La fecha '{raw}' no indica el año; no la doy por buena."
        if len(year_text) == 2:
            return None, f"La fecha '{raw}' no indica el año completo; no la doy por buena."
        parsed = _safe_date(int(year_text), month, day)
        return parsed, None if parsed else f"Fecha ilegible: {raw}"

    return None, f"No reconozco la fecha '{raw}'."


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


_RE_TIME = re.compile(r"^(\d{1,2})[:.](\d{2})(?::(\d{2}))?\s*([ap]\.?\s?m\.?)?$", re.IGNORECASE)


def normalize_extracted_time(raw: str | None) -> time | None:
    """Normaliza una hora de ticket ("18:42", "6:30 pm", "18.42")."""
    if not raw:
        return None
    m = _RE_TIME.match(raw.strip().lower())
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if m.group(4):
        period = m.group(4).replace(".", "").replace(" ", "")
        if period.startswith("p") and hour < 12:
            hour += 12
        elif period.startswith("a") and hour == 12:
            hour = 0
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


# --------------------------------------------------------------------------- #
# Validación del ticket: coherencia suma de líneas vs total                   #
# --------------------------------------------------------------------------- #
def _validate_ticket_items(items: list[dict[str, str]]) -> tuple[Decimal, list[str]]:
    """Valida líneas frente a total parcial y suma. Nunca lanza: avisa.

    Cada línea se convierte con `parse_extracted_amount`; las líneas ilegibles
    se descartan para el sumatorio (y avisan). Devuelve (suma_céntimos, avisos).
    """
    total_sum = Decimal("0.00")
    warnings: list[str] = []
    errores_lineas = 0
    for raw in items:
        description = (raw.get("description") or "").strip()
        total_price_raw = raw.get("total_price") or ""
        quantity_raw = raw.get("quantity") or ""
        unit_price_raw = raw.get("unit_price") or ""
        if total_price_raw:
            try:
                total_sum += parse_extracted_amount(total_price_raw)
            except ValueError:
                errores_lineas += 1
                continue
            if quantity_raw and unit_price_raw:
                try:
                    quantity = parse_quantity(quantity_raw)
                    unit_price = parse_extracted_amount(unit_price_raw)
                    if (
                        quantity is not None
                        and abs(quantity * unit_price - parse_extracted_amount(total_price_raw)) > ITEM_TOLERANCE
                    ):
                        warnings.append(
                            f"La línea '{description or total_price_raw}' no cuadra "
                            f"({quantity_raw} × {unit_price_raw} ≠ {total_price_raw})."
                        )
                except ValueError:
                    pass
        elif quantity_raw and unit_price_raw:
            try:
                quantity = parse_quantity(quantity_raw)
                unit_price = parse_extracted_amount(unit_price_raw)
                if quantity is not None:
                    total_sum += quantity * unit_price
            except ValueError:
                errores_lineas += 1
        else:
            errores_lineas += 1
    if errores_lineas:
        warnings.append(f"{errores_lineas} líneas del ticket no se pudieron leer por completo.")
    return total_sum, warnings


def parse_quantity(raw: str | None) -> Decimal | None:
    """Cantidad de una línea: "2", "1 kg", "2 unidades" → Decimal; ilegible → None."""
    if not raw:
        return None
    text = raw.strip() or ""
    if not text:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    digits = match.group(0)
    try:
        return parse_extracted_amount(digits)
    except ValueError:
        return None


def _warn_confidence(confidence: str, warning: str) -> str:
    """Degrada confianza si un campo válido dependía de un dato dudoso."""
    if warning and confidence == "high":
        return "medium"
    return confidence


# --------------------------------------------------------------------------- #
# Servicio de dominio                                                         #
# --------------------------------------------------------------------------- #
class ImageAnalysisService:
    def __init__(
        self,
        *,
        provider: ImageUnderstandingProvider,
        settings: Settings,
        transactions: Any,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._transactions = transactions

    # -- entrada principal -------------------------------------------------- #
    async def analyze(
        self,
        *,
        user_id: int,
        user_tz: str,
        attachments: list[ImageAttachment],
        caption: str | None = None,
    ) -> ImageAnalysis:
        from asistente.ai.vision import anonymized_user

        user_anon = anonymized_user(user_id)
        self._validate_limits(attachments)

        image = attachments[0]
        self._validate_image(image)

        started = utc_now()
        logger.info("image_analysis_started", user_anon=user_anon, size_bytes=image.size, mime_type=image.mime_type)
        try:
            raw = self._provider.understand(image=image, hint=caption)
            analysis = self._normalize(raw, user_tz=user_tz)
        except ImageExtractionError as exc:
            logger.warning(
                "image_analysis_failed",
                user_anon=user_anon,
                error=exc.category,
                duration_ms=int((utc_now() - started).total_seconds() * 1000),
            )
            raise
        except Exception as exc:  # noqa: BLE001 - errores de proveedor nunca rompen el bot
            logger.warning(
                "image_analysis_failed",
                user_anon=user_anon,
                error="provider_exception",
                duration_ms=int((utc_now() - started).total_seconds() * 1000),
            )
            raise ImageExtractionError("provider_unavailable", f"error inesperado del proveedor: {exc}") from exc

        if analysis.proposal["kind"] == "ticket":
            await self._warn_possible_duplicate(user_id, analysis)

        logger.info(
            "image_analysis_completed",
            user_anon=user_anon,
            kind=analysis.proposal["kind"],
            confidence=analysis.confidence,
            duration_ms=int((utc_now() - started).total_seconds() * 1000),
        )
        return analysis

    # -- límites ------------------------------------------------------------ #
    def _validate_limits(self, attachments: list[ImageAttachment]) -> None:
        max_images = self._settings.image_max_per_message
        if not attachments:
            raise ImageExtractionError("unreadable", "No hay imagen que analizar.")
        if len(attachments) > max_images:
            raise ImageExtractionError(
                "too_many_images",
                f"Máximo {max_images} imagen(es) por mensaje.",
            )

    def _validate_image(self, image: ImageAttachment) -> None:
        if image.mime_type not in self._settings.image_allowed_mime_types:
            raise ImageExtractionError(
                "unsupported_format",
                f"Formato no soportado: {image.mime_type or 'desconocido'}.",
            )
        if image.size > self._settings.image_max_size_bytes:
            max_mb = self._settings.image_max_size_mb
            raise ImageExtractionError(
                "size_exceeded",
                f"La imagen supera el límite de {max_mb} MB.",
            )
        if not image.data:
            raise ImageExtractionError("unreadable", "La imagen está vacía.")

    # -- normalización y propuesta ----------------------------------------- #
    def _normalize(self, raw: dict[str, Any], *, user_tz: str) -> ImageAnalysis:
        if not isinstance(raw, dict) or "kind" not in raw:
            raise ImageExtractionError("invalid_response", "El análisis no devolvió datos estructurados con tipo.")
        kind = raw.get("kind")
        if kind == "unknown" or kind not in ("ticket", "shopping_list", "task_list", "structured_note"):
            raise ImageExtractionError("unreadable", "La imagen no contiene un ticket, lista o nota reconocible.")

        warnings = _clean_strings(raw.get("warnings"))
        if kind == "ticket":
            return self._normalize_ticket(raw, warnings, user_tz=user_tz)
        if kind == "shopping_list":
            return self._normalize_shopping_list(raw, warnings)
        if kind == "task_list":
            return self._normalize_tasks(raw, warnings)
        return self._normalize_note(raw, warnings)

    def _normalize_ticket(self, raw: dict[str, Any], warnings: list[str], *, user_tz: str) -> ImageAnalysis:
        section = _as_dict(raw.get("ticket"))
        merchant_v, merchant_c = _field(section, "merchant")
        total_v, total_c = _field(section, "total")
        date_v, date_c = _field(section, "date")
        time_v, _ = _field(section, "time")
        currency_v, _ = _field(section, "currency")
        items = [_clean_item_leaf(i) for i in _as_list(section.get("items")) if isinstance(i, dict)]

        if currency_v.strip() and currency_v.strip().upper() != "EUR":
            raise ImageExtractionError(
                "insufficient_confidence",
                f"El ticket está en '{currency_v}' y solo soporto gastos en EUR.",
            )

        # Importes → Decimal (nunca float). Si no cuadra, se degrada la confianza.
        total: Decimal | None = None
        if total_v:
            try:
                total = parse_extracted_amount(total_v)
            except ValueError as exc:
                warnings.append(str(exc))
                total_c = "low"
                total_v = ""

        sum_items, item_warnings = _validate_ticket_items(items)
        warning_sum = ""
        if total is not None and sum_items:
            discrepancy = abs(sum_items - total)
            if discrepancy > TICKET_TOTAL_TOLERANCE:
                warning_sum = (
                    f"La suma de las líneas legibles ({fmt_amount_eu(sum_items)} €) no coincide "
                    f"con el total del ticket ({fmt_amount_eu(total)} €)."
                )
                total_c = "low"
            elif discrepancy > 0:
                warning_sum = (
                    f"La suma de las líneas ({fmt_amount_eu(sum_items)} €) difiere ligeramente "
                    f"del total ({fmt_amount_eu(total)} €)."
                )
                total_c = _warn_confidence(total_c, warning_sum)
        warnings.extend(item_warnings)
        if warning_sum:
            warnings.append(warning_sum)

        # Fechas: nunca asumir año perdido.
        ticket_date, date_warning = normalize_extracted_date(date_v)
        if date_warning:
            warnings.append(date_warning)
            date_c = "low"
        ticket_time = normalize_extracted_time(time_v)
        if ticket_time is None and time_v:
            warnings.append(f"No reconozco la hora '{time_v}'.")

        tz = user_tz or self._settings.default_timezone
        occurred_utc = _to_occurred_utc(ticket_date, ticket_time, tz)
        if occurred_utc is None:
            warnings.append("El ticket no muestra una fecha fiable completa; no la doy por buena sin confirmarla.")

        merchant = merchant_v or None
        if not merchant:
            warnings.append("No he podido leer bien el comercio del ticket.")
            merchant_c = "low"

        overall = _overall_confidence(total_c, merchant_c, date_c, total is not None)
        max_chars = self._settings.image_max_extracted_chars
        proposal: dict[str, Any] = {
            "kind": "ticket",
            "expense": {
                "amount": fmt_amount(total) if total is not None else None,
                "merchant": merchant,
                "occurred_at": occurred_utc.isoformat() if occurred_utc else None,
                "category": None,
                "note": _ticket_note(merchant, len(items)),
            },
            "display": {
                "merchant": merchant or "¿?",
                "date": date_v or "",
                "time": time_v or "",
                "total": fmt_amount_eu(total) + " €" if total is not None else "¿?",
                "items": _display_items(items, max_chars),
                "sum_items": fmt_amount_eu(sum_items) if sum_items else None,
            },
            "confidence": overall,
            "warnings": warnings[:10],
        }
        reply = _ticket_reply(proposal)
        return ImageAnalysis(proposal=proposal, reply=reply, confidence=overall, warnings=warnings)

    def _normalize_shopping_list(self, raw: dict[str, Any], warnings: list[str]) -> ImageAnalysis:
        section = _as_dict(raw.get("shopping_list"))
        title_v, _ = _field(section, "title")
        items = []
        for i in _as_list(section.get("items")):
            if not isinstance(i, dict):
                continue
            name, _ = _field(i, "name")
            if not name:
                warnings.append("Una línea de la lista de la compra no se pudo leer.")
                continue
            quantity, _ = _field(i, "quantity")
            notes, _ = _field(i, "notes")
            items.append({"name": name[:200], "quantity": quantity[:50] or "", "notes": notes[:300] or ""})
        if not items:
            raise ImageExtractionError("unreadable", "La lista de la compra no tiene líneas legibles.")

        title = title_v.strip() or f"Lista de la compra del {date.today().isoformat()}"
        proposal: dict[str, Any] = {
            "kind": "shopping_list",
            "shopping_list": {
                "title": title[:200],
                "items": items,
            },
            "confidence": "high" if not warnings else "medium",
            "warnings": warnings[:10],
        }
        return ImageAnalysis(
            proposal=proposal,
            reply=_shopping_list_reply(proposal),
            confidence=proposal["confidence"],
            warnings=warnings,
        )

    def _normalize_tasks(self, raw: dict[str, Any], warnings: list[str]) -> ImageAnalysis:
        section = _as_dict(raw.get("task_list"))
        tasks = []
        for i in _as_list(section.get("tasks")):
            if not isinstance(i, dict):
                continue
            title, _ = _field(i, "title")
            if not title:
                warnings.append("Una tarea de la imagen no se pudo leer.")
                continue
            due_v, due_c = _field(i, "due_date")
            priority, _ = _field(i, "priority")
            due_date, due_warning = normalize_extracted_date(due_v)
            if due_warning:
                warnings.append(due_warning)
            tasks.append(
                {
                    "title": title[:300],
                    "due_date": due_date.isoformat() if due_date else None,
                    "priority": (priority or "").strip()[:50] or None,
                }
            )
        if not tasks:
            raise ImageExtractionError("unreadable", "La lista de tareas no tiene títulos legibles.")
        proposal: dict[str, Any] = {
            "kind": "tasks",
            "tasks": tasks,
            "confidence": "high" if not warnings else "medium",
            "warnings": warnings[:10],
        }
        return ImageAnalysis(
            proposal=proposal,
            reply=_tasks_reply(proposal),
            confidence=proposal["confidence"],
            warnings=warnings,
        )

    def _normalize_note(self, raw: dict[str, Any], warnings: list[str]) -> ImageAnalysis:
        section = _as_dict(raw.get("structured_note"))
        title_v, _ = _field(section, "title")
        content_v, _ = _field(section, "content")
        if not content_v:
            raise ImageExtractionError("unreadable", "La nota no tiene contenido legible.")
        max_chars = self._settings.image_max_extracted_chars
        content = content_v[:max_chars]
        title = title_v.strip() or "Nota"
        proposal: dict[str, Any] = {
            "kind": "structured_note",
            "structured_note": {
                "title": title[:200],
                "content": content,
            },
            "confidence": "high" if not warnings else "medium",
            "warnings": warnings[:10],
        }
        return ImageAnalysis(
            proposal=proposal,
            reply=_note_reply(proposal),
            confidence=proposal["confidence"],
            warnings=warnings,
        )

    # -- duplicados --------------------------------------------------------- #
    async def _warn_possible_duplicate(self, user_id: int, analysis: ImageAnalysis) -> None:
        expense = analysis.proposal.get("expense") or {}
        merchant = expense.get("merchant")
        amount_raw = expense.get("amount")
        occurred_raw = expense.get("occurred_at")
        if not merchant or not amount_raw or not occurred_raw:
            return
        amount = Decimal(amount_raw)
        occurred = datetime.fromisoformat(occurred_raw)
        start = start_of_day(to_local(occurred, "UTC"))
        end = start + timedelta(days=1)
        rows = await self._transactions.list(
            user_id,
            start_utc=start,
            end_utc=end,
            transaction_type="expense",
            merchant=merchant,
            limit=100,
        )
        for txn in rows:
            if Decimal(txn.amount) == amount:
                txns_iso = to_local(txn.occurred_at, "UTC").strftime("%d/%m/%Y") if txn.occurred_at else "?"
                warning = (
                    f"Posible duplicado: ya tienes un gasto de {fmt_amount_eu(amount)} € en "
                    f"{merchant} el {txns_iso}. No bloqueo nada; confirma si quieres registrarlo."
                )
                analysis.warnings.append(warning)
                analysis.reply += f"\n⚠ Aviso: {warning}"
                if len(analysis.warnings) <= 10:
                    analysis.proposal["warnings"] = analysis.warnings[:10]
                return


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #
def _clean_strings(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:300])
    return out


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_item_leaf(i: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("description", "quantity", "unit_price", "total_price", "name", "notes"):
        leaf = i.get(key) if isinstance(i.get(key), dict) else {}
        out[key] = str(leaf.get("value") or "").strip() if leaf else str(i.get(key) or "").strip()
    return out


def _to_occurred_utc(ticket_date: date | None, ticket_time: time | None, tz_name: str) -> datetime | None:
    if ticket_date is None:
        return None
    from zoneinfo import ZoneInfo

    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001
        tz = ZoneInfo("Europe/Madrid")
    if ticket_time is not None:
        local = datetime.combine(ticket_date, ticket_time, tzinfo=tz)
    else:
        local = datetime.combine(ticket_date, time(0, 0), tzinfo=tz)
    return to_utc(local)


def _overall_confidence(total_c: str, merchant_c: str, date_c: str, has_total: bool) -> str:
    if not has_total:
        return "low"
    ranking = {"high": 0, "medium": 1, "low": 2}
    worst = max(ranking[total_c], ranking[merchant_c], ranking[date_c])
    return ("high", "medium", "low")[worst]


def _ticket_note(merchant: str | None, line_count: int) -> str:
    base = f"Ticket de {merchant}" if merchant else "Ticket"
    return f"{base} · {line_count} líneas"


def _display_items(items: list[dict[str, str]], max_chars: int) -> list[str]:
    lines: list[str] = []
    budget = max_chars
    for item in items:
        description = item.get("description") or item.get("name") or ""
        qty = item.get("quantity") or ""
        total = item.get("total_price") or item.get("unit_price") or ""
        line = "".join(
            [
                f"{qty}x " if qty else "",
                description,
                f" — {total}" if total else "",
            ]
        ).strip()
        if not line:
            continue
        budget -= len(line) + 1
        if budget < 0:
            lines.append("…")
            break
        lines.append(line)
    if not lines:
        lines = ["(sin líneas legibles)"]
    return lines[:30]


def _bullet(value: str, confident: bool) -> str:
    suffix = "" if confident else "  (no lo tengo claro)"
    return f"• {value}{suffix}"


def _ticket_reply(proposal: dict[str, Any]) -> str:
    expense = proposal["expense"]
    display = proposal["display"]
    lines: list[str] = ["He leído tu ticket y esto es lo que he extraído:"]
    merchant = expense.get("merchant")
    lines.append(_bullet(f"Comercio: {merchant or '¿?'}", bool(merchant)))
    total = expense.get("amount")
    lines.append(_bullet(f"Total: {fmt_amount_eu(Decimal(total)) if total else '¿?'} €", bool(total)))
    date_text = display.get("date") or ""
    time_text = display.get("time") or ""
    lines.append(_bullet(f"Fecha: {date_text} {time_text}".strip(), bool(display.get("date"))))
    items = display.get("items") or []
    if items:
        preview = ", ".join(items[:5])
        lines.append(f"Productos: {preview}")
    if display.get("sum_items"):
        lines.append(f"Suma de líneas legibles: {display['sum_items']} €")
    for warning in proposal.get("warnings") or []:
        lines.append(f"⚠ Aviso: {warning}")
    if total:
        lines.append(f"¿Registro este gasto de {fmt_amount_eu(Decimal(total))} € en {merchant or 'el ticket'}?")
    else:
        lines.append("No tengo un total fiable. ¿Me indicas cuánto fue?")
    return "\n".join(lines)


def _shopping_list_reply(proposal: dict[str, Any]) -> str:
    items = proposal["shopping_list"]["items"]
    lines = [f"He extraído tu lista de la compra ({len(items)} líneas):"]
    for item in items[:10]:
        line = item["name"]
        if item.get("quantity"):
            line += f" — {item['quantity']}"
        lines.append(f"• {line}")
    if len(items) > 10:
        lines.append(f"… y {len(items) - 10} más")
    for warning in proposal.get("warnings") or []:
        lines.append(f"⚠ Aviso: {warning}")
    lines.append("¿La guardo como nota?")
    return "\n".join(lines)


def _tasks_reply(proposal: dict[str, Any]) -> str:
    tasks = proposal["tasks"]
    lines = [f"He extraído tu lista de tareas ({len(tasks)}):"]
    for task in tasks[:10]:
        line = task["title"]
        if task.get("due_date"):
            line += f" — vence {task['due_date']}"
        if task.get("priority"):
            line += f" ({task['priority']})"
        lines.append(f"• {line}")
    if len(tasks) > 10:
        lines.append(f"… y {len(tasks) - 10} más")
    for warning in proposal.get("warnings") or []:
        lines.append(f"⚠ Aviso: {warning}")
    lines.append("¿Las creo en tu agenda?")
    return "\n".join(lines)


def _note_reply(proposal: dict[str, Any]) -> str:
    lines = [f"He leído tu nota: «{proposal['structured_note']['title']}»"]
    for warning in proposal.get("warnings") or []:
        lines.append(f"⚠ Aviso: {warning}")
    lines.append("¿La guardo?")
    return "\n".join(lines)
