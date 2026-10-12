#!/usr/bin/env bash
# Spin up a throwaway PostgreSQL 16 for the SOU migrations (SOU-17 / ADR-034).
#
#   scripts/dev_postgres.sh start      # -> prints the DATABASE_URL to use
#   scripts/dev_postgres.sh stop
#   scripts/dev_postgres.sh status
#
# Two paths to the same server, because CI runners and laptops differ:
#   1. Docker (postgres:16), when the docker CLI is available.
#   2. Otherwise the PostgreSQL 16 distribution shipped by the `pgserver` wheel,
#      started straight from .tooling/ — no daemon, no root, no image pull.
#      Install it once with:
#        python3 .tooling/pip.pyz install --target .tooling/site pgserver
#
# Data lives in .tooling/pgdata (gitignored). Port 55432 avoids a local 5432.
set -euo pipefail

PORT="${SOU_PG_PORT:-55432}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGDATA="$ROOT_DIR/.tooling/pgdata"
PGLOG="$ROOT_DIR/.tooling/pgdata.log"
PGBIN="$ROOT_DIR/.tooling/site/pgserver/pginstall/bin"
DB="sou"
CONTAINER="sou-dev-pg"

url() {
  echo "postgresql+psycopg2://postgres@127.0.0.1:${PORT}/${DB}"
}

status() {
  if command -v docker >/dev/null 2>&1 && docker ps -q -f "name=^${CONTAINER}$" | grep -q .; then
    echo "running: docker container ${CONTAINER} on ${PORT}"
    return 0
  fi
  if [[ -x "$PGBIN/pg_isready" ]] && "$PGBIN/pg_isready" -h 127.0.0.1 -p "$PORT" -q; then
    echo "running: embedded PostgreSQL on ${PORT}"
    return 0
  fi
  echo "not running"
  return 1
}

start_docker() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$CONTAINER" \
    -e POSTGRES_PASSWORD=devonly \
    -e POSTGRES_DB="$DB" \
    -p "${PORT}:5432" postgres:16 >/dev/null
  until docker exec "$CONTAINER" pg_isready -U postgres -q; do sleep 1; done
  # The psycopg2 URL above is for the embedded server; over Docker the password
  # is part of the DSN.
  echo "postgresql+psycopg2://postgres:devonly@127.0.0.1:${PORT}/${DB}"
}

start_embedded() {
  if [[ ! -x "$PGBIN/pg_ctl" ]]; then
    echo "no PostgreSQL binaries in $PGBIN" >&2
    echo "install them: python3 .tooling/pip.pyz install --target .tooling/site pgserver" >&2
    return 1
  fi
  export LD_LIBRARY_PATH="$PGBIN/../lib"
  if [[ ! -f "$PGDATA/PG_VERSION" ]]; then
    "$PGBIN/initdb" -D "$PGDATA" -U postgres --auth=trust -E utf8 >/dev/null
  fi
  "$PGBIN/pg_ctl" -D "$PGDATA" -l "$PGLOG" -w \
    -o "-h 127.0.0.1 -p ${PORT} -c dynamic_shared_memory_type=posix" start >/dev/null
  "$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U postgres "$DB" 2>/dev/null || true
  "$PGBIN/psql" -h 127.0.0.1 -p "$PORT" -U postgres -d "$DB" -Atc 'select version()'
  url
}

stop() {
  if command -v docker >/dev/null 2>&1 && docker ps -q -f "name=^${CONTAINER}$" | grep -q .; then
    docker rm -f "$CONTAINER" >/dev/null && echo "stopped ${CONTAINER}"
  fi
  if [[ -x "$PGBIN/pg_ctl" && -d "$PGDATA" ]]; then
    LD_LIBRARY_PATH="$PGBIN/../lib" "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop >/dev/null 2>&1 \
      && echo "stopped embedded PostgreSQL" || true
  fi
}

case "${1:-start}" in
  start)
    if command -v docker >/dev/null 2>&1; then start_docker; else start_embedded; fi
    ;;
  stop) stop ;;
  status) status ;;
  url) url ;;
  *) echo "usage: $0 {start|stop|status|url}" >&2; exit 2 ;;
esac
