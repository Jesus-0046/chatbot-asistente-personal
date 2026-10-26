# Asistente (Fase 1 + Fase 2 + Fase 2.1 + Fase 3 + Fase 3.2 + Fase 3.3)

Asistente personal inteligente vía Telegram. **Fase 1**: núcleo de conversación
completo — orquestador canal-agnóstico, capa de IA (LLM + function calling),
herramientas con validación Pydantic, confirmaciones explícitas para acciones
sensibles, memoria de usuario (reglas) y persistencia de historial.

**Fase 2**: agenda personal — eventos (citas, cumpleaños y fechas importantes),
tareas con vencimiento, recordatorios persistentes enviados por el bot sin
conversación activa (poller), fechas en lenguaje natural y recurrencias.

**Fase 2.1** (endurecimiento): claim atómico de recordatorios con lease
(`processing` + `claimed_at`), sincronización de recordatorios vinculados a
tareas recurrentes, duración de evento separada del fin de serie, desplazamiento
de la serie al editar un evento recurrente y fechas "el día N" estrictas (sin
ajustes silenciosos a días imposibles).

**Fase 3** (núcleo financiero): gestión de cuentas bancarias, tarjetas de
crédito y efectivo; registro de gastos e ingresos clasificados por categoría;
transferencias entre cuentas; saldos derivados (patrimonio, liquidez, deuda de
tarjetas) calculados en tiempo real; resumen de gastos por categoría/comercio;
herramientas de finanzas conversacionales vía LLM.

**Fase 3.2** (presupuestos y ahorro): presupuestos mensuales generales o por
categoría con estado (normal/warning/exceeded); objetivos de ahorro como metas
con contribuciones y progreso; una contribución nunca crea gastos ni
transferencias automáticas (y si se vincula a una transferencia real, no cambia
el patrimonio). El ahorro real son los saldos de cuentas `savings`.

**Fase 3.3** (recurrencias financieras): recurrencias (diaria/semanal, mensual,
anual) ancla-preservantes (31 → 28/29/feb → 31 → 30...), pagos planificados
generados de forma perezosa y NO confirmados (no tocan saldos hasta que se
confirman), suscripciones que crean su recurrencia espejo, cancelación que
conserva el histórico y desactiva/cancela lo futuro, y herramientas de finanzas
conversacionales para todo ello.

## Estado

- [x] Base Python 3.12 async (sin frameworks superfluos)
- [x] Configuración por variables de entorno (`pydantic-settings`)
- [x] Logging estructurado JSON con redacción de secretos (`structlog`)
- [x] PostgreSQL 16 vía Docker Compose con healthcheck
- [x] Migraciones Alembic (async) hasta `0007` (conversación, confirmaciones, memoria, agenda, claim Fase 2.1, finanzas Fase 3, presupuestos y ahorro Fase 3.2, recurrencias/planificados/suscripciones Fase 3.3)
- [x] Adaptador Telegram con `/start`, whitelist de propietarios y poller de recordatorios (JobQueue)
- [x] Mensajes normalizados canal-agnósticos (`InboundMessage`/`OutboundMessage` únicos en `application/context.py`)
- [x] Orquestador de conversación canal-agnóstico (rate limit → identidad → bucle LLM → persistencia)
- [x] Capa IA con `LLMClient` (protocolo), `OpenAIClient` y system prompt versionado
- [x] `ToolRegistry` con validación Pydantic y gating de confirmación (10 tools de Fase 1 + 14 de Fase 2 + 12 de Fase 3 + 5 de presupuestos + 5 de ahorro + 16 de Fase 3.3 = 62)
- [x] Confirmaciones explícitas antes de acciones sensibles (TTL, `sí`/`no`, aislamiento por usuario)
- [x] Reglas de memoria propuestas (inactivas) y activables solo tras confirmación
- [x] Rate limiting básico in-memory por usuario
- [x] Aislamiento por usuario (el LLM nunca accede a la BD; solo servicios de dominio)
- [x] Fase 2: eventos/tareas/recordatorios con fechas en lenguaje natural y recurrencias
- [x] Fase 2: `ReminderPoller` con reintento, ventana de recuperación y envío por Telegram
- [x] Fase 2.1: claim atómico de recordatorios (lease + `claimed_at`), sincronización de recordatorios de tareas recurrentes, edición de series recurrentes y fechas "el día N" estrictas
- [x] Fase 3: cuentas (bancos, tarjetas de crédito, efectivo), gastos/ingresos con clasificación automática de categorías, transferencias entre cuentas, saldos derivados (patrimonio/liquidez/deuda de tarjetas) y resumen de gastos
- [x] Fase 3.1.1: saneamiento del núcleo financiero — moneda única EUR (rechazo explícito de otras monedas), saldo inicial por tipo de cuenta (tarjetas de crédito con deuda inicial, `<= 0`), protección del `initial_balance` (no modificable con movimientos/traspasos), coherencia de moneda en movimientos y traspasos (sin migración: solo validación de dominio/servicio)
- [x] Fase 3.2: presupuestos mensuales (generales o por categoría) con estado, objetivos de ahorro con contribuciones, vinculación opcional de contribuciones a transferencias reales (sin doble contabilización) y bonus del ahorro real (saldos de cuentas `savings`)
- [x] Fase 3.3: recurrencias (ancla-preservantes, `next_due_at` UTC, generación perezosa e idempotente de pagos planificados), planificados manuales con anti-duplicación, conversión a transacción solo al confirmar (atómica), suscripciones con recurrencia espejo y cancelación que conserva histórico
- [x] Tests unitarios (496), Ruff y mypy
- [ ] Tests de integración con PostgreSQL real (pendiente de disponer de la BD)
- [ ] Fases posteriores: personas/deudas, gastos compartidos, memoria avanzada

## Verificación manual pendiente

Ninguna de estas comprobaciones se ha ejecutado en este entorno:

- Docker real **no se ha ejecutado**: el `Dockerfile` y `docker-compose.yml` están
  validados estáticamente, pero el entorno no tiene Docker instalado.
- Telegram real **no se ha probado con un token real**: el ensamblado, la
  whitelist y el poller de recordatorios están cubiertos por tests unitarios,
  pero no se ha hecho `/start` ni se ha recibido un recordatorio real contra el
  servidor de Telegram.
- OpenAI real **no se ha probado con una API key real**: el cliente y el flujo de
  function calling están cubiertos por fakes en tests, pero no se ha hecho una
  llamada real contra la API.
- Los tests de integración PostgreSQL quedan **pendientes** hasta disponer de una
  PostgreSQL 16 (`TEST_DATABASE_URL`); sin ella se omiten automáticamente. En
  particular, `claim_due` con `FOR UPDATE SKIP LOCKED` solo se ejerce contra PG.

## Requisitos

- Python 3.12+
- Docker + Docker Compose (para la base de datos y el bot en contenedor)
- Un bot de Telegram creado en [@BotFather](https://t.me/BotFather) (token)
- Una API key de OpenAI (`OPENAI_API_KEY`)

## Puesta en marcha (recomendada)

### 1) Configurar entorno

Copie `.env.example` a `.env` y rellénelo:

```bash
cp .env.example .env
```

- `TELEGRAM_TOKEN`: token del bot (nunca lo comitee).
- `OWNER_TELEGRAM_IDS`: lista JSON de IDs autorizados, p. ej. `[123456789]`
  (obtenga su ID con [@userinfobot](https://t.me/userinfobot) o similar).
- `OPENAI_API_KEY`: clave de la API de OpenAI.
- `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB`: credenciales de PostgreSQL.

### 2) Levantar PostgreSQL

```bash
docker compose up -d db
docker compose ps        # db debe estar "healthy"
```

### 3) Instalar y ejecutar en local

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell
pip install -e ".[dev]"
alembic upgrade head
python -m asistente.main
```

Con el bot arrancado, abra su chat con el bot y envíe `/start`. Un usuario
**no autorizado** recibe un mensaje de rechazo. Después, chatee con el asistente:
puede crear notas, pedir la fecha/hora, proponer reglas de memoria (siempre con
confirmación explícita antes de activarlas) y gestionar su agenda:

- "apunta una reunión con Ana el lunes a las 10"
- "regar las plantas todos los lunes y jueves"
- "recuérdame un día antes del cumple de Luca" (recordatorio vinculado)
- "¿qué tengo esta semana?" (agenda del periodo)

Finanzas (Fase 3):

- "crea una cuenta 'La Caixa' tipo banco con 1000 euros de saldo inicial"
- "apunta 28,80 de Mercadona" (clasifica la categoría automáticamente)
- "he cobrado la nómina de 1500 €"
- "paso 100 euros de la cuenta corriente a la cuenta de ahorro" (traspaso)
- "¿cuál es mi patrimonio y mi deuda de tarjetas?" (saldos derivados)
- "¿cuánto me he gastado este mes en comida?" (resumen por categoría)

Presupuestos y ahorro (Fase 3.2):

- "crea un presupuesto de 500 € al mes para Ocio" o "presupuesto general de 1200 €"
- "¿cómo va el presupuesto de la compra este mes?" (estado normal/warning/exceeded)
- "quiero ahorrar 3000 € para el coche antes de agosto de 2027" (objetivo de ahorro)
- "he metido 200 € al ahorro este mes" (contribución; sugería vincular a la transferencia real)
- "¿cuánto me falta para el coche?" (progreso del objetivo)

Recurrencias, planificados y suscripciones (Fase 3.3):

- "el alquiler de 850 € se paga el día 1 de cada mes" (recurrencia mensual)
- "¿qué pagos tengo este mes?" (lista de planificados; generación perezosa)
- "recuerda que Netflix son 13,99 € al mes" (suscripción con recurrencia espejo)
- "confirmé el pago de Netflix" (confirma un planificado → transacción posted)
- "la factura de la luz ya no se domicilia" (cancela recurrencia/suscripción conservando histórico)

Un detalle importante: pagar la tarjeta de crédito desde el banco es un
**traspaso**, no un gasto (la compra con tarjeta ya fue registrada como gasto).
Igual que las transferencias y las contribuciones a objetivos de ahorro: no son
gastos y no cambian el patrimonio neto.

Los recordatorios se envían automáticamente aunque no haya conversación activa
(cada `REMINDER_POLLER_INTERVAL_SECONDS`, por defecto 30 s).

### 4) Otras opciones

- **Todo vía Docker** (producción 24/7): `cp .env.example .env`, rellena los
  secretos y `docker compose up -d --build`. Levanta `db`, `migrate` (aplica
  migraciones una vez), `bot` (Telegram + WhatsApp + pollers) y `api` (Web API).
  Ver `DEPLOYMENT.md`, `OPERATIONS.md` y `BACKUP_RESTORE.md`.
- **Probar en la BD** (si no usa Docker o hay otra PostgreSQL 16 disponible):

  ```bash
  TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@localhost:5432/asistente pytest tests/integration
  ```

---

## Tests, lint y tipos

```bash
pytest                          # tests unitarios
pytest -m db                    # tests de integración con TEST_DATABASE_URL
ruff check .                    # lint
ruff format --check .           # formato
mypy src                        # tipos
```

## Estructura

```
.
├── pyproject.toml
├── alembic.ini
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── migrations/
│   ├── env.py                  # Alembic async, URL desde Settings
│   └── versions/               # 0001 baseline + 0002 Fase 1 + 0003-0004 Fase 2 + 0005 Fase 3 + 0006 Fase 3.2 + 0007 Fase 3.3
├── src/asistente/
│   ├── main.py                 # entrypoint (cablea capas y arranca Telegram)
│   ├── config.py               # pydantic-settings (secretos por entorno)
│   ├── logging_setup.py        # structlog JSON + redacción de secretos
│   ├── adapters/
│   │   ├── base.py             # ChannelAdapter (interfaz de canal)
│   │   └── telegram/
│   │       ├── bot.py          # Application PTB + polling + JobQueue (poller)
│   │       ├── handlers.py     # /start, whitelist y mensajes de texto
│   │       ├── formatter.py    # escape HTML seguro
│   │       └── notification.py # envío de recordatorios por Telegram
│   ├── application/
│   │   ├── context.py          # InboundMessage/OutboundMessage (única definición)
│   │   ├── orchestrator.py     # flujo de conversación canal-agnóstico
│   │   ├── confirmations.py    # confirmaciones genéricas + clasificador sí/no
│   │   ├── ratelimit.py        # rate limiting in-memory
│   │   ├── notifications.py    # NotificationSender (protocolo canal-agnóstico)
│   │   └── services.py         # Services (inyección de dependencias)
│   ├── ai/
│   │   ├── llm.py              # LLMClient (protocolo) + OpenAIClient
│   │   └── prompts.py          # system prompt versionado (v2.3.0)
│   ├── tools/
│   │   ├── registry.py         # ToolRegistry + validación Pydantic + confirmación
│   │   ├── context.py          # ToolContext (user_id autenticado)
│   │   ├── definitions.py      # tools de Fase 1
│   │   ├── agenda.py           # tools de Fase 2 (eventos/tareas/recordatorios/agenda)
│   │   ├── finance.py          # tools de Fase 3 (cuentas/movimientos/traspasos/saldos/resumen)
│   │   ├── budgets.py          # tools de Fase 3.2 (presupuestos)
│   │   ├── savings.py          # tools de Fase 3.2 (objetivos de ahorro)
│   │   ├── recurring.py        # tools de Fase 3.3 (recurrencias financieras)
│   │   ├── planned.py          # tools de Fase 3.3 (pagos planificados)
│   │   └── subscriptions.py    # tools de Fase 3.3 (suscripciones)
│   ├── domain/
│   │   ├── users.py            # UserService
│   │   ├── notes.py            # NoteService
│   │   ├── memory.py           # MemoryService (propuesta → activación)
│   │   ├── events.py           # EventService (citas, cumpleaños, fechas)
│   │   ├── tasks.py            # TaskService (vencimientos, completar)
│   │   ├── reminders.py        # ReminderService (vinculados/sueltos, recurrencias)
│   │   ├── agenda.py           # AgendaService (expansión de recurrentes por periodo)
│   │   ├── accounts.py         # AccountService Fase 3 (cuentas, default, activación)
│   │   ├── transactions.py     # TransactionService Fase 3 (gastos/ingresos, categorías)
│   │   ├── transfers.py        # TransferService Fase 3 (traspasos entre cuentas)
│   │   ├── finance.py          # FinanceService Fase 3 (saldos y resumen de gastos)
│   │   ├── budgets.py          # BudgetService Fase 3.2 (presupuestos y estados)
│   │   ├── savings.py          # SavingsGoalService Fase 3.2 (objetivos y contribuciones)
│   │   ├── recurring.py        # RecurringTransactionService Fase 3.3 (recurrencias, generación perezosa)
│   │   ├── planned.py          # PlannedTransactionService Fase 3.3 (pagos planificados, confirmación)
│   │   ├── subscriptions.py    # SubscriptionService Fase 3.3 (suscripciones, espejo con recurring)
│   │   ├── amounts.py          # Decimal/Numeric(12,2), parseo y formato de importes
│   │   └── categories.py       # clasificación automática de categorías
│   ├── persistence/
│   │   ├── db.py               # engine async y Base
│   │   ├── models.py           # User, Conversation, Confirmación, Memoria, Nota + Fase 2 + Fase 3 + Fase 3.2 + Fase 3.3
│   │   └── repositories/       # acceso a datos scoped por user_id
│   ├── scheduler/
│   │   └── reminder_poller.py  # ReminderPoller (claim_due → send → advance/sent)
│   └── shared/
│       ├── errors.py           # BusinessError (incluye NotFoundError)
│       ├── timeutil.py         # UTC/zonas horarias y formato local
│       ├── dateparse.py        # fechas en lenguaje natural (es-ES)
│       ├── recurrence.py       # motor de recurrencias
│       └── language.py         # prioridades y recurrencias en natural
└── tests/
    ├── unit/
    ├── integration/            # requieren TEST_DATABASE_URL
    ├── fixtures/
    └── e2e/
```

## Seguridad

- Los secretos solo llegan por variables de entorno (`.env`, ignorado por git).
- El logging estructura JSON y **redacta** cualquier campo que parezca token,
  contraseña o API key antes de emitirlo.
- La whitelist de Telegram controla quién puede usar el bot.
- El LLM **nunca accede a la BD**: las tools solo reciben `ToolContext` con
  `user_id` autenticado y servicios de dominio.
- Las acciones sensibles (borrar notas, borrar eventos, cancelar tareas o
  recordatorios, activar/revocar memoria, borrar presupuestos o cancelar
  objetivos de ahorro, cancelar recurrencias, pagos planificados o
  suscripciones) requieren una confirmación explícita con expiración
  (TTL).
- Todas las operaciones financieras están aisladas por `user_id`: un usuario
  solo puede ver y modificar sus propias cuentas, movimientos y transferencias.
  Los saldos se calculan dinámicamente (nunca se almacenan como valores
  derivados) para evitar inconsistencias.
- Las fechas se interpretan en la zona horaria del usuario y se persisten como
  UTC aware; los recordatorios se envían solo por el canal autenticado del
  usuario (nunca entre usuarios).

## Roadmap (resumen)

- **Fase 1 (hecha)**: orquestador, capa IA con LLM y herramientas utilitarias,
  confirmaciones y memoria de usuario.
- **Fase 2 (hecha)**: agenda (eventos, cumpleaños y fechas importantes, tareas
  con vencimiento, recordatorios persistentes con poller) y recurrencias.
- **Fase 2.1 (hecha)**: endurecimiento — claim atómico de recordatorios con
  lease, recordatorios de tareas recurrentes, series recurrentes editables y
  fechas "el día N" estrictas.
- **Fase 3 (hecha)**: núcleo financiero — cuentas (bancos, tarjetas de crédito,
  efectivo), gastos/ingresos con clasificación automática, transferencias entre
  cuentas y saldos derivados (patrimonio, liquidez, deuda de tarjetas).
- **Fase 3.2 (hecha)**: presupuestos mensuales (generales o por categoría) con
  estado normal/warning/exceeded y objetivos de ahorro con contribuciones
  (metas; sin doble contabilización con transferencias reales).
- **Fase 3.3 (hecha)**: recurrencias financieras (diaria/semanal, mensual y
  anual ancla-preservantes), pagos planificados (generación perezosa, solo se
  convierten en transacción al confirmar) y suscripciones (recurrencia espejo,
  cancelación que conserva histórico). NO implementado: deudas, gastos
  compartidos, OCR de recibos, IA financiera predictiva, recomendaciones y
  notificaciones proactivas/scheduler complejo.
- **Fase 4+**: personas/deudas, gastos compartidos, memoria avanzada.

Detalle completo en [`PLAN_TECNICO.md`](PLAN_TECNICO.md).