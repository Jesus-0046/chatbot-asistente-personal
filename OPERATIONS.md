# OPERACIONES 24/7 (Fase 10.1)

Runbook para el día a día: monitorización, recuperación de los procesos y casos
típicos. Lectura complementaria: `DEPLOYMENT.md` (despliegue) y
`BACKUP_RESTORE.md` (copias de seguridad).

## 1. Estado del sistema

```bash
docker compose ps                 # db/bot/api: healthy; migrate: Exit 0
docker compose top                # procesos vivos por contenedor
curl -s localhost:8000/health     # siempre {"status":"ok",...} si el contenedor api responde
curl -s localhost:8000/ready      # 200 solo si BD y web_auth responden; 503 si alguno falta
```

Significado:
- `/health` (liveness): el proceso responde; no depende de BD/OpenAI.
- `/ready` (readiness): comprueba la BD (`db.ping()`) y que la capa `web_auth`
  está cableada. Es el que debe decidir si el pod/contenedor recibe tráfico.

Si `docker compose ps` muestra `Unhealthy` durante más de unos minutos o
`Restarting` en bucle, ver §3.

## 2. Intel de los logs (structlog JSON)

```bash
docker compose logs -f bot
docker compose logs -f api
docker compose logs -f db
```

La app redacta automáticamente los secretos en los logs (claves
token/password/secret/key/authorization), así que es seguro volcarlos a tu
agregador.

Eventos habituales en `bot`:
- `api_configuration_error` (bot y api): faltan variables obligatorias; el
  proceso **no arranca** hasta corregir `.env`.
- Telegram `... dispute conflict ...` / floods: temporales, con retry del SDK.
- WhatsApp `...` con `X-Hub-Signature-256` invalid → webhook no verificado
  (endpoint configurado mal en Meta).
- Recordatorios: el poller salta cada `REMINDER_POLLER_INTERVAL_SECONDS`;
  si se ve "leashed/claim" en rojo prolongado, revisar §3.

## 3. Recuperación / restarts

Todo los servicios tienen `restart: unless-stopped`, así que un crash se
reintenta automáticamente. Si un contenedor entra en bucle de reinicio:

```bash
docker compose logs --tail=200 bot   # diagnóstico
# espera del poller: los claims de recordatorios/proactividad marcan
# `processing`; los leases (reminder_claim_lease_seconds / proactive…) se
# reclaman por otros workers/arranques, así que un reinicio NO pierde avisos.
docker compose restart bot
docker compose restart api
# caída total:
docker compose up -d                # re-crea los servicios
```

¿Pérdida de datos? Solo si falla `db` con pérdida del volumen `pgdata`; por eso
hay backups (§BACKUP_RESTORE.md) y el volumen NO se deja montado a un directorio
ajeno. El reinicio de la BD por sí solo no pierde datos (el dato está en el
volumen `pgdata`).

## 4. Reinicio de la base de datos

```bash
docker compose stop db
docker compose up -d db             # mismo volumen pgdata → misma data
docker compose ps db                # espera a healthy
# bot/api siguen funcionando con sus pools (pool_pre_ping=True).
```

Si el host se queda sin disco (`df -h`): Postgres entra en estado de solo
lectura o falla; tras liberar espacio/ampliar, `docker compose restart db`.

## 5. Rate limiting y límites del proceso

- El rate-limit de `/api/v1/auth/*` es **en memoria del proceso API** (un solo
  contenedor): un reinicio de `api` lo resetea. No mueve a Redis/Celery en esta
  fase (decisión documentada) porque el despliegue es un único proceso.
- El límite diario de proactividad es **por usuario y día local en PostgreSQL**
  (atómico, `proactive_daily_counters`), trasciende reinicios.

## 6. Monitorización mínima

Sin infraestructura externa es suficiente con:

- Un cron/systemd timer cada 5 min que haga `curl -fsS localhost:8000/ready` y
  alerte si falla (p. ej. `curl` + `mailx`).
- La programación de **backups diarios** (BACKUP_RESTORE.md §4).
- `docker compose logs` a un archivo rotado (`logrotate`).

## 7. Diag de casos típicos

| Síntoma | Causa probable | Acción |
|---|---|---|
| `bot`/`api` `Exit 1` con `api_configuration_error` en logs | falta una variable obligatoria | completar `.env` y `docker compose up -d` |
| `/ready` 503 "Base de datos no disponible" | DB caída/reinicio | `docker compose ps db`; §4 |
| `/ready` 503 "Autenticación Web no disponible" | `services.web_auth` sin cablear | revisar deploy de `api` (build real, no `--no-...`) |
| Meta rechaza el webhook al guardar | verify token / ruta mal | verificar `WHATSAPP_VERIFY_TOKEN` y la ruta configurada en Meta |
| cuadro "latest" de Telegram sin responder | flood/timeouts del LLM | comprobar `OPENAI_MAX_RETRIES` y logs de OpenAI |
| El bot no arranca (salida silenciosa) | validación startup | ver logs; el proceso sale rápido con el mensaje de config |