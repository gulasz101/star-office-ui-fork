#!/usr/bin/env python3
"""Contract test for the Star Office UI container image.

Stdlib only, no Docker needed: this parses ``Dockerfile``, ``entrypoint.sh`` and
``.dockerignore`` and asserts the container contract agreed on **SOU-12** (the
first production image) and extended for **SOU-62 / Phase 5** (Alembic migration
on startup, multi-stage, healthcheck on the event feed).

Phase 5 contract, keyed to the acceptance criteria on SOU-62
  AC1  base image Python 3.10+ (pinned ``python:3.13-slim`` for ``int | None``
       syntax), deps installed with uv from ``pyproject.toml`` + ``uv.lock``
       — the single dependency source since SOU-170 — backend +
       frontend copied, port 19000 / ``STAR_BACKEND_PORT``
  AC2  ``entrypoint.sh`` runs ``alembic upgrade head`` **before** the server,
       resolves the backend through ``DATABASE_URL`` (Postgres) with the SQLite
       fallback left to ``backend/migrations/env.py`` — never a hard-coded URL
  AC3  multi-stage: a deps stage that installs, a runtime stage that only copies
  AC4  ``HEALTHCHECK --interval=30s --retries=3`` probing ``/events/recent?limit=1``
  +    non-root final user and no baked secrets (the SOU-12 guarantees, kept)

The uv tool stage (``ghcr.io/astral-sh/uv``, COPY --from= target only) is the
one non-python FROM the contract allows. It must carry an exact ``X.Y.Z`` pin
that matches the version the CI workflow installs, and every stage that *runs*
uv must first copy the binary in (``COPY --from=uv /uv /uvx /bin/``) — declaring
the stage is not enough (SOU-170 review).

Exit code 0 = contract satisfied, 1 = violation(s). Run from anywhere:

    python3 scripts/validate_dockerfile.py
"""

from __future__ import annotations

import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
ENTRYPOINT = ROOT / "entrypoint.sh"
PYPROJECT = ROOT / "pyproject.toml"
UV_LOCK = ROOT / "uv.lock"
WORKFLOW = ROOT / ".github" / "workflows" / "docker-image.yml"

# AC1: "Base: Python 3.10+ (3.13 preferred, supports int | None syntax)".
# fullmatch, not match: a permissive pattern let `ghcr.io/astral-sh/uv:0.13`
# slip through as "python 3.1x" once (SOU-170).
BASE_IMAGE_RE = re.compile(r"^python:3\.(?P<minor>1[0-9])(?:\.\d+)?-slim$")
BASE_IMAGE_MIN_MINOR = 10
# The fork pins one base so the image and the CI smoke test can never drift.
PINNED_BASE = "python:3.13-slim"
# The uv tool image (SOU-170): allowed as a FROM, used only as a COPY --from=
# source, and it must be pinned to a tag, never :latest.
UV_IMAGE_RE = re.compile(r"^ghcr\.io/astral-sh/uv:[^:]+$")

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


def stage_name(from_line: str) -> str | None:
    """`FROM ghcr.io/astral-sh/uv:0.13.1 AS uv` -> `uv`."""
    tokens = from_line.split()
    for i, token in enumerate(tokens):
        if token.upper() == "AS" and i + 1 < len(tokens):
            return tokens[i + 1]
    return None


def split_stages(lines: list[str]) -> list[dict]:
    """The Dockerfile cut into build stages: base image, alias and body."""
    stages: list[dict] = []
    for line in lines:
        if line.upper().startswith("FROM "):
            stages.append(
                {"from": line, "name": stage_name(line), "image": image_of(line), "body": []}
            )
        elif stages:
            stages[-1]["body"].append(line)
    return stages


def runs_uv(instruction: str) -> bool:
    """Does this RUN invoke the uv binary (not merely mention uv.lock)?"""
    if not instruction.upper().startswith("RUN "):
        return False
    # `uv` as its own word followed by an argument: `RUN uv venv ...`,
    # `... && uv sync ...`. `uv.lock` and a `/install/bin/uvx` do not match.
    return re.search(r"(?:^|[\s;&|(])uv(?=[\s;]|$)", instruction) is not None


def copy_from_ref(instruction: str) -> str | None:
    """`COPY --from=uv /uv /uvx /bin/` -> `uv` (None for a build-context COPY)."""
    match = re.match(r"^COPY\s+--from=(?P<ref>\S+)", instruction, re.IGNORECASE)
    return match.group("ref") if match else None


def uv_tag(lines: list[str]) -> str | None:
    """The tag of the ghcr.io/astral-sh/uv base, or None when there is no such stage."""
    for line in lines:
        if line.upper().startswith("FROM "):
            image = image_of(line)
            if UV_IMAGE_RE.match(image):
                return image.rsplit(":", 1)[1]
    return None


def ci_uv_pin() -> str | None:
    """The uv version the CI workflow pins, so image and runner cannot drift.

    The job bootstraps uv with the runner's own pip (`pip install 'uv==0.13.1'`)
    because the workflow allows no third-party actions. Returns None when the
    workflow or its pin is missing; the caller then skips the cross-check
    instead of inventing a version.
    """
    if not WORKFLOW.is_file():
        return None
    match = re.search(r"['\"]uv==(\d+\.\d+\.\d+)['\"]", WORKFLOW.read_text(encoding="utf-8"))
    return match.group(1) if match else None


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

    # --- AC1 base images --------------------------------------------------
    uv_stages = [
        line for line in froms
        if UV_IMAGE_RE.match(image_of(line))
    ]
    py_stages = [
        line for line in froms
        if BASE_IMAGE_RE.fullmatch(image_of(line))
    ]
    check(
        len(uv_stages) + len(py_stages) == len(froms),
        "AC1: a FROM base is neither python:3.x-slim nor the pinned ghcr.io/astral-sh/uv "
        "tool image (SOU-170)",
    )
    for from_line in py_stages:
        base = image_of(from_line)
        check(
            int(BASE_IMAGE_RE.fullmatch(base).group("minor")) >= BASE_IMAGE_MIN_MINOR,
            f"AC1: FROM {base!r} is older than the Python 3.10 floor",
        )
        check(
            base == PINNED_BASE,
            f"AC1: FROM {base!r} is not pinned to {PINNED_BASE!r} (the fork pins 3.13)",
        )
    check(
        bool(uv_stages),
        "AC1: no ghcr.io/astral-sh/uv tool stage; the SOU-170 contract installs "
        "dependencies with uv, not pip",
    )
    check(
        len(py_stages) >= 1 and all(image_of(l) == PINNED_BASE for l in py_stages),
        f"AC1: the python bases are not all pinned to {PINNED_BASE!r}",
    )

    last_from = stage_index(df_lines)
    final_stage = df_lines[last_from:] if last_from >= 0 else df_lines
    if final_stage:
        check(
            image_of(final_stage[0]) == PINNED_BASE,
            f"AC1: the final stage base is {image_of(final_stage[0])!r}, expected {PINNED_BASE!r}",
        )

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

    # --- AC1 dependencies from pyproject.toml + uv.lock (SOU-170) ---------
    df_text = "\n".join(df_lines)
    check(
        not any("requirements.txt" in line.upper() for line in df_lines),
        "AC1: the Dockerfile still references backend/requirements.txt; pyproject.toml "
        "+ uv.lock are the single dependency source (SOU-170)",
    )
    check(PYPROJECT.is_file(), "pyproject.toml is missing at repo root")
    check(UV_LOCK.is_file(), "uv.lock is missing at repo root")
    if PYPROJECT.is_file():
        with PYPROJECT.open("rb") as handle:
            deps_blob = " ".join(
                str(d) for d in tomllib.load(handle)["project"]["dependencies"]
            ).lower()
        check("gunicorn" in deps_blob, "gunicorn is not declared in pyproject.toml")
        for needed in ("sqlalchemy", "alembic", "psycopg2"):
            check(
                needed in deps_blob,
                f"pyproject.toml no longer declares {needed} (Phase 1 DB layer)",
            )
    check(
        bool([line for line in df_lines if line.upper().startswith("COPY ") and "uv.lock" in line])
        and bool([line for line in df_lines if line.upper().startswith("COPY ") and "pyproject.toml" in line]),
        "AC1: the deps stage does not COPY both pyproject.toml and uv.lock",
    )
    check(
        re.search(r"(?im)^\s*RUN\b.*uv sync\b", df_text) is not None
        and "--frozen" in df_text
        and "--no-dev" in df_text,
        "AC1: the Dockerfile does not install with `uv sync --frozen --no-dev`",
    )
    check(
        re.search(r"(?im)\bpip3?\s+install\b", df_text) is None,
        "AC1: a pip install survived in the Dockerfile",
    )

    # --- SOU-170: uv is only on PATH where the binary was actually copied --
    # `FROM ghcr.io/astral-sh/uv:0.13.1 AS uv` installs nothing into the deps
    # stage: uv arrives through the documented binary copy
    #     COPY --from=uv /uv /uvx /bin/
    # Without it, `RUN uv sync` fails with "uv: not found" (exit 127) and the
    # image never builds. The check above only looked for the string "uv sync",
    # which is how 186e3a0 passed review with a broken build; this is that gate.
    stages = split_stages(df_lines)
    uv_images = [s for s in stages if UV_IMAGE_RE.match(s["image"])]
    uv_refs = {s["image"] for s in uv_images} | {s["name"] for s in uv_images if s["name"]}
    uv_consumed = False
    for stage in stages:
        copied_uv_at = None
        for pos, instruction in enumerate(stage["body"]):
            ref = copy_from_ref(instruction)
            if ref is not None and ref in uv_refs:
                uv_consumed = True
                if "/uv" in copy_sources(instruction):
                    copied_uv_at = pos
                else:
                    check(
                        False,
                        f"SOU-170: COPY --from={ref} does not bring /uv, so uv stays off "
                        "PATH; the pattern is `COPY --from=uv /uv /uvx /bin/`",
                    )
            if runs_uv(instruction):
                check(
                    copied_uv_at is not None,
                    f"SOU-170: stage {stage['name'] or stage['image']} runs uv with no "
                    f"COPY --from=uv before it: {instruction[:72]!r}",
                )
    check(
        not uv_images or uv_consumed,
        "SOU-170: a ghcr.io/astral-sh/uv stage is declared but no stage ever COPY --from= "
        "it: dead weight in the build and uv missing everywhere it is run",
    )
    # The tag has to be an exact X.Y.Z pin, and the same uv version the CI job
    # installs: a floating minor can point at a tag that stops existing, and two
    # uv versions in one repo resolve differently.
    tag = uv_tag(df_lines)
    if tag is not None:
        check(
            re.fullmatch(r"\d+\.\d+\.\d+", tag) is not None,
            f"SOU-170: the uv tool image tag {tag!r} is not an exact X.Y.Z pin",
        )
        pin = ci_uv_pin()
        if pin is not None:
            check(
                tag == pin,
                f"SOU-170: the image pins uv {tag} but the CI workflow installs uv {pin}",
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

    # AC1 (learned the hard way in the SOU-62 CI run): the app writes its JSON
    # state - join-keys.json, state.json, agents-state.json, runtime-config.json -
    # into the root of /app at import time, and .dockerignore keeps those files out
    # of the image, so the directory itself must belong to the runtime user.
    app_writable = re.search(r"(?im)^\s*RUN\s+chown\s+[\w.-]+:[\w.-]+\s+/app/?\b", df_text)
    whole_tree = re.search(r'(?im)^\s*COPY\s+--chown=\S+\s+\.\s+/app\b', df_text)
    check(
        app_writable is not None or whole_tree is not None,
        "AC1: /app is not made writable by the runtime user; the backend creates "
        "join-keys.json / state.json there at start-up (PermissionError otherwise)",
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
