# Star Office UI - production container image (SOU-12, rebuilt for Phase 5 by
# SOU-62; dependency install moved onto uv by SOU-170)
#
# Two stages: only the deps stage installs anything; the runtime stage copies
# the result plus the files the server actually serves. Alembic runs on every
# start through entrypoint.sh, so a fresh Postgres database or a fresh
# backend/sou.db is migrated before the first request lands.
#
# Python 3.13 is pinned (AC1's preference): the Phase 1 code uses `int | None`
# syntax, and SOU-64 proved every dependency resolves to a cp313 manylinux
# wheel, so neither stage needs a compiler.
#
# No secret is set here. FLASK_SECRET_KEY / STAR_OFFICE_SECRET, ASSET_DRAWER_PASS
# and DATABASE_URL arrive at deploy time; STAR_OFFICE_ENV=production makes the
# app refuse to boot while those are weak, which is the behaviour we want.

# uv as a static binary, the pattern astral-sh documents (docs/guides/docker.md):
# COPY --from= gets uv onto PATH without apt or pip bootstrap in the deps image.
FROM ghcr.io/astral-sh/uv:0.13 AS uv

FROM python:3.13-slim AS deps

ENV PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/install

WORKDIR /build

# pyproject.toml + uv.lock in their own layer, so a code change never re-resolves
# the environment. SOU-170: this is the single dependency source; the old
# backend/requirements.txt is generated from it via `uv export` when someone
# needs a pip-style snapshot. --frozen refuses to touch the lock, --no-dev
# keeps pytest and friends out of the image (the SOU-62 "pytest travels with
# requirements.txt" ruling retired together with the file).
COPY pyproject.toml uv.lock /build/

# The venv is created --relocatable on purpose: console scripts (gunicorn,
# alembic) get `exec <their-own-dir>/python` trampolines instead of absolute
# shebangs, and the environment resolves its prefix from the script location,
# so the /install tree copied into the runtime stage works untouched.
RUN uv venv --relocatable --python /usr/local/bin/python3 /install \
 && uv sync --frozen --no-dev

FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="star-office-ui-fork" \
      org.opencontainers.image.source="https://github.com/gulasz101/star-office-ui-fork" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STAR_BACKEND_PORT=19000 \
    VIRTUAL_ENV=/install \
    PATH="/install/bin:${PATH}"

# Unprivileged, fixed uid 10001: the homelab PVC can grant that uid write access
# and nothing in the container ever runs as root (kept from SOU-12).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

# /app itself has to be writable by the app user: at import the backend creates
# its runtime JSON state (join-keys.json, state.json, agents-state.json,
# runtime-config.json) next to backend/, and .dockerignore deliberately keeps
# the local copies out of the image. Found by the CI smoke run on the real
# image, not by the sandbox tests - PermissionError: '/app/join-keys.json'.
# A single chown of the directory, never a recursive one (that would duplicate
# the whole tree in a new layer).
RUN chown app:app /app

# SOU-170: the whole uv environment travels as one self-contained, relocated
# tree — console scripts (gunicorn, alembic) are `exec <their-dir>/python`
# trampolines that resolve their prefix from the script location, so nothing
# is merged into /usr/local and nothing custom has to be on PYTHONPATH.
# /install is root-owned; the app user only reads and executes it.
COPY --from=deps /install /install

# The server serves backend/ (Flask app + migrations) and frontend/ (its static
# root, see app.py's static_folder) and reads/writes assets/. Everything else in
# the repo - docs, desktop shells, tooling, git metadata - stays out (AC3).
COPY --chown=app:app backend /app/backend
COPY --chown=app:app frontend /app/frontend
COPY --chown=app:app assets /app/assets
COPY LICENSE /app/LICENSE

# Root-owned on purpose: the app user may execute its start-up script but must
# not be able to rewrite it.
COPY entrypoint.sh /app/entrypoint.sh

# Build-time proof that the copied packages import in the *runtime* image, not
# just in the stage that installed them. psycopg2-binary is the one with native
# libraries, so this is what catches a missing shared object before deploy does.
RUN python -c "import flask, sqlalchemy, alembic, psycopg2, PIL, jsonschema, gunicorn; print('runtime imports ok')"

USER app

# The app listens on 19000; override the port with STAR_BACKEND_PORT.
EXPOSE 19000

# Liveness on the read path the dashboard itself uses (AC4): /events/recent
# answers from the migrated database, so this probe fails when the schema or the
# connection is broken while /health would still lie. Stdlib urllib only -
# there is no curl in this image.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c 'import os,sys,urllib.request; p=os.environ.get("STAR_BACKEND_PORT","19000"); sys.exit(0 if urllib.request.urlopen("http://127.0.0.1:"+p+"/events/recent?limit=1", timeout=3).status == 200 else 1)'

ENTRYPOINT ["/app/entrypoint.sh"]
