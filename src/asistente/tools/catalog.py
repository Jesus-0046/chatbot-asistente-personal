"""Catálogo de tools del modo local: núcleo fijo y grupos bajo demanda.

El proveedor local tiene una ventana de 4096 tokens. No caben las 135 tools,
así que el modo local anuncia:

  - un NÚCLEO fijo de 22 tools (siempre presente, en este orden exacto),
  - uno o varios GRUPOS especializados que casan con el mensaje del usuario.

El orden del núcleo es deliberado: si un grupo no cabe, el compositor elimina
el último grupo enrutado (nunca toca el núcleo). El registro sigue teniendo las
135 tools: aquí solo se decide qué se anuncia al modelo.

Las 19 tools que no están en ningún grupo se documentan en
`NOT_IN_LOCAL_CATALOG` con el motivo. `apply_extracted_data` es especial: solo
la usa el flujo de imagen (nunca se anuncia al modelo).
"""

from __future__ import annotations

from collections.abc import Iterable

# Núcleo fijo (22). El orden es estable a propósito: el prompt [system][tools]
# debe conservar el prefijo entre turnos para reutilizar la cache del servidor.
CORE_TOOL_NAMES: tuple[str, ...] = (
    # Resúmenes de vida
    "get_daily_summary",
    "get_weekly_summary",
    "get_upcoming_summary",
    "get_pending_summary",
    # Agenda (lectura y escritura básica)
    "list_events",
    "list_tasks",
    "list_reminders",
    "create_event",
    "update_event",
    "create_task",
    "update_task",
    "complete_task",
    "create_reminder",
    # Cuentas y movimientos
    "list_accounts",
    "get_balance_overview",
    "list_transactions",
    "create_expense",
    "spending_summary",
    # Presupuestos y pagos próximos
    "get_budget_status",
    "get_upcoming_payments",
    # Notas
    "create_note",
    "get_notes",
)

# Grupos especializados. Cada tool aparece como mucho en un grupo y ninguno
# repite tools del núcleo (lo verifica `validate_catalog`).
TOOL_GROUPS: dict[str, tuple[str, ...]] = {
    "agenda_extra": (
        "get_event",
        "delete_event",
        "cancel_task",
        "cancel_reminder",
        "get_agenda",
        "get_current_datetime",
    ),
    "habits": (
        "create_habit",
        "list_habits",
        "update_habit",
        "deactivate_habit",
        "log_habit",
        "get_habit_status",
    ),
    "subscriptions": (
        "create_subscription",
        "list_subscriptions",
        "update_subscription",
        "cancel_subscription",
    ),
    "trips_read": (
        "list_trips",
        "get_trip",
        "list_trip_checklist",
        "get_trip_financial_summary",
    ),
    "trips_edit": (
        "create_trip",
        "update_trip",
        "cancel_trip",
        "complete_trip",
        "add_trip_checklist_item",
        "toggle_trip_checklist_item",
        "delete_trip_checklist_item",
    ),
    "trips_links": (
        "link_transactions_to_trip",
        "unlink_transactions_from_trip",
        "link_task_to_trip",
        "unlink_task_from_trip",
        "link_event_to_trip",
        "unlink_event_from_trip",
        "link_reminder_to_trip",
        "unlink_reminder_from_trip",
    ),
    "finance_accounts": (
        "create_account",
        "update_account",
        "set_default_account",
        "deactivate_account",
        "create_income",
        "create_transfer",
        "get_transfer_history",
    ),
    "planned_recurring": (
        "create_recurring_transaction",
        "update_recurring_transaction",
        "cancel_recurring_transaction",
        "pause_recurring_transaction",
        "resume_recurring_transaction",
        "list_recurring_transactions",
        "create_planned_transaction",
        "cancel_planned_transaction",
        "confirm_planned_transaction",
        "list_planned_transactions",
        "get_upcoming_income",
    ),
    "budgets_savings": (
        "create_budget",
        "update_budget",
        "delete_budget",
        "list_budgets",
        "create_savings_goal",
        "cancel_savings_goal",
        "list_savings_goals",
        "record_savings_contribution",
        "get_savings_goal_status",
    ),
    "intelligence": (
        "get_financial_overview",
        "get_estimated_available",
        "get_spending_capacity",
        "analyze_spending",
        "get_spending_changes",
        "detect_unusual_expenses",
        "get_largest_expenses",
        "get_recurring_spending_summary",
        "get_savings_overview",
        "get_financial_budget_overview",
        "get_month_end_projection",
        "compare_financial_periods",
    ),
    "people": (
        "add_person",
        "list_people",
        "update_person",
        "deactivate_person",
        "record_debt",
        "settle_debt",
        "get_debts_status",
        "create_shared_expense",
        "cancel_shared_expense",
        "get_shared_expense_status",
    ),
    "vehicles": (
        "add_vehicle",
        "list_vehicles",
        "update_vehicle",
        "deactivate_vehicle",
        "add_vehicle_insurance",
        "update_vehicle_insurance",
        "get_vehicle_status",
        "get_vehicle_expirations",
        "record_fuel",
        "record_vehicle_maintenance",
    ),
}

# Orden canónico de anuncio: núcleo primero y grupos en este orden. Se usa
# también para que los conjuntos anidados compartan prefijo.
GROUP_ORDER: tuple[str, ...] = (
    "agenda_extra",
    "habits",
    "subscriptions",
    "trips_read",
    "trips_edit",
    "trips_links",
    "finance_accounts",
    "planned_recurring",
    "budgets_savings",
    "intelligence",
    "people",
    "vehicles",
)

# Prioridad de enrutado (de más a menos prioritario). El compositor descarta
# por el final de esta lista cuando el prompt no cabe.
ROUTE_PRIORITY: tuple[str, ...] = (
    "habits",
    "subscriptions",
    "vehicles",
    "people",
    "trips_read",
    "trips_edit",
    "trips_links",
    "finance_accounts",
    "planned_recurring",
    "budgets_savings",
    "agenda_extra",
    "intelligence",
)

# Dominios funcionales del modo local (agrupación para el informe y los tests).
DOMAINS: dict[str, tuple[str, ...]] = {
    "habitos": ("habits",),
    "suscripciones": ("subscriptions",),
    "viajes": ("trips_read", "trips_edit", "trips_links"),
    "agenda_ampliada": ("agenda_extra",),
    "finanzas_ampliadas": (
        "finance_accounts",
        "planned_recurring",
        "budgets_savings",
        "intelligence",
    ),
    "personas": ("people",),
    "vehiculos": ("vehicles",),
}

# Tool que solo usa el flujo de imagen: nunca se anuncia al modelo.
IMAGE_ONLY_TOOL = "apply_extracted_data"

# Tools del registro que NO entran en el catálogo local y por qué.
NOT_IN_LOCAL_CATALOG: dict[str, str] = {
    # Notas: el modo local crea y consulta; estas son destructivas/de detalle.
    "pin_note": "notas: acción de organización no esencial en local",
    "unpin_note": "notas: acción de organización no esencial en local",
    "delete_note": "notas: acción destructiva no esencial en local",
    # Memoria: el asistente no gestiona reglas por conversación.
    "propose_memory_rule": "memoria: requiere un flujo de confirmación aparte",
    "activate_memory_rule": "memoria: requiere un flujo de confirmación aparte",
    "list_memory_rules": "memoria: la memoria activa ya viaja en el sistema",
    "revoke_memory_rule": "memoria: requiere un flujo de confirmación aparte",
    # Hogar: dominio completo fuera del presupuesto del modo local.
    "add_home": "hogar: dominio no incluido en el modo local",
    "list_homes": "hogar: dominio no incluido en el modo local",
    "update_home": "hogar: dominio no incluido en el modo local",
    "deactivate_home": "hogar: dominio no incluido en el modo local",
    "record_home_maintenance": "hogar: dominio no incluido en el modo local",
    "get_home_status": "hogar: dominio no incluido en el modo local",
    "get_home_upcoming": "hogar: dominio no incluido en el modo local",
    # Imagen: solo se ejecuta tras confirmar una extracción del sistema.
    IMAGE_ONLY_TOOL: "imagen: solo lo usa el flujo de imagen, nunca el modelo",
    # Proactividad: configuración, no conversación.
    "get_proactivity_settings": "proactividad: configuración fuera del modo local",
    "update_proactivity_settings": "proactividad: configuración fuera del modo local",
    # Canales: vinculación de identidades, no conversación.
    "link_whatsapp": "canales: vinculación de identidad, no conversacional",
    "link_web": "canales: vinculación de identidad, no conversacional",
}

# Frases que activan cada grupo (normalizadas: minúsculas y sin acentos).
GROUP_KEYWORDS: dict[str, tuple[str, ...]] = {
    "agenda_extra": (
        "cancela el recordatorio",
        "cancelar el recordatorio",
        "quita el recordatorio",
        "elimina el recordatorio",
        "cancela la tarea",
        "cancelar la tarea",
        "quita la tarea",
        "elimina la tarea",
        "borra el evento",
        "borrar el evento",
        "elimina el evento",
        "quitar el evento",
        "cancela el evento",
        "detalle del evento",
        "detalles del evento",
        "ver el evento",
        "info del evento",
        "agenda completa",
        "toda la agenda",
        "mi agenda",
        "ensename la agenda",
        "muestra la agenda",
        "que hora es",
        "que dia es hoy",
        "fecha de hoy",
        "hora actual",
    ),
    "habits": (
        "habito",
        "habitos",
        "racha",
        "rachas",
        "beber agua",
        "vasos de agua",
        "tomar agua",
        "litros de agua",
        "ejercicio",
        "entrenar",
        "entrenamiento",
        "caminar",
        "correr",
        "meditar",
        "meditacion",
        "gimnasio",
        "pasos",
        "marcar habito",
        "registrar habito",
        "cumplimiento de habitos",
    ),
    "subscriptions": (
        "suscripcion",
        "suscripciones",
        "netflix",
        "spotify",
        "hbo",
        "disney",
        "prime video",
        "membresia",
        "membresias",
    ),
    "trips_read": (
        "viaje",
        "viajes",
        "vacaciones",
        "escapada",
        "itinerario",
        "itinerarios",
        "que viajes",
        "mis viajes",
        "checklist del viaje",
        "lista del viaje",
        "gastos del viaje",
        "resumen del viaje",
    ),
    "trips_edit": (
        "crear viaje",
        "crea un viaje",
        "nuevo viaje",
        "nueva escapada",
        "planear viaje",
        "planificar viaje",
        "organizar viaje",
        "completar viaje",
        "terminar viaje",
        "cancelar viaje",
        "borrar viaje",
        "eliminar viaje",
        "cambiar viaje",
        "editar viaje",
        "anadir a la checklist",
        "anade a la checklist",
        "agregar a la checklist",
        "marcar la checklist",
        "marcar en la checklist",
    ),
    "trips_links": (
        "vincular al viaje",
        "vincula al viaje",
        "vincular con el viaje",
        "asociar al viaje",
        "asocia al viaje",
        "asociar con el viaje",
        "enlazar con el viaje",
        "enlaza con el viaje",
        "desvincular del viaje",
        "desvincula del viaje",
        "apunta el gasto en el viaje",
        "apuntar el gasto en el viaje",
        "anade la tarea al viaje",
        "anade el evento al viaje",
        "anade el recordatorio al viaje",
        "vincula el gasto al viaje",
    ),
    "finance_accounts": (
        "cuenta bancaria",
        "cuentas bancarias",
        "mis cuentas",
        "crear cuenta",
        "nueva cuenta",
        "cuenta principal",
        "cuenta por defecto",
        "transferencia",
        "transferencias",
        "traspaso",
        "traspasos",
        "hacer un traspaso",
        "historial de transferencias",
        "ingreso",
        "ingresos",
        "nomina",
        "salario",
    ),
    "planned_recurring": (
        "planificado",
        "planificados",
        "pago planificado",
        "pagos planificados",
        "pago programado",
        "pagos programados",
        "recurrente",
        "recurrentes",
        "recurrencia",
        "recurrencias",
        "alquiler",
        "hipoteca",
        "confirmar el pago",
        "confirmar el alquiler",
        "pausar la recurrencia",
        "reanudar la recurrencia",
        "proximo ingreso",
        "proximos ingresos",
    ),
    "budgets_savings": (
        "presupuesto",
        "presupuestos",
        "objetivo de ahorro",
        "objetivos de ahorro",
        "meta de ahorro",
        "metas de ahorro",
        "ahorrar",
        "ahorro",
        "aportacion",
        "aportaciones",
        "aportar al objetivo",
    ),
    "intelligence": (
        "prevision",
        "previsiones",
        "proyeccion",
        "proyecciones",
        "capacidad de gasto",
        "cuanto puedo gastar",
        "dinero disponible",
        "gastos inusuales",
        "gasto inusual",
        "patrones de gasto",
        "analiza mis gastos",
        "analisis de gastos",
        "comparar meses",
        "comparativa",
        "fin de mes",
        "resumen financiero",
        "salud financiera",
        "estimacion",
    ),
    "people": (
        "persona",
        "personas",
        "deuda",
        "deudas",
        "me debe",
        "me deben",
        "le debo",
        "les debo",
        "prestamo",
        "prestamos",
        "gasto compartido",
        "gastos compartidos",
        "compartir gasto",
        "dividir la cuenta",
        "repartir la cuenta",
        "quien me debe",
        "quien debe",
    ),
    "vehicles": (
        "coche",
        "coches",
        "vehiculo",
        "vehiculos",
        "moto",
        "motos",
        "gasolina",
        "combustible",
        "repostar",
        "repostaje",
        "itv",
        "seguro del coche",
        "seguro del vehiculo",
        "mantenimiento del coche",
        "revision del coche",
        "aceite",
        "ruedas",
        "neumaticos",
    ),
}

# Verbos que, junto a una mención de viaje, activan también la edición.
TRIP_EDIT_VERBS: tuple[str, ...] = (
    "crear",
    "crea",
    "nuevo",
    "nueva",
    "planear",
    "planea",
    "planificar",
    "planifica",
    "organizar",
    "organiza",
    "completar",
    "completa",
    "terminar",
    "termina",
    "cancelar",
    "cancela",
    "borrar",
    "borra",
    "eliminar",
    "elimina",
    "cambiar",
    "cambia",
    "editar",
    "edita",
    "marcar",
    "marca",
)

# Verbos/frases que, junto a una mención de viaje, activan los vínculos.
TRIP_LINK_VERBS: tuple[str, ...] = (
    "vincula",
    "vincular",
    "asocia",
    "asociar",
    "enlaza",
    "enlazar",
    "desvincula",
    "desvincular",
    "apunta el gasto",
    "apuntar el gasto",
    "anade la tarea",
    "anade el evento",
    "anade el recordatorio",
    "agrega la tarea",
    "agrega el evento",
    "agrega el recordatorio",
)


def group_of_tool(name: str) -> str | None:
    """Grupo al que pertenece una tool (None si es del núcleo o no está)."""
    for group, names in TOOL_GROUPS.items():
        if name in names:
            return group
    return None


def groups_of_tool(name: str) -> tuple[str, ...]:
    """Grupos que contienen la tool (como mucho uno por diseño)."""
    return tuple(group for group, names in TOOL_GROUPS.items() if name in names)


def tool_names_for_groups(groups: Iterable[str]) -> tuple[str, ...]:
    """Núcleo + grupos en orden canónico, sin duplicados."""
    selected = set(groups)
    names: list[str] = list(CORE_TOOL_NAMES)
    for group in GROUP_ORDER:
        if group in selected:
            names.extend(TOOL_GROUPS[group])
    return tuple(dict.fromkeys(names))


def catalog_names() -> tuple[str, ...]:
    """Todas las tools anunciables (núcleo + grupos), sin duplicados."""
    return tool_names_for_groups(GROUP_ORDER)


def validate_catalog(registry_names: Iterable[str]) -> list[str]:
    """Comprueba la cobertura completa del registro y devuelve los errores.

    Debe cumplirse: núcleo + grupos + excluidas == registro, sin duplicados.
    """
    registry = set(registry_names)
    errors: list[str] = []

    seen: dict[str, str] = {}
    for name in CORE_TOOL_NAMES:
        if name in seen:
            errors.append(f"duplicada en el catálogo: {name}")
        seen[name] = "nucleo"
    for group, names in TOOL_GROUPS.items():
        for name in names:
            if name in seen:
                errors.append(f"duplicada: {name} ({seen[name]} y {group})")
            seen[name] = group

    for name in NOT_IN_LOCAL_CATALOG:
        if name in seen:
            errors.append(f"excluida pero también anunciada: {name}")
        seen[name] = "excluida"

    missing = registry - set(seen)
    unknown = set(seen) - registry
    errors.extend(f"sin clasificar: {name}" for name in sorted(missing))
    errors.extend(f"no existe en el registro: {name}" for name in sorted(unknown))
    return errors
