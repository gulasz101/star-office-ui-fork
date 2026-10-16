#!/usr/bin/env bash
# run.sh - SOU-124 S5: serve the real app, run the browser proof against it.
# Everything v2 lives under frontend/office_v2/**; the backend is started
# read-only on a throwaway SQLite DB (DATABASE_URL override, SOU-115 recipe).
#
# usage: bash tools/office_v2_proof/run.sh   (from anywhere; paths are absolute)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# worker-local python deps (flask, sqlalchemy, ...) live in .tooling/site
# (absolute path required - relative PYTHONPATH breaks backend subprocesses)
export PYTHONPATH="${REPO}/.tooling/site:${PYTHONPATH:-}"
OUT="${REPO}/tools/office_v2_proof/out"
PORT="${PORT:-$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')}"
DB="${HOME}/sou124-proof.db"

rm -f "$DB"
mkdir -p "$OUT"
cd "$REPO"

STAR_BACKEND_PORT="$PORT" DATABASE_URL="sqlite:///$DB" python3 backend/app.py > "${OUT}/server.log" 2>&1 &
SRV=$!
cleanup() {
  kill "$SRV" 2>/dev/null || true
  wait "$SRV" 2>/dev/null || true
}
trap cleanup EXIT

for i in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then break; fi
  sleep 0.5
  if ! kill -0 "$SRV" 2>/dev/null; then echo "server died; see ${OUT}/server.log"; tail -20 "${OUT}/server.log"; exit 1; fi
done
curl -fsS "http://127.0.0.1:${PORT}/health" > /dev/null || { echo "server not ready"; exit 1; }

echo "== proof on port ${PORT} =="
node "${REPO}/tools/office_v2_proof/proof.mjs" "$PORT" "$OUT"
