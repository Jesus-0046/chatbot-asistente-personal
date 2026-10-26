# BACKUP / RESTAURACIÓN (Fase 10.1)

Copia y recuperación del PostgreSQL de `asistente` (volumen `pgdata`).

## 1. Qué incluye un backup

Un dump lógico `pg_dump -Fc` contiene **todo**: esquema, datos y versión de
Alembic (`alembic_version`). Tablas que importan para la continuidad:

- negocio: `users`, `accounts`, `transactions`, `reminders`, `trips`, etc.
- canales/identidad: `user_channels`, `processed_inbound_events`.
- Web API: `web_credentials`, `web_sessions`, `web_binding_codes`.
- sistema: `alembic_version` (head `0015_fase10_web_auth`).

## 2. Scripts (carpeta `scripts/`)

| Script | Qué hace |
|---|---|
| `backup.sh` | `pg_dump -Fc` → `.sql.gz` con fecha, copia `_latest`, purga copias > `BACKUP_KEEP_DAYS` |
| `restore.sh` | drop-and-create de una BD destino y `pg_restore`. Por defecto restaura a una BD **scratch** (`BACKUP_TEST_DBNAME`) |
| `verify_backup.sh` | restaura a scratch y comprueba `alembic_version` + filas de las tablas críticas |

Variables comunes (mismas del `.env`):

```
POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
BACKUP_DIR         (def. ./backups)
BACKUP_KEEP_DAYS   (def. 14)
BACKUP_TEST_DBNAME (def. asistente_restore_test)
PG_BIN             (opcional: ruta de los binarios postgres; en Linux basta con tener psql/pg_dump/pg_restore en el PATH)
```

Los tres se ejecutan con Git Bash en Windows sin tocar código (buscan los
binarios con `PG_BIN`). En el servidor: `chmod +x scripts/*.sh`.

## 3. Backup manual

```bash
# servidor (en el directorio del proyecto, con .env presente):
./scripts/backup.sh
# Windows (Git Bash):
PG_BIN=/c/Users/jesus/pgbin1612/pgsql/bin POSTGRES_USER=postgres POSTGRES_PASSWORD=... ./scripts/backup.sh
```

Genera: `backups/asistente_<BD>_<stamp>.sql.gz` y refresca la copia `_latest`.

## 4. Backup programado (recomendado)

Con systemd (servidor Linux):

```
# /etc/systemd/system/asistente-backup.service
[Unit]
Description=Backup PostgreSQL asistente
[Service]
Type=oneshot
WorkingDirectory=/srv/asistente
EnvironmentFile=/srv/asistente/.env
ExecStart=/srv/asistente/scripts/backup.sh
```

```
# /etc/systemd/system/asistente-backup.timer
[Unit]
Description=Backup diario asistente (02:30)
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
```

```bash
systemctl enable --now asistente-backup.timer
systemctl list-timers asistente-backup
```

Alternativa crontab: `20 2 * * * cd /srv/asistente && ./scripts/backup.sh`.
La retención (`BACKUP_KEEP_DAYS`) purga los antiguos automáticamente.

**Regla de oro**: los backups se guardan en otro disco y, si es crítico, se
sincronizan fuera del servidor (rclone/rsync) — nunca solo en el mismo volumen
`pgdata`. Un backup no restaurado alguna vez no es un backup (§6).

## 5. Restauración (pruebas / real)

Prueba sin tocar producción (restaurar a BD scratch):

```bash
./scripts/restore.sh backups/asistente_asistente_20260923_185302.sql.gz
# → restaura a BACKUP_TEST_DBNAME (asistente_restore_test)
./scripts/verify_backup.sh backups/asistente_asistente_20260923_185302.sql.gz
# → vuelve a restaurar y comprueba versión + tablas críticas
```

Restauración real (sobreescribe produccion):

```bash
RESTORE_TARGET_DB=asistente ./scripts/restore.sh backups/asistente_asistente_20260923_185302.sql.gz
```

Consideraciones:

- `restore.sh` hace `DROP DATABASE` + `CREATE DATABASE` (vacía); es una
  sobreescritura total, no un merge.
- `--no-owner`/`--no-privileges`: los objetos quedan propiedad del rol que
  restaura (operativo); luego reaplica los GRANT si usas roles separados.
- Tras restaurar a producción, reinicia `bot` y `api` para que refresquen los
  pools (`docker compose restart bot api`).

## 6. Verificación de un backup (política)

Cada backup DESTINADO a restaurarse debe validarse antes:

1. `./scripts/verify_backup.sh <fichero>` → restaura y comprueba:
   - `version_num` coincide con el head (`0015_fase10_web_auth`, o el que toque
     en tu deploy).
   - Las tablas críticas existen y `count(*)` es plausible (>= 0; revisa que
     tus volúmenes de datos reales cuadren, p. ej. usuarios/recordatorios).
2. Prueba a leer un dato de negocio en la BD scratch (spot-check).
3. Deja `BACKUP_KEEP_DAYS >= 14` como mínimo; nunca confíes en una sola copia.

## 7. Qué NO se respalda y por qué

- Los **bytes de imágenes** no se persisten en BD (solo análisis estructurado),
  así que no hay que recuperarlos.
- Las credenciales viven en `.env` (cópialo aparte, junto a los backups, si se
  restaura en un servidor nuevo).
- El rate-limit por IP de la API es in-memory (proceso) y no se respalda.

## 8. Restaurar en un servidor nuevo (drill completo)

1. Instala Docker y `git clone` el repo; `cp .env.example .env` y rellena
   secretos (producción).
2. Sube un backup: `docker compose up -d db` (o arranca el stack completo).
3. Copia el `.sql.gz` al host y restaura:

   ```bash
   PGPASSWORD=... docker compose exec -T db pg_restore --no-owner -U asistente -d asistente < backup.sql.gz
   ```

   (con la BD ya creada y migraciones aplicadas, o bien restaura primero y
   luego `migrate` hará no-op porque `alembic_version` ya está en head).
4. `./scripts/verify_backup.sh <fichero>` y arranca el resto del stack.