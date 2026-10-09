#!/usr/bin/env python3
"""Contract test for the Star Office UI container image (SOU-12).

Stdlib only. Parses the repo-root ``Dockerfile`` and ``.dockerignore`` and
asserts the container contract agreed on SOU-12:

  1. base image is ``python:3.11-slim``
  2. gunicorn is installed from the app requirements
  3. the container entrypoint runs gunicorn, never ``python app.py``
  4. it runs as a NON-ROOT user (no ``USER root``)
  5. no secrets are baked in (no ``.env`` copied, no secret-valued ENV/ARG)
  6. it EXPOSEs 19000, the port the app actually listens on
  7. ``.dockerignore`` excludes the code/dirt that must never enter the image

Exit code 0 = contract satisfied, 1 = violation(s). Run from the repo root.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
REQUIREMENTS = ROOT / "backend" / "requirements.txt"

BASE_IMAGE_RE = re.compile(r"^python:3\.11-slim$")
APP_PORT = "19000"

# Names that must never carry a literal value in the image. Deliberately
# broad: a false positive is safe here, a baked secret is not.
SECRET_NAME_RE = re.compile(
    r"(SECRET|PASSWORD|PASSWD|_PASS\b|\bPASS\b|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY|PRIVATE[_-]?KEY)",
    re.IGNORECASE,
)
# Names that are known-baked-secret offenders even if assigned empty.
FORBIDDEN_SECRET_NAMES = {"FLASK_SECRET_KEY", "ASSET_DRAWER_PASS", "GEMINI_API_KEY"}

# Patterns .dockerignore must contain (substring match, comments stripped).
REQUIRED_IGNORES = [
    ".git",
    ".env",
    ".venv",
    "__pycache__",
    "state.json",
    "agents-state.json",
    "join-keys.json",
    ".tooling",
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


def main() -> int:
    # --- existence -------------------------------------------------------
    check(DOCKERFILE.is_file(), "Dockerfile is missing at repo root")
    check(DOCKERIGNORE.is_file(), ".dockerignore is missing at repo root")
    if not DOCKERFILE.is_file() or not DOCKERIGNORE.is_file():
        return report()

    df_lines = logical_lines(DOCKERFILE.read_text(encoding="utf-8"))
    if not df_lines:
        failures.append("Dockerfile is empty")
        return report()

    # --- AC1 base image --------------------------------------------------
    froms = [line for line in df_lines if line.upper().startswith("FROM ")]
    check(bool(froms), "Dockerfile has no FROM instruction")
    if froms:
        base = froms[0].split(None, 1)[1].strip().split()[0]  # drop 'AS stage'
        check(
            BASE_IMAGE_RE.match(base) is not None,
            f"first FROM is {base!r}, expected python:3.11-slim",
        )

    last_from = stage_index(df_lines)
    final_stage = df_lines[last_from : ] if last_from >= 0 else df_lines

    # --- AC6 EXPOSE ------------------------------------------------------
    exposes = [line for line in df_lines if line.upper().startswith("EXPOSE ")]
    check(bool(exposes), "Dockerfile has no EXPOSE instruction")
    expose_ports = " ".join(e.split(None, 1)[1] for e in exposes)
    check(
        APP_PORT in expose_ports.split(),
        f"EXPOSE is {expose_ports!r}, expected to include {APP_PORT}",
    )

    # --- AC4 non-root USER ----------------------------------------------
    users = [line for line in df_lines if line.upper().startswith("USER ")]
    check(bool(users), "Dockerfile has no USER instruction (would run as root)")
    if users:
        final_user = users[-1].split(None, 1)[1].strip()
        check(
            final_user not in {"root", "0", "root:root", "0:0"},
            f"final USER is {final_user!r}; the container must run non-root",
        )

    # --- AC3 gunicorn entrypoint ----------------------------------------
    cmd_lines = [line for line in final_stage if line.upper().startswith(("CMD ", "ENTRYPOINT "))]
    check(bool(cmd_lines), "Dockerfile defines no CMD/ENTRYPOINT")
    if cmd_lines:
        entry = " ".join(cmd_lines)
        check("gunicorn" in entry, f"entrypoint does not run gunicorn: {entry!r}")
        check(
            "app.py" not in entry,
            f"entrypoint shells out to app.py (dev server): {entry!r}",
        )
        check(
            "0.0.0.0:19000" in entry or "0.0.0.0:${STAR_BACKEND_PORT" in entry
            or "0.0.0.0:$STAR_BACKEND_PORT" in entry or ":19000" in entry,
            f"gunicorn bind is not the app port {APP_PORT}: {entry!r}",
        )
    # No 'python app.py' anywhere as the runtime command.
    check(
        not any(re.search(r"python3?\s+.*app\.py", line) for line in df_lines),
        "Dockerfile runs `python app.py` (Flask dev server) somewhere",
    )

    # --- AC2 gunicorn in requirements -----------------------------------
    check(REQUIREMENTS.is_file(), "backend/requirements.txt is missing")
    if REQUIREMENTS.is_file():
        req_text = REQUIREMENTS.read_text(encoding="utf-8").lower()
        check("gunicorn" in req_text, "gunicorn is not declared in backend/requirements.txt")
    df_text = "\n".join(df_lines)
    check(
        "requirements.txt" in df_text,
        "Dockerfile does not install backend/requirements.txt",
    )

    # --- AC5 no baked secrets -------------------------------------------
    copy_lines = [line for line in df_lines if line.upper().startswith(("COPY ", "ADD "))]
    for line in copy_lines:
        tokens = line.split()
        srcs = tokens[1:-1] if len(tokens) > 2 else []
        for src in srcs:
            check(
                not src.rstrip("/").endswith(".env") and src not in {".env", "./.env"},
                f"COPY/ADD pulls in a .env file: {line!r}",
            )
    check(
        not any(re.search(r"(^|\s)\.env(\s|$)", line) for line in copy_lines),
        "Dockerfile copies the .env file into the image",
    )
    for line in df_lines:
        upper = line.upper()
        if upper.startswith(("ENV ", "ARG ")):
            body = line.split(None, 1)[1]
            # Support both `KEY=value` and `KEY value` forms.
            if "=" in body:
                pairs = [p for p in body.split() if "=" in p]
            else:
                pairs = [" ".join(body.split()[:2])]
            for pair in pairs:
                if "=" not in pair:
                    continue
                name, _, value = pair.partition("=")
                name, value = name.strip(), value.strip().strip('"').strip("'")
                check(
                    name.upper() not in FORBIDDEN_SECRET_NAMES,
                    f"baked secret env var {name}",
                )
                if SECRET_NAME_RE.search(name) and value:
                    check(
                        False,
                        f"ENV/ARG {name} carries a literal secret value ({value[:4]}...)",
                    )

    # --- AC7 .dockerignore ----------------------------------------------
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
    print(f"PASS: Dockerfile + .dockerignore satisfy the SOU-12 container contract ({checks} checks).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
