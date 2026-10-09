# Star Office UI - production container image (SOU-12)
#
# Upstream runs the Flask development server (`python app.py`). This image
# serves the same Flask app with gunicorn, as a non-root user, with no
# secrets baked in. Deployment (Flux/SOPS/Tunnel/OTEL) is SOU-10, not here.
FROM python:3.11-slim

# Deterministic, quiet Python and the port the app listens on by default.
# NOTE: no secret is set here - FLASK_SECRET_KEY / ASSET_DRAWER_PASS / API
# keys are injected at deploy time (SOU-10, SOPS) and must never be baked.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STAR_BACKEND_PORT=19000

WORKDIR /app

# Install the app requirements first so this layer is cached across code
# changes. flask + pillow + gunicorn all ship manylinux wheels for cp311,
# so no compiler or system packages are required.
COPY backend/requirements.txt /app/backend/requirements.txt
# Run as an unprivileged user (never root). /app is owned by it so the app
# can write its runtime state files (state.json, agents-state.json, assets).
RUN pip install --no-cache-dir -r /app/backend/requirements.txt \
 && useradd --create-home --uid 10001 --shell /usr/sbin/nologin app
COPY --chown=app:app . /app
USER app

# The app listens on 19000 (STAR_BACKEND_PORT) - see backend/app.py.
EXPOSE 19000

# Liveness probe using only the stdlib (no curl in the image).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:19000/health', timeout=3).status == 200 else 1)"]

# gunicorn, NOT the Flask dev server. A single gthread worker keeps the
# app's in-process state and background tasks consistent (multiple sync
# workers would each hold divergent state).
CMD ["gunicorn", "--chdir", "backend", "--bind", "0.0.0.0:19000", \
     "--worker-class", "gthread", "--workers", "1", "--threads", "8", \
     "--timeout", "120", "--graceful-timeout", "30", \
     "--access-logfile", "-", "--error-logfile", "-", "app:app"]
