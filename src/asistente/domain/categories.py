"""Categorías básicas de movimientos (Fase 3).

Estructura mínima que permitirá categorías configurables en fases
posteriores. Hoy el conjunto es fijo y la asignación es manual o por
palabras clave sencillas del `merchant` ("mercadona" → Alimentación,
"netflix" → Suscripciones). NO hay aún reglas/memoria de personalización
("mercadona siempre alimentación" llega en su fase).
"""

from __future__ import annotations

CATEGORIES: tuple[str, ...] = (
    "Alimentación",
    "Transporte",
    "Vivienda",
    "Salud",
    "Suscripciones",
    "Belleza",
    "Ocio",
    "Compras",
    "Educación",
    "Viajes",
    "Otros",
)

DEFAULT_CATEGORY = "Otros"

# (categoría, palabras clave). El ordenden importa: se comprueba en este orden
# y gana la primera coincidencia (ej. "amazon prime" → Suscripciones antes que
# la genérica "amazon" → Compras). La comparación ignora acentos y mayúsculas.
_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Alimentación",
        (
            "mercadona",
            "carrefour",
            "lidl",
            "aldi",
            "dia ",
            "supermercado",
            "hipermercado",
            "consum",
            "charcutería",
            "panadería",
            "frutería",
            "carnicería",
            "pescadería",
            "verduras",
        ),
    ),
    (
        "Transporte",
        (
            "gasolina",
            "gasoil",
            "diésel",
            "diesel",
            "combustible",
            "carburante",
            "repostar",
            "parking",
            "aparcamiento",
            "peaje",
            "taxi",
            "uber",
            "bolt",
            "cabify",
            "transporte",
            "renfe",
            "autobús",
            "metro",
            "cercanías",
            "billete",
        ),
    ),
    (
        "Vivienda",
        (
            "alquiler",
            "hipoteca",
            "comunidad",
            "ibi",
            "luz",
            "electricidad",
            "agua",
            "butano",
            "gas natural",
            "seguro de hogar",
        ),
    ),
    (
        "Salud",
        (
            "farmacia",
            "médico",
            "dentista",
            "dental",
            "óptica",
            "fisio",
            "psicólogo",
            "hospital",
            "clínica",
            "seguro médico",
        ),
    ),
    (
        "Suscripciones",
        (
            "netflix",
            "spotify",
            "disney",
            "hbo",
            "amazon prime",
            "prime video",
            "youtube",
            "suscripción",
            "icloud",
            "apple one",
            "streaming",
        ),
    ),
    ("Belleza", ("peluquería", "barbería", "estética", "manicura", "uñas", "cosmética", "perfumería")),
    ("Ocio", ("cine", "teatro", "concierto", "restaurante", "cafetería", "bar", "gimnasio", "discoteca")),
    (
        "Compras",
        (
            "amazon",
            "zara",
            "decathlon",
            "ikea",
            "leroy",
            "media markt",
            "fnac",
            "corte inglés",
            "ropa",
            "zapatos",
            "ferretería",
            "electrónica",
            "shein",
            "aliexpress",
        ),
    ),
    ("Educación", ("curso", "academia", "matrícula", "libros", "librería", "universidad", "colegio", "formación")),
    ("Viajes", ("vuelo", "avión", "hotel", "airbnb", "booking", "viaje", "albergue", "hostel")),
)

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def _fold(text: str) -> str:
    return (text or "").strip().lower().translate(_ACCENTS)


def normalize_category(category: str | None) -> str:
    """Normaliza una categoría al conjunto admitido; None/duda → Otros."""
    if not category:
        return DEFAULT_CATEGORY
    folded = _fold(category)
    for known in CATEGORIES:
        if _fold(known) == folded:
            return known
    return DEFAULT_CATEGORY


def classify_category(merchant: str | None, suggested: str | None = None) -> str:
    """Asigna la categoría: preferencia explícita, luego palabras clave."""
    if suggested:
        normalized = normalize_category(suggested)
        if normalized != DEFAULT_CATEGORY:
            return normalized
    if merchant:
        folded = _fold(merchant)
        for category, keywords in _KEYWORDS:
            for word in keywords:
                if _fold(word) in folded:
                    return category
    return DEFAULT_CATEGORY
