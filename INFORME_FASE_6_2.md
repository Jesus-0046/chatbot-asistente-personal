# INFORME FINAL — FASE 6.2: CORRECCIÓN DE CONCURRENCIA, CLAIM/LEASE Y ROBUSTEZ DEL PROACTIVE POLLER

**Fecha:** 2026-09-18
**Proyecto:** Asistente Personal AI (ChatBot)

---

## 1. Resumen Ejecutivo
Fase 6.2 completa: **842 tests pasan, 11 skipped** (los 11 son los de PostgreSQL real, que requieren `TEST_DATABASE_URL`), **0 errores ruff**, **0 errores mypy**, **formato OK**, `alembic heads` → `0013_fase6_2_claim_lease`. Se implementó un claim/lease real con `FOR UPDATE SKIP LOCKED` (PostgreSQL), recuperación de leases expirados (reclaim), verificación de propiedad del worker en `mark_sent`/`mark_skipped`, límite diario atómico vía `proactive_daily_counters`, dedup race-safe ante `IntegrityError` y reescritura del poller al flujo create→claim→send→mark_sent.

## 2. Objeto y Alcance
El problema resuelto: la Fase 6.1 no ofrecía garantías reales bajo concurrencia. `claim_pending` marcaba pending→processing sin lease, el límite diario se calculaba con `count_delivered_since` (no atómico) y un crash entre el envío y `mark_sent` rompía la coherencia. Fase 6.2 corrige la capa técnica de proactividad SIN rehacer el diseño funcional (Fase 6/6.1).

## 3. Diseño Claim/Lease (FOR UPDATE SKIP LOCKED)
`ProactiveNotificationRepository.claim_pending` ahora es un claim real dentro de UNA transacción:
- Selección de IDs con `SELECT ... FOR UPDATE SKIP LOCKED` (solo dialecto `postgresql`, mismo patrón que `ReminderRepository.claim_due`).
- Marca las filas como `processing` con `worker_id`, `claimed_at` y `lease_until = now_utc + lease_seconds`, `UPDATE ... RETURNING`.
- Las filas se ordenan por id para un orden determinista (el RECORDING no garantiza orden).
- En SQLite (tests) no se emite locking; la semántica se valida con fakes y repos reales de un solo worker.

## 4. Contador Diario Atómico (proactive_daily_counters)
Tabla nueva `proactive_daily_counters(user_id, day, count)` con clave única `(user_id, day)` y `CHECK count >= 0`:
- Upsert `ON CONFLICT DO NOTHING` + `SELECT ... FOR UPDATE` en la MISMA transacción del claim: la fila queda bloqueada hasta el commit, por lo que **varios workers no pueden reservar más presupuesto que `daily_limit`** en el día local.
- El contador incrementa SOLO por claims fresh (pending). Los reclaims de leases expirados NO incrementan (su presupuesto ya se reservó al reclamarlos).
- `mark_sent`/`mark_skipped` no tocan el contador: el límite mide notificaciones reclamadas (reservadas), no entregadas, lo que es conservador y seguro.

## 5. Recuperación de Leases Expirados (Reclaim)
- Criterio: `status='processing' AND worker_id IS NOT NULL AND lease_until < now`. Esas filas vuelven a ser elegibles en el claim de cualquier worker.
- Los reclaims se devuelven ANTES que los fresh y NO consumen presupuesto nuevo; el total de `sent` real no puede superar `daily_limit` (fresh + reclaims ya entraron en el cupo cuando se reservaron).
- Un crash tras el claim y antes del envío queda automáticamente recuperable al expirar el lease (por defecto 600 s).

## 6. Propiedad del Lease en mark_sent / mark_skipped
Ambos métodos finalizan únicamente si el UPDATE afecta a una fila
`id = :id AND user_id = :user_id AND status='processing' AND worker_id = :worker_id AND lease_until > now`.
- Si otro worker reclamó la fila (lease expirado + reclaim) o el lease ya no es válido, el UPDATE no afecta a ninguna fila y se devuelve `None`: **un worker zombie NO puede marcar como enviado un claim ajeno**.
- Al finalizar se limpian `claimed_at`, `lease_until`, `worker_id` (y `sent_at` se fija).
- Factory method `_settle`-style verificado también por objeto con `SELECT` implícito: la fila debe seguir siendo del worker.

## 7. Dedup Race-Safe (IntegrityError del índice único)
`ProactivityService.create_or_reuse` tolera la carrera clásica (dos workers ven "sin fila" e insertan el mismo dedup): el INSERT perdedor levanta `IntegrityError` del índice parcial `ix_proactive_notifications_user_kind_dedup`; se captura, se **verifica que sea el índice de dedup** (`_is_dedup_error`: `orig.constraint_name` de asyncpg, `diag.constraint_name` de psycopg, mensaje de SQLite) y se re-busca la fila ganadora para reutilizarla.
- Cualquier `IntegrityError` que NO venga del índice de dedup se re-lanza (no se ocultan errores reales).
- La fila `sent` devuelve `None` (no se reenvía); la `skipped` se reutiliza como intento pending.

## 8. Modelo y Migración Alembic 0013
- `proactive_notifications`: +`claimed_at`, +`lease_until`, +`worker_id` (String 64); CHECK de status ampliado a `('pending','processing','sent','skipped')`; índice nuevo `(status, lease_until)` para localizar reclaims.
- Tabla nueva `proactive_daily_counters` con su clave única y `CHECK count >= 0`.
- `0013_fase6_2_claim_lease.py`: migración ADITIVA de campos, recrea el CHECK e índices, crea la tabla de contadores; `downgrade` revierte sin destruir datos. `alembic heads` lo muestra como head.

## 9. Configuración — PROACTIVE_NOTIFICATION_LEASE_SECONDS
- `Settings.proactive_notification_lease_seconds: int = 600` en `config.py`.
- El poller lo recibe en `_build_proactive_poller` (bot.py) y pasa al claim. El repositorio usa `_DEFAULT_LEASE_SECONDS = 600` como fallback.
- `worker_id` es `str(uuid4())` por instancia de `ProactivePoller` (identidad del proceso).

## 10. Flujo del ProactivePoller (create → claim → send → mark_sent)
`_deliver` reescrito:
1. Genera candidatos, los filtra por `importance_threshold` (filtrados → `stats.filtered`).
2. `create_or_reuse` de cada candidato (dedup race-safe; `sent` → `stats.deduped`).
3. `claim_pending` atómico con `worker_id`, `day_local`, `daily_limit`, `limit=len(candidates)`, `lease_seconds`. El repo reserva presupuesto y devuelve solo lo que cabe (reclaims primero, fresh después).
4. Por cada fila reclamada: `send(user_id, title\body)`. Si falla → `stats.failed`, la fila queda `processing` a la espera de que expire el lease (no consume envíos ni presupuesto de más).
5. `mark_sent` con propiedad de lease → si devuelve `None` (otro worker recuperó la fila o lease expirado) NO se cuenta como entregada.
6. `daily_limit == 0` se salta al usuario (`stats.limited`); respeta horas de silencio (`stats.quiet`).

## 11. Semántica de Entrega — At-Least-Once (NO Exactly-Once)
- **Garantizado:** una misma fila no es enviada por dos workers simultáneamente; el total de `sent` por día local no supera `daily_limit`; un crash no deja notificaciones perdidas (lease + reclaim).
- **NO se ofrece exactly-once:** la entrega al canal es externa; un crash entre `send` y `mark_sent` puede causar un reenvío (la dedup por (user_id, kind, dedup_key) impide éste en pasadas posteriores solo si el estado es `sent`). El sistema es por tanto **at-least-once**, consistente con el resto del subsistema de notificaciones.
- **Decisión documentada:** ante fallo del sender, la fila NO se marca `sent` ni se salta; se deja en `processing` para que el lease expire y sea reclamada. No se decrementa el contador al fallar: es conservador (el presupuesto reservado se considera consumido), evita doble reserva en reclaims y mantiene el límite como cota superior de envíos.

## 12. Testing — Cobertura por Categoría
Nuevos tests Fase 6.2: **37** (25 unit/fake + 6 repos reales SQLite + 6 integración PostgreSQL marcados `db`).

| Categoría | Test | Obs. |
|---|---|---|
| Claim (6) | marca processing+lease+contador / respeta daily_limit / sin presupuesto no fresh / vacío sin candidatos / reclaims antes que fresh / no duplica con lease vigente | fakes |
| Lease (5) | expirado reclaimable / vigente no reclamable / mark_sent denegado tras reclaim / denegado sin claim / limpia claim y mueve a sent | fakes |
| Poller (5) | envía tras claim y limpia lease / respeta daily_limit / fallo de sender queda processing y reclaimable / daily_limit 0 → limited / reclaim entre pasadas sin gastar presupuesto | fakes |
| Daily limit (5) | contador acumula entre claims / reclaim no consume / 20 candidatos con límite 5 / mark_sent no altera contador / 10 workers no superan límite | fakes |
| Dedup (3) | perdedor recupera fila ganadora / reusa fila skipped / IntegrityError no-dedup se re-lanza | fakes |
| IDOR/aislamiento (1) | claim y mark_sent de otro user_id no tocan filas ajenas | fakes |
| Stress (1) | **10 workers × 20 candidatos × daily_limit=5 → total ≤ 5**, cada fila una vez | fakes + PG |
| Repos SQLite (6) | flujo claim+contador / presupuesto / propiedad mark_sent / lease expirado + zombie / mark_skipped lease / dedup conflicto SQL recuperado | real SQLite |
| PostgreSQL (6) | claim concurrente no supera límite / reclaim sin gastar / propiedad mark_sent / mark_skipped / dedup concurrente IntegrityError recuperable / stress 10×20×5 | `@pytest.mark.db` |

## 13. Testing — PostgreSQL Real (cómo ejecutar)
Se levantó una instancia local de **PostgreSQL 16.4 portable** (binarios oficiales EDB, sin Docker) en `127.0.0.1:5432`, con `TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@127.0.0.1:5432/asistente_test`. Los 11 tests `db` se ejecutaron y **pasan**:
```
pytest tests/integration -m db -q        → 11 passed
```
Verificados sobre una base real: dos workers (`asyncio.gather` sobre el mismo engine) no superan el `daily_limit` con `FOR UPDATE SKIP LOCKED`; el reclaim de leases expirados no consume presupuesto; `mark_sent` exige propiedad (worker zombie rechazado); la carrera de dedup (dos INSERT concurrentes) genera UNA fila, se clasifica el `IntegrityError` (`_is_dedup_error`) y la fila ganadora se recupera; y stress real 10×20×5 → 5 `sent`, 15 `pending`, 0 `processing`.

## 14. Calidad de Código
| Gate | Resultado |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 191 files already formatted |
| `mypy src/asistente` | Success (0 errores, 107 archivos) |
| `pytest tests` | 853 passed (con tests PostgreSQL) |
| `pytest tests/integration -m db` | 11 passed |
| `alembic heads` | 0013_fase6_2_claim_lease (head único) |
| `alembic upgrade` (BD desde cero) | 0001 → 0013 OK (37 tablas) |
| `alembic downgrade 0012 → upgrade head` | OK (camino 0012→0013 verificado) |

## 15. Limitaciones, Verificación Pendiente y Conclusión
- **Sí verificada**: toda la suite `db` (11 tests) se ejecutó y pasó contra PostgreSQL 16.4 real (ver §13). Quedan bajo responsabilidad del operador usar una instancia PostgreSQL real en CI/producción; el comando es el mismo (`pytest tests/integration -m db -q`) con `TEST_DATABASE_URL`.
- **Correcciones descubiertas durante la verificación** (ninguna en la lógica de negocio de Fase 6.2):
  - `migrations/env.py`: Alembic creaba `alembic_version.version_num` como `VARCHAR(32)`, pero los revision ids (p. ej. `0007_fase3_3_recurrencias_*`) superan 32 caracteres → el `upgrade` sobre BD real fallaba con truncation. Se garantiza ahora una columna `VARCHAR(255)` y un commit explícito del paso previo (de lo contrario Alembic no gestiona su propio COMMIT y revierte TODO al cerrar la conexión). No se modificó ninguna migración histórica.
  - `tests/integration/test_alembic.py`: aserciones corregidas (el progreso de alembic va a stderr, no stdout; y `current` tras upgrade a head muestra `0013_fase6_2_claim_lease`, no el baseline).
  - `tests/integration/test_claim_concurrency_pg.py`: faltaba `await` en `_count_due` y el conteo debía tomarse ANTES del claim (tras reclamar, las filas ya no son `pending`).
  - `tests/integration/test_proactive_claim_concurrency_pg.py`: la aserción de "5 workers distintos" era un supuesto erróneo (un solo worker puede reclamar todo el presupuesto; lo prohibido es reclamar dos veces una misma fila). Se sustituyó por el invariante real.
- Fases 1–6.2 intactas: suite completa pasa (853 con tests PG incluidos).
- **Conclusión:** Fase 6.2 implementada y **verificada sobre PostgreSQL real** conforme a las garantías requeridas (claim/lease atómico, límite diario seguro bajo concurrencia, dedup race-safe, entrega at-least-once documentada, sin exactly-once). El sistema queda listo para revisión.

## Archivos Nuevos (Fase 6.2)
- `migrations/versions/0013_fase6_2_claim_lease.py`
- `tests/unit/test_phase6_2.py` (25 tests)
- `tests/unit/test_phase6_2_sqlite.py` (6 tests)
- `tests/integration/test_proactive_claim_concurrency_pg.py` (6 tests `db`)

## Archivos Modificados (Fase 6.2)
- `src/asistente/persistence/models.py`: ProactiveNotification (claimed_at/lease_until/worker_id, status processing, índice status_lease) + ProactiveDailyCounter.
- `src/asistente/persistence/repositories/proactivity.py`: claim_pending real (FOR UPDATE SKIP LOCKED + contador), mark_sent/mark_skipped con propiedad de lease.
- `src/asistente/domain/proactivity.py`: create_or_reuse race-safe (IntegrityError dedup), claim_pending/mark_sent/mark_skipped, `_is_dedup_error`.
- `src/asistente/scheduler/proactive_poller.py`: flujo create→claim→send→mark_sent con worker_id + lease.
- `src/asistente/adapters/telegram/bot.py`: lease_seconds en `_build_proactive_poller`.
- `src/asistente/config.py`: proactive_notification_lease_seconds.
- `tests/unit/fakes.py`: FakeProactiveNotificationRepo con claim/lease/contador/dedup.
- `tests/unit/test_proactive_poller.py`, `tests/unit/test_proactivity.py`: llamadas actualizadas al nuevo contrato.
- `migrations/env.py`: columna `alembic_version.version_num` ensanchada a VARCHAR(255) y commit explícito del paso previo (fix infraestructura, no toca migraciones históricas).
- `tests/integration/test_alembic.py`, `tests/integration/test_claim_concurrency_pg.py`: aserciones corregidas en la verificación real (stderr, versión de cabecera, `await`, conteo antes del claim).