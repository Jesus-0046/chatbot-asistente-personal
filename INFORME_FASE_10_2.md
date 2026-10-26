# INFORME FASE 10.2 — VPS REAL + DOMINIO + HTTPS + OPERACIÓN 24/7

**Fecha:** 2026-09-23 · **Base previa:** Fase 10.1 cerrada (1076 tests, mypy 0, ruff/format PASS, alembic head único `0015_fase10_web_auth`, smoke HTTP 22/22, prod gates 5/5).

## 1. Resumen ejecutivo

La Fase 10.2 exige desplegar el proyecto en **infraestructura real** (VPS con IP pública, dominio con DNS, HTTPS, Telegram/WhatsApp/OpenAI de producción). Tras la auditoría de esta sesión se constata que **ninguno de esos elementos existe todavía**:

- No hay VPS (ni IP pública ni acceso SSH): la única máquina es la de desarrollo (Windows local).
- No hay dominio ni acceso DNS.
- No hay credenciales de producción (Telegram/WhatsApp/OpenAI).
- No hay Docker Engine en esta máquina (mismo blocker de Fase 10.1).

En consecuencia, **los gates obligatorios de infraestructura (§40) no pueden ejecutarse** y, siguiendo la regla §42 ("NO inventes el resultado... no declares la fase completada si un gate obligatorio no se ha verificado"), esta fase queda **documentada como NO COMPLETADA**, con el checklist completo y el plan exacto para verificarla en cuanto exista la infraestructura.

**No se ha modificado ningún código** en esta fase (sin funcionalidades, sin migraciones, sin commits de despliegue). El estado del código es exactamente el de Fase 10.1.

## 2. Auditoría previa (regla principal)

Se releyeron y verificaron los artefactos de despliegue de Fase 10.1:

| Artefacto | Estado |
|---|---|
| `DEPLOYMENT.md` | Presente y completo (requisitos VPS, arranque, reverse proxy HTTPS, checklist de seguridad) |
| `OPERATIONS.md` | Presente (liveness/readiness, logs, restarts, casos típicos) |
| `BACKUP_RESTORE.md` | Presente (política, systemd timer, drill de restauración) |
| `.env.example` | Presente: `POSTGRES_*`, `DATABASE_URL`, `TELEGRAM_TOKEN`, `OPENAI_API_KEY`, `WHATSAPP_*`, `API_*`/CORS, pollers, backup. Sin valores reales |
| `docker-compose.yml` | `db`+`migrate`+`bot`+`api`, volumen `pgdata`, healthchecks, `restart: unless-stopped`, `POSTGRES_PASSWORD` obligatoria (`:?`), puerto DB externo cerrado por defecto |
| `Dockerfile` | `python:3.12-slim`, usuario no-root, `procps`, CMD bot |
| `scripts/backup.sh`, `restore.sh`, `verify_backup.sh` | Presentes; probados en Fase 10.1 sobre PostgreSQL 16.12 real (ciclo dump→restore→verify OK) |

**Decisiones de cara al despliegue (cuando exista VPS):**
- Reutilizar tal cual el compose y el Dockerfile de Fase 10.1 (no rehacer Docker).
- Método de deploy más simple reproducible: **`scp` de los artefactos** (no hay git remote en este repo) o `git clone` si se añade remoto. Los dos figuran en §6.
- Reverse proxy HTTPS: **Nginx + Certbot** (única opción; no instalar varias).

## 3. Hardware / SO del VPS

**NO EJECUTADO** — no existe VPS. Cuando exista, registrar (comandos definidos en §40): `uname -a`, `cat /etc/os-release`, `free -h`, `df -h`, y la arquitectura/CPU/disco.

## 4. Docker (real)

**NO EJECUTADO** — no hay Docker Engine disponible en este entorno ni VPS:

- `docker version` → no disponible (misma situación que Fase 10.1).
- `docker compose version` → no disponible.
- `docker compose build` → **NO EJECUTADO**; es el gate pendiente que Fase 10.1 dejó sin verificar.

Cómo se verificará después: en el VPS ejecutar `docker version`, `docker compose version`, `docker compose build` y registrar la salida real.

## 5. PostgreSQL

**NO EJECUTADO** en VPS. Queda preparado en compose (volumen persistente, healthcheck `pg_isready`, restart, red interna, sin puerto externo). Verificación posterior: `docker compose ps`, `docker compose logs db`, y que `pg_isready` pasa (healthcheck).

## 6. Método de despliegue registrado

Dos opciones documentadas, sin subir `.env`, claves, tokens, backups ni `.venv` (están en `.gitignore`/`.dockerignore`):

- **Opción A (sin remoto):** `scp` del proyecto al VPS y `docker compose up -d --build` en el VPS.
- **Opción B:** `git clone <remoto>` (requiere añadir remoto al repo local; hoy `git remote` está vacío) + `docker compose up -d --build`.

Registro de versión: al desplegar, capturar `git rev-parse HEAD` en el VPS (o hash/sello de la copia).

## 7. Variables de entorno / secrets

**NO EJECUTADO** en VPS (no existe `.env` de producción; el repo no contiene `.env`). Plan registrado:

1. `cp .env.example .env` en el VPS y rellenar SOLO las variables que definen `config.py` (sin inventar ninguna).
2. En el informe del despliegue se declarará `CONFIGURADO` (nunca valores).
3. `chmod 600 .env` + ownership de un usuario de operación.
4. Verificación de fail-fast: ausencia de `TELEGRAM_TOKEN`/`OPENAI_API_KEY` (bot) y de `DATABASE_URL` incoherente (API) impide el arranque (`ConfigError` → `SystemExit` / `RuntimeError`). Este comportamiento ya está probado en `tests/integration/test_production.py` (Fase 10.1).

## 8. Firewall / red

**NO EJECUTADO** (no hay VPS). Política registrada: permitir `22/tcp`, `80/tcp`, `443/tcp`; bloquear el puerto de PostgreSQL hacia el exterior; verificar desde fuera que 5432 no es alcanzable (Docker y el firewall del host, no asumir).

## 9. Dominio / DNS / HTTPS / reverse proxy

**NO EJECUTADO** — no hay dominio ni acceso DNS. Plan registrado: registrar A/AAAA hacia la IP pública, verificar con `dig`/`nslookup`, HTTPS con **Nginx + Certbot** (certificado Let's Encrypt válido), redirect HTTP→HTTPS, y `https://DOMINIO/health` + `/ready` sin advertencia. Reverse proxy con `Host`, `X-Forwarded-For`, `X-Forwarded-Proto`, `X-Request-ID`; no exponer `:8000` ni `:8088` a la WAN.

## 10. API Web

**NO EJECUTADO** desde Internet (no hay red pública). El comportamiento a verificar ya está cubierto por smoke test HTTP **22/22 PASS** y prod gates **5/5 PASS** (Fase 10.1, contra PostgreSQL real en localhost); falta re-ejecutar el mismo flujo atravessando Internet→HTTPS→proxy→FastAPI: auth, `/me`, dashboard, un router, 401 sin token, logout, revocación, IDOR. CORS queda configurado con el origin real del dominio (nunca `*`).

## 11. Telegram / WhatsApp reales

- **Telegram: NO EJECUTADO** (no hay credenciales de producción ni VPS). Plan: arrancar el bot en el VPS, enviar mensaje real, verificar respuesta, identidad, confirmación y creación+entrega de recordatorio.
- **WhatsApp: NO EJECUTADO** (no hay credenciales de Meta/WhatsApp de producción ni dominio). Plan: configurar webhook `https://DOMINIO/...`, validar GET (verify token + challenge), POST (HMAC), y una prueba real de mensaje → webhook → identidad → orchestrator → tool → respuesta.

## 12. Poller / persistencia / restart / reboot

Todo **NO EJECUTADO** en infraestructura real. El diseño ya está probado en repo (claim/lease PostgreSQL de recordatorios y proactividad, tests de concurrencia y de persistencia entre reconexiones en `tests/integration/test_production.py`). Falta la evidencia en VPS: restart de app, restart de PostgreSQL con datos de prueba creados antes, reboot controlado del VPS (solo si hay vía de recuperación SSH), y comprobar leases/límite diario.

## 13. Backups

- **NO EJECUTADO** en VPS. Los scripts ya están probados en local con PG 16.12 real (backup→restore→verify OK, versión `0015`). Cuando exista VPS: ejecutar `scripts/backup.sh`, `scripts/restore.sh`, `scripts/verify_backup.sh` contra la BD real (restore SIEMPRE a scratch `BACKUP_TEST_DBNAME`, nunca encima de producción) y automatizar con systemd timer o cron (frecuencia/retención/logs).
- **Limitación documentada:** un backup solo en el disco del propio VPS no constituye recuperación completa si se pierde el VPS; el almacenamiento externo (si se decide) requiere autorización expresa.

## 14. Logs / log rotation / observabilidad / disco

**NO EJECUTADO** en VPS. Política registrada: rotación Docker/journald/proxy, `df -h` y `docker system df` antes/después del despliegue, y que los logs JSON identifican servicio, timestamp, `X-Request-ID`, error y componente sin secretos (redacción automática ya implementada y testeada).

## 15. Actualización / rollback

Procedimiento documentado (pendiente de ejecutar en VPS):

```
backup → pull/scp nueva versión → docker compose up -d --build
  (migrate aplica automáticamente al head) → health → /ready
```

Rollback: restaurar la versión anterior (imagen/tag o fuente) y, si hubo migración, **no usar downgrade de producción** (un downgrade de una migración irreversible puede destruir datos); se restaura desde backup.

## 16. Checklist de producción (§37)

```text
[ ] VPS preparado             -> NO EJECUTADO (no existe VPS)
[ ] Docker instalado          -> NO EJECUTADO (no disponible; sin VPS)
[ ] Compose operativo         -> NO EJECUTADO (idem)
[ ] Firewall configurado      -> NO EJECUTADO (idem)
[ ] PostgreSQL privado        -> NO EJECUTADO (idem); preparado en compose (Fase 10.1)
[ ] Persistencia verificada   -> NO EJECUTADO (idem); probada en repo (PG real, Fase 10.1)
[ ] .env productivo           -> NO EJECUTADO (no existe producción; repo sin .env)
[ ] Secrets protegidos        -> NO EJECUTADO (se aplicará chmod 600 + informe "CONFIGURADO")
[ ] Alembic 0015              -> NO EJECUTADO en VPS; head único `0015_fase10_web_auth` verificado en repo
[ ] App healthy               -> NO EJECUTADO
[ ] API healthy               -> NO EJECUTADO en Internet; smoke local 22/22 y prod gates 5/5 PASS
[ ] HTTPS válido              -> NO EJECUTADO (sin dominio)
[ ] Dominio resuelve          -> NO EJECUTADO (sin dominio/DNS)
[ ] Telegram real             -> NO EJECUTADO (sin credenciales de producción)
[ ] WhatsApp real             -> NO EJECUTADO (sin credenciales Meta/WhatsApp de producción)
[ ] Poller real               -> NO EJECUTADO; claim/lease probado en repo
[ ] Restart app               -> NO EJECUTADO
[ ] Restart PostgreSQL        -> NO EJECUTADO
[ ] Reboot VPS                -> NO EJECUTADO (sin VPS)
[ ] Backup                    -> NO EJECUTADO en VPS; scripts probados en local con PG real
[ ] Restore                   -> NO EJECUTADO en VPS; idem
[ ] Backup periódico          -> NO EJECUTADO (systemd timer/cron pendiente de configurar)
[ ] Logs                      -> NO EJECUTADO en VPS (rotación JSON ya implementada y testada)
[ ] Log rotation              -> NO EJECUTADO
[ ] Recovery                  -> NO EJECUTADO
```

## 17. Gates obligatorios (§40) — resultados reales

| Gate | Resultado |
|---|---|
| `VPS operativo` | **NO EJECUTADO** — no existe VPS |
| `docker version` / `docker compose version` | **NO EJECUTADO** — Docker engine no disponible |
| `docker compose build` | **NO EJECUTADO** — pendiente desde Fase 10.1 |
| `docker compose up -d` / `ps` | **NO EJECUTADO** |
| `DNS real` / `HTTPS real` / `certificado válido` | **NO EJECUTADO** — sin dominio ni DNS |
| `puertos externos verificados` / `PostgreSQL NO público` | **NO EJECUTADO** — sin VPS |
| `/health` `/ready` externos | **NO EJECUTADO** — local/proxy smoke PASS (Fase 10.1): `/health` 200, `/ready` 200 con BD+web_auth |
| `API smoke real externo` / `auth` / `logout` / `IDOR` | **NO EJECUTADO** — cubierto en localhost por smoke 22/22 (auth, logout, revocación, IDOR) |
| `Telegram real` | **NO EJECUTADO** — sin credenciales de producción |
| `WhatsApp webhook real` | **NO EJECUTADO** — sin credenciales Meta/WhatsApp |
| `restart app` / `restart PostgreSQL` / `reboot VPS` | **NO EJECUTADO** |
| `datos conservados` | **NO EJECUTADO** — persistencia entre reconexiones probada en repo (PG real) |
| `recordatorio/proactividad real` / `claim/lease` / `recovery` | **NO EJECUTADO** — leases probados en repo |
| `backup real` / `restore real` / `verify real` | **NO EJECUTADO en VPS** — scripts probados en local con PG 16.12 (backup→restore→verify OK, versión `0015`) |
| Código: `pytest tests -q` | **1076 passed, 0 failed, 0 skipped** (sin cambios de código en esta fase) |
| Código: `ruff check src tests` | **PASS** (0 errores) |
| Código: `ruff format --check` | **PASS** (252 files) |
| Código: `mypy src/asistente` | **PASS** (0 errores, 140 files) |
| Código: `alembic heads` | head único **`0015_fase10_web_auth`** |

## 18. Limitaciones (§42) — qué falta y por qué

| Elemento | Qué falta | Por qué | Cómo se verificará posteriormente |
|---|---|---|---|
| VPS | IP pública + usuario SSH + acceso | No existe infraestructura contratada | El operador aporta VPS; ejecutar §4-§8 (auditoría SO, SSH, firewall, Docker) con salidas reales |
| Dominio/DNS | Dominio + acceso al registro A/AAAA | No existe dominio | Registrar registro hacia la IP del VPS, `dig`/`nslookup`, HTTPS con nginx+Certbot |
| Docker real | Docker Engine (build/start) | No hay Docker en ninguna máquina disponible; blocker arrastrado de Fase 10.1 | `docker compose build` + `up -d` + `ps` en el VPS |
| Telegram producción | Token del bot real | No hay credenciales de producción | Configurar `.env` del VPS y prueba real E2E (envío→respuesta→confirmación→recordatorio→poller) |
| WhatsApp producción | Verify/access/app_secret + número de Meta | No hay credenciales Meta/WhatsApp de producción | Configurar webhook sobre el dominio y prueba real GET/POST |
| OpenAI producción | API key válida de producción | No hay credenciales de producción | Configurar key en `.env` del VPS; el bot falla rápido si falta (`validate_critical_env`) |
| Backups automatizados | systemd timer/cron en el VPS + almacenamiento externo (si aplica) | Sin VPS; el almacenamiento externo requiere autorización | Configurar en el VPS; documentar explícitamente si el backup queda solo en el disco del VPS |

## 19. Comandos de operación (referencia para cuando exista el deploy — §35)

Sin secretos; se instalarán en `OPERATIONS.md` con los valores reales al desplegar.

```text
# estado / logs / restart / actualizar
docker compose ps
docker compose logs -f bot          # u `api`, `db`
docker compose restart bot api
docker compose up -d --build        # build+restart; migrate aplica al head
# health (urls reales tras dominio)
curl -s https://DOMINIO/health
curl -s https://DOMINIO/ready
# backup / restore (SIEMPRE a BD scratch salvo RESTORE_TARGET_DB explícito)
./scripts/backup.sh
./scripts/verify_backup.sh backups/<fichero>.sql.gz
RESTORE_TARGET_DB=<prod> ./scripts/restore.sh backups/<fichero>.sql.gz
# SSH (procedimiento general, no secretos)
ssh <usuario>@<IP-DEL-VPS>
```

## 20. Conclusión

La Fase 10.2 exige evidencia en infraestructura real que **no existe en este momento** (VPS, dominio/DNS, credenciales de canales de producción, Docker engine). El proyecto aporta toda la infraestructura de despliegue lista y probada en local (compose, Dockerfile no-root, scripts de backup/restore/verify con PG real, health/ready, fail-fast de config, y los 5 gates de código verdes). Siguiendo el criterio de cierre (§41) y la regla de no inventar resultados (§42), **la fase no puede declararse completada hoy**: se documenta el estado, el checklist y el plan exacto de verificación, y queda **pendiente de ejecutar cuando el operador disponga de VPS, dominio y credenciales de producción**.

No se han añadido funcionalidades, no se ha creado ninguna migración y no se han modificado los artefactos de Fase 10.1. No se ha empezado la Fase 10.3.

FASE 10.2: NO COMPLETADA