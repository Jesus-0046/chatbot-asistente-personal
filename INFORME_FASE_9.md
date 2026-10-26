# INFORME FINAL — FASE 9: WHATSAPP VÍA CLOUD API DE META (IDENTIDAD POR CANAL E IDEMPOTENCIA)

**Fecha:** 2026-09-21
**Proyecto:** Asistente Personal AI (ChatBot)

---

## 1. Resumen Ejecutivo
Fase 9 completa: **1062 tests pasan, 0 skipped** (1044 unitarios + 18 de PostgreSQL real), **0 errores ruff**, **0 errores mypy** (120 ficheros), **formato OK**, `alembic heads` → `0014_fase9_whatsapp` (head único). Se implementó el **segundo canal oficial** del asistente vía la **WhatsApp Cloud API de Meta**: webhook HTTP firmado con `X-Hub-Signature-256`, cliente aiohttp con reintentos y manejo de rate limit, adaptador `channel` reutilizando el orquestador, **identidad canal-agnóstica** (`user_channels`), **idempotencia de eventos entrantes** (`processed_inbound_events`) e **vinculación por código de 8 caracteres** (sha-256, TTL 30 min, single-use). Trabajo con ciclo **TEST → FIX → TEST** (§44): se corrigieron 8 problemas reales descubiertos al escribir los tests. Toda la validación se ejecutó contra **PostgreSQL 16.12 real** (incluida una prueba de concurrencia de deduplicación) y se validaron las migraciones `0013→0014` (upgrade con backfill y downgrade) y `0001→head` desde cero. **No se ha empezado la Fase 10** (multi-canal REST / separación de procesos).

## 2. Funcionalidad implementada (WhatsApp Cloud API, Meta)
- `adapters/whatsapp/webhook.py` — aplicación aiohttp: `GET /webhook` (verificación con `hub.verify_token` y challenge) y `POST /webhook` (autenticado; ver §4).
- `adapters/whatsapp/client.py` — `WhatsAppClient` implementa el contrato `WhatsAppGateway` (enviar texto y descargar media) con aiohttp, `max_retries`/timeout y categorización de errores (`WhatsAppRateLimitedError`, `WhatsAppTimeoutError`, `WhatsAppConnectionError`, `WhatsAppInvalidResponseError`, `WhatsAppMediaError`), incluyendo el código 131026 y el 429.
- `adapters/whatsapp/channel.py` — `WhatsAppChannel`: traduce el payload de la Cloud API a `InboundMessage`, **deduplica antes de tocar negocio** (ver §5), aplica pairing cuando la identidad no está vinculada y reutiliza `Orchestrator` para todo lo demás. Flujo de media: descarga, extracción con visión y propuesta con confirmación.
- `adapters/whatsapp/formatting.py` — salida limitada: `split_for_send` trocea respuestas por el límite de texto de la Cloud API (~4096) sin romper líneas.
- `application/pairing.py` / `tools/link.py` — códigos de enlace de 8 caracteres (alfabeto `ABCDEFGHJKMNPQRSTUVWXYZ23456789`), guardados **solo como sha-256** (`pair_code_hash`), TTL 30 min, single-use. La herramienta `link_whatsapp` se invoca desde Telegram.
- `domain/channels.py` / `persistence/repositories/channels.py` — `ChannelIdentityService`: `ensure_pending_pair` (no re-expone el código vigente), `link_with_code` (single-use), `resolve_user`, `has_linked`, `mark_processed`.
- `main.py` — arranque unificado: con WhatsApp activado, Telegram (polling) + webhook en el mismo event loop.
- `config.py` — credenciales (`WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_MEDIA_BASE_URL`), `whatsapp_enabled` y `owner_ids_set` compartido.
- Límites por canal: **proactividad sigue siendo Telegram-only**; outbound WhatsApp solo texto (sin botones/plantillas), con rate limit por `user.id`.

## 3. Correcciones aplicadas (TEST → FIX → TEST, §44)
1. **`_channel(settings_override=...)`** en helpers: el parámetro era `class`-scope y la toma/limpieza del monkeypatch causaba `NameError` intermitente; se parametrizó por instancia.
2. **Webhook bajo aiohttp testeable**: los tests usan un stub `_FakeHeadersRequest` de `web.Request` para ejercitar `verify_signature` sincrona y el handler asíncrono con `aiohttp.test_utils`.
3. **`handle_payload` robusto**: guards `isinstance` en `payload`/`entry`/`change`/`value` para que cualquier payload malformado se responda 200 sin excepción.
4. **Comparación de fechas naive/aware** en `domain/channels.py:46`: `to_utc(row.pair_expires_at) > now` (PG devuelve timestamptz aware; el naive se convertía).
5. **Timeouts del cliente**: los tests usan `_silent_server` (TCP que acepta y nunca responde) con cancelación de tasks en cleanup, en vez de simular latencia.
6. **`split_for_send`**: filler de >4096 en el test para verificar el troceado sin romper palabras.
7. **E2E**: `_count` filtra `status="open"` porque las filas confirmadas persisten tras el flujo.
8. **`test_alembic_current_muestra_cabecera`**: actualizado del head `0013_fase6_2_claim_lease` al `0014_fase9_whatsapp`.

## 4. Seguridad del webhook (§45)
- La firma `X-Hub-Signature-256` es **obligatoria**: HMAC-SHA-256 del **body crudo** con `WHATSAPP_APP_SECRET`. Ausente o inválida → `401`. La variante `md5=` es rechazada explícitamente (solo HMAC-SHA-256).
- A Meta se responde **siempre 200** (también ante payloads malformados): la retransmisión es inofensiva porque la idempotencia la garantiza `processed_inbound_events`.
- Verificación de suscripción (`GET /webhook`) exige `hub.verify_token` correcto antes de devolver el challenge.
- Ningún error interno, traceback o detalle de proveedor se devuelve al llamante; los errores se registran con structlog y el usuario recibe mensajes genéricos de dominio.

## 5. Idempotencia y concurrencia
- `processed_inbound_events` con **UNIQUE `(channel, external_message_id)`** (`uq_processed_events_channel_message`) y `INSERT … ON CONFLICT DO NOTHING` **antes** de cualquier lógica de negocio.
- Prueba de concurrencia real (`tests/integration/test_whatsapp_pg.py`): **8 mark_processed simultáneos del mismo wamid → exactamente 1 gana** y permanece 1 sola fila (PG).

## 6. Identidad por canal y vinculación
- `user_channels`: **UNIQUE `(channel, external_user_id)`** (`uq_user_channels_channel_external`), FK `user_id → users.id` (ON DELETE CASCADE) y CHECK `ck_user_channels_status` (`pending`/`linked`).
- `users.telegram_user_id` pasa a **nullable** (multi-NULL permitido en PG): usuarios solo-WhatsApp existen sin Telegram. Las identidades Telegram existentes se **backfillean** en la migración 0014.
- Ciclo completo `pending → código → link → resolve` validado contra PG real, incluido el **single-use** (el código consumido no re-vincula; un tercer usuario no puede robar una identidad ya `linked`).

## 7. Migración 0014 y validación de migraciones
- `0014_fase9_whatsapp` (head único; `down_revision=0013_fase6_2_claim_lease`).
- Validado sobre PostgreSQL real con BDs scratch (creadas y destruidas para la validación):
  - `0013 → 0014` con seed de un usuario Telegram: backfill crea la fila `user_channels` (`channel='telegram'`, `status='linked'`), `telegram_user_id` queda `NULL`-able y aparecen los dos UNIQUEs.
  - `0014 → 0013` (downgrade): `user_channels` y `processed_inbound_events` se eliminan y `telegram_user_id` vuelve a `NOT NULL`. (El downgrade **falla a propósito** si existen usuarios solo-WhatsApp, documentado en la migración.)
  - `0001 → head` desde cero: todo aplica limpio y `alembic_version=0014_fase9_whatsapp`.
- `alembic heads` → único head `0014_fase9_whatsapp`.

## 8. Entorno PostgreSQL (causa raíz aislada y documentada)
Durante la fase, la instancia portable dejó de arrancar: **todo hijo creado por el postmaster moría con `0xC0000142`** al ejecutarse desde el perfil de usuario (independiente de datos, ubicación, versión 16.12/16.15 y exclusiones de Defender; confirmado con clústeres scratch). Se resolvió registrando el **servicio Windows `postgresql`** (binarios 16.12, `pg_ctl register`, LocalSystem) → arranque estable en `127.0.0.1:5432`. El patrón de arranque seguro para no colgar la shell es `Start-Process -Wait` con redirección de salida. No se tocó el `pgdata` real.

## 9. Cobertura de tests de Fase 9
Unitarios nuevos (6 ficheros, 63 tests):
- `test_whatsapp_webhook.py` (11): firma válida/inválida/ausente/md5, challenge, 200 incondicional, body crudo.
- `test_whatsapp_channel.py` (19): idempotencia, pairing, media, confirmaciones, rate limit, gateway erróneo.
- `test_whatsapp_channels_domain.py` (11): servicio de identidad, single-use, TTL, alfabeto/única exposición del código.
- `test_whatsapp_client.py` (10): reintentos, timeouts, 429/100-códigos, descarga de media.
- `test_whatsapp_formatting.py` (7): `split_for_send`, límites de longitud.
- `test_whatsapp_e2e.py` (5): flujos completos webhook→orquestador→respuesta (texto e imagen).

Integración PG nuevos (`test_whatsapp_pg.py`, 5): dedup concurrente del mismo wamid, UNIQUE + FK + CHECK del esquema real, `telegram_user_id` nullable con ciclo completo de pairing/single-use, y comprobación del head en `alembic_version`.

## 10. Puertas completas (§42)
| Gate | Resultado |
|---|---|
| `pytest tests -q` (con `TEST_DATABASE_URL`) | **1062 passed, 0 skipped** |
| `pytest tests -q` (sin PG) | 1044 passed, 18 skipped |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 201 files already formatted |
| `mypy src/asistente` | Success (120 source files) |
| `alembic heads` | `0014_fase9_whatsapp` (head único) |
| Migraciones `0013→0014` / `0001→head` | Upgrade + downgrade + backfill y fresh OK sobre PG real |
| Tests de integración PG | **18/18 passed** (13 previos + 5 nuevos Fase 9) |

## 11. Verificación — PostgreSQL Real
Instancia **16.12** como servicio Windows en `127.0.0.1:5432`; `TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@127.0.0.1:5432/asistente_test`. Pasan los 13 tests previos (alembic, ping/sesión, concurrencia de claims, flujo de imagen) y los 5 nuevos de Fase 9: dedup concurrente con `asyncio.gather`, constraints del esquema vía `information_schema`/`pg_constraint`, y ciclo de identidad WhatsApp completo contra la BD real.

## 12. Conclusión
Fase 9 cerrada: WhatsApp Cloud API operativo con autenticación HMAC-SHA-256, idempotencia race-safe verificada con concurrencia real, identidad canal-agnóstica con vinculación segura por código single-use, y migración validada en ambas direcciones. Todos los gates pasan (1062 tests, 0 errores ruff/mypy, formato OK, migraciones OK sobre PG real). **No se ha empezado la Fase 10.**

## Archivos Modificados (Fase 9)
- **Nuevos:** `src/asistente/adapters/whatsapp/{__init__,webhook,client,channel,formatting}.py` · `src/asistente/application/pairing.py` · `src/asistente/application/channels.py` · `src/asistente/domain/channels.py` · `src/asistente/persistence/repositories/channels.py` · `src/asistente/tools/link.py` · `migrations/versions/0014_fase9_whatsapp.py` · `tests/unit/{test_whatsapp_webhook,test_whatsapp_channel,test_whatsapp_channels_domain,test_whatsapp_client,test_whatsapp_formatting,test_whatsapp_e2e}.py` + `tests/unit/helpers.py` · `tests/integration/test_whatsapp_pg.py`.
- **Modificados:** `src/asistente/adapters/base.py` · `src/asistente/application/services.py` · `src/asistente/application/orchestrator.py` · `src/asistente/persistence/models.py` · `src/asistente/config.py` · `src/asistente/main.py` · `.env.example` · `tests/integration/test_alembic.py` (head → 0014).