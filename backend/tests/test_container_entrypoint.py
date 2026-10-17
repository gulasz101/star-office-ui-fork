"""Start-up contract of the container entrypoint (SOU-62, AC2).

``entrypoint.sh`` is the thing that makes the image deployable: it must migrate
the schema and only then start the server, on either backend (ADR-034). That is
behaviour, not text, so this module does not grep the script for comfort — it
copies the repo's ``backend/`` plus ``entrypoint.sh`` into a throwaway sandbox,
puts a stub ``gunicorn`` on ``PATH`` that records what it saw at the moment the
server was launched, and runs the real script with ``sh``.

Proved here (SOU-62 AC2):
  * ``alembic upgrade head`` runs before the server starts, and stamps head
  * a fresh SQLite install creates ``backend/sou.db`` out of nothing
  * ``DATABASE_URL`` selects Postgres and the stamp lands there
  * a failed migration stops the start-up: no server against a half-migrated schema
  * the server is exec'd with the port/gunicorn contract the image relies on

Run from ``backend/`` (the DB extras must be importable; this repo keeps them in
``.tooling/site`` — see README/AGENTS notes on ``python3 .tooling/pip.pyz install``)::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_container_entrypoint.py -q

The Postgres leg needs a reachable server and is skipped when
``TEST_POSTGRES_URL`` is unset::

    bash ../scripts/dev_postgres.sh start
    TEST_POSTGRES_URL=postgresql+psycopg2://postgres@127.0.0.1:55432/sou \\
        PYTHONPATH=../.tooling/site python3 -m pytest tests/test_container_entrypoint.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENTRYPOINT = os.path.join(REPO_ROOT, "entrypoint.sh")
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
TOOLING_SITE = os.path.join(REPO_ROOT, ".tooling", "site")

# The revision head stamps. It is derived from backend/migrations/versions so
# the container checks always run against the real head; when a new migration
# lands, this follows it. ``test_single_head_is_current`` keeps the script
# directory honest about being single-headed.
def _head_revision() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(os.path.join(REPO_ROOT, "backend", "alembic.ini"))
    return ScriptDirectory.from_config(config).get_current_head()


HEAD_REVISION = _head_revision()
TABLES = {"agents", "events", "sessions", "alembic_version"}

# The stub server writes this; the tests read it back. setUp prefixes it with a
# `#!<sys.executable>` shebang, so the stub always runs on the same interpreter
# (and the same SQLAlchemy) as the tests themselves, in a venv or in .tooling.
STUB = """import json
import os
import sys

record = {"argv": sys.argv[1:], "cwd": os.getcwd(), "env": dict(os.environ)}
url = os.environ.get("DATABASE_URL") or ""
if not url:
    url = "sqlite:///" + os.path.join(os.getcwd(), "backend", "sou.db")
try:
    import sqlalchemy as sa
    engine = sa.create_engine(url)
    inspector = sa.inspect(engine)
    record["tables"] = sorted(inspector.get_table_names())
    with engine.connect() as conn:
        record["alembic_version"] = [
            row[0] for row in conn.execute(sa.text("SELECT version_num FROM alembic_version"))
        ]
    engine.dispose()
except Exception as exc:  # recorded, not raised: the point is what the DB looked like
    record["db_error"] = f"{type(exc).__name__}: {exc}"
with open(os.environ["SOU_STUB_RECORD"], "w", encoding="utf-8") as handle:
    json.dump(record, handle)
"""


def _alembic_bin() -> str | None:
    """Where the alembic CLI lives: PATH first, then this repo's .tooling/site."""
    found = shutil.which("alembic")
    if found:
        return found
    candidate = os.path.join(TOOLING_SITE, "bin", "alembic")
    return candidate if os.access(candidate, os.X_OK) else None


class EntrypointSandbox(unittest.TestCase):
    """Run the real entrypoint.sh against a throwaway copy of backend/."""

    sandbox = ""
    stub_record = ""
    bin_dir = ""

    def setUp(self) -> None:
        if not os.path.isfile(ENTRYPOINT):
            self.fail(
                f"AC2: {ENTRYPOINT} does not exist — the container has no "
                "migration-on-startup step"
            )
        if not os.path.isfile(os.path.join(BACKEND_DIR, "alembic.ini")):
            self.fail("backend/alembic.ini is missing; the entrypoint has nothing to run")
        self.alembic = _alembic_bin()
        if self.alembic is None:
            self.skipTest(
                "alembic CLI unavailable (install the DB extras into .tooling/site "
                "or put alembic on PATH)"
            )

        self.sandbox = tempfile.mkdtemp(prefix="sou-entrypoint-")
        self.addCleanup(shutil.rmtree, self.sandbox, ignore_errors=True)

        # A miniature of the image: /app/entrypoint.sh + /app/backend/.
        shutil.copytree(
            BACKEND_DIR,
            os.path.join(self.sandbox, "backend"),
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.db", "*.db-*", ".mypy_cache"),
        )
        script = os.path.join(self.sandbox, "entrypoint.sh")
        shutil.copyfile(ENTRYPOINT, script)
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        # Stub server on PATH, ahead of any real gunicorn.
        self.bin_dir = os.path.join(self.sandbox, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)
        stub_path = os.path.join(self.bin_dir, "gunicorn")
        with open(stub_path, "w", encoding="utf-8") as handle:
            handle.write(f"#!{sys.executable}\n")
            handle.write(STUB)
        os.chmod(stub_path, 0o755)

        self.stub_record = os.path.join(self.sandbox, "server-record.json")

    def _env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = dict(os.environ)
        path_dirs = [self.bin_dir]
        tooling_bin = os.path.join(TOOLING_SITE, "bin")
        if os.path.isdir(tooling_bin):
            path_dirs.append(tooling_bin)
        env["PATH"] = os.pathsep.join(path_dirs + [env.get("PATH", "")])
        if os.path.isdir(TOOLING_SITE):
            existing = env.get("PYTHONPATH", "")
            env["PYTHONPATH"] = f"{TOOLING_SITE}{os.pathsep}{existing}".rstrip(os.pathsep)
        env["SOU_STUB_RECORD"] = self.stub_record
        # Never let the developer's own DATABASE_URL leak into a "fallback" test.
        env.pop("DATABASE_URL", None)
        for key, value in (extra or {}).items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
        return env

    def run_entrypoint(self, extra_env: dict[str, str] | None = None):
        proc = subprocess.run(
            ["/bin/sh", os.path.join(self.sandbox, "entrypoint.sh")],
            cwd=self.sandbox,
            env=self._env(extra_env),
            capture_output=True,
            text=True,
            timeout=180,
        )
        return proc

    def server_record(self) -> dict:
        self.assertTrue(
            os.path.isfile(self.stub_record),
            "entrypoint.sh never started the server (no stub record written)",
        )
        with open(self.stub_record, encoding="utf-8") as handle:
            return json.load(handle)

    def assert_migrated(self, record: dict, *, backend: str) -> None:
        self.assertNotIn(
            "db_error", record, f"server started against an unreadable database: {record.get('db_error')}"
        )
        self.assertEqual(
            record.get("alembic_version"),
            [HEAD_REVISION],
            f"AC2: {backend} was not at head when the server started",
        )
        self.assertTrue(
            TABLES <= set(record.get("tables", [])),
            f"AC2: {backend} tables missing at server start: {sorted(set(record.get('tables', [])))}",
        )

    # -- AC2: SQLite fallback ------------------------------------------------

    def test_fresh_sqlite_install_creates_backend_sou_db(self) -> None:
        db_file = os.path.join(self.sandbox, "backend", "sou.db")
        self.assertFalse(os.path.exists(db_file), "sandbox started with a database already")

        proc = self.run_entrypoint()
        self.assertEqual(proc.returncode, 0, f"entrypoint failed: {proc.stdout}\n{proc.stderr}")
        self.assertTrue(
            os.path.isfile(db_file),
            "AC2: entrypoint.sh did not create backend/sou.db on a fresh install",
        )
        self.assert_migrated(self.server_record(), backend="sqlite")

    def test_start_up_log_announces_the_migration(self) -> None:
        proc = self.run_entrypoint()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        blob = proc.stdout + proc.stderr
        self.assertIn(
            "upgrade head",
            blob,
            "AC2: the start-up log never says which migration step ran, so a "
            "deployer cannot tell migration from server start apart",
        )

    def test_second_start_is_idempotent(self) -> None:
        first = self.run_entrypoint()
        self.assertEqual(first.returncode, 0, first.stderr)
        os.remove(self.stub_record)
        second = self.run_entrypoint()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assert_migrated(self.server_record(), backend="sqlite (second start)")

    # -- SOU-270: the SOU_MIGRATE switch ------------------------------------

    def test_sou_migrate_zero_skips_the_migration_and_still_starts_the_server(self) -> None:
        """The public face: no alembic run, and the server still comes up.

        ``sou-public`` starts with SOU_MIGRATE=0 against the schema
        ``sou-ingest`` already brought to head (docs/public-lodge.md). This
        sandbox has never migrated, so what this case pins is the other half of
        the contract: the entrypoint runs no migration, does not fail, and hands
        a readable-but-unmigrated database to the server — the public face
        reports that loudly at read time instead of writing to the database.
        """
        proc = self.run_entrypoint({"SOU_MIGRATE": "0"})
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        blob = proc.stdout + proc.stderr
        self.assertIn("[entrypoint] SOU_MIGRATE=0: skipping migrations", blob)
        self.assertNotIn(
            "upgrade head", blob, "SOU_MIGRATE=0 still ran (or announced) a migration"
        )
        record = self.server_record()
        self.assertNotEqual(
            record.get("alembic_version"),
            [HEAD_REVISION],
            "SOU_MIGRATE=0 migrated the schema anyway",
        )
        self.assertIn(
            "db_error",
            record,
            "this sandbox never migrated, so the stub server must find no schema "
            "(that is the loud operator error the public face wants, not a silent pass)",
        )

    def test_sou_migrate_zero_keeps_an_already_migrated_database_at_head(self) -> None:
        """The deployed shape: ingest migrates, public skips and reads head."""
        first = self.run_entrypoint()
        self.assertEqual(first.returncode, 0, first.stderr)
        os.remove(self.stub_record)

        second = self.run_entrypoint({"SOU_MIGRATE": "0"})
        self.assertEqual(second.returncode, 0, f"{second.stdout}\n{second.stderr}")
        blob = second.stdout + second.stderr
        self.assertIn("[entrypoint] SOU_MIGRATE=0: skipping migrations", blob)
        self.assertNotIn("upgrade head", blob)
        self.assert_migrated(self.server_record(), backend="sqlite (SOU_MIGRATE=0)")

    # -- AC2 / AC1: how the server is started --------------------------------

    def test_server_command_keeps_the_image_contract(self) -> None:
        self.run_entrypoint()
        argv = self.server_record()["argv"]
        joined = " ".join(argv)
        self.assertIn("app:app", joined, "gunicorn must serve the Flask app object")
        self.assertIn("--chdir", joined, "gunicorn needs --chdir backend for app.py's imports")
        self.assertIn("gthread", joined, "SOU-12 ruling: a single gthread worker keeps state coherent")
        self.assertIn("0.0.0.0:", joined, "the server must bind all interfaces inside the container")

    def test_default_port_is_19000(self) -> None:
        self.run_entrypoint()
        self.assertIn("0.0.0.0:19000", " ".join(self.server_record()["argv"]))

    def test_star_backend_port_override_reaches_the_bind(self) -> None:
        self.run_entrypoint({"STAR_BACKEND_PORT": "3009"})
        self.assertIn("0.0.0.0:3009", " ".join(self.server_record()["argv"]))

    def test_database_url_reaches_the_server_process(self) -> None:
        url = f"sqlite:///{os.path.join(self.sandbox, 'elsewhere.db')}"
        proc = self.run_entrypoint({"DATABASE_URL": url})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = self.server_record()
        self.assertEqual(record["env"].get("DATABASE_URL"), url, "AC2: DATABASE_URL was not passed through")
        # The override is honoured: the default file must NOT be created.
        self.assertFalse(
            os.path.exists(os.path.join(self.sandbox, "backend", "sou.db")),
            "AC2: entrypoint.sh migrated the fallback file even though DATABASE_URL was set",
        )
        self.assertTrue(os.path.exists(os.path.join(self.sandbox, "elsewhere.db")))

    # -- AC2: a broken migration must not boot a server ----------------------

    def test_unreachable_database_stops_start_up(self) -> None:
        # Port 1 is never a Postgres. Credentials in the URL must not be echoed.
        url = "postgresql+psycopg2://sou:sup3rs3cr3tpw@127.0.0.1:1/sou"
        proc = self.run_entrypoint({"DATABASE_URL": url})
        self.assertNotEqual(proc.returncode, 0, "AC2: entrypoint booted a server against an unreachable DB")
        self.assertFalse(
            os.path.isfile(self.stub_record),
            "AC2: the server started even though the migration failed",
        )
        self.assertNotIn(
            "sup3rs3cr3tpw", proc.stdout + proc.stderr, "start-up log leaked the DATABASE_URL password"
        )

    # -- AC2: Postgres backend ------------------------------------------------

    def _postgres_url(self) -> str | None:
        url = os.environ.get("TEST_POSTGRES_URL")
        if not url:
            return None
        try:
            import sqlalchemy as sa
        except ImportError:
            return None
        try:
            engine = sa.create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
            with engine.connect() as conn:
                conn.execute(sa.text("SELECT 1"))
            engine.dispose()
        except Exception:
            return None
        return url

    def test_postgres_backend_migrates_before_the_server(self) -> None:
        url = self._postgres_url()
        if url is None:
            self.skipTest("TEST_POSTGRES_URL is unset or the server is unreachable (scripts/dev_postgres.sh start)")

        import sqlalchemy as sa

        engine = sa.create_engine(url)
        with engine.begin() as conn:  # start from a clean database, every time
            for table in ("events", "sessions", "agents", "alembic_version"):
                conn.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))
        engine.dispose()

        proc = self.run_entrypoint({"DATABASE_URL": url})
        self.assertEqual(proc.returncode, 0, f"{proc.stdout}\n{proc.stderr}")
        record = self.server_record()
        self.assert_migrated(record, backend="postgres")

        # And the stamp really is in Postgres, not in some sandbox side file.
        engine = sa.create_engine(url)
        with engine.connect() as conn:
            stamped = [row[0] for row in conn.execute(sa.text("SELECT version_num FROM alembic_version"))]
            names = set(sa.inspect(engine).get_table_names())
        engine.dispose()
        self.assertEqual(stamped, [HEAD_REVISION])
        self.assertTrue(TABLES <= names)
        self.assertFalse(
            os.path.exists(os.path.join(self.sandbox, "backend", "sou.db")),
            "AC2: Postgres was configured but a SQLite file appeared anyway",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
