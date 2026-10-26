"""Modelos SQLAlchemy — Fase 1, 2, 2.1, 3, 3.3, 4, 6, 6.2 y 9.

Fase 9 (WhatsApp / canal-agnóstico):

- `user_channels`            identidad del usuario por canal (telegram/whatsapp);
                             la única fuente de verdad `(channel, external_user_id)`.
- `processed_inbound_events` idempotencia de mensajes entrantes (wamid).

Fase 1 (núcleo de conversación):

- `users`                 identidad y perfil (timezone, idioma, settings)
- `conversation_messages` memoria corta (ventana móvil)
- `pending_confirmations` confirmaciones pendientes (estado machine)
- `memory_rules`          reglas de memoria (propuestas / activas / revocadas)
- `notes`                 notas (tool mínima de Fase 1)

Fase 2 (agenda y recordatorios):

- `recurrence_rules`      reglas de recurrencia reutilizables (eventos/tareas)
- `events`                citas, reuniones, cumpleaños, fechas importantes
- `tasks`                 tareas independientes con estado
- `reminders`             recordatorios persistentes a un evento/tarea o sueltos

Fase 3 (núcleo financiero):

- `accounts`              cuentas del usuario (banco, ahorro, efectivo, tarjeta
                          de crédito, otras). El saldo es DERIVADO, no almacenado.
- `transactions`          movimiento de gasto (expense) o ingreso (income).
- `transfers`             traspaso entre dos cuentas del mismo usuario; NO se
                          contabiliza como gasto/ingreso.

Fase 3.3 (recurrencias, pagos planificados y suscripciones):

- `recurring_transactions` regla de recurrencia financiera (la REGLA, no un
                          movimiento): frecuencia + intervalo + ancla de mes.
- `planned_transactions`  ocurrencia futura planificada (NO un movimiento real
                          hasta confirmarse; status planned|converted|cancelled).
- `subscriptions`         suscripción vinculada a una recurrencia (la recurrencia
                          es el motor; la suscripción su facturación).

Fase 4 (coche, casa y salud y hábitos):

- `vehicles`              vehículo del usuario (nombre, matrícula, odómetro).
- `fuel_entries`          repostaje (gasto SIEMPRE con `amount`; cada repostaje
                          crea EXACTAMENTE UNA transacción vinculada).
- `vehicle_maintenances`  mantenimiento/revisión/ITV del vehículo (la ITV es un
                          mantenimiento tipo "itv" con próxima fecha/odómetro).
- `vehicle_insurances`    seguro del vehículo (registro no financiero; NO genera
                          movimientos de cuenta).
- `homes`                 vivienda del usuario.
- `home_maintenances`     mantenimiento de la vivienda (revisión de la caldera,
                          reparaciones); con coste crea exactamente una transacción.
- `habits`                hábito/actividad registrable (agua, ejercicio...).
- `habit_logs`            registro puntual de un hábito.

Fase 6 (viajes, resúmenes y proactividad controlada):

- `trips`                 viaje del usuario (destino, fechas, estado).
- `trip_checklist_items`  ítems de checklist del viaje.
- `user_proactivity_settings` preferencias de proactividad (opt-in, horas de
                          silencio, límite diario, ventana de envío).
- `proactive_notifications` notificación proactiva entregada o descartada;
                          la clave única (user_id, kind, dedup_key) impide
                          duplicados entre pasadas del poller. El claim/lease
                          (claimed_at, lease_until, worker_id) coordina su
                          entrega SIN envíos duplicados ni pérdidas (Fase 6.2).
- `proactive_daily_counters` contador atómico del límite diario local por
                          usuario; se reserva al reclamar y garantiza que el
                          total de `sent` no supere `daily_limit` aunque varios
                          procesos poller corran a la vez (Fase 6.2).

Los movimientos de un viaje se vinculan con `transactions.trip_id` (FK a
`trips.id`, ON DELETE SET NULL) y las tareas/eventos/recordatorios del viaje
con sus propias columnas `trip_id` (misma semántica). El gasto del viaje SIEMPRE
se persiste como una `Transaction` de expense vinculada; el viaje NO crea
movimientos por sí mismo.

En el módulo de salud, las citas médicas se expresan como `events` etiquetados
con `events.category='salud'` (NO existe un calendario médico separado) y los
recordatorios ("revisar la caldera cada año", "pasar la ITV el día X") usan los
mecanismos existentes de reminders/tasks/recurrencias.

Integración financiera: `fuel_entries.amount`/`home_maintenances.cost` y
`vehicle_maintenances.cost` crean SIEMPRE una única `Transaction`
(`type=expense`) y guardan su id para trazabilidad. Un detalle + su transacción
se insertan en LA MISMA transacción (rollback atómico). Los seguros y los
hábitos NO tocan finanzas.

Todas las tablas de usuario incluyen `user_id` para garantizar aislamiento.
Importes SIEMPRE en `Numeric(12,2)` (Decimal), nunca floats.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from asistente.persistence.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Identidad Telegram (Fase 9): ahora nullable porque la identidad canal-agnóstica
    # vive en `user_channels`. Se conserva para notificaciones/compatibilidad con los
    # pollers, y el índice unique es seguro en PG (múltiples NULL permitidos).
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, index=True, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    username: Mapped[str | None] = mapped_column(String(200), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    lang: Mapped[str] = mapped_column(String(16), default="es-ES")
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserChannel(Base):
    """Identidad del usuario por canal (Fase 9).

    Aísla la identidad de Telegram: `(channel, external_user_id)` resuelve al
    `users.id` interno. `status='pending'` corresponde a una identidad aún no
    vinculada a un usuario: el código de enlace (`pair_code_hash`, TTL 30 min)
    espera a que el propietario lo confirme desde Telegram (tool `link_whatsapp`).

    - `channel`: 'telegram' | 'whatsapp' (ver `asistente.application.channels`).
    - `external_user_id`: identificador ESTABLE del canal (telegram `id`;
      wa_id de WhatsApp). Nunca nombres impostables.
    """

    __tablename__ = "user_channels"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32))
    external_user_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="linked")  # linked|pending
    pair_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pair_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("channel", "external_user_id", name="uq_user_channels_channel_external"),
        CheckConstraint("status IN ('linked', 'pending')", name="ck_user_channels_status"),
    )


class ProcessedInboundEvent(Base):
    """Idempotencia de eventos entrantes (Fase 9).

    Un mismo `external_message_id` (wamid) de WhatsApp llega más de una vez
    (tandas del proveedor, reintentos, tests). El insert dedup con
    `ON CONFLICT DO NOTHING` se hace ANTES de tocar lógica de negocio, de modo
    que un mismo evento nunca crea gasto/tarea/confirmación dos veces.
    """

    __tablename__ = "processed_inbound_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(32))
    external_message_id: Mapped[str] = mapped_column(String(255))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("channel", "external_message_id", name="uq_processed_events_channel_message"),)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), index=True)  # user|assistant|tool|system
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class PendingConfirmation(Base):
    __tablename__ = "pending_confirmations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    proposed_reply: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|confirmed|cancelled|expired
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MemoryRule(Base):
    __tablename__ = "memory_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # merchant_category|keyword_category|account_keyword|preference
    key: Mapped[str] = mapped_column(String(200))
    value: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    scope: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="proposed")  # user|proposed|ai
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_memory_rules_user_active", "user_id", "active"),)


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecurrenceRule(Base):
    """Regla de recurrencia reutilizable (eventos y tareas).

    Frecuencias soportadas: daily | weekly | monthly | yearly.
    `days_of_week` aplica a recurrencia semanal: lista 0..6 (0=lunes..6=domingo,
    ISO). `start_at` marca la primera ocurrencia y `end_at` opcional la fecha
    final (inclusive).
    """

    __tablename__ = "recurrence_rules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    freq: Mapped[str] = mapped_column(String(16))  # daily|weekly|monthly|yearly
    interval: Mapped[int] = mapped_column(default=1)  # cada N días/semanas/meses/años
    days_of_week: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Event(Base):
    """Cita/reunión/cumpleaños/fecha importante.

    `kind` distingue el tipo de evento: `event` (cita/reunión genérica),
    `birthday` (cumpleaños) o `important_date` (fecha importante).
    Los eventos con `recurrence_id` representan la PRIMERA ocurrencia; las
    siguientes se generan a partir de la regla (no se materializan cientos
    de filas). `all_day` marca eventos sin hora (cumpleaños, fechas).
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), default="event")  # event|birthday|important_date
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="scheduled")  # scheduled|cancelled
    priority: Mapped[str] = mapped_column(String(16), default="medium")  # high|medium|low
    person: Mapped[str | None] = mapped_column(String(200), nullable=True)  # para cumpleaños/fechas de personas
    category: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # etiqueta libre (Fase 4): 'salud' para citas médicas
    recurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurrence_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trip_id: Mapped[int | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )  # viaje vinculado (Fase 6): el evento se desvincula si el viaje se borra
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_events_user_start", "user_id", "start_at"),)


class Task(Base):
    """Tarea independiente (o vinculada a recurrencia).

    Estados: pending | completed | cancelled. `due_at` es opcional (tarea sin
    fecha). Con recurrencia, `due_at` marca la primera ocurrencia y se
    genera la siguiente al completarse/cancelarse.
    """

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|completed|cancelled
    priority: Mapped[str] = mapped_column(String(16), default="medium")  # high|medium|low
    recurrence_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurrence_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    trip_id: Mapped[int | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )  # viaje vinculado (Fase 6)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_tasks_user_status", "user_id", "status"),)


class Reminder(Base):
    """Recordatorio persistente.

    Puede referirse a un evento (`event_id`), a una tarea (`task_id`) o ser
    independiente. `offset_minutes` solo aplica a recordatorios vinculados
    (minutos antes del evento/tarea; ej. 1440 = un día antes). `remind_at`
    se referencia en UTC; para recordatorios vinculados se recalcula al
    conectar.

    Estados del ciclo de vida (claim atómico Fase 2.1):
    - `pending`:    candidato; lo reclama el poller.
    - `processing`: reclamado y en proceso de envío (`claimed_at` = inicio
                    del lease). Si el proceso muere, otro poller lo recupera
                    cuando el lease expira.
    - `sent`:       enviado definitivamente (o avanzado/sincronizado).
    - `cancelled`:  cancelado por el usuario, por serie terminada, o por
                    vencimiento irreversible (recovery window).

    `claimed_at` identifica el propietario del claim: las operaciones de
    liquidación del poller (`mark_sent`/`advance`/`cancel_claimed`/`release`)
    exigen que `status == 'processing'` y que `claimed_at` siga siendo el del
    worker que reclamó, para que un lease expirado y re-clamado por otro
    worker nunca se liquide dos veces.
    """

    __tablename__ = "reminders"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=True, index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True, index=True)
    message: Mapped[str] = mapped_column(Text)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    offset_minutes: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|sent|cancelled
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trip_id: Mapped[int | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )  # viaje vinculado (Fase 6)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_reminders_user_status", "user_id", "status"),)


class Account(Base):
    """Cuenta del usuario (Fase 3).

    Tipos: `bank` (cuenta bancaria / tarjeta de débito asociada), `savings`
    (ahorro/reserva), `cash` (efectivo), `credit_card` (deuda) y `other`.

    El saldo NO se almacena: se deriva de `initial_balance` + ingresos - gastos
    +- transferencias (ver `FinanceService.balance_overview`). `is_default`
    marca la cuenta por defecto (máximo una por usuario); `active=False`
    impide asignarle nuevos movimientos. Las cuentas con histórico no se
    eliminan físicamente (solo se desactivan).
    """

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    type: Mapped[str] = mapped_column(String(32), index=True)  # bank|savings|cash|credit_card|other
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    initial_balance: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0.00"))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_accounts_user_default", "user_id", unique=True, postgresql_where=text("is_default")),)


class Transaction(Base):
    """Movimiento de gasto/ingreso (Fase 3).

    `amount` es siempre positivo; el signo lo da `type` (expense | income).
    `account_id` puede ser NULL temporalmente si aún no hay cuenta, pero las
    tools asignan automáticamente la cuenta por defecto cuando existe.
    `category` pertenece al conjunto básico de `asistente.domain.categories`.
    `status` distingue movimientos registrados (`posted`) de futuras
    correcciones/anulaciones (`cancelled`); hoy solo se usa `posted`.
    `trip_id` vincula el gasto/ingreso a un viaje (Fase 6).
    """

    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # expense|income
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merchant: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(64), default="Otros", index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(32), default="manual")  # manual|llm
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted|cancelled
    trip_id: Mapped[int | None] = mapped_column(
        ForeignKey("trips.id", ondelete="SET NULL"), nullable=True, index=True
    )  # viaje vinculado (Fase 6): se desliga si el viaje se borra
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_transactions_user_occurred", "user_id", "occurred_at"),)


class Transfer(Base):
    """Traspaso entre cuentas del mismo usuario (Fase 3).

    Un traspaso NO es un gasto ni un ingreso: modifica el saldo derivado de
    ambas cuentas (una disminuye, la otra aumenta) sin tocar los totales de
    gastos/ingresos ni el patrimonio neto.
    """

    __tablename__ = "transfers"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    from_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    to_account_id: Mapped[int] = mapped_column(
        ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="posted")  # posted|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_transfers_user_occurred", "user_id", "occurred_at"),)


class Budget(Base):
    """Presupuesto mensual (Fase 3.2).

    Puede ser general (`category = None`) o por categoría.
    `spent`, `remaining` y `percent_used` NO se almacenan: se calculan a
    partir de `amount` + las transacciones posted del período.
    """

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    year: Mapped[int] = mapped_column()
    month: Mapped[int] = mapped_column()
    category: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_budgets_amount_positive"),
        CheckConstraint("month >= 1 AND month <= 12", name="ck_budgets_month_range"),
        # Un único presupuesto general por usuario/mes (category IS NULL)
        Index(
            "ix_budgets_user_year_month_null",
            "user_id",
            "year",
            "month",
            unique=True,
            postgresql_where=text("category IS NULL"),
        ),
        # Un único presupuesto por categoría por usuario/mes
        Index(
            "ix_budgets_user_year_month_category",
            "user_id",
            "year",
            "month",
            "category",
            unique=True,
            postgresql_where=text("category IS NOT NULL"),
        ),
    )


class SavingsGoal(Base):
    """Objetivo de ahorro (Fase 3.2).

    El progreso se calcula a partir de `savings_contributions`, NO
    almacenado aquí. `target_amount` es la meta en EUR.
    """

    __tablename__ = "savings_goals"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    target_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    deadline: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|completed|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (CheckConstraint("target_amount > 0", name="ck_savings_goals_target_positive"),)


class SavingsContribution(Base):
    """Contribución a un objetivo de ahorro (Fase 3.2).

    Una contribución es una asignación contable/metadato respecto a un
    objetivo. NO crea automáticamente un gasto ni una transferencia.
    Si se vincula a una transferencia (`transfer_id`), la transferencia
    ya es el movimiento financiero real.
    """

    __tablename__ = "savings_contributions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("savings_goals.id", ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    contributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transfer_id: Mapped[int | None] = mapped_column(
        ForeignKey("transfers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_savings_contributions_amount_positive"),
        # Una transferencia solo puede vincularse a UNA contribución (ura real
        # de la Fase 3.2/3.3.1; la comprobación de dominio es la defensa blanda).
        Index(
            "ux_savings_contributions_transfer_id",
            "transfer_id",
            unique=True,
            postgresql_where=text("transfer_id IS NOT NULL"),
            sqlite_where=text("transfer_id IS NOT NULL"),
        ),
    )


class RecurringTransaction(Base):
    """Regla de recurrencia financiera (Fase 3.3).

    Representa la REGLA, no un movimiento: el motor materializa ocurrencias
    futuras como `planned_transactions` y las avanza de forma idempotente.

    - `frequency`: daily | weekly | monthly | yearly, con `interval` como
      "cada N días/semanas/meses/años" ("cada 2 semanas", "cada 3 meses").
    - `day_of_month` guarda el día del mes ANCLA para monthly/yearly (1-31).
      Esto permite la política de saneo de fin de mes sin desviarse:
      31 ene → 28/29 feb → 31 mar → 30 abr → 31 may. Nunca se deriva del
      `next_due_at` saneado (eso provocaría desviación permanente).
    - `month_of_year` ancla el mes para recurrencias anuales ("cada año en
      marzo").
    - `start_date` es la fecha (local) de la primera ocurrencia; `next_due_at`
      (UTC) es la PRÓXIMA ocurrencia a materializar; None ⇒ serie terminada.
    - `end_date` (local, inclusiva) corta la serie.
    - `active=False` cancela la serie (no borra histórico) y `paused=True` la
      detiene temporalmente (sin cancelar planificados futuros).
    """

    __tablename__ = "recurring_transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # expense|income
    title: Mapped[str] = mapped_column(String(300))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merchant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="Otros")
    frequency: Mapped[str] = mapped_column(String(16))  # daily|weekly|monthly|yearly
    interval: Mapped[int] = mapped_column(default=1)
    day_of_month: Mapped[int | None] = mapped_column(nullable=True)  # ancla mensual/anual (1-31)
    month_of_year: Mapped[int | None] = mapped_column(nullable=True)  # ancla anual (1-12)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_recurring_transactions_amount_positive"),
        CheckConstraint("type IN ('expense', 'income')", name="ck_recurring_transactions_type"),
        CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_recurring_transactions_frequency",
        ),
        CheckConstraint("day_of_month >= 1 AND day_of_month <= 31", name="ck_recurring_day_of_month"),
        CheckConstraint("month_of_year >= 1 AND month_of_year <= 12", name="ck_recurring_month_of_year"),
    )


class PlannedTransaction(Base):
    """Ocurrencia futura planificada (Fase 3.3).

    Un planificado NO es un movimiento real: no toca saldos ni presupuestos.
    Al confirmarlo se convierte en un `Transaction` posted (status=converted) y
    se guarda `converted_transaction_id` para trazabilidad y anti-duplicación
    (nunca coexistir planned + transaction por el mismo hecho).

    Ciclo de vida:
    - `planned`:    pendiente de confirmar (materializado por el motor o creado
                    a mano).
    - `converted`:  convertido en transacción real (ya no se puede volver a
                    convertir).
    - `cancelled`:  cancelado (recurrencia cancelada o usuario explícito).

    La unicidad (recurring_transaction_id, planned_at) impide materializar dos
    veces la misma ocurrencia de una recurrencia (idempotencia).
    """

    __tablename__ = "planned_transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16), index=True)  # expense|income
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    merchant: Mapped[str | None] = mapped_column(String(300), nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="Otros")
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    recurring_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|converted|cancelled
    converted_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_planned_transactions_amount_positive"),
        CheckConstraint("type IN ('expense', 'income')", name="ck_planned_transactions_type"),
        CheckConstraint(
            "status IN ('planned', 'converted', 'cancelled')",
            name="ck_planned_transactions_status",
        ),
        Index("ix_planned_user_planned", "user_id", "planned_at"),
        # Idempotencia: una sola fila por (recurrencia, ocurrencia). Las filas
        # manuales (recurring_transaction_id NULL) no colisionan (NULL != NULL).
        Index("ix_planned_recurring_occurrence", "recurring_transaction_id", "planned_at", unique=True),
    )


class Person(Base):
    """Persona relacionada con el usuario (Fase 3.4).

    Una persona es alguien con quien el usuario tiene deudas/personales o
    gastos compartidos ("Juan", "María"...). Pertenece SIEMPRE a un único
    usuario y nunca se elimina físicamente si tiene histórico financiero:
    se desactiva (`active=False`).

    `normalized_name` es la forma canónica (minúsculas, sin acentos, espacios
    colapsados) usada para búsqueda por lenguaje natural y resolución; dos
    usuarios pueden tener una persona llamada "Juan" (no hay unicidad global).
    """

    __tablename__ = "people"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_people_user_normalized", "user_id", "normalized_name"),)


class Debt(Base):
    """Deuda/préstamo personal entre el usuario y una persona (Fase 3.4).

    `direction` indica QUIÉN debe a quién:
    - `owed_to_user`: la otra persona debe dinero al usuario ("Juan me debe 100").
    - `user_owes`:    el usuario debe dinero a la otra persona ("yo le debo 50 a María").

    `amount` es el importe original; `remaining_amount` lo que queda pendiente
    (igual a `amount` al crearse; pasa a 0 al liquidar). La liquidación
    (`settle`) pone `status=settled` y `remaining_amount=0` de forma atómica
    (una sola UPDATE).

    Una deuda NO crea ni es un gasto/ingreso: no toca saldos de cuentas, ni
    presupuestos, ni patrimonio. Es una relación financiera personal.
    """

    __tablename__ = "debts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="RESTRICT"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16))  # owed_to_user|user_owes
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    remaining_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|settled|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_debts_amount_positive"),
        CheckConstraint("remaining_amount >= 0", name="ck_debts_remaining_non_negative"),
        CheckConstraint("direction IN ('owed_to_user', 'user_owes')", name="ck_debts_direction"),
        CheckConstraint("status IN ('open', 'settled', 'cancelled')", name="ck_debts_status"),
        Index("ix_debts_user_status", "user_id", "status"),
    )


class SharedExpense(Base):
    """Gasto compartido entre personas (Fase 3.4).

    Representa UN gasto real (total, quién lo pagó, cómo se reparte, y las
    posiciones resultantes). El reparto vive en `shared_expense_shares`. El
    estado derivado (QUIÉN debe a QUIÉN) se CALCULA a partir de los shares y
    de qué pagó cada participante: el gasto compartido es la fuente de verdad
    y NO se materializan deudas duplicadas.

    Pagador:
    - `paid_by_user=True`    → pagó el usuario.
    - `paid_by_person_id`    → pagó una persona.
    - Ambos nunca a la vez. El pagador no tiene que ser participante (p. ej.
      el usuario paga la cena completa de tres amigos).

    Consultar `SharedExpenseService` para la semántica de reparto igualitario
    (Decimal, suma EXACTA al total) y reparto manual (suma == total).
    """

    __tablename__ = "shared_expenses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    paid_by_person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    paid_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active|cancelled
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("total_amount > 0", name="ck_shared_expenses_total_positive"),
        CheckConstraint("status IN ('active', 'cancelled')", name="ck_shared_expenses_status"),
        CheckConstraint(
            "(paid_by_user AND paid_by_person_id IS NULL) OR (NOT paid_by_user AND paid_by_person_id IS NOT NULL)",
            name="ck_shared_expenses_payer",
        ),
        Index("ix_shared_expenses_user_occurred", "user_id", "occurred_at"),
    )


class SharedExpenseShare(Base):
    """Participación de un gasto compartido (Fase 3.4).

    Un participante es el usuario (`is_user=True`, `person_id=None`) o una
    persona (`person_id` fijado, `is_user=False`); NUNCA ambos. Cada
    participante aparece exactamente una vez por gasto (índices únicos
    parciales).

    La suma de `share_amount` de todas las shares equivale SIEMPRE a
    `shared_expenses.total_amount` (garantizado en dominio).
    """

    __tablename__ = "shared_expense_shares"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shared_expense_id: Mapped[int] = mapped_column(ForeignKey("shared_expenses.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    is_user: Mapped[bool] = mapped_column(Boolean, default=False)
    share_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("share_amount > 0", name="ck_shares_amount_positive"),
        CheckConstraint(
            "(is_user AND person_id IS NULL) OR (NOT is_user AND person_id IS NOT NULL)",
            name="ck_shares_participant_kind",
        ),
        # El usuario solo aparece UNA vez por gasto.
        Index(
            "ux_shares_user",
            "shared_expense_id",
            unique=True,
            postgresql_where=text("is_user"),
            sqlite_where=text("is_user"),
        ),
        # Una persona solo aparece UNA vez por gasto.
        Index(
            "ux_shares_person",
            "shared_expense_id",
            "person_id",
            unique=True,
            postgresql_where=text("person_id IS NOT NULL"),
            sqlite_where=text("person_id IS NOT NULL"),
        ),
    )


class SharedExpensePayment(Base):
    """Pago (aportación) a un gasto compartido (Fase 3.4).

    Un gasto puede pagarlo UNA persona o VARIAS ("quién o quiénes lo pagaron").
    Cada fila dice cuánto aportó un pagador:
    - `is_user=True`  → pagó el usuario.
    - `person_id`     → pagó esa persona.
    Nunca ambos en la misma fila.

    `SUM(shared_expense_payments.amount)` == `shared_expenses.total_amount`
    (garantizado en dominio). El pagador no tiene que ser participante (el
    usuario puede pagar la pizza completa de tres amigos sin comerse una rebanada).
    """

    __tablename__ = "shared_expense_payments"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    shared_expense_id: Mapped[int] = mapped_column(ForeignKey("shared_expenses.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[int | None] = mapped_column(
        ForeignKey("people.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    is_user: Mapped[bool] = mapped_column(Boolean, default=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payments_amount_positive"),
        CheckConstraint(
            "(is_user AND person_id IS NULL) OR (NOT is_user AND person_id IS NOT NULL)",
            name="ck_payments_payer_kind",
        ),
        Index(
            "ux_payments_user",
            "shared_expense_id",
            unique=True,
            postgresql_where=text("is_user"),
            sqlite_where=text("is_user"),
        ),
        Index(
            "ux_payments_person",
            "shared_expense_id",
            "person_id",
            unique=True,
            postgresql_where=text("person_id IS NOT NULL"),
            sqlite_where=text("person_id IS NOT NULL"),
        ),
    )


class Subscription(Base):
    """Suscripción del usuario (Fase 3.3).

    La suscripción NO es un motor independiente: se apoya SIEMPRE en una
    recurrencia (`recurring_transaction_id`). La recurrencia genera los pagos
    planificados; la suscripción aporta la semántica de a facturación
    (nombre, periodo, próxima factura).

    - `billing_period`: daily | weekly | monthly | yearly (misma semántica que
      la frecuencia de la recurrencia vinculada).
    - `next_billing_date` refleja `recurring_transactions.next_due_at`.
    - Cancelar la suscripción desactiva la recurrencia exclusiva y cancela los
      planificados futuros, conservando histórico (transactions convertidas).
    """

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    billing_period: Mapped[str] = mapped_column(String(16))  # daily|weekly|monthly|yearly
    next_billing_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    category: Mapped[str] = mapped_column(String(64), default="Suscripciones")
    recurring_transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("recurring_transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_subscriptions_amount_positive"),
        CheckConstraint(
            "billing_period IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_subscriptions_billing_period",
        ),
    )


class Vehicle(Base):
    """Vehículo del usuario (Fase 4).

    `normalized_name` (minúsculas, sin acentos, espacios colapsados) permite
    la resolución por lenguaje natural. `current_odometer` (km) es el odómetro
    más reciente conocido; se actualiza al registrar un repostaje o un
    mantenimiento con odómetro mayor (nunca retrocede).
    """

    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    brand: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    plate: Mapped[str | None] = mapped_column(String(20), nullable=True)
    year: Mapped[int | None] = mapped_column(nullable=True)  # año de matriculación
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # gasolina|diesel|hibrido|electrico
    current_odometer: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("year IS NULL OR year BETWEEN 1900 AND 2100", name="ck_vehicles_year_range"),
        CheckConstraint("current_odometer IS NULL OR current_odometer >= 0", name="ck_vehicles_odometer"),
        Index("ix_vehicles_user_normalized", "user_id", "normalized_name"),
        Index("ix_vehicles_user_active", "user_id", "active"),
    )


class FuelEntry(Base):
    """Repostaje de un vehículo (Fase 4).

    `amount` (€) es OBLIGATORIO: todo repostaje genera EXACTAMENTE una
    transacción de gasto (`transactions.type='expense'`) cuyo id se guarda en
    `transaction_id` (misma transacción atómica, ver `VehicleService`). Este
    detalle es la fuente de verdad del gasto de combustible: nunca se registra
    un repostaje sin su transacción ni se usa la transacción para otro fin.
    """

    __tablename__ = "fuel_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    liters: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    odometer: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    fuel_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    full_tank: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_fuel_entries_amount_positive"),
        CheckConstraint("liters IS NULL OR liters > 0", name="ck_fuel_entries_liters_positive"),
        CheckConstraint("odometer IS NULL OR odometer >= 0", name="ck_fuel_entries_odometer"),
        Index("ix_fuel_entries_user_occurred", "user_id", "occurred_at"),
    )


class VehicleMaintenance(Base):
    """Mantenimiento/revisión/ITV de un vehículo (Fase 4).

    La ITV NO es una entidad aparte: se registra con `maintenance_type='itv'`
    y se consulta con `next_due_at`/`next_due_odometer`. `cost` es opcional:
    si se indica, se crea exactamente UNA transacción de gasto vinculada
    (`transaction_id`); si no, el mantenimiento es un registro sin coste.
    """

    __tablename__ = "vehicle_maintenances"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True)
    maintenance_type: Mapped[str] = mapped_column(String(32), default="revision")  # revision|itv|reparacion|otro
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    odometer: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    next_due_odometer: Mapped[Decimal | None] = mapped_column(Numeric(12, 1), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("cost IS NULL OR cost > 0", name="ck_vehicle_maintenances_cost_positive"),
        CheckConstraint("odometer IS NULL OR odometer >= 0", name="ck_vehicle_maintenances_odometer"),
        CheckConstraint(
            "maintenance_type IN ('revision', 'itv', 'reparacion', 'otro')",
            name="ck_vehicle_maintenances_type",
        ),
        Index("ix_vehicle_maintenances_user_occurred", "user_id", "occurred_at"),
    )


class VehicleInsurance(Base):
    """Seguro de un vehículo (Fase 4).

    Es un REGISTRO informativo (compañía, número de póliza, cobertura, vencimiento,
    cuota); NO es un movimiento financiero y no genera transacciones ni pagos
    planificados. `billing_period` describe la facturación (one_time | monthly |
    quarter_year | half_year | yearly) solo a efectos informativos.
    """

    __tablename__ = "vehicle_insurances"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True)
    company: Mapped[str | None] = mapped_column(String(200), nullable=True)
    policy_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    insurance_type: Mapped[str | None] = mapped_column(String(64), nullable=True)  # a terceros, a todo riesgo...
    coverage: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # cuota informativa
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    billing_period: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # one_time|monthly|quarter_year|half_year|yearly
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount > 0", name="ck_vehicle_insurances_amount_positive"),
        CheckConstraint(
            "billing_period IS NULL OR "
            "billing_period IN ('one_time', 'monthly', 'quarter_year', 'half_year', 'yearly')",
            name="ck_vehicle_insurances_billing_period",
        ),
        Index("ix_vehicle_insurances_user_active", "user_id", "active"),
    )


class Home(Base):
    """Vivienda del usuario (Fase 4)."""

    __tablename__ = "homes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    home_type: Mapped[str | None] = mapped_column(String(32), nullable=True)  # piso|casa|garaje|local|otro
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_homes_user_normalized", "user_id", "normalized_name"),
        Index("ix_homes_user_active", "user_id", "active"),
    )


class HomeMaintenance(Base):
    """Mantenimiento de una vivienda (Fase 4).

    Plantilla pendiente ("revisar la caldera") o mantenimiento hecho (con
    `occurred_at`/`cost`). El estado se infiere al registrar: si hay
    `occurred_at` o `cost` → `done`; si no → `pending` (aparece en
    `get_home_upcoming`). Con `cost` se crea exactamente UNA transacción de
    gasto vinculada (`transaction_id`). Las revisiones periódicas
    ("revisar la caldera cada año") se gestionan como recordatorios/tareas
    existentes, no duplicando aquí la recurrencia.
    """

    __tablename__ = "home_maintenances"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    home_id: Mapped[int] = mapped_column(ForeignKey("homes.id", ondelete="RESTRICT"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(300))
    maintenance_type: Mapped[str] = mapped_column(String(32), default="revision")  # revision|reparacion|otro
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|done
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    transaction_id: Mapped[int | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("cost IS NULL OR cost > 0", name="ck_home_maintenances_cost_positive"),
        CheckConstraint("status IN ('pending', 'done')", name="ck_home_maintenances_status"),
        CheckConstraint(
            "maintenance_type IN ('revision', 'reparacion', 'otro')",
            name="ck_home_maintenances_type",
        ),
        Index("ix_home_maintenances_user_occurred", "user_id", "occurred_at"),
    )


class Habit(Base):
    """Hábito/actividad registrable del usuario (Fase 4, salud).

    `target_per_period` es el OBJETIVO (sin dimensión de coaching): el
    cumplimiento se calcula como el número de registros (`habit_logs`) en el
    periodo actual frente al objetivo. `period` define el periodo de cómputo
    (day|week|month|year). `unit` es informativo ("litros", "minutos", "veces").
    """

    __tablename__ = "habits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    target_per_period: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    period: Mapped[str] = mapped_column(String(16), default="week")  # day|week|month|year
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "target_per_period IS NULL OR target_per_period > 0",
            name="ck_habits_target_positive",
        ),
        CheckConstraint("period IN ('day', 'week', 'month', 'year')", name="ck_habits_period"),
        Index("ix_habits_user_normalized", "user_id", "normalized_name"),
        Index("ix_habits_user_active", "user_id", "active"),
    )


class HabitLog(Base):
    """Registro puntual de un hábito (Fase 4, salud).

    `value` (opcional) añade la cantidad de ese registro (p. ej. litros de
    agua); sin él, el registro cuenta 1 a efectos de cumplimiento. Es un log
    histórico: nunca se borra (FK RESTRICT desde `habits`).
    """

    __tablename__ = "habit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    habit_id: Mapped[int] = mapped_column(ForeignKey("habits.id", ondelete="RESTRICT"), nullable=False, index=True)
    value: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    logged_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        CheckConstraint("value IS NULL OR value > 0", name="ck_habit_logs_value_positive"),
        Index("ix_habit_logs_user_logged", "user_id", "logged_at"),
    )


class Trip(Base):
    """Viaje del usuario (Fase 6).

    `name` es el nombre visible y `normalized_name` el normalizado para
    búsquedas únicas por usuario. `destination`, opcional, es el lugar de
    destino. Estados: `planned` (planificado) | `active` (en curso) |
    `completed` (terminado) | `cancelled` (cancelado). Un viaje cancelado o
    completado conserva su histórico (movimientos/mantenimientos vinculados).
    """

    __tablename__ = "trips"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300))
    destination: Mapped[str | None] = mapped_column(String(300), nullable=True)
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(16), default="planned")  # planned|active|completed|cancelled
    trip_type: Mapped[str] = mapped_column(String(16), default="other")  # leisure|business|family|other
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "start_at IS NULL OR end_at IS NULL OR end_at >= start_at",
            name="ck_trips_dates_ordered",
        ),
        CheckConstraint(
            "status IN ('planned', 'active', 'completed', 'cancelled')",
            name="ck_trips_status",
        ),
        CheckConstraint(
            "trip_type IN ('leisure', 'business', 'family', 'other')",
            name="ck_trips_trip_type",
        ),
        CheckConstraint("budget IS NULL OR budget >= 0", name="ck_trips_budget_positive"),
        Index("ix_trips_user_normalized", "user_id", "normalized_name"),
        Index("ix_trips_user_status", "user_id", "status"),
    )


class TripChecklistItem(Base):
    """Ítem de checklist de un viaje (Fase 6).

    Documentos, reservas, equipaje... `is_done` marca completado. Los ítems
    se ordenan por id de creación. FK RESTRICT desde `trips`: los ítems NO
    sobreviven al borrado físico del viaje (un viaje no se borra con
    histórico: se cancela o completa).
    """

    __tablename__ = "trip_checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("trips.id", ondelete="RESTRICT"), nullable=False, index=True)
    text: Mapped[str] = mapped_column(String(500))
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_trip_checklist_user_trip", "user_id", "trip_id"),)


class UserProactivitySettings(Base):
    """Preferencias de proactividad del usuario (Fase 6).

    La proactividad es OPT-IN: con `enabled=False` (valor por defecto) el
    poller no genera notificaciones para el usuario. `quiet_hours_start` y
    `quiet_hours_end` (formato "HH:MM", 24h) definen la franja de silencio en
    la zona `timezone` del usuario; `daily_limit` limita las notificaciones
    proactivas entregadas por día natural local.
    """

    __tablename__ = "user_proactivity_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    quiet_hours_start: Mapped[str] = mapped_column(String(5), default="22:00")  # HH:MM (24h)
    quiet_hours_end: Mapped[str] = mapped_column(String(5), default="08:00")  # HH:MM (24h)
    daily_limit: Mapped[int] = mapped_column(default=3)
    importance_threshold: Mapped[int] = mapped_column(default=3)  # 1=low+med+high, 2=med+high, 3=high only
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Madrid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("daily_limit >= 0 AND daily_limit <= 50", name="ck_proactivity_daily_limit"),
        CheckConstraint(
            "importance_threshold >= 1 AND importance_threshold <= 3", name="ck_proactivity_importance_threshold"
        ),
    )


class ProactiveNotification(Base):
    """Notificación proactiva generada por el poller (Fase 6 / 6.2).

    Representa UN mensaje candidato. `dedup_key` identifica el hecho concreto
    ("trip:12" = el viaje 12, "summary:daily:2026-09-18"); la clave única
    (user_id, kind, dedup_key) garantiza que la misma notificación no se
    entrega dos veces entre pasadas del poller, aunque el proceso muera tras
    el envío. `importance` puntúa (1=baja, 2=media, 3=alta). Estados:
    `pending` (generada, aún no enviada) | `processing` (reclamada por un
    worker con lease vigente) | `sent` | `skipped` (fuera de la franja de
    envío o límite diario alcanzado).

    Claim/lease (Fase 6.2): al reclamar, el worker marca la fila
    `status='processing'`, guarda `worker_id` y fija `claimed_at`/
    `lease_until`. Si `lease_until` expira sin que el worker finalice, la fila
    vuelve a ser elegible para reclaim (recuperación ante caídas). La
    finalización (`mark_sent`/`mark_skipped`) solo es válida si el worker
    sigue siendo dueño de la fila (mismo `worker_id` y lease no expirado).
    """

    __tablename__ = "proactive_notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # trip_reminder|daily_summary|weekly_summary|upcoming|pending_tasks
    dedup_key: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    importance: Mapped[int] = mapped_column(default=2)  # 1|2|3
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|processing|sent|skipped
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        CheckConstraint("importance >= 1 AND importance <= 3", name="ck_proactive_notifications_importance"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'sent', 'skipped')", name="ck_proactive_notifications_status"
        ),
        # Dedup: un mismo hecho (kind + dedup_key) solo puede existir una vez por usuario.
        Index(
            "ix_proactive_notifications_user_kind_dedup",
            "user_id",
            "kind",
            "dedup_key",
            unique=True,
            postgresql_where=text("status != 'skipped'"),
        ),
        Index("ix_proactive_notifications_user_status", "user_id", "status"),
        Index("ix_proactive_notifications_user_sent_at", "user_id", "sent_at"),
        Index("ix_proactive_notifications_status_lease", "status", "lease_until"),
    )


class ProactiveDailyCounter(Base):
    """Contador atómico del límite diario de proactividad (Fase 6.2 / 6.2.1).

    Un registro por usuario y día local. El valor `count` se incrementa con
    `reserve_send`, justo antes de emitir cada envío, mediante un UPDATE
    atómico (`count = count + 1 WHERE count < daily_limit`) que bloquea la fila
    y serializa a los workers concurrentes. La invariante es sobre ENVÍOS
    efectivos, no sobre claims: incluso con reclaims y reenvíos tras un crash,
    el número de envíos reales por usuario y día local nunca supera
    `daily_limit`. `release_send` devuelve una unidad cuando la entrega falla.
    """

    __tablename__ = "proactive_daily_counters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)  # día natural local del usuario
    count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_proactive_daily_counters_user_day"),
        CheckConstraint("count >= 0", name="ck_proactive_daily_counters_count"),
    )


class WebCredential(Base):
    """Credencial de acceso Web (Fase 10.0).

    Email (único, normalizado) + hash Argon2id del password. El email NO es la
    identidad del usuario: esa vive en `user_channels` (`channel='web'`), así
    un mismo `users.id` puede tener Telegram, WhatsApp y Web. Solo existe UNA
    credencial Web por usuario.
    """

    __tablename__ = "web_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WebSession(Base):
    """Sesión Web persistente (Fase 10.0).

    El token Bearer crudo se entrega una única vez al iniciar sesión; aquí solo
    se guarda su SHA-256 (`token_hash`, 64 hex), de modo que una fuga de BD no
    expone tokens válidos. Revocación explícita (`revoked_at`) y expiración
    (`expires_at`, `WEB_SESSION_TTL`). `last_used_at` permite limpiar sesiones
    inactivas.
    """

    __tablename__ = "web_sessions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WebBindingCode(Base):
    """Código de enlace Web single-use (Fase 10.0).

    Permite a un usuario EXISTENTE vincular credenciales Web a su cuenta: el
    código lo genera el asistente en Telegram (tool `link_web`) y va atado al
    email que el propio usuario escribirá en el formulario Web. `code_hash` es
    el SHA-256 del código. El consumo es atómico y single-use: la constraint
    única (user_id, email) garantiza un solo código pendiente por par.
    """

    __tablename__ = "web_binding_codes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(255))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "email", name="uq_web_binding_codes_user_email"),
        CheckConstraint("length(code_hash) = 64", name="ck_web_binding_codes_code_hash"),
    )
