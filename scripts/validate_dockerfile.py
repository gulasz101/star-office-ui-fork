#!/usr/bin/env python3
"""Contract test for the Star Office UI container image.

Stdlib only, no Docker needed: this parses ``Dockerfile``, ``entrypoint.sh`` and
``.dockerignore`` and asserts the container contract agreed on **SOU-12** (the
first production image) and extended for **SOU-62 / Phase 5** (Alembic migration
on startup, multi-stage, healthcheck on the event feed).

Phase 5 contract, keyed to the acceptance criteria on SOU-62
  AC1  base image Python 3.10+ (pinned ``python:3.13-slim`` for ``int | None``
       syntax), deps installed from ``backend/requirements.txt``, backend +
       frontend copied, port 19000 / ``STAR_BACKEND_PORT``
  AC2  ``entrypoint.sh`` runs ``alembic upgrade head`` **before** the server,
       resolves the backend through ``DATABASE_URL`` (Postgres) with the SQLite
       fallback left to ``backend/migrations/env.py`` — never a hard-coded URL
  AC3  multi-stage: a deps stage that installs, a runtime stage that only copies
  AC4  ``HEALTHCHECK --interval=30s --retries=3`` probing ``/events/recent?limit=1``
  +    non-root final user and no baked secrets (the SOU-12 guarantees, kept)

Exit code 0 = contract satisfied, 1 = violation(s). Run from anywhere:

    python3 scripts/validate_dockerfile.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ENTRYPOINT = ROOT / "entrypoint.sh"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"

# AC1: "Base: Python 3.10+ (3.13 preferred, supports int | None syntax)".
BASE_IMAGE_RE = re.compile(r"^python:3\.(?P<minor>1[0-9])(?:\.\d+)?-slim$")
BASE_IMAGE_MIN_MINOR = 10
# The fork pins one base so the image and the CI smoke test can never drift.
PINNED_BASE = "python:3.13-slim"

APP_PORT = "19000"
HEALTH_PATH = "/events/recent?limit=1"

# AC3: the runtime stage may only copy what the server actually serves.
# Anything else (docs, tests, desktop shells, tooling) stays out of the image.
RUNTIME_COPY_ALLOWLIST = ("backend", "frontend", "assets", "entrypoint.sh", "LICENSE")

# Names that must never carry a literal value in the image. Deliberately
# broad: a false positive is safe here, a baked secret is not.
SECRET_NAME_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|_PASS\b|\bPASS\b|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)",
    re.IGNORECASE,
)
# Names that are known-baked-secret offenders even if assigned empty.
FORBIDDEN_SECRET_NAMES = {"FLASK_SECRET_KEY", "ASSET_DRAWER_PASS", "GEMINI_API_KEY"}

# Patterns .dockerignore must contain (substring match, comments stripped).
# Phase 5 adds the SQLite artifacts: backend/sou.db is runtime state now, and
# baking a developer's database into a public image would leak its contents.
REQUIRED_IGNORES = [
    ".git",
    ".env",
    ".venv",
    "__pycache__",
    "state.json",
    "agents-state.json",
    "join-keys.json",
    ".tooling",
    "*.db",
    "*.db-wal",
    "*.db-shm",
]

failures: list[str] = []
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)


def logical_lines(text: str) -> list[str]:
    """Join backslash continuations and drop comments/blank lines."""
    out: list[str] = []
    buf = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not buf and (not line or line.startswith("#")):
            continue
        buf = f"{buf} {line}".strip()
        if buf.endswith("\\"):
            buf = buf[:-1].rstrip()
            continue
        out.append(buf)
        buf = ""
    if buf:
        out.append(buf)
    return out


def stage_index(lines: list[str]) -> int:
    """Index of the last FROM line (the final build stage)."""
    idx = -1
    for i, line in enumerate(lines):
        if line.upper().startswith("FROM "):
            idx = i
    return idx


def image_of(from_line: str) -> str:
    """`FROM python:3.13-slim AS deps` -> `python:3.13-slim`."""
    body = from_line.split(None, 1)[1].strip()
    tokens = body.split()
    if len(tokens) >= 3 and tokens[1].upper() == "AS":
        return tokens[0]
    return tokens[0]


def copy_sources(line: str) -> list[str]:
    """Sources of a COPY/ADD instruction, flags stripped."""
    tokens = line.split()[1:]
    srcs = [t for t in tokens if not t.startswith("--") and not t.startswith('"')]
    # Drop the destination (last token) and any shell-form json array tail.
    return srcs[:-1] if len(srcs) > 1 else []


def main() -> int:
    # --- existence -------------------------------------------------------
    check(DOCKERFILE.is_file(), "Dockerfile is missing at repo root")
    check(DOCKERIGNORE.is_file(), ".dockerignore is missing at repo root")
    check(ENTRYPOINT.is_file(), "entrypoint.sh is missing at repo root (AC2)")
    if not DOCKERFILE.is_file() or not DOCKERIGNORE.is_file():
        return report()

    df_text_raw = DOCKERFILE.read_text(encoding="utf-8")
    df_lines = logical_lines(df_text_raw)
    if not df_lines:
        failures.append("Dockerfile is empty")
        return report()

    froms = [line for line in df_lines if line.upper().startswith("FROM ")]
    check(bool(froms), "Dockerfile has no FROM instruction")

    # --- AC3 multi-stage -------------------------------------------------
    check(
        len(froms) >= 2,
        f"AC3: single-stage build ({len(froms)} FROM); Phase 5 wants a deps "
        "stage plus a runtime stage that copies only what is needed",
    )

    # --- AC1 base image --------------------------------------------------
    for from_line in froms:
        base = image_of(from_line)
        match = BASE_IMAGE_RE.match(base)
        check(
            match is not None,
            f"AC1: FROM {base!r} is not a python:-slim base of the form python:3.x-slim",
        )
        if match:
            check(
                int(match.group("minor")) >= BASE_IMAGE_MIN_MINOR,
                f"AC1: FROM {base!r} is older than the Python 3.10 floor",
            )
        check(
            base == PINNED_BASE,
            f"AC1: FROM {base!r} is not pinned to {PINNED_BASE!r} (the fork pins 3.13)",
        )

    last_from = stage_index(df_lines)
    final_stage = df_lines[last_from:] if last_from >= 0 else df_lines

    # --- AC3 the runtime stage installs nothing ---------------------------
    installs = [
        line
        for line in final_stage
        if line.upper().startswith("RUN ")
        and re.search(r"\b(pip3?\s+install|apt-get\s+install|npm\s+install)\b", line)
    ]
    check(
        not installs,
        "AC3: the runtime stage still installs packages; the deps stage must do "
        "that and the runtime stage must only COPY the result",
    )
    check(
        any(line.upper().startswith("COPY --FROM=") for line in final_stage),
        "AC3: the runtime stage does not COPY --from= another stage",
    )

    # --- AC3 only needed files reach the image ----------------------------
    for line in final_stage:
        if line.upper().startswith(("COPY ", "ADD ")) and "--from=" not in line:
            for src in copy_sources(line):
                top = src.rstrip("/").split("/")[0].lstrip("./")
                check(
                    top in RUNTIME_COPY_ALLOWLIST,
                    f"AC3: runtime stage copies {src!r}, which is outside the "
                    f"allowed set {RUNTIME_COPY_ALLOWLIST}",
                )

    # --- AC1 dependencies from requirements.txt ---------------------------
    check(REQUIREMENTS.is_file(), "backend/requirements.txt is missing")
    if REQUIREMENTS.is_file():
        req_text = REQUIREMENTS.read_text(encoding="utf-8").lower()
        check("gunicorn" in req_text, "gunicorn is not declared in backend/requirements.txt")
        for needed in ("sqlalchemy", "alembic", "psycopg2"):
            check(
                needed in req_text,
                f"backend/requirements.txt no longer declares {needed} (Phase 1 DB layer)",
            )
    df_text = "\n".join(df_lines)
    check(
        "requirements.txt" in df_text,
        "AC1: Dockerfile does not install backend/requirements.txt",
    )

    # --- AC1 copies backend and frontend ----------------------------------
    check(
        re.search(r"(?im)^\s*COPY\b.*\s(?:\./)?backend\b", df_text) is not None,
        "AC1: no COPY of backend/ into the image",
    )
    check(
        re.search(r"(?im)^\s*COPY\b.*\s(?:\./)?frontend\b", df_text) is not None,
        "AC1: no COPY of frontend/ into the image (the app serves it as static)",
    )

    # --- AC1 port --------------------------------------------------------
    exposes = [line for line in df_lines if line.upper().startswith("EXPOSE ")]
    check(bool(exposes), "Dockerfile has no EXPOSE instruction")
    expose_ports = " ".join(e.split(None, 1)[1] for e in exposes)
    check(
        APP_PORT in expose_ports.split(),
        f"EXPOSE is {expose_ports!r}, expected to include {APP_PORT}",
    )

    # --- non-root USER (SOU-12 guarantee, kept) ---------------------------
    users = [line for line in df_lines if line.upper().startswith("USER ")]
    check(bool(users), "Dockerfile has no USER instruction (would run as root)")
    if users:
        final_user = users[-1].split(None, 1)[1].strip()
        check(
            final_user not in {"root", "0", "root:root", "0:0"},
            f"final USER is {final_user!r}; the container must run non-root",
        )

    # --- AC2 + AC1 the container starts through entrypoint.sh -------------
    entry_lines = [
        line for line in final_stage if line.upper().startswith(("CMD ", "ENTRYPOINT "))
    ]
    check(bool(entry_lines), "Dockerfile defines no CMD/ENTRYPOINT")
    check(
        any(line.upper().startswith("ENTRYPOINT ") for line in final_stage),
        "AC2: no ENTRYPOINT; Phase 5 starts through entrypoint.sh so the "
        "migration always runs, even when a CMD is overridden",
    )
    entry_blob = " ".join(entry_lines)
    check(
        "entrypoint.sh" in entry_blob,
        f"AC2: the container start command is not entrypoint.sh: {entry_blob!r}",
    )
    check(
        not any(re.search(r"python3?\s+\S*app\.py", line) for line in df_lines),
        "Dockerfile runs `python app.py` (Flask dev server) somewhere",
    )

    # --- AC2 entrypoint.sh itself -----------------------------------------
    if ENTRYPOINT.is_file():
        ep_text = ENTRYPOINT.read_text(encoding="utf-8")
        ep_lines = [ln for ln in ep_text.splitlines() if ln.strip() and not ln.strip().startswith("#")]

        check(
            os.access(ENTRYPOINT, os.X_OK),
            "AC2: entrypoint.sh is not executable (the image COPY preserves the mode)",
        )
        check("alembic" in ep_text, "AC2: entrypoint.sh never mentions alembic")
        check(
            re.search(r"alembic\b[^\n]*upgrade\s+head", ep_text) is not None,
            "AC2: entrypoint.sh does not run `alembic upgrade head`",
        )
        check(
            "gunicorn" in ep_text,
            "AC2: entrypoint.sh does not start gunicorn",
        )
        # Positions are measured on the code, not on the prose: a comment that
        # mentions gunicorn in the header must not look like a start command.
        ep_code = "\n".join(
            ln for ln in ep_text.splitlines() if not ln.strip().startswith("#")
        )
        pos_migrate = ep_code.find("upgrade head")
        pos_serve = ep_code.find("gunicorn")
        check(
            pos_migrate != -1 and pos_serve != -1 and pos_migrate < pos_serve,
            "AC2: entrypoint.sh starts the server before the migration ran",
        )
        check(
            re.search(r"\bset\s+-[a-z]*e", ep_text) is not None,
            "AC2: entrypoint.sh has no `set -e`; a failed migration must stop the start-up",
        )
        check(
            "|| true" not in ep_code.split("gunicorn")[0],
            "AC2: entrypoint.sh swallows migration failures with `|| true`",
        )
        check(
            "alembic.ini" in ep_text and "-c" in ep_text,
            "AC2: entrypoint.sh must run alembic against backend/alembic.ini "
            "so env.py keeps resolving DATABASE_URL and the SQLite fallback",
        )
        # The URL stays in one place: DATABASE_URL -> env.py -> db.py fallback.
        check(
            re.search(r"sqlite:///|postgres(ql|al)?\+?\w*://", ep_text) is None,
            "AC2: entrypoint.sh hard-codes a database URL; DATABASE_URL and "
            "alembic.ini/env.py own that decision",
        )
        check(
            "STAR_BACKEND_PORT" in ep_text,
            "AC1: entrypoint.sh does not honour STAR_BACKEND_PORT",
        )
        check(
            re.search(r"--chdir\s+backend", ep_text) is not None,
            "AC1: gunicorn must run with --chdir backend so app.py and its "
            "top-level imports resolve",
        )
        check(
            re.search(r"--bind\s+\"?'?0\.0\.0\.0:\$\{?STAR_BACKEND_PORT", ep_code) is not None,
            "AC1: gunicorn bind is not 0.0.0.0:${STAR_BACKEND_PORT:-19000}",
        )
        check(
            "--workers" in ep_text and "gthread" in ep_text,
            "AC2: gunicorn must keep the SOU-12 single gthread worker ruling "
            "(in-process state must not fork into per-worker copies)",
        )
        check(
            re.search(r"\bexec\b[^\n]*gunicorn", ep_text) is not None,
            "AC2: gunicorn must be exec'd so it stays PID 1 and gets SIGTERM",
        )
        check(
            not re.search(r"python3?\s+\S*app\.py", ep_text),
            "AC2: entrypoint.sh falls back to the Flask dev server",
        )
        del ep_lines

    # --- AC4 healthcheck --------------------------------------------------
    health = [line for line in df_lines if line.upper().startswith("HEALTHCHECK ")]
    check(bool(health), "AC4: Dockerfile has no HEALTHCHECK instruction")
    if health:
        probe = health[-1]
        check(
            HEALTH_PATH in probe,
            f"AC4: HEALTHCHECK must probe {HEALTH_PATH} (the DB-backed feed), got: {probe!r}",
        )
        check(
            "/health" not in probe,
            f"AC4: HEALTHCHECK still probes /health, which answers without touching the database: {probe!r}",
        )
        check(
            re.search(r"--interval=30s", probe) is not None,
            f"AC4: HEALTHCHECK interval must be 30s: {probe!r}",
        )
        check(
            re.search(r"--retries=3\b", probe) is not None,
            f"AC4: HEALTHCHECK retries must be 3: {probe!r}",
        )

    # --- no baked secrets (SOU-12 guarantee, kept) ------------------------
    copy_lines = [line for line in df_lines if line.upper().startswith(("COPY ", "ADD "))]
    for line in copy_lines:
        for src in copy_sources(line) + [line]:
            check(
                ".env" not in Path(src).name,
                f"COPY/ADD pulls in a .env file: {line!r}",
            )
    for line in df_lines:
        upper = line.upper()
        if upper.startswith(("ENV ", "ARG ")):
            body = line.split(None, 1)[1]
            pairs = [p for p in body.split() if "=" in p] if "=" in body else [" ".join(body.split()[:2])]
            for pair in pairs:
                if "=" not in pair:
                    continue
                name, _, value = pair.partition("=")
                name, value = name.strip(), value.strip().strip('"').strip("'")
                check(name.upper() not in FORBIDDEN_SECRET_NAMES, f"baked secret env var {name}")
                if SECRET_NAME_RE.search(name) and value:
                    check(False, f"ENV/ARG {name} carries a literal secret value ({value[:4]}...)")

    # --- AC1 hygiene: .dockerignore ---------------------------------------
    di_lines = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    di_blob = "\n".join(di_lines)
    for needed in REQUIRED_IGNORES:
        check(needed in di_blob, f".dockerignore does not exclude {needed!r}")

    return report()


def report() -> int:
    if failures:
        print(f"FAIL: {len(failures)} contract violation(s) out of {checks} checks:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(
        "PASS: Dockerfile + entrypoint.sh + .dockerignore satisfy the SOU-12/SOU-62 "
        f"container contract ({checks} checks)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
