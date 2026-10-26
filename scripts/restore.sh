#!/usr/bin/env bash
# restore.sh — restaura un dump -Fc de backup.sh a una base PostgreSQL.
#
# Por defecto restaura a una BD SCRATCH (BACKUP_TEST_DBNAME) para verificar la
# copia SIN tocar la BD de producción. Para restaurar a producción:
#   RESTORE_TARGET_DB=asistente ./scripts/restore.sh <fichero.sql.gz>
#
# Uso:
#   POSTGRES_HOST=localhost POSTGRES_PORT=5432 \
#   POSTGRES_USER=asistente POSTGRES_PASSWORD=... POSTGRES_DB=asistente \
#   BACKUP_TEST_DBNAME=asistente_restore_test ./scripts/restore.sh backups/asistente_asistente_20260101_120000.sql.gz
set -euo pipefail

PG_BIN="${PG_BIN:-}"
if [ -n "${PG_BIN}" ]; then
  export PATH="${PG_BIN}:${PATH}"
fi

DUMP="${1:?Uso: restore.sh <fichero-backup.sql.gz>}"
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:?Defina POSTGRES_USER}"
PG_PASSWORD="${POSTGRES_PASSWORD:?Defina POSTGRES_PASSWORD}"
PG_DB="${POSTGRES_DB:?Defina POSTGRES_DB}"
TEST_DB="${BACKUP_TEST_DBNAME:-asistente_restore_test}"

TARGET="${RESTORE_TARGET_DB:-${TEST_DB}}"

export PGPASSWORD="${PG_PASSWORD}"
[ -f "${DUMP}" ] || { echo "ERROR: no existe ${DUMP}" >&2; exit 1; }

# Recrea la BD de destino (drop-and-create).
psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d postgres \
  -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"${TARGET}\";" \
  -c "CREATE DATABASE \"${TARGET}\";"

# Restaura. --no-owner: los objetos quedan propiedad del usuario que restaura;
# --no-privileges: coherente con el dump (--no-privileges en backup.sh).
gunzip -c "${DUMP}" | pg_restore --no-owner --exit-on-error \
  -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" \
  -d "${TARGET}"

echo "Restaurado a: ${TARGET}"
echo "OK (restore.sh)"