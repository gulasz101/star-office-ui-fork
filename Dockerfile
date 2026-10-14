# Star Office UI - production container image (SOU-12, rebuilt for Phase 5 by SOU-62)
#
# Two stages: only the deps stage installs anything; the runtime stage copies
# the result plus the files the server actually serves. Alembic runs on every
# start through entrypoint.sh, so a fresh Postgres database or a fresh
# backend/sou.db is migrated before the first request lands.
#
# Python 3.13 is pinned (AC1's preference): the Phase 1 code uses `int | None`
# syntax, and SOU-64 proved every entry in backend/requirements.txt resolves to
# a cp313 manylinux wheel, so neither stage needs a compiler.
#
# No secret is set here. FLASK_SECRET_KEY / STAR_OFFICE_SECRET, ASSET_DRAWER_PASS
# and DATABASE_URL arrive at deploy time; STAR_OFFICE_ENV=production makes the
# app refuse to boot while those are weak, which is the behaviour we want.

FROM python:3.13-slim AS deps

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Requirements land in their own layer so a code change never re-resolves them.
# pytest travels with requirements.txt by ruling on SOU-62: the file is the one
# dependency contract the fork keeps, and a runtime/dev split would be a
# separate change (a few MB, no runtime impact).
COPY backend/requirements.txt /build/backend/requirements.txt
RUN pip install --no-cache-dir --prefix=/install -r /build/backend/requirements.txt

FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="star-office-ui-fork" \
      org.opencontainers.image.source="https://github.com/gulasz101/star-office-ui-fork" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    STAR_BACKEND_PORT=19000

# Unprivileged, fixed uid 10001: the homelab PVC can grant that uid write access
# and nothing in the container ever runs as root (kept from SOU-12).
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin app

WORKDIR /app

# Installed packages and their console scripts (gunicorn, alembic) merge into
# the base image's own /usr/local, so nothing custom has to be on PYTHONPATH.
COPY --from=deps /install/ /usr/local/

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
