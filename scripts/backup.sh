#!/usr/bin/env bash
# backup.sh — dump lógico de PostgreSQL con copia configurable y retención.
#
# Uso:
#   POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
#   POSTGRES_USER=asistente POSTGRES_PASSWORD=... POSTGRES_DB=asistente \
#   BACKUP_DIR=./backups BACKUP_KEEP_DAYS=14 ./scripts/backup.sh
#
# Compatible con el stack docker-compose (host `db`) y con una instancia local.
# Requiere psql/pg_dump en el PATH (en el PC local, los binarios suelen estar
# en C:\Program Files\PostgreSQL\16\bin o similar).
set -euo pipefail

# PG_BIN: ruta de los binarios postgres (solo Windows). En servidores Linux
# con postgresql-client en el PATH no hace falta definirlo.
PG_BIN="${PG_BIN:-}"
if [ -n "${PG_BIN}" ]; then
  export PATH="${PG_BIN}:${PATH}"
fi

PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:?Defina POSTGRES_USER}"
PG_PASSWORD="${POSTGRES_PASSWORD:?Defina POSTGRES_PASSWORD}"
PG_DB="${POSTGRES_DB:?Defina POSTGRES_DB}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

export PGPASSWORD="${PG_PASSWORD}"
mkdir -p "${BACKUP_DIR}"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="${BACKUP_DIR}/asistente_${PG_DB}_${STAMP}.sql.gz"

# -Fc (formato binario comprimido) es el recomendado por PostgreSQL para
# pg_restore: permite restaurar parcialmente y respeta orden de dependencias.
pg_dump --no-owner --no-privileges -Fc \
  -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${PG_DB}" \
  | gzip > "${OUT}"

LATEST="${BACKUP_DIR}/asistente_${PG_DB}_latest.sql.gz"
cp -f "${OUT}" "${LATEST}"
echo "Backup creado: ${OUT}"

# Retención: elimina copias más antiguas que BACKUP_KEEP_DAYS (nunca la latest).
find "${BACKUP_DIR}" -name "asistente_${PG_DB}_*.sql.gz" -mtime "+${BACKUP_KEEP_DAYS}" -delete

echo "OK (backup.sh)"