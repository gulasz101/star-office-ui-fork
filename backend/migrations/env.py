"""Alembic environment for the SOU database layer (ADR-034).

The backend is picked the same way ``backend/db.py`` will pick it (SOU-18):
``DATABASE_URL`` first, the SQLite fallback from ``alembic.ini`` second. Nothing
else may hard-code a connection string.

``target_metadata`` is the adapter's model metadata once ``backend/db.py``
is importable (SOU-18), which lets ``alembic revision --autogenerate`` and
``alembic check`` compare future migrations against the same contract the
code uses. Without the database extras the import fails and autogenerate
comparison simply stays disabled — the Flask-only install path never breaks.

Comparison against the migrated schema is clean on Postgres. On SQLite the
only residual shape is a remove/add pair for the two ``created_at/started_at
DESC`` expression indexes: SQLite reflection materialises the text element as
a column default rather than returning it verbatim, so autogenerate cannot
recognise it as the same index. That pair — and only that pair — is expected
noise in ``alembic check`` output on SQLite; tests/test_db.py pins the real
equality as compiled-DDL comparison against migration 001 on both dialects.
"""

from __future__ import annotations

import os
import re
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

# backend/ — the directory that holds alembic.ini.
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# sqlite:///sou.db and sqlite+driver:///sou.db, but not sqlite:////abs/path.
_RELATIVE_SQLITE = re.compile(
    r"^(?P<scheme>sqlite(?:\+\w+)?:///)(?P<path>[^/].*?)(?P<query>\?[^?]*)?$"
)

# ALEMBIC_SKIP_FILECONFIG keeps Alembic's log configuration out of the root
# logger for programmatic callers (backend/tests/test_migrations.py); the CLI
# still logs every Running upgrade line, which is the migration evidence.
if config.config_file_name is not None and not os.environ.get("ALEMBIC_SKIP_FILECONFIG"):
    fileConfig(config.config_file_name)

# backend/ is on sys.path via alembic.ini's prepend_sys_path, so the adapter's
# metadata is reachable from both the CLI and the programmatic Alembic API.
try:
    from db import Base as _AdapterBase
except ImportError:  # pragma: no cover - only hit without the DB extras
    target_metadata = None
else:
    target_metadata = _AdapterBase.metadata


def _resolve_url() -> str:
    """Return the URL to migrate, anchoring a relative SQLite path at backend/."""
    url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError(
            "No database URL: set DATABASE_URL or sqlalchemy.url in alembic.ini"
        )

    match = _RELATIVE_SQLITE.match(url)
    if match:
        path = match.group("path")
        if not os.path.isabs(path):
            path = os.path.join(BACKEND_DIR, path)
        url = f"{match.group('scheme')}{path}{match.group('query') or ''}"

    # DATABASE_URL is the natural place for production credentials; keep them out
    # of any log line Alembic may print.
    config.set_main_option("sqlalchemy.url", url)
    return url


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting (--sql mode)."""
    url = _resolve_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(url),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run the migrations against a live connection."""
    url = _resolve_url()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(url),
        )

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
