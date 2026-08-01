#!/usr/bin/env bash
# Creates the Postgres database and applies db/schema.sql.
#
#   ./db/setup.sh            # create 'scai' if missing, then apply the schema
#   DB_NAME=scai_dev ./db/setup.sh
#
# Safe to re-run: the database is only created when absent, and schema.sql is written with
# CREATE TABLE IF NOT EXISTS throughout, so an existing database keeps its data.
# To start over from scratch instead:  dropdb scai && ./db/setup.sh

set -euo pipefail

DB_NAME="${DB_NAME:-scai}"
SCHEMA_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/schema.sql"

echo "==> Checking Postgres is running"
if ! pg_isready -q; then
  echo "Postgres is not accepting connections."
  echo "Start it with:  brew services start postgresql@15"
  exit 1
fi

echo "==> Checking the pgvector extension is installed"
# pgvector must be built against the SAME major version Postgres is running. Homebrew's bottle
# currently targets pg17/18 only, so on pg15 it has to be compiled from source.
if ! psql -d postgres -tAc "SELECT 1 FROM pg_available_extensions WHERE name='vector'" | grep -q 1; then
  PG_CONFIG="$(command -v pg_config || true)"
  echo "pgvector is not available to this Postgres server."
  echo
  echo "Install it, matching your server version:"
  echo "  brew install pgvector                 # works if your Postgres is 17 or 18"
  echo
  echo "For Postgres 15/16, build from source instead:"
  echo "  git clone --depth 1 --branch v0.8.0 https://github.com/pgvector/pgvector.git /tmp/pgvector"
  echo "  cd /tmp/pgvector"
  echo "  make        PG_CONFIG=${PG_CONFIG:-/opt/homebrew/opt/postgresql@15/bin/pg_config}"
  echo "  make install PG_CONFIG=${PG_CONFIG:-/opt/homebrew/opt/postgresql@15/bin/pg_config}"
  exit 1
fi

if psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
  echo "==> Database '${DB_NAME}' already exists — leaving its data alone"
else
  echo "==> Creating database '${DB_NAME}'"
  createdb "${DB_NAME}"
fi

echo "==> Applying ${SCHEMA_FILE}"
psql -v ON_ERROR_STOP=1 -q -d "${DB_NAME}" -f "${SCHEMA_FILE}"

echo "==> Done. Tables in '${DB_NAME}':"
psql -d "${DB_NAME}" -c '\dt'

cat <<EOF

Next:
  1. Point the app at it if you used a custom name — set  db.dsn  in config.yaml
     (or export SCAI_DB_DSN=postgresql:///${DB_NAME})
  2. Populate the knowledge base:
     python -m scai.ingest --manifest knowledge_base/sources.yaml
EOF
