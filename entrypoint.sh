#!/bin/sh
# Star Office UI container start-up (SOU-62, Phase 5).
#
# One job: bring the schema to head, then hand PID 1 over to gunicorn.
#
# Alembic owns the schema and backend/migrations/env.py owns the backend
# choice — DATABASE_URL first (Postgres in the homelab, ADR-034), the
# alembic.ini SQLite fallback second, which creates backend/sou.db on a fresh
# install. So this script deliberately never reads, writes or builds a
# connection string of its own; one rule, one place.
set -eu

# /app in the image; the directory this script sits in anywhere else, so the
# same file runs under docker and under
# backend/tests/test_container_entrypoint.py unchanged.
ROOT_DIR="${SOU_ROOT:-$(cd "$(dirname "$0")" && pwd)}"
ALEMBIC_INI="$ROOT_DIR/backend/alembic.ini"

# Name the backend without ever echoing the URL, which may carry credentials.
case "${DATABASE_URL:-}" in
  "")    echo "[entrypoint] database: SQLite fallback (backend/sou.db)" ;;
  postgres*) echo "[entrypoint] database: Postgres (DATABASE_URL)" ;;
  sqlite*)   echo "[entrypoint] database: SQLite (DATABASE_URL)" ;;
  *)            echo "[entrypoint] database: custom URL (DATABASE_URL)" ;;
esac

# set -e makes this the hard gate: a server on a half-migrated schema is worse
# than no server at all, so a failed migration aborts the start-up here.
echo "[entrypoint] alembic upgrade head"
alembic -c "$ALEMBIC_INI" upgrade head

# Debugging escape hatch: `docker run IMAGE sh` must still work.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

cd "$ROOT_DIR"

# One gthread worker on purpose (the SOU-12 ruling): the app keeps in-process
# state and background tasks, and multiple workers would each hold a divergent
# copy. The port follows STAR_BACKEND_PORT, defaulting to 19000.
echo "[entrypoint] starting gunicorn on 0.0.0.0:${STAR_BACKEND_PORT:-19000}"
exec gunicorn --chdir backend --bind "0.0.0.0:${STAR_BACKEND_PORT:-19000}" \
  --worker-class gthread --workers 1 --threads 8 \
  --timeout 120 --graceful-timeout 30 \
  --access-logfile - --error-logfile - \
  app:app
