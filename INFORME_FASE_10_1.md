# INFORME FASE 10.1 — DESPLIEGUE PRODUCTIVO 24/7

**Fecha:** 2026-09-23 · **Base previa:** Fase 10.0.1 cerrada (1062 tests, mypy 0, alembic head `0015_fase10_web_auth`, smoke HTTP 22/22).

## Resumen ejecutivo

Se ha preparado el proyecto para correr **24/7 en un servidor limpio** con Docker, persistencia, reinicio autónomo, HTTPS documentado y backups verificados. Sin cambios de funcionalidad de negocio (ninguna migración nueva; head sigue `0015_fase10_web_auth`). Se reutilizó (no se duplicó) la infraestructura existente de Fase 0 (Dockerfile, compose, .env.example, .dockerignore).

Todos los gates ejecutables en esta máquina **pasan con resultados reales** (PostgreSQL 16.12 real, `127.0.0.1:5432`). **Único gate NO EJECUTADO: build/start de Docker** (este Windows no tiene Docker engine; se entregan artefactos Docker validados estáticamente y con tests del repo, y la validación real queda pendiente del despliegue con `DEPLOYMENT.md` §3, que detalla el procedimiento).

## Qué se ha implementado (Fase 10.1)

1. **Validación de configuración en arranque (fail-fast)** en `src/asistente/config.py`:
   - `ConfigError(RuntimeError)` (bloquea arranque, sin exponer secretos).
   - `validate_critical_env` (proceso **bot**): exige `TELEGRAM_TOKEN` + `OPENAI_API_KEY` siempre y `WHATSAPP_VERIFY_TOKEN`/`WHATSAPP_ACCESS_TOKEN`/`WHATSAPP_APP_SECRET` si `WHATSAPP_ENABLED`; mensaje lista solo nombres de variables.
   - `validate_api_env` (proceso **API**, sin Telegram/OpenAI obligatorios por ser proceso separado): exige `DATABASE_URL` no vacía y `api_docs_enabled ⇒ api_enabled`.
   - `main.py` integra el check (salida con `SystemExit` + log) y **`WHATSAPP_WEBHOOK_HOST` configurable** (antes hardcoded `127.0.0.1`) — en producción/proxy va a `0.0.0.0`.
2. **API Web**: `create_app` construye por defecto su `Database(settings.database_url)` cuando no se inyecta (antes fallaba en producción con `db=None`); lifespan valida config → conecta BD si es suya → cierra en shutdown (`api_shutdown_complete`).
3. **Docker de producción**:
   - `Dockerfile`: imagen `python:3.12-slim`, **usuario no-root**, `procps` para healthchecks por proceso, CMD por defecto `alembic upgrade head && python -m asistente.main`.
   - `docker-compose.yml`: servicios `db` (healthcheck `pg_isready`, volumen `pgdata`, **sin puerto publicado por defecto** `:?`/`-0`), `migrate` (una vez, `service_completed_successfully`), `bot` (healthcheck `pgrep`, telegram+whatsapp+pollers) e `api` (healthcheck HTTP `/health`, `API_ENABLED=true`, `API_HOST=0.0.0.0`); `restart: unless-stopped`; **`POSTGRES_PASSWORD` obligatoria vía `${VAR:?}`** (se eliminó el default débil).
   - `docker-compose.yml` recalcula `DATABASE_URL` al host del servicio `db`.
   - `.dockerignore`: excluye `.env`, tests, logs y `backups`, conserva `README.md` (COPY del Dockerfile).
4. **`.env.example` actualizado**: documentado `WHATSAPP_WEBHOOK_HOST`, `API_*` (host/port/cors/docs/rate-limits), `PROACTIVE_*`, y bloque `BACKUP_*`/`POSTGRES_HOST/PORT` para los scripts. Defaults coherentes (`API_DOCS_ENABLED=false`).
5. **Backup / restauración** (`scripts/backup.sh`, `restore.sh`, `verify_backup.sh`):
   - `pg_dump -Fc --no-owner --no-privileges | gzip`, copia `_latest`, retención `BACKUP_KEEP_DAYS`; `restore.sh` hace drop-and-create (por defecto a BD **scratch** `BACKUP_TEST_DBNAME`, nunca a producción salvo `RESTORE_TARGET_DB`); `verify_backup.sh` restaura y comprueba `alembic_version` + filas de las tablas críticas. `PG_BIN` permite ejecutarlos en Windows (Git Bash) sin tocar código.
6. **Documentación**: `DEPLOYMENT.md` (requisitos del VPS, arranque, reverse proxy/HTTPS con nginx, checklist de seguridad), `OPERATIONS.md` (liveness/readiness, logs, restarts, diag de casos típicos), `BACKUP_RESTORE.md` (política de backup, systemd timer, drill de restauración), y actualización de la sección Docker de `README.md`.

## Tests añadidos

`tests/integration/test_production.py` (14 tests, de los que 3 requieren `TEST_DATABASE_URL` con marcador `db` y se ejecutaron sobre PostgreSQL real):

- `/health` responde ok sin BD ni servicios (liveness).
- `/ready` no responde 200 sin BD/servicios; responde **200** con BD real + `web_auth` real.
- Validación de startup: bot (falta Telegram/WhatsApp; pasa con config mínima) y API (`DATABASE_URL` vacía y `api_docs_enabled` incoherente → no arranca); `as_dict_for_logging` nunca filtra secretos.
- Ciclo de vida: la API no cierra una BD externa; con BD propia la conecta en startup y la cierra en shutdown (sobre PostgreSQL real).
- Persistencia 24/7: escritura con una conexión, lectura con **otra conexión nueva** (sobrevive a reconexiones).
- Docker estático: Dockerfile (non-root, python 3.12) y compose (db+bot+api, `pgdata`, healthcheck y restart en db/bot). (La validación YAML usa PyYAML, instalado en el venv solo para esta máquina.)

## Gates finales (resultados reales)

| Gate | Comando | Resultado |
|---|---|---|
| Tests unitarios + PG real | `pytest -q` con `TEST_DATABASE_URL` | **1076 passed, 0 failed, 0 skipped** (15 warnings) en 27.5 s |
| Tipos | `mypy src` | **PASS** — `Success: no issues found in 140 source files` |
| Lint | `ruff check .` | **PASS** — `All checks passed!` |
| Formato | `ruff format --check .` | **PASS** — 252 files already formatted |
| Migraciones | `alembic heads` | head único **`0015_fase10_web_auth`** (sin migración nueva) |
| Ciclo de migración | `upgrade head → downgrade 0014 → upgrade head` | **PASS** (rc 0; versión final `0015… (head)`) sobre BD scratch |
| Smoke HTTP capa Web real | `smoke_web_api.py` (BD scratch migrada) | **22/22 checks OK — PASS** |
| Gates de producción | `smoke_prod_gates.py` (BD real) | **5/5 checks OK** (`/health` 200, `/ready` 200 con BD+web_auth, `/docs` 404 con docs off, fail-fast msg, redacción de secretos) — **PASS** |
| Backup → Restore → Verify | `backup.sh` + `restore.sh` + `verify_backup.sh` con `pg_dump 16.12` | **PASS** real: dump 22 KB, restaurado a scratch con version `0015_fase10_web_auth`, seed 1 user/1 account/1 transaction/1 reminder intacto; `_latest` y retención OK; ciclo completo ejecutado vía Git Bash (`PG_BIN`) |
| Docker build/start | `docker compose up -d --build` | **NO EJECUTADO** — Docker engine ausente en este entorno (Windows). Artefactos entregados y validados estáticamente (test del repo + revisión YAML/Dockerfile) |

## Limitaciones / dependencias pendientes

- **Docker real no ejecutable aquí**: el gate de build/start/restart y los healthchecks en vivo del contenedor se validarán en el servidor siguiendo `DEPLOYMENT.md` §3 (`docker compose up -d --build`, `docker compose ps`, `/health`+`/ready`).
- El commit git del repo es la raíz del home (no un repo aislado); los cambios de esta fase son los listados en §"Qué se ha implementado" (ver `git status`), y `backups/` queda en `.gitignore`.
- La verificación de restore detecta el head y los contadores; el drill completo en servidor nuevo está documentado en `BACKUP_RESTORE.md` §8.
- El rate-limit por IP de `/api/v1/auth/*` sigue siendo in-memory (documentado; despliegue de un solo proceso; reinicios lo resetean).

## Cierre

Todos los gates ejecutables en este entorno pasan con evidencia real (Python 1076, mypy 0, ruff/format PASS, alembic head único con ciclo up/down OK, smoke HTTP 22/22 y prod 5/5, backup→restore→verify real con PG 16.12). **El único gate no ejecutable aquí es el build/start de Docker** (sin Docker engine en este equipo), cuyos artefactos están entregados y validados estáticamente; su verificación en vivo se realiza en el servidor con `DEPLOYMENT.md` §3, tal y como indica el plan ("propuesta reproducible, testearlo en el servidor"). No se ha empezado la Fase 10.2.

FASE 10.1: COMPLETADA