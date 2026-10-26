# INFORME FINAL — FASE 7: IMÁGENES, OCR Y EXTRACCIÓN ESTRUCTURADA

**Fecha:** 2026-09-20
**Proyecto:** Asistente Personal AI (ChatBot)

---

## 1. Resumen Ejecutivo
Fase 7 completa: **979 tests pasan, 0 skipped** (966 unitarios + 13 de PostgreSQL real, `pytest tests -q` con `TEST_DATABASE_URL`), **0 errores ruff**, **0 errores mypy**, **formato OK**, `alembic heads` → `0013_fase6_2_claim_lease` (head único, **sin migración nueva**: no hubo cambio de esquema). El bot ahora acepta **fotos** en Telegram (con caption opcional), extrae estructuradamente el contenido mediante un modelo de visión (ticket, lista de tareas, lista de la compra o nota), valida lo extraído, muestra una **propuesta de confirmación** con avisos y, solo tras confirmar, escribe en la base de datos a través de **una única puerta de escritura** (`apply_extracted_data`). Nada se persiste sin confirmación; los importes se tratan como `Decimal`/texto canónico sin floats intermedios; no se inventan años ni totales; los posibles duplicados solo avisan (nunca bloquean); y la privacidad está garantizada (el payload nunca lleva la imagen ni su `file_id`).

## 2. Objeto y Alcance
### 2.1. Qué aporta la Fase 7
- **Recepción por Telegram**: `MessageHandler(filters.PHOTO, photo_message_handler)`; se elige la foto de mayor resolución, se descarga a memoria (`download_as_bytearray`), se valida tamaño (`file_size` ≤ `image_max_size_bytes`) y formato (`mime_type == image/jpeg`) y se enruta al orquestador como `ImageAttachment` junto al caption.
- **Extracción estructurada**: `ImageUnderstandingProvider` (protocolo) + `OpenAIImageUnderstandingClient` (reutiliza `create_openai_client`). Devuelve JSON `{kind, warning fields, bloque de datos}`.
- **Normalización y validación** (`domain/images.py`): 4 tipos — `ticket`, `task_list`, `shopping_list`, `structured_note`. Construye una **propuesta** (ticket → gasto; tareas → agenda; lista → nota; nota → nota) y un **texto de confirmación** humanizado.
- **Confirmación reutilizada**: la propuesta se guarda como confirmación pendiente (`PendingConfirmation.payload = {"tool": "apply_extracted_data", "arguments": propuesta}`). El "sí/no" existente del orquestador resuelve el flujo.
- **Única puerta de escritura**: la tool `apply_extracted_data` (en `tools/images.py`) valida la propuesta contra un esquema, crea el movimiento/tareas/nota con los servicios reales y devuelve error claro si algo no cuadra. Es la única forma de persistir lo extraído.
- **Sin migración de esquema**: se reutiliza el sistema de confirmaciones (JSONB) y los modelos existentes.

### 2.2. Fuera de alcance
- No se procesan vídeos, documentos ni stickers; solo `image/jpeg` (Telegram envía las fotos como JPEG; png/webp se admiten a nivel de servicio).
- No exactly-once sobre el proveedor de visión (es externo); sí escrituras únicas confirmadas (una sola transacción de gasto por confirmación).
- No se ha empezado la Fase 8.

## 3. Invariantes de la Fase 7
1. **Nada se escribe sin confirmación.** El análisis de una imagen solo produce una propuesta en memoria y una confirmación pendiente transitoria (TTL 10 min). La escritura ocurre exclusivamente en `apply_extracted_data` cuando el payload es confirmado.
2. **Nunca se inventa el año ni el total.** Fechas sin año o de 2 dígitos → `None` + aviso (el ticket no se da por bueno sin fecha completa; `apply_extracted_data` rechaza tickets sin `occurred_at`). Total ilegible/ambigüo → propuesta sin importe o confianza `low` con aviso; nunca se asume.
3. **Importes sin floats intermedios.** Toda la cadena usa `Decimal` a partir de texto canónico (punto, p. ej. `"28.80"`). La presentación al usuario usa coma europea (`fmt_amount_eu` → `"28,80 €"`). Ningún valor de propuesta ni persistencia pasa por `float`.
4. **Duplicados avisan, nunca bloquean.** Mismo `(comercio, día local, importe)` en el histórico → aviso "Posible duplicado" añadido al texto de confirmación; la propuesta sigue activa.
5. **Privacidad del payload.** La confirmación pendiente y los logs nunca contienen los bytes de la imagen, ni su `file_id`, ni base64. Los logs de análisis usan `user_anon` (sha1 truncado).

## 4. Diseño
### 4.1. Límites configurables (`config.py` + `.env.example`)
| Setting | Default | Uso |
|---|---|---|
| `image_max_size_mb` | `10` | Tope de peso (con propiedad derivada `image_max_size_bytes`) |
| `image_max_per_message` | `1` | Máximo de imágenes por turno |
| `image_max_extracted_chars` | `20000` | Recorte de texto extraído (nota/lista) |
| `image_timeout_seconds` | `30.0` | Timeout del proveedor de visión |
| `image_allowed_mime_types` | jpeg/png/webp | Whitelist de formatos |

### 4.2. Cadena de errores categorizada
`ImageExtractionError(category, mensaje)` con `too_many_images`, `unsupported_format`, `size_exceeded`, `unreadable`, `invalid_response`, `insufficient_confidence`, `timeout`, `provider_unavailable`. El servicio degrada/avisa en vez de lanzar cuando puede (confianza `low`, total ausente, fecha sin año), y el orquestador traduce los errores a texto amigable vía `friendly_image_error`.

### 4.3. Confianza por campo → overall
Cada campo lleva su `confidence` (high/medium/low) del proveedor *y* las comprobaciones propias (total parseable, suma de líneas vs total con tolerancia `TICKET_TOTAL_TOLERANCE`, comercio leído, fecha completa). `_overall_confidence` toma el peor campo; sin total → `low`.

### 4.4. Importes: parseo estricto europeo
`parse_extracted_amount` (en `domain/amounts.py`):
- Coma ⇒ decimal (`"28,80"` → `28.80`).
- Punto solo se acepta sin ambigüedad: 2 partes, fracción de 1-2 dígitos con int ≤3 ⇒ decimal (`"28.50"`); fracción de 3 dígitos con int ≤3 dígitos ⇒ millares (`"1.250"`).
- Rechaza comas repetidas (`"28,,80"`), varios puntos (`"1.2.3"`), 3+ decimales (`"28,803"`), negativos, ceros y texto no numérico; cada rechazo produce aviso y degrada a `low`.

### 4.5. Fechas (nunca inventar año)
`normalize_extracted_date`: ISO (`2026-09-20`), europea (`20/09/2026`, `20-09-2026`, `20.09.2026`), texto español ("20 de septiembre de 2026", "20 sep 2026"). Sin año o con 2 dígitos ⇒ `None` + aviso claro. `occurred_at` se construye combinando fecha+UTC hora y el `user_tz` (default `default_timezone`); sin fecha ⇒ `occurred_utc = None` (lo que a su vez impide registrar el ticket en `apply_extracted_data`).

### 4.6. Duplicados
`_warn_possible_duplicate` consulta el histórico (`transactions.list`) con ventana del **día local** del gasto propuesto, `transaction_type="expense"`, `merchant` (ilike) e importe igual. Si coincide: aviso (una sola vez) en `warnings` **y en el texto de confirmación**; nunca bloquea.

### 4.7. Orquestador (lazy)
- `Orchestrator.__init__` ya no construye el cliente de visión: `self._vision` y `self._images` se crean la primera vez que llega una imagen (`_image_service()`). Así los flujos sin imagen no dependen de `OPENAI_API_KEY`.
- `handle()`: rate limit → usuario → confirmación pendiente → si `attachments` ⇒ `_image_turn` (analiza → crea confirmación pendiente con el payload → guarda la conversación → responde con la propuesta) → si no, turno normal.
- `_image_turn` recibe `user_id`, `caption` y `attachments`; usa el `timezone_for(uid)` del usuario cuando existe, con fallback a `default_timezone`.

## 5. Proveedor de visión
`ai/vision.py`: protocolo `ImageUnderstandingProvider.understand(*, image, hint) -> dict`. `OpenAIImageUnderstandingClient` construye el prompt del sistema (reglas Fase 7, sin `"automáticamente"`; el test pre-existente lo exige ausente), manda la imagen en base64 al modelo de visión, valida la respuesta JSON y expone `ImageExtractionError` con categorías. `hint` recibe el caption como pista. Nunca se guarda la imagen.

## 6. Migration
**No hay migración nueva.** `alembic heads` → `0013_fase6_2_claim_lease` (único). Se reutilizan `pending_confirmations` (JSONB) y los modelos de movimientos/tareas/notas existentes. Se re-verificó que el camino de migración 0001→0013 sigue funcionando (`test_alembic_upgrade_head_sin_errores` pasa sobre PostgreSQL real).

## 7. Testing
### 7.1. Pruebas nuevas (Fase 7)
| Fichero | Tests | Cobertura |
|---|---|---|
| `tests/unit/test_image_parsing.py` | 16 | `parse_extracted_amount` (europeo, puntos ambigüos, rechazos), fechas (nunca inventar año, ISO/europea/texto), no floats |
| `tests/unit/test_image_service.py` | 30 | Límites, categorías de error, confianza por campo, suma vs total, otra moneda, fechas sin año, caracteres limitados, 4 tipos de extracción, duplicados (avisan, por usuario, otro día/importe), privacidad del payload, caption como pista, ventana de consulta |
| `tests/unit/test_image_tools.py` | 10 | Sin confirmación no escribe nada; gasto único (entrega/persistencia sin floats); tickets sin fecha no se registran; tareas creadas; lista → nota; nota guardada; tipo incoherente rechazado; confirmación scoped al usuario |
| `tests/unit/test_image_orchestrator.py` | 9 | Propuesta sin escritura hasta confirmar; payload sin imagen; cancelación no crea nada; errores amigables; duplicado avisado sin bloqueo; resolución del contexto pendiente antes que el flujo imagen; historial guardado; sin imagen el flujo no se dispara |
| `tests/unit/test_handlers.py` | 8 (4 foto) | Handler foto autorizado/denegado (no toca el orquestador), elección de mayor resolución, mensaje sin foto no rompe |
| `tests/unit/test_config.py` / `test_prompts.py` / `test_telegram_bot.py` | ampliados | Settings de imagen, reglas Fase 7 + versión `5.1.0`, registro de handlers (3) |
| `tests/integration/test_image_flow_pg.py` | 1 | Flujo completo ticket-imagen → propuesta → confirmación → una sola transacción expense en PostgreSQL real (cleanup por `DELETE` cascada) |

### 7.2. Puertas completas
| Gate | Resultado |
|---|---|
| `pytest tests -q` (con `TEST_DATABASE_URL`) | **979 passed, 0 skipped** (966 unit + 13 PG) |
| `pytest tests/integration` (PG real) | 13 passed (incluye el nuevo flujo de imagen) |
| `pytest tests -q` (sin PG) | 966 passed, 13 skipped (los PG saltan sin `TEST_DATABASE_URL`) |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 201 files already formatted |
| `mypy src/asistente` | Success (110 source files) |
| `alembic heads` | `0013_fase6_2_claim_lease` (head único) |
| Migración nueva | NO |

## 8. Verificación — PostgreSQL Real
Se recreó la instancia local **PostgreSQL 16.4 portable** (binarios EDB en `C:\Users\jesus\AppData\Local\Programs\PostgreSQL\16\bin`, data dir `C:\Users\jesus\pgdata`, arrancada con `pg_ctl start`) en `127.0.0.1:5432`; `TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@127.0.0.1:5432/asistente_test`. Los **13 tests de integración** (alembic, ping/sesión, concurrencia de claims, flujo de imagen) pasan. El flujo Fase 7 se verifica de extremo a extremo sobre el repositorio real con `make_connected_services`.

## 9. Calidad de Código
- Correcciones aplicadas durante la verificación:
  - `ai/vision.py`: faltaba `Literal` (import añadido).
  - `application/orchestrator.py`: visión perezosa (constructor sin dependencia de API key); `friendly_image_error` importado de `domain/images.py`.
  - `domain/images.py`: logging con structlog (no estándar); refactor de secciones con helpers `_as_dict`/`_as_list` (mypy limpio); warnings de duplicado y mensajes de fecha sin `%`-format.
  - Propuestas de lista y nota anidadas bajo `shopping_list` / `structured_note` (coherentes con el esquema de `apply_extracted_data`).
  - Tickets sin fecha: `apply_extracted_data` los rechaza (no se inventa la ocurrencia con `utc_now`).

## 10. Limitaciones y Conclusión
- La calidad de la extracción depende del proveedor de visión (externo); el sistema protege degradando confianza, degradando/categorizando errores y exigiendo confirmación, nunca asumiendo datos dudosos.
- El bot solo acepta fotos `image/jpeg` de Telegram (el resto de MIME se admite a nivel de servicio según `image_allowed_mime_types`).
- El aviso de duplicado es best-effort (ventana de un día local + comercio + importe exacto); si el histórico no tiene el dato, simplemente no avisa. Nunca bloquea.
- **Conclusión:** Fase 7 implementada y verificada: imagen → extracción estructurada → validación → propuesta → confirmación → `apply_extracted_data` (única puerta de escritura) → persistencia con una única transacción, sin cambios de esquema. Todos los gates pasan (979 tests, 0 errores ruff/mypy, formato OK, migraciones OK). Sin empezar Fase 8.

## Archivos Modificados (Fase 7)
- `src/asistente/ai/vision.py`: protocolo proveedor + `OpenAIImageUnderstandingClient` + `ImageExtractionError` (nuevo).
- `src/asistente/domain/images.py`: servicio de extracción/normalización/validación, `friendly_image_error`, duplicados, respuestas (nuevo).
- `src/asistente/domain/amounts.py`: `parse_extracted_amount` endurecido + `fmt_amount_eu` (presentación con coma).
- `src/asistente/tools/images.py`: `apply_extracted_data` (única escritura) (nuevo).
- `src/asistente/tools/__init__.py`: registro de `IMAGE_TOOLS`.
- `src/asistente/application/orchestrator.py`: visión perezosa + `_image_turn`.
- `src/asistente/adapters/telegram/handlers.py`, `bot.py`: `photo_message_handler` + registro.
- `src/asistente/ai/prompts.py`: reglas Fase 7 + `SYSTEM_PROMPT_VERSION = "5.1.0"`.
- `src/asistente/config.py`, `.env.example`: límites de imagen.
- `tests/unit/image_fakes.py`, `tests/unit/helpers.py` (`make_connected_services`), `tests/unit/test_image_*.py`, `tests/unit/test_handlers.py`, `test_config.py`, `test_prompts.py`, `test_telegram_bot.py`, `tests/integration/test_image_flow_pg.py`.