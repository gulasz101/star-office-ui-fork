"""App-level database contract for the SOU-19 store→db refactor.

``backend/app.py`` must own the startup path (AC3): importing the app
against a *fresh* database runs ``alembic upgrade head`` itself and seeds
the main agent — no manual migration step. The boot is exercised in a
subprocess, exactly the way ``run.sh`` / gunicorn import the module, so
each case gets an untouched interpreter.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_app_db.py -v

Every case targets SQLite via ``DATABASE_URL`` into a temp dir; the
dual-backend contract itself lives in ``tests/test_db.py``. When the
runtime extras (flask/sqlalchemy/alembic) are missing the module skips —
same convention as the rest of the suite.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import sqlalchemy as sa
    import flask  # noqa: F401  (the app under test is a Flask module)
    import alembic  # noqa: F401
except ImportError as exc:  # pragma: no cover - only hit without the extras
    sa = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

SCHEMA_TABLES = ("agents", "events", "sessions", "alembic_version")


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class StartupDbTest(unittest.TestCase):
    """AC3 (SOU-19): a fresh database is migrated and seeded at app import."""

    def setUp(self) -> None:
        self.workdir = tempfile.mkdtemp(prefix="sou19-app-")
        self.url = f"sqlite:///{os.path.join(self.workdir, 'boot.db')}"

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)

    def boot(self, times: int = 1) -> None:
        """Import backend/app.py in a fresh interpreter against ``self.url``."""
        env = dict(os.environ, DATABASE_URL=self.url, ALEMBIC_SKIP_FILECONFIG="1")
        code = "import app, db; db.get_engine()"
        for _ in range(times):
            proc = subprocess.run(
                [sys.executable, "-c", code],
                env=env,
                cwd=BACKEND_DIR,
                capture_output=True,
                text=True,
                timeout=180,
            )
            self.assertEqual(
                proc.returncode, 0, f"import app failed:\n{proc.stderr[-2000:]}"
            )

    def _query(self, sql: str):
        engine = sa.create_engine(self.url)
        try:
            with engine.connect() as conn:
                return conn.execute(sa.text(sql)).fetchall()
        finally:
            engine.dispose()

    def test_fresh_database_gets_the_full_schema(self) -> None:
        self.boot()
        engine = sa.create_engine(self.url)
        try:
            tables = set(sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()
        for name in SCHEMA_TABLES:
            self.assertIn(
                name, tables,
                f"app import must run 'alembic upgrade head' when tables are missing; {name} absent",
            )

    def test_fresh_startup_seeds_the_main_agent_exactly_once(self) -> None:
        self.boot(times=2)  # two consecutive boots: idempotent seed, no duplicates

        mains = self._query("SELECT name, agent_type FROM agents WHERE agent_type = 'main'")
        self.assertEqual([tuple(r) for r in mains], [("Star", "main")])

        rows = self._query(
            "SELECT e.event_type, e.payload FROM events e "
            "JOIN agents a ON a.id = e.agent_id "
            "WHERE a.agent_type = 'main' ORDER BY e.id"
        )
        self.assertEqual(len(rows), 1, "the seed state_change event must not duplicate across boots")
        self.assertEqual(rows[0][0], "state_change")
        payload = rows[0][1]
        if isinstance(payload, str):
            payload = json.loads(payload)
        self.assertEqual(payload["state"], "idle")


if __name__ == "__main__":
    unittest.main(verbosity=2)
