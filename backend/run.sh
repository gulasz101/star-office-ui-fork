#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Auto-load project env file when present.
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

# uv is the single dependency source (SOU-170): no hand-made virtualenv
# interpreter exec any more. uv discovers the project at $ROOT_DIR and
# creates/prunes .venv itself; --project pins the discovery so the script
# keeps working when invoked from anywhere.
exec uv run --project "$ROOT_DIR" python "$ROOT_DIR/backend/app.py"
