# DESPLIEGUE PRODUCTIVO 24/7 (Fase 10.1)

Guía para llevar `asistente` a un servidor (VPS/cloud) haciendo que Telegram,
WhatsApp y la Web API funcionen 24/7 sin depender del PC de desarrollo.

Arquitectura objetivo (un único host, `docker compose`):

```
                          ┌──────────────────────────────────────────────┐
  Telegram (long-polling) │  contenedor `bot`  (Telegram + WhatsApp +    │
       ◄────────────────►  │    pollers con leases PostgreSQL, :8088)     │
  Meta WhatsApp (HTTPS) ►  ├──────────────────────────────────────────────┤
  web / móvil (HTTPS) ►    │  contenedor `api`  (FastAPI /api/v1, :8000)  │
                          ├──────────────────────────────────────────────┤
                          │  contenedor `db`  (PostgreSQL, volumen pgdata)│
                          └──────────────────────────────────────────────┘
```
Teléfono del VPS → API y webhook WhatsApp solo a través del reverse proxy con
HTTPS. Además de todo esto, la integración con el bot se hace con **long
polling** (no Webhook), por lo que Telegram no exige puerto alguno.

Tres procesos en la misma red Docker:

| Servicio | Imagen interna | Qué hace |
|---|---|---|
| `db` | `postgres:16-alpine` | Persistencia. Volumen `pgdata` (dato) |
| `migrate` | imagen de la app (`asistente`) | `alembic upgrade head` una vez, luego sale |
| `bot` | » | Telegram (polling) + WhatsApp webhook + pollers (recordatorios y proactividad) con leases PostgreSQL |
| `api` | » | FastAPI `/api/v1` (sesiones Web), escucha en `:8000` |

## 1. Requisitos del servidor

- Docker Engine + compose (plugin v2), p. ej. `docker compose version`.
- Un dominio y TLS (p. ej. Let's Encrypt) si se exponen WhatsApp/API (sección 4).
- 1 CPU / 512 MB–1 GB RAM bastan para el volumen habitual.
- Linux x86_64/arm64 (imagen `python:3.12-slim` y `postgres:16-alpine`).

## 2. Configuración del entorno (SECRETOS)

```bash
cp .env.example .env
```

Rellena **obligatoriamente** (sin estos, `bot` y `api` no arrancan; el arranque
falla rápido con un mensaje que lista las variables que faltan):

| Variable | Por qué |
|---|---|
| `TELEGRAM_TOKEN` | Bot de Telegram. La app falla rápido si falta |
| `OPENAI_API_KEY` | LLM del orquestador. Ídem |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Credenciales del entorno de arranque Postgres (host remoto). El compose lanza error si no están definidas: no hay password por defecto |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_APP_SECRET` | Solo si `WHATSAPP_ENABLED=true` |

Ajustes recomendados en el VPS:

- `APP_ENV=production`, `LOG_LEVEL=INFO` (o `WARNING`), `API_DOCS_ENABLED=false`
  (desactiva swagger en producción), `API_CORS_ORIGINS=["https://app.tudominio.es"]`.
- `WHATSAPP_WEBHOOK_HOST=0.0.0.0` y `API_HOST=0.0.0.0` para que el reverse proxy
  alcance a los contenedores por la red de compose.

`docker-compose.yml` sobreescribe `DATABASE_URL` apuntando al host `db` (nombre
de servicio compose), no a `localhost`; no lo toques.

**Nunca** se debe:
- comitear `.env` (está en `.gitignore`),
- usar los valores del `.env.example` como secretos,
- publicar `POSTGRES_PORT` (por defecto no se publica: `${POSTGRES_PORT:-0}:5432`).

## 3. Primer arranque

```bash
docker compose up -d --build
```

Orden garantizado por `depends_on`:
1. `db` espera `pg_isready` (healthcheck).
2. `migrate` aplica migraciones y termina correctamente.
3. `bot` y `api` arrancan cuando `migrate` completó con éxito.

Comprobaciones:

```bash
docker compose ps            # db healthy, bot healthy, api healthy, migrate exited 0
docker compose logs -f bot   # my working/…; structlog JSON
curl -s localhost:8000/health  # {"status":"ok",...}
curl -s localhost:8000/ready   # 200 cuando BD y web_auth responden
```

Recuerda: para Telegram **no** necesitas webhook (usa long-polling). Solo
necesitan URL pública/HTTPS el webhook de WhatsApp y la API (si quieres acceder
desde internet).

### Actualizaciones tras un cambio de código

```bash
git pull
docker compose up -d --build
```

Si el pull trae migraciones nuevas, `migrate` las aplica automáticamente en el
siguiente `up` (el servicio `bot`/`api` espera a que termine). El data del
volumen `pgdata` persiste entre despliegues.

## 4. Reverse proxy y HTTPS

Ejemplo mínimo con **nginx** (no incluido en compose a propósito; usa el que ya
tengas o Caddy/Cloudflare Tunnel).

```nginx
server {
  listen 443 ssl http2;
  server_name bot.midominio.es;

  # TLS: usa certbot o tu proveedor.

  location /whatsapp/ {
    proxy_pass http://127.0.0.1:8088/;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
  }

  location /api/ {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
  }
}
```

Detalles importantes:

- Pulso de la API y webhook de WhatsApp están firmados/validados por la app
  (`X-Hub-Signature-256` para Meta; sesiones Bearer para la API). No expongas
  el webhook de WhatsApp sin verificación o dejarás un endpoint abierto.
- `X-Forwarded-For` se respeta para el rate-limit por IP de `/api/v1/auth/*`
  y para el `X-Request-ID` (función de logs distribuidos).
- CORS: define `API_CORS_ORIGINS` con los orígenes reales (nunca `*`).
- En Meta Developer: URL del webhook = `https://bot.midominio.es/whatsapp/webhook`
  (ajústalo a la ruta real expuesta) y subscribe el mensaje "messages".

## 5. Puesta en marcha tras apagón / reinicio del servidor

```bash
systemctl enable --now docker   # o el equivalente de tu distro
# el daemon de compose reinicia los contenedores:
docker compose up -d
```

Con `restart: unless-stopped` en `db`, `bot` y `api`, un reinicio del servidor
deja el stack operativo sin intervención. La recuperación de crashes a mitad de
un envío la garantiza la arquitectura de leases de PostgreSQL (los pollers
reclaman `processing` vencido). Ver `OPERATIONS.md`.

## 6. Seguridad (checklist)

- [ ] `.env` nunca en git; secretos fuertes (32+ caracteres) para `POSTGRES_PASSWORD`.
- [ ] Postgres NO publicado al exterior.
- [ ] `API_DOCS_ENABLED=false` en producción.
- [ ] `API_CORS_ORIGINS` cerrado a orígenes reales.
- [ ] Webhook WhatsApp solo accesible por HTTPS; sesiones HTTP solo por HTTPS.
- [ ] Contenedores corren como usuario no-root (USER en el Dockerfile).
- [ ] Backups programados (BACKUP_RESTORE.md) y copia de seguridad del `.env`.
- [ ] Logs JSON sin secretos (redacción automática de token/password/secret/key).

## 7. Verificación del despliegue (checklist)

- [ ] `docker compose ps` → todos `healthy` / `(exit 0)` para `migrate`.
- [ ] `/health` responde 200 sin depender de BD/OpenAI.
- [ ] `/ready` responde 200 cuando la BD contesta y `web_auth` está cableado.
- [ ] Telegram envía recordatorios en hora (JobQueue del proceso `bot`).
- [ ] WhatsApp recibe y responde mensajes (si `WHATSAPP_ENABLED=true`).
- [ ] Un reinicio de `docker compose restart bot api` no pierde datos y los
      pollers recuperan claims vencidos (leases).