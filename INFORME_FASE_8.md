# INFORME FINAL — FASE 8: ENDURECIMIENTO Y PREPARACIÓN PARA PRODUCCIÓN

**Fecha:** 2026-09-20
**Proyecto:** Asistente Personal AI (ChatBot)

---

## 1. Resumen Ejecutivo
Fase 8 completa: **990 tests pasan, 0 skipped** (977 unitarios + 13 de PostgreSQL real), **0 errores ruff**, **0 errores mypy**, **formato OK**, `alembic heads` → `0013_fase6_2_claim_lease` (head único, **sin migración nueva**). No se ha añadido NINGUNA funcionalidad nueva (sin WhatsApp/web/móvil/LangChain/Redis/Celery/OCR nuevo): es una fase de hardening, auditoría y corrección con ciclo **TEST → FIX → TEST**. Se corrigieron 5 problemas reales (atomicidad del lote de tareas, compare-and-set de confirmaciones, fechas en zona local de la proactividad, fuga de excepciones crudas al usuario en el registry y mensaje del proveedor visual), se hizo la configuración de reintentos de OpenAI, se añadió la **matriz de regresión IDOR** y **flujos E2E** del orquestador, y se documentaron 4 hallazgos que se dejan intactos de forma deliberada (con su justificación). Todo verificado: **no se ha empezado la Fase 9**.

## 2. Correcciones aplicadas (TEST → FIX → TEST)

### 2.1. ALTO — Lote de tareas no atómico desde imágenes
- **Problema:** `_apply_tasks` (`tools/images.py`) creaba las tareas una a una con commit propio por tarea: si la 2ª fallaba, las anteriores ya estaban persistidas (parcial).
- **Fix:**
  - `TaskRepository.create(session=None)` ahora usa `self._session(session)` (commitea solo si no hay sesión inyectada) y `flush()+refresh()` en vez de `commit()`.
  - Nuevo `TaskService.create_batch(*, user_id, tasks) -> list[Task]` envuelto en `self._tasks.transaction()` (un único commit; rollback total ante fallo).
  - `_apply_tasks` construye la lista y delega en `create_batch`.
- **Test (regresión):** `test_lote_de_tareas_es_atomico_si_una_falla` (la 2ª creación falla → `list(uid) == []`). `test_image_tools.py` **11/11**.

### 2.2. ALTO — Confirmaciones sin compare-and-set
- **Problema:** `set_status` actualizaba `id=?` desde cualquier estado → una confirmación ya resuelta (confirmada o cancelada) podía re-confirmarse y re-ejecutarse (riesgo de doble efecto).
- **Fix:** el `UPDATE` ahora lleva `PendingConfirmation.status == "open"` en el `WHERE` (transición solo desde `open`). Confirmar dos veces → la segunda devuelve `None`.
- **Tests (regresión):** `test_confirmar_dos_veces_la_segunda_es_rechazada`, `test_cancelar_impide_confirmar_despues` y `test_repo_real_cas_contra_confirmaciones_ya_resueltas` (SQLite real). `test_confirmations.py` **14/14**.

### 2.3. MEDIO — Fechas de proactividad en zona local
- **Problema:** `build_candidates` (`domain/proactivity.py`) hacía `.date()` sobre marcas **UTC**: con España en UTC+1/+2 el amanecer desplazaba el día (off-by-one) en viajes, ITV, mantenimiento del hogar y recordatorios.
- **Fix:** `to_local(...).date()` en viajes (`days_left`), ITV (deseado y body), mantenimiento del hogar y recordatorios (`remind_local`). `to_midnight` queda sin uso y se conserva.
- **Tests (regresión):** `test_viaje_dias_y_fechas_en_zona_local` y `test_itv_se_avisa_en_fecha_local` (fechas futuras dinámicas, ya que `get_expirations`/`get_upcoming` usan `utc_now()` real). `test_proactivity.py` **17/17**.

### 2.4. MEDIO — Fuga de excepciones crudas al usuario (registry)
- **Problema:** el `except Exception` del registry devolvía `f"Error inesperado en '{name}': {exc}"` y el flujo de confirmación lo ecoaba al usuario (SQLAlchemy, red, tracebacks internos).
- **Fix:** log estructurado (`tool_unexpected_error`, `exc_info`) + mensaje genérico (`"Error interno al ejecutar la acción. Inténtalo de nuevo."`); `TypeError` → `"Argumentos no válidos para '{name}'."`. Los mensajes amigables de dominio que las tools producen con `_ko(exc)` se conservan.
- **Tests:** 2 actualizados + 1 nuevo (`test_fuga_de_nivel_bajo_nunca_llega_al_usuario` incluye un `UniqueViolation` simulado). `test_tools_registry.py` **12/12**.

### 2.5. BAJO — Mensaje del proveedor visual
- `OpenAIImageUnderstandingClient` incrustaba `{exc}` de OpenAI en `ImageExtractionError("provider_unavailable", ...)`. Ahora el detalle va a los logs (structlog) y el usuario recibe un mensaje genérico. La categoría se conserva. `test_image_service.py` + `test_image_orchestrator.py` **39/39**.

### 2.6. Config — Reintentos de OpenAI
- Nuevo setting `openai_max_retries` (default **2**) y `.env.example` documentado. `create_openai_client` ahora pasa `max_retries` al SDK. El timeout se sigue pasando como `float` (`openai_timeout_seconds`) para ser compatible con el tipado del SDK; el timeout de conexión usa el default de httpx (10 s) y no se configura explícitamente.

## 3. Matriz de regresión IDOR (sin oráculo de existencia)
Nuevo `tests/unit/test_idor_matrix.py`: para **9 superficies sensibles** se verifica con repositorios reales que un ID de **otro usuario** y un ID **inexistente** producen el **mismo** `NotFoundError` y el **mismo mensaje** (`"... no existe o no te pertenece"`):

- `accounts.get` · `transactions.get` · `transfers.create` (cuenta ajena) · `savings.record_contribution` · `savings.cancel_goal` · `debts.settle` · `budgets.get_status` · `vehicles.get_status` · `trips.get`.

Resultado de la auditoría complementaria: el mensaje observable es **uniforme en las 71 líneas** de `raise NotFoundError` revisadas (todas las entidades), por lo que el backend no revela existencia. Los refuerzos defensivos a nivel de repositorio sin `user_id` (`SharedExpenseRepository.list_for_expense`, `ProactiveNotificationRepository.mark_pending`) **solo se alcanzan tras resolver una entidad scoped** — son internos y no constituyen IDOR.

## 4. Flujos E2E del orquestador (nuevos)
Nuevo `tests/unit/test_e2e_confirm_flows.py` (3 flujos completos con tools/servicios reales y dobles deterministas del LLM):
- **Mantenimiento de vehículo con coste**: la tool registra mantenimiento *y* mueve dinero (`expense` de `80.50 €`).
- **Viaje → cancelación con confirmación**: antes del "sí" el viaje sigue `planned`; tras "sí" queda `cancelled`.
- **Viaje rechazado con "no"**: el viaje sigue `planned` (nada se cancela).

Complementa la cobertura E2E existente (imagen→propuesta→confirmación→escritura, repostaje, mantenimiento de casa con coste, desactivación de vehículo con confirmación, agenda, inteligencia).

## 5. Decisión de idempotencia (documentada)
El orquestador marca la confirmación como `confirmed` **antes** de ejecutar la tool y una tool que falla **consume** la confirmación (no se reintenta). Es "exactly-once-attempt", deliberado para **evitar duplicados** (un reintento podría repetir un gasto). Riesgo residual asumido: si falla de forma transitoria se pierde una intención confirmada; el usuario deberá repetir la petición. El CAS de la sección 2.2 garantiza que, incluso con concurrencia, una confirmación resuelta nunca vuelve a ejecutarse.

## 6. Hallazgos documentados, NO corregidos (intencionado)
1. **Código muerto en proactividad (BAJO):** en producción `main.py` inyecta `ReminderRepository` como `reminders` de `ProactivityService`, pero ese repositorio NO tiene `list_pending` → el bloque "Recordatorios pendientes" de `build_candidates` lanza `AttributeError` tragado por `except Exception: pass` → el bloque es **inerte**. NO se ha activado porque cambiaría el volumen/comportamiento de notificaciones proactivas; se documenta para una decisión de producto explícita.
2. **Fixtures que limitan tests de hogar (tests):** `conftest.py` construye `ProactivityService` sin vehicles/homes/reminders y `FakeHomeMaintenanceRepo` devuelve registros sin `.description` → el camino de hogares no es testeable con fakes. Se cubrió con repos reales en SQLite.
3. **Índice parcial en SQLite (tests, no en prod):** `Index("ix_accounts_user_default", ..., postgresql_where=text("is_default"))` en SQLite se crea como `UNIQUE(user_id)` **simple** (no traduce el `postgresql_where`) → en tests con esquema real cada usuario solo puede tener **1 cuenta** (PG sí permite varias). Limita el E2E de traspaso con repos reales en SQLite; el flujo de traspaso sigue cubierto por los tests de servicio/tool (`test_transactions_transfers`, `test_finance_tools`).
4. **Confirmación antes de ejecución (sección 5)**: ver decisión asumida.

## 7. Puertas completas
| Gate | Resultado |
|---|---|
| `pytest tests -q` (con `TEST_DATABASE_URL`) | **990 passed, 0 skipped** (977 unit + 13 PG) |
| `pytest tests -q` (sin PG) | 977 passed, 13 skipped |
| `ruff check src tests` | All checks passed |
| `ruff format --check src tests` | 184 files already formatted |
| `mypy src/asistente` | Success (110 source files) |
| `alembic heads` | `0013_fase6_2_claim_lease` (head único) |
| Migración nueva | **NO** |

## 8. Verificación — PostgreSQL Real
Instancia local PostgreSQL 16.4 portable en `127.0.0.1:5432`, `TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@127.0.0.1:5432/asistente_test`. Los **13 tests de integración** (alembic, ping/sesión, concurrencia de claims, flujo de imagen) pasan. Durante la fase se detectó que la matriz IDOR y los flujos E2E necesitan `make_connected_services` (SQLite en memoria conectada con esquema completo), que es donde se ejerce el comportamiento con repositorios reales.

## 9. Conclusión
Fase 8 cerrada: **sin funcionalidades nuevas**, 5 correcciones con evidencia de test, configuración de reintentos de OpenAI, matriz IDOR sin oráculo de existencia (9 superficies), flujos E2E de dinero y confirmación, y 4 hallazgos documentados con la decisión de no activarlos. Todos los gates pasan (990 tests, 0 errores ruff/mypy, formato OK, migraciones OK). **No se ha empezado la Fase 9.**

## Archivos Modificados (Fase 8)
- `src/asistente/persistence/repositories/tasks.py`: `create(session=None)` + `flush/refresh` (sin commit propio).
- `src/asistente/domain/tasks.py`: `TaskService.create_batch` (transacción atómica).
- `src/asistente/tools/images.py`: `_apply_tasks` → `create_batch`.
- `src/asistente/persistence/repositories/confirmations.py`: `set_status` con compare-and-set.
- `src/asistente/domain/proactivity.py`: fechas en zona local.
- `src/asistente/tools/registry.py`: sin fuga de excepciones (logs + mensaje genérico).
- `src/asistente/ai/vision.py`: mensaje genérico del proveedor + log.
- `src/asistente/ai/llm.py`, `src/asistente/config.py`, `.env.example`: `openai_max_retries`.
- `tests/unit/test_tools_registry.py`: endurecido (sin leak) + 1 test nuevo.
- `tests/unit/test_idor_matrix.py` (nuevo): matriz IDOR.
- `tests/unit/test_e2e_confirm_flows.py` (nuevo): flujos E2E.
- (Complementan esta fase los tests añadidos en su arranque: `test_image_tools.py` atomicidad, `test_confirmations.py` CAS, `test_proactivity.py` timezone — todos en verde en los gates de la sección 7.)