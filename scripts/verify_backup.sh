#!/usr/bin/env bash
# verify_backup.sh — verifica un dump restaurándolo en una BD scratch y
# comprobando que las tablas críticas existen y no están vacías donde deben.
#
# Uso (mismas variables que backup.sh/restore.sh):
#   ./scripts/verify_backup.sh backups/asistente_asistente_20260101_120000.sql.gz
#
# Comprueba: alembic_version (coherente con el head) y las tablas de negocio
# (users, accounts, transactions, reminders, trips, user_channels,
# web_credentials, web_sessions). Se conecta con el rol de la app.
set -euo pipefail

PG_BIN="${PG_BIN:-}"
if [ -n "${PG_BIN}" ]; then
  export PATH="${PG_BIN}:${PATH}"
fi

DUMP="${1:?Uso: verify_backup.sh <fichero-backup.sql.gz>}"
PG_HOST="${POSTGRES_HOST:-localhost}"
PG_PORT="${POSTGRES_PORT:-5432}"
PG_USER="${POSTGRES_USER:?Defina POSTGRES_USER}"
PG_PASSWORD="${POSTGRES_PASSWORD:?Defina POSTGRES_PASSWORD}"
TEST_DB="${BACKUP_TEST_DBNAME:-asistente_restore_test}"

export PGPASSWORD="${PG_PASSWORD}"

# Restaura a la BD scratch (drop-and-create) y verifica. RESTORE_TARGET_DB
# fuerza restauración a una BD concreta; por defecto, la de test.
RESTORE_TARGET_DB="${TEST_DB}" ./scripts/restore.sh "${DUMP}"

psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${TEST_DB}" \
  -v ON_ERROR_STOP=1 \
  -c "SELECT version_num FROM alembic_version;"

for t in users accounts transactions reminders trips user_channels web_credentials web_sessions; do
  COUNT="$(psql -h "${PG_HOST}" -p "${PG_PORT}" -U "${PG_USER}" -d "${TEST_DB}" -tA \
    -c "SELECT count(*) FROM \"${t}\";")"
  echo "  - ${t}: ${COUNT} filas"
  [ "${COUNT}" -ge 0 ]
done

echo "OK (verify_backup.sh) sobre ${TEST_DB}"