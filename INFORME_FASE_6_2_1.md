# INFORME FINAL — FASE 6.2.1: CORRECCIÓN DEL DAILY LIMIT EN PRESENCIA DE RECLAIMS

**Fecha:** 2026-09-20
**Proyecto:** Asistente Personal AI (ChatBot)

---

## 1. Resumen Ejecutivo
Fase 6.2.1 completa: **861 tests pasan, 0 skipped**, incluidos **12 tests de PostgreSQL real** (`pytest tests/integration -m db -q`), **0 errores ruff**, **0 errores mypy**, **formato OK**, `alembic heads` → `0013_fase6_2_claim_lease` (head único, **sin migración nueva**: no hubo cambio de esquema). Se corrigió el defecto del límite diario: el presupuesto se reservaba en el **claim** (solo fresh) y los reclaims de leases expirados NO reservaban, de modo que con la semántica at-least-once (crash tras `send` antes de `mark_sent` + reclaim + reenvío) un usuario podía recibir MÁS envíos reales que `daily_limit` mientras el contador marcaba exactamente N. Ahora el límite se aplica por **ENVÍO** (reserve-before-send) en una transacción atómica, y la invariante es sobre envíos efectivos: **nunca más de `daily_limit` entregas reales por usuario y día local**, incluso con reclaims, reintentos y reenvíos.

## 2. Objeto y Alcance
### 2.1. Defecto corregido (Fase 6.2)
En Fase 6.2, `claim_pending` incrementaba `proactive_daily_counters` por las filas fresh reclamadas dentro de la transacción del claim (`FOR UPDATE`), y los reclaims de leases expirados NO incrementaban. El contador medía "notificaciones reclamadas como fresh", no "envíos emitidos". Con at-least-once:
1. `claim` fresh → reserva 1 unidad … `send` → **crash antes de `mark_sent`** (fila queda `processing`, reserva ya computada).
2. El lease expira → **reclaim** (sin reservar) → nuevo `send` (REENVÍO) → `mark_sent`.

Resultado: un usuario con `daily_limit=N` podía recibir N envíos fresh + M reenvíos (M = nº de crashes) en el mismo día local; el contador solo subía N. El patrón de negocio lo exigía: el reclaim "gratis" era la recuperación del crash, pero el reenvío es un ENVÍO REAL y debe comprar su unidad.

### 2.2. Alcance
- NO se rehace la arquitectura de Fase 6.2 (claim/lease, `FOR UPDATE SKIP LOCKED`, propiedad del lease, dedup race-safe, reclaim, poller create→claim→send→mark_sent) — todo se conserva.
- NO se añaden dependencias nuevas, ni locks Python/globales/Redis/externos: la serialización sigue siendo 100 % transaccional en la base de datos.
- NO se tocó ninguna migración histórica y NO se crea migración (el esquema ya tenía `proactive_daily_counters` con su CHECK `count >= 0`).

## 3. Invariante (Fase 6.2.1)
**Invariante:** Para cada `(user_id, day_local)`, el número de **envíos externos efectivos** nunca supera `daily_limit`, aunque haya reclaims, reintentos tras fallo del sender o reenvíos tras crash (throughput).

Definición del contador: `count` == envíos **reservados y no liberados** de ese día local (confirmados con `mark_sent` + "crash-post-send" sin confirmar). Cada envío que se emite compra su unidad en `reserve_send` justo antes de llamar a `sender.send()`. Dos corolarios:
- Un **crash antes** de `reserve_send`/`send` no reserva nada → el reintento posterior reserva UNA unidad. ✓
- Un **crash después** de `send` (antes de `mark_sent`) mantiene la reserva → el reenvío del reclaim necesita una unidad NUEVA (si no hay presupuesto, se bloquea). Conservador contra sobre-envíos. ✓

## 4. Diseño — Presupuesto por ENVÍO (reserve-at-send)
### 4.1. `ProactiveNotificationRepository.reserve_send(user_id, *, day_local, daily_limit) -> bool`
- Upsert `ON CONFLICT DO NOTHING` de la fila `(user_id, day_local)` + **`UPDATE proactive_daily_counters SET count = count + 1 WHERE count < daily_limit RETURNING count`**.
- El `UPDATE` bloquea la FILA del contador: dos workers que reservan a la vez se serializan (aunque el claim previo usara `FOR UPDATE SKIP LOCKED`, la barrera del límite es el lock de esta fila).
- Devuelve `False` si el límite ya está lleno (el envío NO se emite). `daily_limit <= 0` → `False` sin crear fila.
- Es la única operación que aumenta `count`.

### 4.2. `release_send(user_id, *, day_local)`
- `UPDATE ... SET count = count - 1 WHERE count > 0` (reforzado por el CHECK `count >= 0`). El poller lo invoca solo cuando su `sender.send()` **lanza** (entrega no emitida): esa unidad vuelve a estar disponible para el reintento del reclaim.

### 4.3. `unclaim(user_id, notification_id, *, worker_id, now_utc) -> bool`
- Cuando `reserve_send` deniega una fila ya reclamada, `unclaim` la devuelve de `processing` → `pending` (con la MISMA verificación de propiedad que `mark_sent`: `worker_id` + lease vigente). Sin esto, las filas sin presupuesto quedarían huérfanas en `processing` todo el día, condenadas a reclaims que tampoco pueden enviarlas.
- Quien pierde la propiedad (lease expirado/reclaim) obtiene `False`: otro worker gestiona la fila.

### 4.4. `claim_pending` (cambios)
- YA NO hace upsert/bloqueo/incremento del contador. Lee `count` **sin lock** como ASPIRACIÓN best-effort para el cap de fresh: `fresh_limit = min(limit - len(reclaims), max(0, daily_limit - counted))`.
- Los **reclaims siempre** se devuelven sin restricción (su presupuesto se decide en el envío).
- Consecuencia: un claim puede devolver más filas que el presupuesto restante (p. ej. 10 workers ven `count=0` y reclaman todo el pending). Eso no es un bug: `reserve_send` por fila es la barrera real y las filas sobrantes se liberan con `unclaim`. **Si** el claim siguiera reservando, la invariante se mantendría pero habría que re-reservar en los reclaims (redundante); la lectura sin lock mantiene el cap fresh (evita reclamar más de la cuenta) sin contabilizar aún.

## 5. Flujo del ProactivePoller (create → claim → reserve → send → mark_sent)
`_deliver` actualizado (líneas ~154-182 de `proactive_poller.py`):
1. Genera y filtra candidatos; `create_or_reuse` race-safe. *(sin cambios)*
2. `claim_pending` atómico (lease) — sin presupuesto. *(cambio: el repo ya no reserva)*
3. Por **cada fila reclamada**:
   - `reserve_send(...)`: `False` → `unclaim` (fila vuelve a `pending`) y `stats.limited += 1`. `True` → continuar.
   - `sender.send(...)`. Si **lanza** → `stats.failed += 1`, `release_send(...)` (la unidad vuelve; la fila queda `processing`, reclamable al expirar el lease).
   - `mark_sent(...)`: `None` (perdimos la propiedad o lease expirado) → **la reserva se mantiene**: el envío ya se emitió y el reenvío del reclaim comprará una unidad nueva (at-least-once contabilizado). Éxito → `stats.sent += 1`.

## 6. Semántica de Entrega — At-Least-Once, ahora DENTRO del límite
- Sigue siendo **at-least-once** (la entrega al canal es externa; un crash entre `send` y `mark_sent` puede redelivery al expirar el lease).
- La novedad: cada reenvío consume una unidad nueva del límite. Por eso los **envíos externos acumulados** (no los claims) son la magnitud acotada: `envíos reales ≤ count ≤ daily_limit`.
- La reserva de un envío que lanza se libera (interpretación estándar: excepción == no entregado). Ventana residual documentada: si la excepción llega DESPUÉS de una entrega externa real, `release_send` subestima el contador (potencial 1 envío extra). Los tests de Fase 6.2.1 asumen la interpretación estándar.

## 7. Aislamiento y estoque (sin cambios funcionales)
- `mark_sent`/`mark_skipped`/`unclaim` comparten el mismo predicado de propiedad (`worker_id` + `lease_until > now`).
- Reclaim, `FOR UPDATE SKIP LOCKED`, dedup race-safe (`_is_dedup_error`) y aislamiento por `user_id` intactos.

## 8. Migración
**No hay migración nueva.** `reserve_send`/`release_send`/`unclaim` reutilizan `proactive_daily_counters` (ya existente desde 0013) y `proactive_notifications`. `alembic heads` → `0013_fase6_2_claim_lease` (único). Se verificó que el camino de migración existente (0001→0013) no se ve afectado.

## 9. Testing — Tests nuevos (Fase 6.2.1)
Se actualizaron los tests de la semántica antigua (contador en el claim) a la nueva (contador en el envío) y se añadieron los 6 escenarios obligatorios:

| # | Test | Cobertura | Dónde |
|---|---|---|---|
| 1 | `test_envio_fresh_reserva_y_contabiliza_una_unidad` | un intento normal fresh consume 1 unidad | unit fakes |
| 2 | `test_fallo_del_sender_libera_la_reserva` | `sender.send()` falla → `release_send`, contador vuelve a 0, la fila queda reclamable | unit fakes |
| 3 | `test_crash_antes_del_envio_no_consume_y_reenvio_reserva_una_vez` | crash ANTES de enviar → el reclaim reenvía con UNA sola reserva (contador = 1) | unit fakes |
| 4a | `test_crash_tras_envio_y_reclaim_reenvia_at_least_once` | crash DESPUÉS de `send` → el reclaim reenvía con presupuesto y suma 1 unidad (at-least-once documentado, contador = 2 = envíos reales) | unit fakes |
| 4b | `test_crash_tras_send_y_reclaim_con_limite_real` | con el límite lleno (2/2), el reclaim NO reenvía y `unclaim` libera a pending | PostgreSQL real |
| 5 | `test_reclaim_y_fresh_comparten_limite_diario` | fresh + reclaim (crash-post-send) compiten por el mismo `daily_limit`; el reenvío se bloquea al llenarlo | unit fakes |
| 6 | `test_stress_10_workers_mezcla_fresh_y_expired_limite_5` | **10 workers × 20 candidatos (5 leases expirados + 15 fresh) × daily_limit=5** → total `sent` = 5, 0 `processing` | PostgreSQL real |

Tests unitarios adicionales: `test_claim_marca_processing_con_lease_sin_consumir_contador`, `test_claim_fresh_sin_contador_limitado_por_hint`, `test_contador_mide_envios_no_claims`, `test_claim_sin_presupuesto_no_reclama_fresh`, `test_diez_workers_no_superan_limite` (reeditado: el claim sobre-reclama 20 y `reserve_send` lo limita a 5), `test_reclaim_entre_pasadas_reserva_solo_al_enviar` (fallos liberan; la 2ª pasada reserva y entrega 2/2). SQLite real: `test_repo_release_send_no_deja_contador_negativo`, `test_repo_unclaim_devuelve_fila_a_pending`, `test_repo_crash_tras_send_y_reclaim_respeta_limite`, `test_repo_claim_y_reserve_flow`, `test_repo_claim_hint_y_reserva_limitan`. PostgreSQL real: `test_claim_paralelo_con_skip_locked_no_duplica_filas` (el claim ya no acota el total; `SKIP LOCKED` evita dobles claims), `test_reclaim_lease_expirado_solo_reserva_al_reenviar`.

## 10. Verificación — PostgreSQL Real
- Instancia local **PostgreSQL 16.4 portable** (EDB, sin Docker) en `127.0.0.1:5432`; `TEST_DATABASE_URL=postgresql+asyncpg://asistente:asistente@127.0.0.1:5432/asistente_test`.
- `pytest tests/integration -m db -q` → **12 passed** (11 previos + el nuevo crash-post-send).
- El stress de mezcla fresh+expired (test 6) y el crash-post-send (test 4b) verifican la invariante bajo `FOR UPDATE SKIP LOCKED` + lock de fila del contador.

## 11. Calidad de Código
| Gate | Resultado |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 191 files already formatted |
| `mypy src/asistente` | Success (0 errores, 107 archivos) |
| `pytest tests` | 861 passed (con tests PostgreSQL) |
| `pytest tests/integration -m db` | 12 passed |
| `alembic heads` | 0013_fase6_2_claim_lease (head único) |
| Migración nueva | NO (sin cambios de esquema) |

## 12. Limitaciones y Conclusión
- **Ventana documentada** (release tras excepción): si el sender entrega externamente pero lanza, `release_send` subestima el contador → a lo sumo 1 envío extra en un día (interpretación estándar: excepción == no entregado).
- **Garantizado:** envíos externos reales ≤ `daily_limit` por `(user_id, day_local)` bajo concurrencia (varios workers), reclaims y reenvíos; ninguna fila reclamada dos veces a la vez; los crash-post-send no pierden entregas (lease + reclaim) y no superan el límite.
- Lo que NO se garantiza: exactly-once (por diseño del subsistema de notificaciones, at-least-once).
- **Conclusión:** Fase 6.2.1 implementada y verificada sobre PostgreSQL real. El límite diario es ahora una cota superior de ENVÍOS, no de claims, sin dependencias externas ni locks en lenguaje, respetando toda la arquitectura de Fase 6.2. Sin migración. Sin empezar Fase 7.

## Archivos Modificados (Fase 6.2.1)
- `src/asistente/persistence/repositories/proactivity.py`: `claim_pending` ya no reserva presupuesto (hint best-effort); nuevos `reserve_send`, `release_send`, `unclaim`.
- `src/asistente/domain/proactivity.py`: passthroughs `reserve_send`/`release_send`/`unclaim`; docs de Fase 6.2.1.
- `src/asistente/scheduler/proactive_poller.py`: flujo reserve→send→(release|mark_sent), `unclaim` y `stats.limited` por fila sin presupuesto; docs.
- `src/asistente/persistence/models.py`: docstring de `ProactiveDailyCounter` (nota: el contador se reserva en el envío).
- `tests/unit/fakes.py`: `FakeProactiveNotificationRepo` con la nueva semántica (claim sin contador; `reserve_send`/`release_send`/`unclaim`).
- `tests/unit/test_phase6_2.py`: tests reescritos + 6 escenarios nuevos (§9).
- `tests/unit/test_phase6_2_sqlite.py`: tests reescritos + `_counter_state`/`_statuses` + 3 tests nuevos.
- `tests/integration/test_proactive_claim_concurrency_pg.py`: tests actualizados + `test_crash_tras_send_y_reclaim_con_limite_real` + stress mezcla fresh/expired.