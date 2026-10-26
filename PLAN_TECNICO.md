# PLAN TÉCNICO — Asistente Personal Inteligente vía Telegram

> **Estado:** Documento de diseño. No se ha escrito código todavía.
> **Alcance:** Fase inicial centrada en Telegram, con arquitectura preparada para WhatsApp, web y móvil.
> **Idioma del sistema:** Español (es-ES). Fechas en formato local del usuario.

---

## 1. Arquitectura general

Arquitectura **modular en capas** con un núcleo de negocio independiente de Telegram (estilo *hexagonal/clean* adaptado a un bot asíncrono).

```
┌──────────────────────────────────────────────────────────────┐
│  ADAPTADORES (interfaces externas)                          │
│  Telegram ── WhatsApp ── Web/API ── App móvil (futuro)      │
└───────────────────────────┬──────────────────────────────────┘
                            │  Mensaje normalizado (texto, chat, user, adjuntos)
┌───────────────────────────▼──────────────────────────────────┐
│  CAPA DE APLICACIÓN (orquestador)                            │
│  - Orquesta la conversación                                  │
│  - Gestiona confirmaciones pendientes                        │
│  - Construye contexto (memoria + perfil)                     │
└───────────────────────────┬──────────────────────────────────┘
                            │  Llamada con herramientas (function calling)
┌───────────────────────────▼──────────────────────────────────┐
│  CAPA DE IA (interfaz LLM intercambiable)                    │
│  - Cliente LLM (OpenAI ahora; abstraído)                     │
│  - Builder de prompts                                         │
│  - Parseo estructurado (Pydantic + function calling)          │
└───────────────────────────┬──────────────────────────────────┘
                            │  Llamadas a herramientas (acciones verificadas)
┌───────────────────────────▼──────────────────────────────────┐
│  REGISTRO DE HERRAMIENTAS (Tools)                            │
│  create_expense, create_event, create_reminder, ...          │
│  - Schema validado con Pydantic                              │
│  - Permisos + tasa + aislamiento por usuario                 │
└───────────────────────────┬──────────────────────────────────┘
                            │
┌───────────────────────────▼──────────────────────────────────┐
│  DOMINIO (lógica de negocio, sin IA ni Telegram)             │
│  Accounts/Transfers · Recurring · Forecast · Expenses ·      │
│  Calendar · Reminders · Budgets · Savings · People/Debts ·   │
│  Vehicle · House · Health · Trips · Memory                   │
└───────────────────────────┬──────────────────────────────────┘
                            │  Repositorios (scope user_id SIEMPRE)
┌───────────────────────────▼──────────────────────────────────┐
│  PERSISTENCIA  PostgreSQL + SQLAlchemy(async) + Alembic      │
└──────────────────────────────────────────────────────────────┘

A un lado:  Scheduler (procesa recordatorios vencidos → envía por el adaptador)
            Seguridad · Logs estructurados · Configuración
```

**Regla de oro:** la IA **nunca** escribe SQL directamente. Solo ejecuta herramientas del registro, que aíslan por usuario y validan cada parámetro.

---

## 2. Tecnologías recomendadas

| Área | Elección | Alternativas consideradas | Por qué esta elección |
|---|---|---|---|
| Lenguaje | **Python 3.12+** (asyncio) | Node.js/TypeScript | Ecosistema IA y bots muy maduro; async idóneo para long-polling + LLM; el desarrollador lo conoce |
| Bot Telegram | **python-telegram-bot v22** | aiogram 3.x | Documentación extensa, soporte webhook y polling, estabilidad; si se migra, la capa de adaptadores aísla el cambio |
| IA / LLM | **OpenAI (gpt-4o / o-series)** vía interfaz propia | Claude, LiteLLM, modelos locales (Ollama) | Excelente en function calling y extracción NL→JSON; se abstrae detrás de `ai/llm.py` para poder cambiar de proveedor o pasar a local |
| Framework de IA | **API directa + propio registro de tools** | LangChain, CrewAI | LangChain añade complejidad y abstracciones que ocultan los esquemas y permisos; con pocos tools es más control, menos coste, más transparencia |
| Base de datos | **PostgreSQL 16** | SQLite, MySQL | JSONB para memoria, consultas analíticas de gastos, concurrencia y futuro multi-usuario; SQLite limitaría estadísticas agregadas |
| ORM | **SQLAlchemy 2.0 (async) + Alembic** | SQLModel | SQLAlchemy es el estándar maduro; Alembic da migraciones versionadas; SQLModel es más nuevo y atado a FastAPI |
| Validación | **Pydantic v2** | marshmallow | Integración con esquemas de tools y configuración, validaciones de tipos/Decimal/fechas |
| Scheduler | **Bucle propio sobre tabla `reminders`** (consulta pendientes cada ~20 s) | APScheduler, Celery+Beat | Un poller sobre la BD es más simple, durable y testeable que jobs en memoria; las ocurrencias recurrentes se materializan como filas |
| Config | **pydantic-settings** (variables de entorno) | dotenv pelado | Validación tipada de secretos y opciones, fail-fast |
| Tests | **pytest + pytest-asyncio + testcontainers(Postgres)** + respuestas LLM grabadas (VCR-style) | unittest, mocks manuales | Tests deterministas y sin coste de API; fixture de LLM grabadas |
| Contenedores | **Docker + docker-compose** | — | Entorno reproducible, fácil de desplegar, BD y app en un compose |
| Logs | **structlog** (JSON estructurado) | logging estándar | Logs analizables, redacción de datos sensibles por diseño |
| Dinero | **decimal.Decimal** + columna `NUMERIC(12,2)` | float | Los floats rompen cálculos monetarios; Decimal es exacto |
| HTTP/IO | **httpx** (asyncio) | requests | Async, retries, timeouts por defecto |

**Nota de diseño IA:** se usa *function calling* con esquemas Pydantic. El LLM pide ejecutar herramientas; el orquestador valida, ejecuta y devuelve resultados. Esto da control total, trazabilidad y confirma/deniega acciones.

---

## 3. Estructura de carpetas

```
asistente/
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── alembic.ini
├── alembic/
│   └── versions/
├── src/asistente/
│   ├── __init__.py
│   ├── main.py                  # entrypoint: arranca bot + scheduler
│   ├── config.py                # pydantic-settings (secretos por env)
│   ├── logging_setup.py         # structlog JSON + redacción
│   │
│   ├── adapters/                # 🔌 TODO lo que toca el mundo exterior
│   │   ├── base.py              # ChannelAdapter (interfaz)
│   │   ├── messages.py          # InboundMessage / OutboundMessage / buttons
│   │   └── telegram/
│   │       ├── bot.py           # PTB app, polling o webhook
│   │       ├── handlers.py      # handlers de texto/adjuntos/callbacks
│   │       └── formatter.py     # Markdown/HTML seguro para Telegram
│   │   # futuro: web/ (FastAPI), whatsapp/
│   │
│   ├── application/             # 🧠 orquestación (sin Telegram)
│   │   ├── orchestrator.py      # flujo del mensaje entrante
│   │   ├── context.py           # contexto de conversación (memoria corta)
│   │   ├── confirmations.py     # confirmaciones pendientes (state machine)
│   │   └── responses.py         # genera respuestas al usuario
│   │
│   ├── ai/                      # 🤖 capa IA (SDK intercambiable)
│   │   ├── llm.py               # Interfaz LLM + implementación OpenAI
│   │   ├── prompts.py           # system prompt, intrucciones, ejemplos
│   │   ├── extraction.py        # parseo estructurado con tools
│   │   └── images.py            # (fase imágenes) OCR/Visión para tickets
│   │
│   ├── tools/                   # 🔧 registro de herramientas IA
│   │   ├── registry.py          # registro + ejecutor con permisos/tasa
│   │   ├── accounts.py          # cuentas, saldos, transferencias
│   │   ├── expenses.py          # ingresos/gastos + correcciones
│   │   ├── calendar.py
│   │   ├── reminder.py
│   │   ├── recurring.py         # recurrentes, suscripciones, previstos
│   │   ├── tasks.py
│   │   ├── budget.py
│   │   ├── savings.py           # ahorro real + objetivos + aportaciones
│   │   ├── forecast.py          # previsión mensual
│   │   ├── people.py
│   │   ├── shared.py            # gastos compartidos
│   │   ├── vehicle.py
│   │   ├── shopping.py
│   │   ├── health.py
│   │   ├── trips.py
│   │   ├── memory.py
│   │   └── summary.py
│   │
│   ├── domain/                  # 📐 lógica de negocio pura
│   │   ├── account_service.py   # saldos calculados, transferencias (doble entrada)
│   │   ├── calendar_service.py
│   │   ├── expense_service.py
│   │   ├── forecast_service.py  # previsión mensual y disponible estimado
│   │   ├── recurrence_service.py # plantillas → próximos pagos/ingresos
│   │   ├── reminder_service.py  # cálculo de recurrencias puro
│   │   ├── budget_service.py    # verificación de límites/alerta
│   │   ├── savings_service.py   # ahorro real, objetivos y aportaciones
│   │   ├── people_service.py
│   │   ├── vehicle_service.py
│   │   ├── classifier.py        # pipeline de clasificación (gastos + cuenta)
│   │   ├── memory_service.py    # reglas de memoria
│   │   └── summary_service.py   # resúmenes consolidados
│   │
│   ├── persistence/
│   │   ├── db.py                # engine async, sesiones
│   │   ├── models.py            # modelos declarativos
│   │   └── repositories/        # scoped por user_id ALWAYS
│   │
│   ├── scheduler/
│   │   ├── runner.py            # bucle que dispara recordatorios
│   │   └── dispatch.py          # envía vía adaptador del canal
│   │
│   └── shared/
│       ├── money.py             # helpers Decimal
│       ├── timeutil.py          # timezone, fechas relativas, recurrencias
│       ├── ids.py               # uuids
│       └── errors.py
│
└── tests/
    ├── unit/                    # lógica pura (recurrencias, clasificador, Decimal)
    ├── integration/             # repositorios vs PostgreSQL real en contenedor
    ├── fixtures/                # respuestas LLM grabadas (VCR) y datos de prueba
    └── e2e/                     # flujos completos (mock LLM + fake bot)
```

---

## 4. Modelo de datos inicial

Todas las tablas incluyen `user_id` (clave foránea a `users`) para garantizar aislamiento. Solo `users`, `categories` (builtins) y catálogos compartidos se omiten de este scope.

### Persona y configuración
- **users** — `id`, `telegram_user_id` (único), `name`, `username`, `timezone` (IANA, p.ej. `Europe/Madrid`), `lang`, `settings JSONB`, `created_at`.

### Comercios, categorías y memoria
- **categories** — `id`, `user_id` (nullable para builtins), `parent_id` (nullable → subcategoría), `name`, `kind` (`expense|income`), `builtin`, `order`. Ejemplo: `transporte` → hijo `combustible`.
- **merchants** — `id`, `user_id`, `name`, `name_normalized`, `default_category_id`, `default_account_id` (nullable: "Mercadona suele ir a la tarjeta"), `is_user_rule` (si el usuario confirmó "Mercadona → alimentación"), `created_at`. Esta tabla es la base de la clasificación automática.
- **memory_rules** — `id`, `user_id`, `kind` (`merchant_category` | `keyword_category` | `account_keyword` | `preference`), `key`, `value JSONB`, `scope`, `source` (`user|proposed|ai`), `active`, `confirmed_at`, `created_at`. Reglas aprendidas que el usuario ha aceptado explícitamente.
- **keyword_rules** — podría colapsarse en `memory_rules` con kind `keyword_category`: "cuando diga `gasolina` → `transporte/combustible`"; y con kind `account_keyword`: "cuando diga `con la tarjeta` → cuenta `Tarjeta`". *(Se implementa dentro de memory_rules.)*

### Dinero: cuentas, saldos y movimientos

- **accounts** — `id`, `user_id`, `name` (banco, ahorro, efectivo, Visa, ...), `type` (`bank|savings|cash|credit_card|other`), `currency` (EUR por defecto), `initial_balance NUMERIC(12,2)` (0 por defecto; se crea al alta), `is_default` (cuenta por defecto para clasificación sin cuenta explícita), `active`, `sort_order`, `created_at`.
  - **Tipos de cuenta:**
    - `bank|savings|cash|other` son **cuentas de dinero real** (activos): el gasto reduce su saldo y el ingreso lo aumenta. `savings` es dinero que el usuario decide "no tocar" (ahorro real). `cash` es efectivo. `other` cubre monederos digitales o cuentas no bancarias.
    - `credit_card` es una **tarjeta de crédito**: NO contiene dinero real, es deuda. Su saldo es habitualmente negativo (≤ 0): cada gasto con ella lo hace más negativo (más deuda) y cada pago desde el banco (`create_transfer banco→tarjeta`) lo acerca a 0 (reduce la deuda). Nunca es `is_default`.
    - **La tarjeta de débito NO es una cuenta aparte:** es el acceso a una cuenta `bank` (la corriente a la que está asociada). "Con la tarjeta" / "con la tarjeta de débito" se resuelve a esa cuenta `bank`; no crea dinero adicional ni deuda (§21.4).
  - **Decisión — balance calculado, no almacenado:** el saldo **nunca se guarda** como columna; se **deriva por cálculo** a partir de `initial_balance + Σ ingresos − Σ gastos + Σ transferencias entrantes − Σ transferencias salientes` sobre `transactions` y `transfers` con `status=confirmed`. Es la única fuente de verdad, evita inconsistencias y enriquece las consultas ("¿cuánto dinero tengo?"). Solo si en el futuro el volumen lo exige se añadirá una caché de saldo con columna `balance` recalculable, **nunca** autoritativa.
- **transactions** — `id`, `user_id`, `type` (`expense|income`), `amount NUMERIC(12,2)`, `currency`, `account_id` FK nullable (si el usuario no indica cuenta en mensajes sin rastro), `merchant_id`, `merchant_name_raw` (texto original), `category_id`, `occurred_at` (timestamp TZ), `note`, `source` (`manual|ai|correction|transfer`), `status` (`pending|confirmed|cancelled`), `trip_id` nullable (FK `trips`), `created_at`.
  - `status=pending` es clave para la gestión de incertidumbre (§11). Las transferencias **no** crean filas en esta tabla (ver `transfers`).
- **transfers** — `id`, `user_id`, `from_account_id`, `to_account_id`, `amount NUMERIC(12,2)`, `currency`, `occurred_at`, `note`, `status` (`pending|confirmed|cancelled`), `created_at`.
  - Movimiento entre cuentas propias: **NO se registra como gasto ni ingreso**. El sistema genera dos impactos equivalentes de signo opuesto (salida de la cuenta origen + entrada en la cuenta destino) y actualiza los saldos calculados correspondientemente (§21.3).
- **recurring_transactions** (ingresos y gastos recurrentes) — `id`, `user_id`, `type` (`expense|income`), `title` ("Nómina", "Alquiler", "Netflix"), `amount NUMERIC(12,2)`, `currency`, `account_id` FK nullable, `category_id` FK nullable, `frequency` (`monthly|yearly|weekly|custom` con `interval`), `start_date`, `end_date` nullable, `status` (`active|inactive`), `created_at`. Sirve para nóminas, alquileres, hipotecas, suscripciones y cualquier pago/cobro reiterado, tanto de ingresos como de gastos.
  - **Conversión de recurrente → previsto → realizado (sin duplicados):** mientras el recurrente está activo, genera su próximo `planned_transactions` en cada `due_date` (materialización). Cuando el usuario confirma el movimiento o registra el gasto/ingreso real, ese previsto se convierte (`status=converted`) y se crea su `transactions` equivalente: el mismo evento **nunca** aparece dos veces (una sola fila `transactions`, y el previsto se cierra).
  - **Cancelar un recurrente no borra histórico:** `cancel_recurring` solo pone `status=inactive` (deja de generar futuros previstos); los movimientos ya confirmados permanecen intactos. Editar `amount/frequency/due_date` no reescribe los movimientos pasados; solo afecta a las próximas ocurrencias.
- **subscriptions** — `id`, `user_id`, `name` (Netflix, Spotify, Gimnasio, Amazon Prime...), `amount NUMERIC(12,2)`, `billing_period` (`monthly|yearly`), `next_billing_date`, `recurring_id` FK nullable (vínculo a `recurring_transactions` si se modela como recurrente), `category_id`, `active`, `created_at`. Módulo dedicado de suscripciones para responder "¿cuánto gasto al mes/año en suscripciones?" y detectar aumentos de precio.
  - **Compatibilidad sin doble gasto:** una suscripción es **o bien** independiente (alimenta la previsión con su `next_billing_date`) **o bien** está vinculada a un `recurring_transactions` (`recurring_id`), en cuyo caso el recurrente es la única fuente de verdad que genera el previsto y la suscripción es solo una vista. Nunca se crean dos previstos/gastos por el mismo concepto: `get_subscriptions_summary` consulta `subscriptions`, mientras `get_upcoming_payments` consulta recurrente/`planned`; el vínculo `recurring_id` garantiza que no se suman ambas fuentes para el mismo importe.
- **planned_transactions** (movimientos previstos, NO confirmados) — `id`, `user_id`, `type` (`expense|income`), `amount NUMERIC(12,2)`, `currency`, `title`, `category_id`, `account_id` nullable, `due_date` (fecha prevista, p.ej. "día 28 cobraré"), `recurring_id` nullable (si proviene de un recurrente), `status` (`pending|confirmed|converted|cancelled`), `created_at`.
  - **Separación explícita:** los movimientos previstos (`planned_transactions`) **nunca se mezclan** con los realizados (`transactions`). No suman en saldos ni en estadísticas de gasto real; solo alimentan la previsión (§21). Al confirmarse, se convierte: se crea su `transactions` equivalente y se marca `status=converted`.

### Presupuestos, ahorro y previsión
- **budgets** — `id`, `user_id`, `category_id` (nullable → presupuesto global), `amount`, `period` (`monthly|yearly`), `starts_on`, `active`, `alert_threshold_pct` (90), `created_at`.
- **budget_alerts** — `id`, `budget_id`, `period_key`, `triggered_at` — evita alertas duplicadas en el mismo periodo.
- **savings_goals** — `id`, `user_id`, `name`, `target_amount NUMERIC(12,2)`, `currency`, `target_date`, `start_date`, `status` (`active|achieved|paused`), `created_at`.
  - **Diferenciación explícita de "ahorro verdadero" vs "objetivo":** el **ahorro real en efectivo** se obtiene de `accounts` con `type='savings'` (saldos calculados). Un **objetivo de ahorro** (`savings_goals`) es solo una meta declarada. Las **aportaciones** al objetivo se registran en `savings_contributions`, normalmente respaldadas por una transferencia hacia una cuenta de ahorro (o por un ingreso destinado a ahorro). Así "he ahorrado 300 € este mes" (aportación/transferencia) y "tengo 300 € ahorrados" (saldo de cuenta de ahorro) son conceptos distintos y no se confunden.
- **savings_contributions** — `id`, `goal_id`, `amount NUMERIC(12,2)`, `occurred_at`, `transfer_id` nullable (si la aportación es una transferencia hacia cuenta de ahorro), `transaction_id` nullable, `note`.
- **forecast_snapshots** (opcional/caché) — `id`, `user_id`, `period_key` (`2025-06`), `json JSONB` (ingresos previstos, gastos fijos, disponible estimado), `computed_at`. Caché de previsión para acelerar respuestas repetidas; se recalcula sobre datos reales — **nunca invalida al dato calculado en vivo**.

### Agenda, tareas y recordatorios
- **events** — `id`, `user_id`, `title`, `kind` (`appointment|event|birthday|important`), `starts_at`, `ends_at`, `all_day`, `location`, `notes`, `person_id` nullable, `created_at`. Las citas médicas y fechas importantes viven aquí.
- **tasks** — `id`, `user_id`, `title`, `notes`, `due_at`, `priority`, `status` (`todo|done|cancelled`), `domain` (`general|home|car|travel|health|work`), `trip_id` nullable, `completed_at`.
- **reminders** — `id`, `user_id`, `title`, `due_at` (UTC), `timezone` (del usuario), `recurrence JSONB` (spec: `once|daily|weekly|monthly|yearly|days_before`, con campos `interval`, `anchor`, `days_before`), `origin` (`event_id|task_id|recurring_id|planned_id` nullable), `status` (`pending|sent|failed|cancelled`), `attempts`, `last_error`, `created_at`.
  - Las recurrencias se **materializan**: una fila por ocurrencia pendiente. Al dispararse, se crea la siguiente. Esto hace el sistema robusto y auditable.
  - Vínculo con finanzas: un pago pendiente (`planned_transactions`) puede generar su `reminder` con `origin=planned_id`; un recurrente (`recurring_transactions`) genera recordatorios en cada `due_date`.
- **notifications_log** — `id`, `reminder_id`, `sent_at`, `result` (`ok|error`), `error`, `channel`.

### Familia / personas / dinero entre personas
- **people** — `id`, `user_id`, `name`, `notes`, `created_at`. "Juan", "Ana".
- **debts** — `id`, `user_id`, `person_id`, `direction` (`lend`=yo presté | `owe`=yo debo), `amount`, `reason`, `status` (`open|settled`), `due_at`, `settled_at`.
- **shared_expenses** — `id`, `user_id`, `title`, `total_amount`, `description`, `paid_by_user` (bool) o `paid_by_person_id`, `occurred_at`, `settled` bool.
- **shared_expense_shares** — `id`, `expense_id`, `person_id`, `amount` (o `equal_split=true`), `settled`. Permite "84 €, somos cuatro, pagué yo" → el sistema calcula 21 €/persona.

### Casa y lista de la compra
- **shopping_lists** — `id`, `user_id`, `name`, `created_at`.
- **shopping_items** — `id`, `list_id`, `product`, `quantity`, `purchased` bool, `purchased_at`, `created_at`.
- Las tareas domésticas y facturas de casa usan `tasks.domain=home` y `transactions` con categoría `hogar` + `recurring_transactions`.

### Coche
- **vehicles** — `id`, `user_id`, `name`, `plate`, `brand`, `fuel_type`, `default_insurance_due`, `created_at`.
- **fuel_logs** — `id`, `vehicle_id`, `occurred_at`, `liters`, `price_per_liter`, `total`, `odometer`, `full_tank` bool.
- **vehicle_services** — `id`, `vehicle_id`, `kind` (`itv|insurance|repair|tires|revision`), `cost`, `due_at`, `done_at`, `odometer`, `notes`. El consumio/coste mensual se calcula de `fuel_logs` y `vehicle_services`.

### Salud y hábitos
- **habits** — `id`, `user_id`, `name`, `category` (`fitness|sleep|water|reading|...`), `frequency` (`daily|weekly`), `goal` (p.ej. 2L agua), `active`.
- **habit_logs** — `id`, `habit_id`, `log_date`, `value` (real).

### Viajes
- **trips** — `id`, `user_id`, `name`, `start_date`, `end_date`, `budget_amount`, `status` (`planned|active|done`), `iso` opcional.
- Los gastos del viaje = `transactions` con `trip_id` (se añade columna nullable). Las listas/tareas del viaje usan `tasks.trip_id` y `shopping_lists` asociadas.

### Conversación / memoria corta
- **conversation_messages** — `id`, `user_id`, `role` (`user|assistant|tool`), `content`, `meta JSONB` (tool calls, confianza), `created_at`. Ventana de últimos ~40 mensajes para contexto.
- **pending_confirmations** — `id`, `user_id`, `topic`, `payload JSONB` (parámetros propuestos), `proposed_reply`, `created_at`, `expires_at` (p.ej. 10 min), `status` (`open|confirmed|cancelled|expired`).

### Catálogos compartidos
- **merchant_stopwords** — ayudan a normalizar nombres ("MERCADONA S.A." → "mercadona"). *(Fase 3.)*

---

## 5. Entidades y relaciones (resumen ER)

```
users 1─N accounts · transactions · transfers · recurring_transactions ·
              planned_transactions · subscriptions · budgets · savings_goals ·
              events · tasks · reminders · people · debts · shared_expenses ·
              habits · trips · vehicles · conversation_messages · pending_confirmations
users 1─N categories (parent self-FK: subcategorías)
users 1─N merchants ─N transactions, merchants ─ default_category / default_account
users 1─N memory_rules ─ categoria / cuenta objetivo
accounts 1─N transactions (account_id) · accounts 1─N transfers (origen/destino)
accounts 1─N recurring_transactions · accounts 1─N planned_transactions
recurring_transactions 1─N planned_transactions (recurring_id)
recurring_transactions 1─0..N subscriptions · planned_transactions 1─0..N reminders
transfers = 2 cuentas (from/to) — nunca generan transactions
credit_card accounts: saldo ≤ 0 = deuda; gasto con la tarjeta = expense (cuenta credit_card); pagarla = transfer (banco → credit_card), nunca gasto
people 1─N debts · people 1─N shared_expense_shares (vía shared_expenses)
events/tasks 1─0..N reminders  →  notifications_log
recurring_transactions/planned_transactions 1─0..N reminders (origin=recurring_id/planned_id)
savings_goals 1─N savings_contributions 1─0..1 transfers/transactions
trips 1─N tasks · 1─N transactions
vehicles 1─N fuel_logs · 1─N vehicle_services
habits 1─N habit_logs
shopping_lists 1─N shopping_items
```

Todo query sale de una sesión con `user_id` fijado por autenticación (nunca del input del LLM).

---

## 6. Herramientas que tendrá el asistente

El **registro de tools** expone al LLM unicamente aquello permitido. Cada tool tiene: schema Pydantic, `required_permission`, `max_calls_per_message`, `needs_confirmation` (dinámico o estático).

**Cuentas y transferencias:**
- `create_account(name, type, initial_balance, currency, is_default)` — tipos: `bank|savings|cash|credit_card|other`. La tarjeta de débito **no** es una cuenta: se resuelve a su cuenta `bank` asociada. (Alta inicial por el propio usuario; el asistente puede sugerirlo).
- `list_accounts()` / `get_balance_overview()` → saldo por cuenta + patrimonio/saldo neto + saldo disponible + ahorro real (cuentas `savings`) + deuda de tarjetas de crédito + dinero que me deben/debo (§21.1).
- `update_account(id, changes)` / `set_default_account(id)` / `deactivate_account(id)` (destructivo → confirmación).
- `create_transfer(from_account_id, to_account_id, amount, occurred_at, note)` — **nunca genera gasto ni ingreso**; ajusta los saldos calculados de ambas cuentas (§21.3). Cubre tanto "paso 500 del banco a ahorro" como "pago la tarjeta de crédito con 500 desde el banco".
- `get_transfer_history(period)`

**Gastos e ingresos:**
- `create_expense(amount, merchant, category, subcategory, account_id, occurred_at, note, planned?)` — si la confianza de clasificación < umbral, responde `needs_confirmation`. Si `planned=true` se registra en `planned_transactions`.
- `create_income(amount, source, category, account_id, occurred_at, note, planned?)`
- `list_transactions(period, category, account_id, merchant, type)` 
- `spending_summary(period, group_by=category|merchant|account|day)`
- `monthly_comparison(months=2)`
- `correction(transaction_id, field, new_value)` — corregir una clasificación previa.
- `get_merchant_mapping(name)` / `set_merchant_default_category(name, category)` / `set_merchant_default_account(name, account_id)` → alimenta `merchants` y memoria.

**Recurrentes, suscripciones y previstos:**
- `create_recurring_transaction(type, title, amount, frequency, account_id, category_id, start_date, end_date)` — nómina, alquiler, hipoteca, Netflix... Genera `planned_transactions` en cada vencimiento dentro del horizonte.
- `update_recurring(id, changes)` / `cancel_recurring(id)` / `list_recurring(type?, active?)` — crear, modificar, cancelar y listar.
- `get_upcoming_payments(horizon)` — próximos pagos: recurrentes activos + `planned_transactions` vencidas/pendientes (p.ej. "seguro 320 € el día 15").
- `get_upcoming_income(horizon)` — próximos ingresos (nómina, alquiler).
- `create_subscription(name, amount, billing_period, next_billing_date, category_id)` / `update_subscription` / `cancel_subscription` / `get_subscriptions_summary(monthly|yearly)` — "¿cuánto gasto en suscripciones?".
- `create_planned_transaction(type, amount, title, due_date, category_id, account_id?, recurring_id?)` — movimiento **previsto** (aún no realizado): "el día 28 cobraré 1500", "no he pagado todavía el seguro, son 320 € el día 15". No suma en saldos ni en estadísticas reales; alimenta la previsión (§21.2).
- `list_planned(period, type?, status?)` / `mark_planned_confirmed(planned_id)` — movimientos previstos (aún no realizados); distinguir siempre de los confirmados.

**Ahorro y presupuestos:**
- `get_real_savings()` — saldo calculado de `accounts.type=savings` (dinero ahorrado real).
- `create_savings_goal(name, target_amount, target_date)` 
- `get_savings_goal_status(id)` → objetivo / aportado (Σ `savings_contributions` confirmadas) / **restante para el objetivo** (objetivo − aportado) / ritmo / meses restantes / previsión. El "aportado" se distingue explícitamente del saldo real de las cuentas de ahorro (§21.1 `get_real_savings`).
- `record_savings_contribution(goal_id, amount, transfer_id?, note)` — aportación al objetivo, normalmente respaldada por una transferencia hacia cuenta de ahorro (§21.3). La aportación **no es un movimiento de dinero por sí sola** si va acompañada de `transfer_id`: solo etiqueta la transferencia como progreso de un objetivo.
- `create_budget(category, amount, period)` / `get_budget_status(period)` / `list_budgets()`
- `get_monthly_forecast(period)` — previsión: ingresos previstos − gastos fijos − ahorro objetivo − gastos variables realizados → "¿cuánto puedo gastar este mes?" (§21.2).

**Agenda y recordatorios:**
- `create_event(title, starts_at, ends_at/null, kind, location)` 
- `create_reminder(title, due, recurrence_spec, origin)` — `origin` puede apuntar a un `event_id`, `task_id`, `recurring_id` (recordatorio de pago recurrente) o `planned_id` (vence el pago → recuérdame pagar).
- `update_reminder(id, changes)` / `cancel_reminder(id)` / `list_reminders(active)`
- `create_task(title, due_at, priority, domain)` / `complete_task(id)` / `list_tasks(status, domain)`
- `list_agenda(period)` — "¿Qué tengo mañana?"

**Personas, préstamos y gastos compartidos:**
- `add_person(name)`
- `record_debt(person_id, direction, amount, reason, due_at)`
- `settle_debt(id)`
- `get_debts_status()` — "¿Quién me debe dinero?"
- `create_shared_expense(title, total, paid_by, people[], equal=true|shares[])` → calcula el reparto y deudas resultantes.
- `get_shared_expense_status()`

**Casa / Coche / Salud / Viajes:**
- `add_shopping_item(list, product, qty)` / `list_shopping_items(list)` / `toggle_item(id)`
- `record_fuel(vehicle_id, liters, total, odometer)` / `get_fuel_stats(vehicle_id)`
- `add_vehicle_service(vehicle_id, kind, cost, due_at)` / `get_vehicle_upcoming(vehicle_id)` (ITV, seguro, revisiones)
- `log_habit(habit_id, value)` / `create_habit(name, frequency, goal)` / `get_habits_status()`
- `create_trip(name, dates, budget)` / `add_trip_expense(trip_id, ...)` / `get_trip_status()`

**Notas y memoria:**
- `create_note(title, body)` / `get_notes(query)` / `pin_note(id)`
- `propose_memory_rule(rule)` / `list_memory_rules()` / `delete_memory_rule(id)` — la IA propone, el usuario confirma.

**Resúmenes:**
- `get_day_summary()` · `get_week_summary()` · `get_financial_summary(period)` · `get_situation_full()` — agenda + tareas + finanzas + ahorro + avisos en una sola respuesta estructurada.

**Tabla de "confidencia":** por defecto, crear/editar/borrar datos **siempre** pasa por una confirmación implícita del orquestador cuando el LLM devuelve `needs_confirmation=true` o la confianza es baja; las lecturas nunca confirman.

---

## 7. Flujo de procesamiento de un mensaje

1. **Recepción:** el adaptador Telegram recibe el update → `InboundMessage(text, user_id, chat_id, reply_to, attachments)`.
2. **Identidad y permisos:** se resuelve el usuario (o se crea si el propietario habla al bot). Se rechaza a no autorizados. Se fija el `scope user_id`.
3. **Contexto:** se carga perfil (timezone, preferencias) + últimos ~40 mensajes de `conversation_messages` + reglas de memoria activas + estado de confirmaciones pendientes + **cuentas activas** (para resolver `account_id` sin preguntar).
4. **Prompt:** el orquestador construye el system prompt con las reglas de misión del asistente, las herramientas disponibles (schema completo) y el contexto.
5. **Llamada LLM:** la capa IA envía la conversación + tools. El modelo puede responder texto, pedir una o N herramientas, o combinar ambas.
6. **Bucle de herramientas (máx. ~5 iteraciones):** para cada tool:
   - **Validación** del schema con Pydantic (falla → se informa al LLM para que rectifique).
   - **Permisos** y **tasa**.
   - **Resolver entidades ambiguas:** si el texto menciona una cuenta ("con la tarjeta") o comercio, se resuelve con las reglas de memoria y, si no se especifica y existe `is_default`, se usa la cuenta por defecto **sin preguntar** (§10).
   - **Ejecución** del servicio de dominio con scope de usuario → resultado JSON.
   - **Confirmación:** si la herramienta devuelve `needs_confirmation=true` → **no se persiste**; se guarda en `pending_confirmations` y se genera pregunta al usuario (§11). Si el usuario responde, se aplica.
7. **Persistencia de memoria corta:** se guardan los mensajes/tool-calls en `conversation_messages` (el propio mensaje del usuario + resumen de lo ejecutado).
8. **Respuesta:** el LLM genera la respuesta final natural; el adaptador la envía.
9. **Post-procesado asíncrono (opcional):** un paso ligero de "aprendizaje" detecta `merchant → categoria` repetidos no confirmados y los marca `proposed` para sugerir al usuario ("¿Quieres que Mercadona sea siempre alimentación?"). Nunca se persiste nada de este paso sin consentimiento.
10. **Logs:** se registra `request_id`, usuario (hash), tools usados, latencia, tokens, resultado y redacción de datos sensibles.

---

## 8. Cómo funcionará la memoria

**Dos niveles:**

1. **Memoria de conversación (corta):** tabla `conversation_messages`, ventana móvil (~40 msgs). Proporciona continuidad ("el de antes", "ese gasto").
2. **Memoria a largo plazo (reglas y preferencias):** tabla `memory_rules`.
   - `merchant_category`: "Mercadona → alimentación" (confirmada por usuario o aprendida de correcciones).
   - `merchant_account`: "Mercadona → tarjeta" (comercio que se suele pagar con una cuenta concreta).
   - `keyword_category`: "decir `gasolina` → transporte/combustible"; "Amazon → regalos".
   - `keyword_account`: "decir `con la tarjeta` → cuenta por defecto (débito, `bank`)" ; "decir `con la Visa`/`con la tarjeta de crédito` → cuenta `credit_card`". Si el usuario solo tiene una tarjeta (débito), "con la tarjeta" se resuelve a su cuenta `bank`.
   - `preference`: preferencias generales ("dame respuestas cortas", "reparte siempre los gastos de cenas a partes iguales", "cuenta por defecto = banco").

**Ciclo de vida de una regla:**
- **Proposición:** el paso de post-procesado detecta un patrón (mismo comercio ≥2 veces con misma categoría, o corrección del usuario) y lo marca `status=proposed` en memoria.
- **Confirmación:** el asistente pregunta explícitamente: *"Veo que Mercadona es siempre alimentación. ¿Quiero que lo recuerde siempre?"*. Solo al `confirm` de la regla pasa a `active`.
- **Uso:** las reglas activas se inyectan como contexto en la clasificación de gastos (prioridad máxima, por encima del LLM libre).
- **Revocación:** `delete_memory_rule` / "ya no" → se desactiva.
- **Límite de seguridad:** la IA **nunca** escribe reglas activas ni modifica información importante sin confirmación. Las correcciones del propio usuario constituyen señal de aprendizaje, pero se proponen, no se aplican en silencio.

---

## 9. Cómo funcionarán los recordatorios

**Modelo:** cada recordatorio es una fila con `due_at` en UTC. Los recurrentes se **materializan** gerando la siguiente ocurrencia al disparar la actual.

**Fechas relativas y zonas horarias:**
- `timeutil` convierte "mañana a las 9", "el lunes que viene", "una semana antes del cumpleaños de Ana" usando la `timezone` del usuario (IANA). Al primer contacto se pide la zona horaria al usuario (o se infiere y se confirma).
- Almacenamiento siempre UTC; presentación siempre en la hora local del usuario.

**Bucle del scheduler (proceso asíncrono independiente):**
1. Cada ~20 s consulta `reminders WHERE status='pending' AND due_at <= now`.
2. Reserva la ocurrencia (marcado `status='sending'` con `updated_at`) para evitar duplicados entre instancias.
3. Entrega por el adaptador del canal activo (Telegram ahora).
4. Éxito → `status='sent'` + `notifications_log`. Si hay `recurrence`, se inserta la siguiente ocurrencia (`interval`, `anchor`, o `days_before` = cumpleaños − N días).
5. Fracaso → `attempts++`; reintentos con backoff (máx 5). Si supera el máximo → `status='failed'`, `notifications_log` registra el error y se notifica de forma amable al usuario ("no pude avisarte de X").
6. **Cambios/cancelaciones:** `update_reminder`/`cancel_reminder` actualizan la ocurrencia pendiente; las futuras ocurrencias de un recordatorio recurrente se regeneran desde el anchor.

**Casos especiales:** cumpleaños y fechas importantes se modelan como `events.kind=birthday/important` y generan recordatorio recurrente anual con `days_before=N`. Vencimientos de suscripciones/facturas/ITV se generan desde `subscriptions`/`recurring_transactions`/`vehicle_services` con `due_at`/`next_billing_date`, y los pagos pendientes puntuales ("no he pagado el seguro todavía") desde `planned_transactions` con su `due_date` (§10 previstos).

> **Decisión:** *poller sobre BD* en vez de APScheduler en memoria → si el proceso se reinicia, los recordatorios no se pierden; es fácil de probar y de escalar a varias instancias con reserva optimista.

---

## 10. Clasificación automática de gastos

Pipeline de decisión (de menor a mayor coste de IA):

1. **Regla de memoria explícita** (`memory_rules` activas / `merchants.default_category_id`): "Mercadona → alimentación". Prioridad máxima. Requiere confirmación si la regla no es del usuario.
2. **Keyword rules** (`memory_rules.kind=keyword_category`): el texto contiene "gasolina" → combustible.
3. **Matching normalizado de comercio:** `merchants.name_normalized` contra `merchant_name_raw` (case/acentos/dígitos comerciales). Si el comercio está en la BD y tiene categoría previa, se usa esa categoría con la confianza histórica.
4. **Resolución de cuenta** (nueva etapa, ordenada tras el comercio): 
   - Si el texto nombra la cuenta ("con la tarjeta", "del banco") → `memory_rules.kind=keyword_account` o `merchants.default_account_id`. "Con la tarjeta" sin más se resuelve al `bank` por defecto (débito); "con la tarjeta de crédito"/"con la Visa" → cuenta `credit_card`.
   - Si no se menciona y hay una cuenta `is_default` → se usa sin preguntar.
   - Si no hay cuenta por defecto ni mención → `account_id` queda nullable y se registra igual (el usuario puede corregir luego); **no se pregunta** en mensajes de registro rápido salvo que el resultado vaya a ser materialmente incorrecto (§11 política de mínima fricción).
   - **Tarjetas de crédito (regla de oro):** pagar/pasar dinero *a/hacia* la tarjeta de crédito desde otra cuenta (banco/ahorro) es una **transferencia**, jamás un gasto ni un ingreso. Solo el consumo *con* la tarjeta ("he pagado 30 con la Visa") es un gasto que aumenta su deuda. Si el LLM duda entre "pagar la tarjeta" (transferencia) y "pagar con la tarjeta" (gasto), se confirma (§11).
5. **LLM clasificador:** dadas las categorías y subcategorías del usuario + texto + comercio + historial + cuentas, decide `merchant`, `category_id`, **`account_id`**, y si el verbo expresa **ingreso/gasto/transferencia/previsto**, y devuelve **confianza (0–1)**.
   - *"He cobrado 1500"* → ingreso → `create_income`. *"He pasado 300 de mi cuenta a la de ahorro"* → `create_transfer`. *"No he pagado el seguro todavía, son 320"* → `create_planned_transaction` (previsión, no gasto confirmado). *"He pagado 30 con la tarjeta de débito"* → gasto en cuenta `bank` (débito). *"He pagado 100 con la Visa"/"con la tarjeta de crédito"* → gasto en cuenta `credit_card` (aumenta deuda). *"He pagado la tarjeta de crédito con 500 desde mi banco"* → `create_transfer(banco→crédito)`, reduce deuda, no genera gasto.
6. **Fallback:** categoría "Sin clasificar" si la confianza es baja y no hay señales.

**Confirmación y aprendizaje:**
- Confianza alta (≥ umbral ~0.85) y resultado no destructivo → se registra directo (`status=confirmed`) y el asistente comunica qué hizo (importe, comercio, categoría **y cuenta**). El usuario puede corregir con *"no, era ocio"* o *"no, era la tarjeta"* → se ejecuta `correction`.
- Confianza media/baja o ambigüedad real ("45 a Juan" = ¿gasto, préstamo, pago compartido?) → `create_expense` responde `needs_confirmation=true` y el orquestador pregunta con opciones concretas.
- Toda corrección alimenta el aprendizaje: se propone una regla de memoria (`proposed`) de categoría y/o de cuenta, o se actualiza `merchants.default_category_id` / `default_account_id` (solo tras confirmación).

**Detección de incongruencias:** si la IA interpreta "28,80 €" en una frase sin verbo claro, se trata como gasto de Mercadona (comercio mencionado); si el comercio es desconocido y la categoría ambigua, se pregunta. El umbral de "auto-registro" es ajustable por magnitud (gastos mayores → más cautela). Si se menciona una **fecha futura de pago**, se registra como `planned_transactions` (previsto) y se cruza con presupuesto y previsión (§21.2).

---

## 11. Gestión de incertidumbres de la IA

**Principios: priorizar la confirmación cuando haya duda, y nunca destruir datos de forma silenciosa.**

- **Confianza estructurada:** el LLM devuelve `confidence` por campo (merchant, categoría, fecha, tipo). Debajo del umbral → confirmación.
- **Matriz de decisión:**
  | Tipo de acción | Regla |
  |---|---|
  | Lectura / consulta | Nunca pide confirmación |
  | Alta confianza, no destructiva (gasto común de importe bajo, comercio conocido) | Registra y avisa |
  | Ambigüedad (persona vs gasto, importe raro, tipo desconocido) | Pregunta con opciones |
  | Cuenta de dinero no mencionada | Usa cuenta por defecto (`is_default`); solo pregunta si el resultado sería materialmente incorrecto |
  | Transferencia con cuentas ambiguas ("de X a Y") | Pregunta cuáles son las cuentas; nunca se interpreta como gasto sin confirmar el tipo |
  | "Pagar la tarjeta" (deuda de crédito) vs "pagar con la tarjeta" (consumo) | Si hay ambigüedad sobre el tipo, se pregunta. "Pagar/devolver a la tarjeta" → transferencia; "pagar con/in la tarjeta" + comercio → gasto. Nunca un pago a la tarjeta crea gasto |
  | Gasto con tarjeta de crédito ("con la Visa") | Gasto real, cuenta `credit_card`, aumenta deuda; aparece en gastos del mes |
  | Monto "pequeño" y comercio desconocido | Registra en "sin clasificar" y avisa (evita fricción) |
  | Crear borradores propuestos | Se guarda `pending` y se pide confirmación |
  | Destructivo (borrar, anular, "olvidar") | SIEMPRE confirmación explícita |
- **Mecanismo `pending_confirmations`:** intento no persiste; se guarda el payload propuesto con expiración. El usuario responde "sí" (confirma y aplica), "no" (cancela) o un valor corregido. Si `no confirma ni cancela` en 10 min → `expired` y se descarta.
- **Preguntas con opciones:** Telegram admite botones inline; en fase 1 se usan botones de texto numerado + teclado inline cuando la confirmación es clasificar un concepto.
- **Incumplimiento del turno:** si el usuario no responde a una confirmación y escribe algo nuevo, la confirmación expira y el nuevo mensaje se procesa normalmente (pero se avisa suavemente de que quedó algo pendiente).
- **Para "45 peluquería":** confianza alta (merchant conocido de la sector) → registra directo y avisa: *"Registrado: 45 €, Peluquería (Cuidado personal), cuenta: efectivo. ¿Correcto?"* El usuario solo interviene si no lo es.
- **Fricción mínima:** si falta la cuenta y hay una por defecto, no preguntar; registrar, avisar y permitir corregir con una palabra ("tarjeta"). Solo se eleva a confirmación cuando el error podría ser grave (cuenta equivocada es fácil de corregir; tipo de operación equivocada —gasto vs transferencia— se confirma SIEMPRE porque cambia el saldo).

---

## 12. Seguridad

- **Secretos fuera del código:** `config.py` (pydantic-settings) lee de `.env` / variables de entorno / secretos de Docker. `.env` nunca se commitea (`.gitignore`). Se usa `secrets` del sistema para tokens en producción.
- **Identidad Telegram:** Whitelist de `telegram_user_id` autorizados (lista de propietario en config). Cualquier otro Telegram ID se ignora o se bloquea. Es la clave de la autenticación.
- **Aislamiento por usuario:** la capa de repositorios **siempre** inyecta `user_id` (de la sesión autenticada, nunca del LLM). Prevenir "IDOR": las operaciones sobre `transaction_id` verifican que pertenezca al usuario. Tests específicos de aislamiento.
- **IA sin acceso directo a BD:** la IA solo llama a tools avaladas. Ninguna tool acepta SQL ni paths libres. Schemas estrictos.
- **Logs redactados:** no se registran API keys, contraseñas ni datos innecesarios. Se registra hash de usuario, sin importes si no aportan (o con política definida). `structlog` + filtro de redacción.
- **Límites de entrada:** longitud de texto (p.ej. 4000 chars), tasas por usuario (protección anti-abuso), timeouts en llamadas LLM y HTTP.
- **HTTP:** si se usa webhook, siempre HTTPS (TLS), token secreto de webhook, y validación de salto (secret path).
- **PostgreSQL:** contraseñas desde entorno, usuario con mínimo privilegio, red aislada en docker-compose (app↔db en subred interna; solo la app expone puertos).
- **Transmisión a la IA:** los datos que se envían al LLM deben ser los mínimos necesarios. Aviso claro en prompts de no inventar datos personales de terceros. *(Opción a futuro: modelo local vía Ollama para quienes prefieran que nada salga del dispositivo.)*

---

## 13. Sistema de permisos

- **Roles (diseño simple, ahora single-owner):** `owner` (el usuario/administrador). Preparado para `guest`, `viewer`, `editor` cuando se añada familia.
- **Config:** `OWNER_TELEGRAM_IDS=[1234,...]` → primer `owner` de `users` con role `owner`.
- **Por tool:** cada tool lleva `required_role` y `scoped`. Un `guest` podrá leer su propia info pero no borrar. El `owner` lo controla todo de su espacio.
- **Enforcement central:** en `tools/registry.py` se comprueba role y scope antes de ejecutar. Los handlers de Telegram solo pasan `user_id` resuelto; nada confía en parámetros del mensaje.
- **Permisos de datos:** toda fila pertence a un `user_id`; no existe operación sin scope.

---

## 14. Estrategia de pruebas

Tres capas + evals:

1. **Unit (lógica pura):** recurrencias de recordatorios (casos límite: 31/30, últimos días, cumpleaños), `money` (Decimal), **cálculo de saldos de cuentas** (initial_balance + ingresos − gastos +/− transferencias), **regla de doble entrada en transferencias** (Σ salidas = Σ entradas; el total de cuentas no varía), **tarjetas de crédito** (gasto con `credit_card` aumenta deuda y aparece como gasto; pago `banco→crédito` reduce deuda sin crear gasto; patrimonio invariante en el pago), **previsión mensual** (fórmula y supuestos, distinción previsto/confirmado), clasificador (pipeline con fixtures), confirmaciones (estado machine), cálculo de presupuesto/ahorro (ritmo, meses restantes), `timeutil` (fechas relativas, timezones).
2. **Integration (BD real):** repositorios contra PostgreSQL en contenedor (testcontainers). Aislamiento entre usuarios (pruebas IDOR), migraciones Alembic aplicables de cero, transiciones de estado de `reminders`, conversión `planned_transactions` → `transactions` (sin duplicar, sin tocar saldos antes de confirmar).
3. **E2E (llm mockeado):** respuestas LLM grabadas (fixtures VCR) para flujos completos: "He gastado 28,80 en Mercadona" → gasto confirmado en cuenta por defecto; "He pasado 300 de mi cuenta a la de ahorro" → transferencia (no transacción); "He pagado la tarjeta de crédito con 500 desde mi banco" → transferencia banco→crédito (no gasto) y patrimonio sin cambios; "He pagado 100 con la Visa" → gasto con cuenta `credit_card` y deuda aumentada; "45 a Juan" → pregunta de desambiguación; "El día 28 cobraré 1500" → previsto de ingreso (no afecta saldo); "Recuérdame el médico mañana a las 9" → reminder `due_at` correcto. Fake adapter para Telegram (sin red).
4. **Evals de IA (opcional Fase 3,** cuando se construya el clasificador): corpus de frases de clasificación de gastos con categoría **y cuenta** esperada; se mide % de acierto; ante regresiones, congelan o rechazan cambios de prompt.

**Infra:** pytest-asyncio, fixtures parametrizadas, `casbin`-ligero o funciones simples para permisos. El costo de LLM se evita grabando respuestas; el scheduler se prueba con WAIT de segundos o inyectando reloj.

---

## 15. Sistema de logs

- **structlog + JSON** a stdout (recogido por Docker). Formato event-driven y legible por máquina.
- Campos: `ts`, `level`, `request_id`, `user_hash`, `component` (`orchestrator|tools|scheduler|db|telegram`), `tool`, `latency_ms`, `tokens`, `outcome`.
- Política de redacción: nunca `api_key`, `token`, contraseñas; datos financieros con longitud limitada o `amount_redacted=true` en niveles altos.
- Niveles: DEBUG (trazas IA/contexto en desarrollo) · INFO (flujos normales) · WARN (reintentos, confianzas bajas) · ERROR (fallos de scheduler, herramientas, BD).
- Trazabilidad: cada mensaje entrante genera `request_id` que se propaga a LLM y tools → se puede reconstruir qué se hizo exactamente.
- **Observabilidad a futuro:** export opcional a Loki/Grafana o Sentry (errores) si se despliega en servidor.

---

## 16. Docker y despliegue

- **compose:** servicios `db` (postgres:16, volumen persistente, healthcheck) y `app` (Python, depende de db healthy).
- **Dockerfile:** multi-stage, imagen slim, usuario no-root, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`.
- **Arranque:** en el entrypoint se aplican migraciones Alembic (`upgrade head`) y se lanza `main.py` = polling Telegram + scheduler en un único proceso (fase 1). Para webhook se exponen puertos 8443 y se configura el token secreto.
- **Config:** `.env` fuera del repositorio; `ENV_FILE` en compose; secretos gestionados por el orquestador de contenedores en producción.
- **Healthchecks:** sondeo a PostgreSQL y, si aplica, endpoint `/health`.
- **Escalado futuro:** separar `scheduler` y `bot` en procesos; con la reserva optimista (`status='sending'`) pueden correr instancias paralelas sin duplicar recordatorios.

---

## 17. Preparación para WhatsApp / web / móvil

- **Capa `adapters` para todo transporte:** el núcleo solo entiende `InboundMessage`/`OutboundMessage` (`txt`, `buttons`, `media_url`, `user_id`, `channel_id`).
- **Los adaptadores no conocen la BD ni la IA:** el `orchestrator` es canal-agnóstico; los recordatorios se entregan por el/los canal/es que el usuario tenga conectado (tabla `user_channels`: `user_id`, `channel_type`, `channel_ref`, `active`).
- **WhatsApp (futuro):** Business API via proveedor (Twilio/360dialog) o Meta Cloud API → un adaptador nuevo que traduzca Webhook↔`InboundMessage`. Mismas herramientas y memoria.
- **Web/móvil (futuro):** exponer un API REST (FastAPI) que llame al mismo orquestador (proceso separado). App móvil consume ese API. El dominio no cambia.
- **Convención:** ningún import de `telegram` fuera de `adapters/telegram/`. Tests E2E se escriben contra el orquestador con una "adaptador de prueba" (ver §14) para demostrar independencia.

---

## 18. Qué construir primero (Fase 1 — núcleo)

1. **Esqueleto del proyecto:** estructura de carpetas, `pyproject`, config, logging, Dockerfile/compose, alembic inicial.
2. **Identidad y seguridad:** whitelist de Telegram ID, resolución de usuario, scope.
3. **Canal Telegram mínimo + encaminador:** recibir mensaje y devolver eco/`start`.
4. **Capa IA + registro de tools mínimos:** `create_note`, `create_task`, `create_event`, `create_reminder`, `list_agenda`, y `propose_memory_rule`.
5. **Confirmaciones (`pending_confirmations`) + memoria básica.**
6. **Agenda y recordatorios (no recurrentes primero).**
7. **Módulo financiero base: cuentas + gastos/ingresos + transferencias.** Se implementa *antes* de expandir gastos para no rehacer el esquema. Incluye: `accounts`, `transactions` con `account_id`, `transfers`, categorías/subcategorías, clasificador con cuenta por defecto, corrección y memoria de comercios/`default_account_id`.
8. **Previsión simple:** "¿cuánto dinero tengo?" (saldos calculados), "¿cuánto he gastado este mes?" y desglose mensual por categoría/cuenta.

*(Detalles por fases en la hoja de ruta.)*

---

## 19. Qué dejar para fases posteriores

- Estadísticas avanzadas, comparativas históricas, detección de patrones y aumenta de suscripciones (IA-informes).
- Presupuestos avanzados con alertas proactivas y previsión completa de "disponible estimado" (entran en Fase 5, cuando estabilice el registro financiero).
- Ingresos/gastos recurrentes plenos y suscripciones (`recurring_transactions`, `subscriptions`, `planned_transactions` con vencimiento) — tablas ya diseñadas, se activan en Fase 4.
- Personas/préstamos/gastos compartidos (requieren modelo `people`, fase 6). 
- Coche (combustible/km/ITV/seguro), casa (lista compra/facturas), salud (citas/hábitos/agua), viajes (asociar gastos/tareas).
- Imágenes (tickets) e integración de visión/OCR.
- Multi-canal (WhatsApp/web/móvil) y API REST.
- Modelo LLM local (privacidad) y evals de IA avanzados.

---

## 20. Riesgos técnicos

| Riesgo | Mitigación |
|---|---|
| **Coste/latencia LLM** en cada mensaje | Prompt compacto; modelos baratos (`gpt-4o-mini`) para clasificar; cache de contexto; grabación de fixtures en tests |
| **Alucinaciones de la IA** en importes/fechas | Function calling con schemas estrictos; verificación por herramientas (`get_expenses` en vez de recordar); confirmaciones en ambigüedad |
| **Datos financieros mal escritos** (Decimal vs float) | `Decimal` desde el front hasta la BD; tests de redondeo |
| **Zonas horarias y fechas relativas** | `timeutil` centralizado con IANA; almacenar UTC; tests de casos límite (31/30/feb, DST, cumpleaños 29-feb) |
| **Saldos de cuenta inconsistentes** | Saldo **calculado** (nunca almacenado autoritativo); transferencias con doble entrada atómica; tests de invarianza (Σ saldos = Σ iniciales + Σ movimientos) |
| **Transferencias mal registradas como gasto/ingreso** | Confirmación del tipo de operación cuando hay duda; `transfers` como entidad separada con doble entrada; E2E "300 de mi cuenta a ahorro" no crea gasto |
| **Confundir gasto *con* la tarjeta (consumo) vs pago *de* la tarjeta (deuda)** | Clasificador distingue "pagar con la tarjeta" (gasto, cuenta `credit_card` o débito) de "pagar/devolver a la tarjeta" (transferencia banco→crédito, no gasto); en ambigüedad se pregunta (§11); tests E2E de ambos flujos |
| **Doble contabilidad en patrimonio/disponible (mismo importe restado dos veces)** | Cuentas `credit_card` con saldo ≤ 0 sumadas una sola vez en patrimonio y disponible; pago de tarjeta = transferencia que no altera patrimonio; regla anti-doble-conteo §21.1 |
| **Mezcla de previstos con confirmados** | Tabla `planned_transactions` separada; nunca suma en saldos/estadísticas reales; conversión explícita al confirmar |
| **Ambigüedad de cuenta al registrar** | Cuenta por defecto (`is_default`) + reglas de memoria (`account_keyword`/`merchant_account`); corrección de una palabra |
| **Fugas de aislamiento entre usuarios** (IDOR) | Repositorios scoped; nunca confiar en ids del mensaje; tests específicos de aislamiento |
| **Recordatorios perdidos si el proceso cae** | Persistencia en BD + materialización + reintentos con backoff + log de fallos; al arrancar se marcan `sending` como `pending` |
| **Duplicados en entrega de recordatorios** | Reserva optimista (`sending`) + único índice |
| **Dependencia del proveedor de IA** (lock-in / corte / precios) | Interfaz `ai/llm.py`; compatible con OpenAI y LiteLLM; posible Ollama local |
| **Telegram API límites** (flood control) | Throttle en envíos, backoff en error 429 |
| **Coste de tokens por contexto largo** | Ventana móvil + resumen periódico de conversación añeja |
| **Arrastre de código no probado por la complejidad** | Fases pequeñas con criterios de salida y evals de IA |
| **Privacidad al enviar datos a una API externa** | Mínima información al LLM; política clara; opción modelo local a futuro |

---

## 21. Cuentas, saldos y previsión ("¿cuánto dinero tengo?" / "¿cuánto puedo gastar?")

### 21.1 Distinción de conceptos (no implementar contabilidad bancaria; solución práctica personal)

Los tipos de cuenta se dividen en dos familias:

- **Cuentas de dinero real (activos):** `bank`, `savings`, `cash`, `other`. Su saldo es el dinero que existe de verdad; un gasto lo reduce, un ingreso lo aumenta.
- **Tarjeta de crédito (deuda):** `credit_card`. No tiene "dinero"; su saldo calculado es ≤ 0 y representa la deuda pendiente. Consumir con ella aumenta la deuda; pagarla desde el banco la reduce.

| Concepto | Definición | Fuente |
|---|---|---|
| **Saldo por cuenta** | Saldo calculado de cada cuenta individual (real para activos; deuda ≤ 0 para `credit_card`) | `initial_balance + Σ tx confirmadas ± Σ transferencias confirmadas` |
| **Patrimonio / saldo neto** | Suma de saldos de **todas** las cuentas (activos + deuda de tarjetas). Es una magnitud única y sin doble contabilidad | `Σ saldos por cuenta` (= activos − deuda crédito) |
| **Saldo disponible** | Dinero real `bank+cash+other` **no reservado en ahorro**, menos lo que queda pendiente de las tarjetas de crédito (porque ese dinero "ya está gastado" aunque el banco aún lo tenga) y menos deudas abiertas donde debo | `Σ(bank+cash+other) − Σ deuda crédito − Σ debts(owe)` |
| **Dinero reservado / ahorro real** | Saldo de cuentas `type=savings`; dinero que el usuario considera "no tocar" | `Σ cuentas savings` |
| **Deuda de tarjetas de crédito** | Σ de saldos negativos de cuentas `credit_card` (cuánto debo a las tarjetas hoy) | `Σ credit_card` (≤ 0) |
| **Dinero que me deben** | Préstamos prestados (`debts.lend`) abiertos + gastos compartidos no saldados a mi favor (`shared_expense_shares` no `settled`) | Tablas debts/shared |
| **Dinero que debo** | Préstamos que debo (`debts.owe`) abiertos + gastos compartidos no saldados en mi contra | Tablas debts/shared |
| **Gastos pendientes** | `planned_transactions` de gasto aún **no realizadas** + vencimientos de recurrentes dentro del periodo. No reducen saldo real todavía | Tablas planned/recurring/subscriptions |
| **Gastos previstos / Ingresos previstos** | Movimientos futuros programados (gastos e ingresos) dentro del periodo | Tablas planned/recurring |
| **Disponible estimado del mes** | Previsión "¿cuánto puedo gastar este mes?" (ver 21.2) | **Estimado** (con etiqueta) |

Regla de presentación: los saldos calculados de cuentas se muestran como **realidad**; todo lo que dependa de previsiones se muestra **siempre** con la etiqueta "estimado" y los supuestos usados.

**Regla anti-doble-conteo (crítica):**
- La deuda de la tarjeta de crédito aparece **una sola vez**: como saldo negativo en la cuenta `credit_card`, que se resta del patrimonio y del disponible.
- El gasto hecho *con* la tarjeta es un `transactions type=expense` (cuenta `credit_card`): reduce patrimonio vía la deuda. **No** se suma además como transferencia cuando luego se paga la tarjeta.
- El pago *de* la tarjeta es una transferencia `banco→crédito`: reduce `bank`, aumenta `credit_card` (reduce deuda), no crea gasto, no cambia el patrimonio (`−500 +500 = 0`).
- Concluyendo: un mismo importe nunca se resta dos veces del patrimonio; el gasto se cuenta una vez (al consumir) y el pago de la tarjeta no lo vuelve a contar.

### 21.2 Previsión mensual ("¿cuánto puedo gastar este mes?")

Fórmula:
```
Disponible estimado = Ingresos previstos
                    − Gastos fijos previstos
                    − Ahorro objetivo del mes
                    − Gastos variables ya realizados
                    − Deudas/pagos pendientes con vencimiento en el mes
```

Supuestos transparentes (siempre mostrados junto al resultado):
- Ingresos previstos: nómina/alquiler de `recurring_transactions` + `planned_transactions` de ingreso con `due_date` en el periodo. **No** aumentan el saldo real hasta que se confirman.
- Gastos fijos: recurrentes activos + suscripciones con `next_billing_date` en el periodo. **No** reducen el saldo real hasta que se pagan (se convierten).
- Ahorro objetivo: aportación comprometida a `savings_goals` (o meta mensual declarada) — no confundir con el saldo ya acumulado en cuentas `savings` (que ya está descontado del saldo disponible).
- Gastos variables ya realizados: consumos confirmados este mes, incluidos los hechos con tarjeta de crédito (cubiertos por su saldo deudor en el patrimonio).
- Deudas/pagos pendientes: `debts.owe` + `planned_transactions` de gasto con vencimiento en el periodo. El pago de la tarjeta de crédito NO se lista aquí como gasto (es transferencia); su deuda ya está en el saldo de la cuenta → se resta vía patrimonio/disponible, no dos veces.
- Si el objetivo de ahorro no está definido, el disponible estimado muestra "sin objetivo de ahorro declarado".
- El sistema aclara: *"esto es una estimación; se basa en 850 € de ingresos previstos y 320 € de gastos fijos"*.

Ejemplo de respuesta a "¿cuánto puedo gastar este mes?":
```
*Estimado del mes (junio)*
- Ingresos previstos: 1.500 € (nómina el 28)
- Gastos fijos previstos: 600 € (alquiler 400, suscripciones 44, luz 156)
- Ahorro objetivo: 300 €
- Gastos variables ya realizados: 200 €
→ Disponible estimado: 400 €
```

### 21.3 Regla de doble entrada y transferencias

- Una transferencia crea una única fila en `transfers` con `from_account_id` y `to_account_id`. El cálculo de saldo la aplica como **salida en origen** y **entrada en destino**; la suma de saldos permanece invariante (no se "crea" ni "destruye" dinero).
- Las transferencias **nunca** generan filas en `transactions`. Si el LLM intentara registrar "pasar de X a Y" como gasto/ingreso, `create_transfer` se usa en su lugar (§10 y §11).
- **Caso "Paso 500 del banco a ahorro":** reduce banco 500, aumenta ahorro 500, no genera gasto ni ingreso, patrimonio sin cambios. Si el usuario lo asocia a un objetivo de ahorro, se crea/vincula una `savings_contribution` con `transfer_id` (la aportación **no** es otro movimiento de dinero, es una etiqueta sobre la transferencia).
- **Caso "Pago 500 de la tarjeta de crédito desde el banco":** `create_transfer(banco → credit_card, 500)`. Reduce banco 500 (dinero real), acerca el saldo de la tarjeta a 0 (reduce deuda 500), no crea gasto. El gasto ya se registró cuando se consumió con la tarjeta; pagarla es solo reordenar dinero.

### 21.4 Tarjetas de débito y crédito (reglas de comportamiento)

- **Tarjeta de débito = acceso a una cuenta `bank`:** no existe una cuenta "tarjeta de débito" separada. La operación se registra en la cuenta corriente asociada (generalmente la `is_default`), por lo que el gasto **reduce el dinero real** de esa cuenta y no crea dinero ni deuda artificiales.
  - *"He gastado 30 € con la tarjeta"* / *"He pagado 30 € con la tarjeta de débito"* → `create_expense(account_id = bank)`.
- **Tarjeta de crédito = cuenta `credit_card` con saldo ≤ 0 (deuda):**
  - Consumir con ella (*"He pagado 100 € con la Visa"*, *"con la tarjeta de crédito"*) es un **gasto real** (`type=expense`, `account_id=credit_card`): aumenta la deuda.
  - Pagarla (*"He pagado la tarjeta de crédito con 500 € desde mi banco"*) es una **transferencia** `banco → credit_card`: reduce la deuda, no es gasto, el patrimonio no cambia.
  - La deuda pendiente de todas las tarjetas de crédito se presenta explícitamente en `get_balance_overview` y se resta del disponible (§21.1).
- **Distingo "pagar *con*" (consumo) de "pagar *a*/*la* tarjeta" (deuda):** el clasificador (§10) y la matriz de incertidumbre (§11) tratan ambos; si hay ambigüedad, se pregunta. Nunca un pago a la tarjeta se registra como gasto.

---

## 22. Flujo de conversación: ejemplos → intención → herramienta

Filosofía: **"El usuario habla normalmente; el asistente se encarga de organizarlo."** Los comandos `/...` existen solo como atajo/ayuda, nunca como requisito (sin menús complejos).

| Frase del usuario | Intención | Tool | Comportamiento |
|---|---|---|---|
| "28,80 Mercadona" | Gasto con comercio conocido | `create_expense` | Clasifica (cat/día), asigna cuenta por defecto, registra, avisa. |
| "28,80 Mercadona con tarjeta" | Gasto + cuenta explícita (débito) | `create_expense(account_id=bank)` | La débito es acceso a la cuenta `bank`; registra; propone regla `merchant_account` "Mercadona→tarjeta (débito)" (confirmación). |
| "He gastado 30 € con la tarjeta" | Gasto con cuenta por defecto (débito) | `create_expense(account_id=default)` | Afecta al dinero real de la cuenta `bank`; sin deuda ni dinero artificial. |
| "He pagado 30 € con la tarjeta de débito" | Gasto con la cuenta `bank` asociada | `create_expense(account_id=bank)` | Igual que "con la tarjeta": resuelve a la cuenta corriente. |
| "He pagado 100 € con la Visa" | Gasto con tarjeta de crédito | `create_expense(account_id=credit_card)` | Gasto real; aumenta la deuda de la tarjeta (saldo negativo). |
| "He pagado 100 € con la tarjeta de crédito" | Gasto con tarjeta de crédito | `create_expense(account_id=credit_card)` | Ídem Visa: gasto real, aumenta deuda. |
| "He pagado la tarjeta de crédito con 500 € desde mi banco" | Pago de deuda de tarjeta | `create_transfer(banco → credit_card, 500)` | NO es gasto; reduce deuda de la tarjeta y reduce banco; patrimonio sin cambio. |
| "He cobrado 1500" | Ingreso | `create_income` (cuenta por defecto) | Registra y avisa. |
| "He pasado 300 de mi cuenta a la de ahorro" | Transferencia | `create_transfer(banco→ahorro, 300)` | NO crea gasto/ingreso; saldos actualizados (§21.3). |
| "El día 28 cobraré 1500" | Ingreso previsto | `create_planned_transaction(income, due 28)` | Solo previsión; no afecta saldos ni estadísticas reales. |
| "No he pagado todavía el seguro, son 320 € y vence el día 15" | Gasto previsto + vencimiento | `create_planned_transaction(expense, due 15)` + `create_reminder(origin=planned_id)` | Previsión + recordatorio el 15. |
| "Quiero ahorrar 300 € este mes" | Aportación a objetivo (o nuevo objetivo) | `record_savings_contribution` o `create_savings_goal` | Si la cuenta destino no es savings, sugiere transferencia. |
| "He ahorrado 300 €" (aporte del mes) | Aportación a objetivo | `record_savings_contribution` (+ opcional `create_transfer` banco→ahorro) | Es lo aportado *este mes/ahora*; no es "tengo 300 ahorrados". |
| "Tengo 300 € ahorrados" (estado) | Saldo de cuentas de ahorro | `get_real_savings` | Es el saldo real acumulado en cuentas `savings`; no es lo aportado hoy. |
| "He pasado 300 desde el banco a la cuenta de ahorro" | Transferencia a cuenta de ahorro | `create_transfer(banco → savings)` | No es gasto ni "aportación a objetivo" por defecto: solo mueve dinero entre cuentas. Si el usuario la vincula a un objetivo, se crea `savings_contribution` con `transfer_id`. |
| "Tengo 4000 € en mi cuenta de ahorro" | Declarar cifra inicial / saldo real | `create_account(savings, initial_balance=4000)` o ajuste de saldo | Ahorro real (cuentas savings). |
| "¿Cuánto dinero tengo?" | Saldo | `get_balance_overview` | Respuesta con desglose por cuenta + disponible. |
| "¿Cuánto puedo gastar este mes?" | Previsión | `get_monthly_forecast` | Estimado con supuestos (etiqueta "estimado"). |
| "¿Cuánto he gastado en ocio?" | Estadística | `spending_summary(category=ocio)` | Suma real de transacciones confirmadas. |
| "¿Cuánto gasto en suscripciones?" | Suscripciones | `get_subscriptions_summary` | Suma de `subscriptions` activas. |
| "¿Qué pagos tengo pendientes?" | Vencimientos | `get_upcoming_payments` | Recurrentes + planned + debts. |
| "¿Cómo voy con el ahorro?" | Estado de objetivos | `get_savings_goal_status` + `get_real_savings` | Distingue aportado vs saldo de ahorro real. |
| "Recuérdame el médico mañana a las 9" | Evento + reminder | `create_event` + `create_reminder` | Agenda + recordatorio. |

---

## Hoja de ruta por fases

Criterios de salida en cada fase: tests verdes, despliegue docker ok, demo reproducible.

**Fase 0 — Preparación:** repo, structure skeleton, pyproject, config env, logging, Dockerfile+compose (db), alembic init, CI básico (lint + tests). *Salida: `docker compose up` levanta Postgres y el bot contesta `/start`.*

**Fase 1 — Núcleo de conversación:** identidad/whitelist, adaptador Telegram, orquestador, LLM+registro de tools mínimo, `pending_confirmations`, memoria (reglas + confirmación), notas. *Salida: conversación natural con memoria y confirmaciones.*

**Fase 2 — Agenda:** eventos, tareas, recordatorios simples y recurrentes, fechas relativas + timezone, cumpleaños (`days_before`), "¿Qué tengo hoy/esta semana?". *Salida: "recuérdame el médico mañana a las 9" funciona y llega.*

**Fase 3 — Cuentas y movimientos (base financiera):** `accounts`, `transactions` con `account_id`, `transfers` (doble entrada), `categories` + subcategorías, clasificador (reglas→matching→LLM→fallback) con cuenta por defecto, correcciones, memoria de comercios y de cuenta, saldos calculados, "¿cuánto dinero tengo?", "¿cuánto he gastado este mes?" y desglose. *Salida: registrar gastos/ingresos/transferencias y consultarlos; sin previstos ni recurrentes.* Colocada aquí para **no rehacer el modelo de gastos después con cuentas**.

**Fase 4 — Recurrentes, suscripciones y previstos:** `recurring_transactions` (ingresos y gastos), `subscriptions`, `planned_transactions` con vencimiento, recordatorios de pago (`origin=planned_id`), próximos pagos/ingresos, detección propuesta de patrones ("veo que pagas 12,99 a Netflix al mes"), conversión previsto→confirmado. *Salida: nómina/alquiler/Netflix modelados; "¿qué pagos tengo pendientes?" y "¿cuánto gasto en suscripciones?".*

**Fase 5 — Presupuestos, ahorro y previsión:** `budgets` + alertas (80%/100% por categoría/global), objetivos de ahorro (diferenciados de cuentas savings), aportaciones, `get_monthly_forecast` ("¿cuánto puedo gastar este mes?" con etiqueta "estimado"), "¿cómo voy con mi objetivo de ahorro?". *Salida: control de límites y previsión mensual con supuestos.*

**Fase 6 — Personas, préstamos y gastos compartidos:** `people`, `debts`, `shared_expenses` (+cálculo de reparto), "¿Quién me debe dinero?". *Salida: desambiguar "45 para Juan" (gasto/préstamo/compartido).*

**Fase 7 — Coche, casa, salud:** combustible/km, ITV/seguro/revisiones (recordatorios), lista de la compra, tareas domésticas, facturas recurrentes, citas médicas (eventos kind=appointment) y hábitos. *Salida: todo lo relativo a rutina registrable y consultable.*

**Fase 8 — Viajes y proactividad:** creación de viajes con presupuesto/lista/tareas/recordatorios; resúmenes ("¿Cuál es mi situación?") consolidando agenda+finanzas+ahorro+avisos; detección de patrones y recomendaciones de ahorro (comparativa mensual), detección de subida de suscripciones. *Salida: resumen en una respuesta.*

**Fase 9 — Imágenes:** recepción de fotos/tickets, extracción (OCR + visión) y propuesta de registro con confirmación. *Salida: fotos de tickets → gasto registrado tras confirmación.*

**Fase 10 — Multi-canal:** API REST (FastAPI) sobre el mismo orquestador + adaptador WhatsApp; tablas `user_channels`; separar procesos scheduler/bot/API. *Salida: mismo núcleo sirviendo web/WhatsApp.*

---

### Notas finales
- El diseño es un **monolito modular** que puede partirse en servicios sin reescribir la lógica.
- El **modelo de datos inicial** es completo en dominio pero se implementa progresivamente; las tablas no usadas en una fase pueden migrarse después (el esquema en fase 1 solo incluye lo de las fases incluidas, evitando esquemas muertos).
- Todas las tablas de usuario incluyen `user_id` y los repositorios lo fijan desde la sesión autenticada (nunca del LLM).
- Las **transferencias** y los **previstos** nunca se contabilizan como gasto/ingreso real: las primeras vía `transfers` con doble entrada; los segundos vía `planned_transactions` separada que se convierte al confirmarse.
- El saldo es siempre **calculado** (initial_balance + movimientos + transferencias), no almacenado, para evitar inconsistencias.
- Toda decisión de datos financieros importantes pasa por **confirmación** o **aviso inmediato**, cumpliendo el objetivo de "la IA no modifica silenciosamente información importante".
- Fechas/importes se tratan como datos críticos: zonas horarias UTC, `Decimal`, y testes exhaustivos.
- Las previsiones son **estimaciones** y se etiquetan como tales en las respuestas (§21).
- Las **tarjetas de crédito** se modelan como cuentas con saldo ≤ 0 (deuda). Consumir con ellas = gasto; pagarlas desde el banco = transferencia (no gasto). La tarjeta de débito no es una cuenta: es el acceso a la cuenta `bank` (§4, §21.4 y §22).