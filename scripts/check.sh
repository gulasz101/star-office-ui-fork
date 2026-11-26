#!/bin/sh
# The gate for every change: container contract, backend tests (SQLite), renderer tests,
# vendored reporter tests. Runs on any branch: the code factory runs it in each work
# order's worktree, and CI runs it on every pull request (.github/workflows/check.yml).
# The Postgres legs of the backend suite run only when TEST_POSTGRES_URL is set.
# Needs: python3, uv, node (>= 22). Usage: scripts/check.sh
set -eu
cd "$(dirname "$0")/.." || exit 1

# Same Python as the image (Dockerfile: python:3.13-slim). Without a pin uv takes the
# runner's newest Python, and pillow 10.4.0 has no wheel for 3.14 (CI red since 2026-11-22).
UV_PYTHON="${UV_PYTHON:-3.13}"
export UV_PYTHON

fail=0
step() {
  name=$1; shift
  echo "== $name"
  if "$@"; then
    echo "PASS $name"
  else
    echo "FAIL $name"
    fail=1
  fi
}

step container-contract python3 scripts/validate_dockerfile.py
step entrypoint-syntax sh -n entrypoint.sh
step uv-sync uv sync --frozen --quiet
step backend-tests uv run pytest backend/tests -q
# the shell expands the glob: older Node versions do not glob in `node --test`
step renderer-tests sh -c 'node --test frontend/v3/tests/*.test.mjs'
step reporter-tests sh -c 'cd reporter/openchamber-star-office-reporter && uv run --project ../.. npm test'

if [ "$fail" -ne 0 ]; then
  echo "check: FAILED"
  exit 1
fi
echo "check: OK"
