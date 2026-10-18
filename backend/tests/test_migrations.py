"""Schema contract for the initial SOU migration (SOU-17, ADR-034).

The same assertions run against both backends; only the expected *types* differ
where the dialects differ (SERIAL vs AUTOINCREMENT, JSONB vs TEXT). That is the
point of the slice: one schema, two backends.

Run from ``backend/``::

    python3 -m unittest tests.test_migrations -v

The Postgres leg needs a reachable server and is skipped when
``TEST_POSTGRES_URL`` is unset::

    TEST_POSTGRES_URL=postgresql+psycopg2://sou@127.0.0.1:55432/sou \\
        python3 -m unittest tests.test_migrations -v

When the database extras (alembic / SQLAlchemy / psycopg2) are not installed —
any interpreter outside the uv-synced project env, before SOU-18's pyproject
dependencies reach it — the module skips itself instead of erroring, so it
never breaks the Flask-only install path.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import unittest

try:
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy.dialects import postgresql
except ImportError as exc:  # pragma: no cover - only hit without the DB extras
    sa = None
    command = None
    Config = None
    ScriptDirectory = None
    postgresql = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")

# Guarded because the class bodies below are evaluated at import time, before
# any skipIf can run: without the DB extras these stay empty and the module
# still imports, so the suite reports a clean skip instead of an ImportError.
TEXT_TYPE = (sa.Text,) if sa is not None else ()
JSONB_TYPE = (postgresql.JSONB,) if postgresql is not None else ()

TABLES = ("agents", "events", "sessions")
IDX_EVENTS = "idx_events_agent_created"
IDX_SESSIONS = "idx_sessions_agent_started"

# ADR-035 state_change payload — what the events table has to be able to hold.
SAMPLE_PAYLOAD = {"state": "writing", "detail": "designing the schema", "progress": 0}


def _alembic_config(url: str) -> "Config":
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    return config


@contextlib.contextmanager
def _alembic_env(url: str):
    """Point every Alembic entry point at one URL and keep its logging quiet."""
    touched = ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")
    previous = {name: os.environ.get(name) for name in touched}
    os.environ["DATABASE_URL"] = url
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_alembic(url: str, action: str, revision: str) -> None:
    """Drive Alembic through its Python API with one URL, whatever the CWD."""
    config = _alembic_config(url)
    with _alembic_env(url):
        getattr(command, action)(config, revision)


def _scalar(engine, sql: str) -> object:
    with engine.connect() as conn:
        return conn.execute(sa.text(sql)).scalar()


def _index_columns(column_names) -> list:
    """Index reflection may spell an ordering column as ``created_at DESC``."""
    out = []
    for name in column_names or []:
        text = str(name).strip().strip('"')
        out.append(text.split()[0] if text else text)
    return out


class _InitialSchemaContract:
    """Assertions shared by both backends; subclasses fix the URL and dialect."""

    dialect: str = ""
    json_payload_type: tuple = ()

    # -- fixture ---------------------------------------------------------------

    def setUp(self) -> None:
        self.workdir = tempfile.mkdtemp(prefix="sou-migrations-")
        self.url = self.database_url(self.workdir)
        _run_alembic(self.url, "downgrade", "base")
        _run_alembic(self.url, "upgrade", "head")
        self.engine = self.connect(self.url)
        self.insp = sa.inspect(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()
        with contextlib.suppress(Exception):
            _run_alembic(self.url, "downgrade", "base")
        shutil.rmtree(self.workdir, ignore_errors=True)

    def database_url(self, workdir: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def connect(self, url: str):
        return sa.create_engine(url)

    # -- helpers ---------------------------------------------------------------

    def columns(self, table: str) -> dict:
        return {c["name"]: c for c in self.insp.get_columns(table)}

    def assert_column(self, table, name, *, type_, nullable, length=None) -> None:
        col = self.columns(table).get(name)
        self.assertIsNotNone(col, f"{table}.{name} is missing")
        self.assertIsInstance(
            col["type"], type_, f"{table}.{name} is {col['type']!r}, expected {type_}"
        )
        self.assertEqual(
            col["nullable"], nullable, f"{table}.{name} nullable={col['nullable']}"
        )
        if length is not None:
            self.assertEqual(
                getattr(col["type"], "length", None),
                length,
                f"{table}.{name} length mismatch",
            )

    def raw_index_sql(self, index: str) -> str:
        """Stored DDL of an index, so the DESC ordering can be checked literally."""
        if self.dialect == "postgresql":
            return (
                _scalar(
                    self.engine,
                    f"SELECT indexdef FROM pg_indexes WHERE indexname = '{index}'",
                )
                or ""
            )
        return (
            _scalar(
                self.engine,
                f"SELECT sql FROM sqlite_master WHERE type='index' AND name='{index}'",
            )
            or ""
        )

    def raw_table_sql(self, table: str) -> str:
        """Stored CREATE TABLE text (SQLite keeps referential rules there)."""
        return (
            _scalar(
                self.engine,
                f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'",
            )
            or ""
        )

    # -- AC1: tables, columns, indexes ----------------------------------------

    def test_three_tables_exist(self) -> None:
        found = set(self.insp.get_table_names())
        for table in TABLES:
            self.assertIn(table, found, f"table {table} was not created")

    def test_agents_columns(self) -> None:
        self.assert_column("agents", "id", type_=sa.Integer, nullable=False)
        self.assert_column("agents", "name", type_=sa.String, nullable=False, length=255)
        self.assert_column("agents", "agent_type", type_=sa.String, nullable=False, length=50)
        self.assert_column("agents", "created_at", type_=sa.DateTime, nullable=False)
        self.assertEqual(
            sorted(self.columns("agents")),
            ["agent_type", "created_at", "hoodie", "id", "name", "room_type"],
            "agents carries columns the schema does not define",
        )

    def test_events_columns(self) -> None:
        self.assert_column("events", "id", type_=sa.Integer, nullable=False)
        self.assert_column("events", "agent_id", type_=sa.Integer, nullable=False)
        self.assert_column("events", "event_type", type_=sa.String, nullable=False, length=100)
        self.assert_column("events", "payload", type_=self.json_payload_type, nullable=False)
        self.assert_column("events", "created_at", type_=sa.DateTime, nullable=False)
        self.assertEqual(
            sorted(self.columns("events")),
            ["agent_id", "created_at", "event_type", "id", "payload"],
            "events carries columns the schema does not define",
        )

    def test_sessions_columns(self) -> None:
        self.assert_column("sessions", "id", type_=sa.Integer, nullable=False)
        self.assert_column("sessions", "agent_id", type_=sa.Integer, nullable=False)
        self.assert_column("sessions", "started_at", type_=sa.DateTime, nullable=False)
        self.assert_column("sessions", "ended_at", type_=sa.DateTime, nullable=True)
        self.assertEqual(
            sorted(self.columns("sessions")),
            ["agent_id", "ended_at", "id", "started_at"],
            "sessions carries columns the schema does not define",
        )

    def test_primary_keys_are_id(self) -> None:
        for table in TABLES:
            pk = self.insp.get_pk_constraint(table)
            self.assertEqual(pk["constrained_columns"], ["id"], f"{table} primary key is not id")

    def test_timestamps_default_to_database_time(self) -> None:
        for table, name in (("agents", "created_at"), ("events", "created_at")):
            default = (self.columns(table)[name].get("default") or "").lower()
            self.assertTrue(
                "current_timestamp" in default or "now()" in default,
                f"{table}.{name} has no timestamp default (got {default!r})",
            )

    def test_events_index_on_agent_and_created_at(self) -> None:
        indexes = {i["name"]: i for i in self.insp.get_indexes("events")}
        self.assertIn(IDX_EVENTS, indexes, f"events indexes: {sorted(indexes)}")
        self.assertEqual(
            _index_columns(indexes[IDX_EVENTS]["column_names"]),
            ["agent_id", "created_at"],
        )
        self.assertIn("desc", self.raw_index_sql(IDX_EVENTS).lower())

    def test_sessions_index_on_agent_and_started_at(self) -> None:
        indexes = {i["name"]: i for i in self.insp.get_indexes("sessions")}
        self.assertIn(IDX_SESSIONS, indexes, f"sessions indexes: {sorted(indexes)}")
        self.assertEqual(
            _index_columns(indexes[IDX_SESSIONS]["column_names"]),
            ["agent_id", "started_at"],
        )
        self.assertIn("desc", self.raw_index_sql(IDX_SESSIONS).lower())

    def test_foreign_keys_point_at_agents_with_cascade(self) -> None:
        for table in ("events", "sessions"):
            fks = self.insp.get_foreign_keys(table)
            self.assertEqual(len(fks), 1, f"{table} must have exactly one FK")
            fk = fks[0]
            self.assertEqual(fk["constrained_columns"], ["agent_id"])
            self.assertEqual(fk["referred_table"], "agents")
            self.assertEqual(fk["referred_columns"], ["id"])
            ondelete = str(fk["options"].get("ondelete", "")).upper()
            if ondelete != "CASCADE":
                # SQLite keeps the rule in the stored DDL rather than reflection.
                self.assertIn(
                    "ON DELETE CASCADE",
                    self.raw_table_sql(table).upper(),
                    f"{table}.agent_id does not cascade",
                )

    def test_id_is_auto_numbered(self) -> None:
        if self.dialect == "postgresql":
            for table in ("agents", "events", "sessions"):
                seq = _scalar(
                    self.engine, f"SELECT pg_get_serial_sequence('{table}', 'id')"
                )
                self.assertTrue(seq, f"{table}.id is not a SERIAL column")
        else:
            for table in TABLES:
                self.assertIn(
                    "AUTOINCREMENT",
                    self.raw_table_sql(table).upper(),
                    f"{table}.id is not AUTOINCREMENT",
                )

    # -- AC2: versions, downgrade ---------------------------------------------

    def test_single_head_and_database_stamped_at_it(self) -> None:
        heads = ScriptDirectory.from_config(_alembic_config(self.url)).get_heads()
        self.assertEqual(len(heads), 1, f"expected one head, got {heads}")
        stamped = _scalar(self.engine, "SELECT version_num FROM alembic_version")
        self.assertEqual(
            stamped, heads[0],
            "local database is not stamped at the single head — run `alembic upgrade head`",
        )

    def test_downgrade_to_base_removes_the_schema(self) -> None:
        _run_alembic(self.url, "downgrade", "base")
        engine = self.connect(self.url)
        try:
            remaining = set(sa.inspect(engine).get_table_names()) & set(TABLES)
        finally:
            engine.dispose()
        self.assertFalse(remaining, f"downgrade left {sorted(remaining)} behind")
        _run_alembic(self.url, "upgrade", "head")
        self.assertTrue(
            set(TABLES) <= set(sa.inspect(self.engine).get_table_names()),
            "re-upgrade after downgrade did not rebuild the schema",
        )

    # -- 004 agent registry (SOU-291) -----------------------------------------

    def test_ingest_tokens_table(self) -> None:
        self.assertIn("ingest_tokens", self.insp.get_table_names())
        self.assertEqual(
            sorted(self.columns("ingest_tokens")),
            ["agent_id", "created_at", "id", "revoked_at", "token_hash"],
        )
        self.assert_column("ingest_tokens", "token_hash", type_=sa.String, nullable=False, length=64)
        self.assert_column("ingest_tokens", "revoked_at", type_=sa.DateTime, nullable=True)
        uniques = [tuple(u["column_names"]) for u in self.insp.get_unique_constraints("ingest_tokens")]
        uniques += [tuple(i["column_names"]) for i in self.insp.get_indexes("ingest_tokens") if i.get("unique")]
        self.assertIn(("token_hash",), uniques, "token_hash must be unique")

    def test_004_downgrade_and_reupgrade(self) -> None:
        _run_alembic(self.url, "downgrade", "-1")
        engine = self.connect(self.url)
        try:
            insp = sa.inspect(engine)
            self.assertNotIn("ingest_tokens", insp.get_table_names())
            self.assertEqual(
                sorted(c["name"] for c in insp.get_columns("agents")),
                ["agent_type", "created_at", "id", "name"],
            )
        finally:
            engine.dispose()
        _run_alembic(self.url, "upgrade", "head")
        engine = self.connect(self.url)
        try:
            self.assertIn("ingest_tokens", sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    # -- functional round trip ------------------------------------------------

    def _insert_agent(self, conn) -> int:
        if self.dialect == "postgresql":
            return conn.execute(
                sa.text(
                    "INSERT INTO agents (name, agent_type) VALUES ('hermes', 'hermes') "
                    "RETURNING id"
                )
            ).scalar()
        conn.execute(
            sa.text("INSERT INTO agents (name, agent_type) VALUES ('hermes', 'hermes')")
        )
        return conn.execute(sa.text("SELECT last_insert_rowid()")).scalar()

    def test_event_round_trip_and_cascade_delete(self) -> None:
        with self.engine.begin() as conn:
            agent_id = self._insert_agent(conn)
            self.assertIsInstance(agent_id, int)

            conn.execute(
                sa.text(
                    "INSERT INTO events (agent_id, event_type, payload) "
                    "VALUES (:agent_id, 'state_change', :payload)"
                ),
                {"agent_id": agent_id, "payload": json.dumps(SAMPLE_PAYLOAD)},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO sessions (agent_id, started_at) "
                    "VALUES (:agent_id, :started)"
                ),
                {"agent_id": agent_id, "started": "2026-10-12 20:00:00"},
            )

            stored = conn.execute(
                sa.text("SELECT payload FROM events WHERE agent_id = :agent_id"),
                {"agent_id": agent_id},
            ).scalar()
            if isinstance(stored, str):
                stored = json.loads(stored)
            self.assertEqual(stored, SAMPLE_PAYLOAD)

            latest = conn.execute(
                sa.text(
                    "SELECT id, created_at FROM events WHERE agent_id = :agent_id "
                    "ORDER BY created_at DESC LIMIT 1"
                ),
                {"agent_id": agent_id},
            ).first()
            self.assertIsNotNone(latest, "created_at ordering found nothing")

            conn.execute(
                sa.text("DELETE FROM agents WHERE id = :agent_id"), {"agent_id": agent_id}
            )
            for table in ("events", "sessions"):
                left = conn.execute(
                    sa.text(f"SELECT count(*) FROM {table} WHERE agent_id = :agent_id"),
                    {"agent_id": agent_id},
                ).scalar()
                self.assertEqual(left, 0, f"{table} rows survived the cascade delete")


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
class SqliteInitialSchemaTest(_InitialSchemaContract, unittest.TestCase):
    """SQLite fallback backend: TEXT payload, AUTOINCREMENT row ids."""

    dialect = "sqlite"
    json_payload_type = TEXT_TYPE

    def database_url(self, workdir: str) -> str:
        return f"sqlite:///{os.path.join(workdir, 'sou.db')}"

    def connect(self, url: str):
        engine = sa.create_engine(url)

        @sa.event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "set TEST_POSTGRES_URL to run the Postgres leg (SERIAL + JSONB)",
)
class PostgresInitialSchemaTest(_InitialSchemaContract, unittest.TestCase):
    """Postgres primary backend: JSONB payload, SERIAL row ids."""

    dialect = "postgresql"
    json_payload_type = JSONB_TYPE

    def database_url(self, _workdir: str) -> str:
        return os.environ["TEST_POSTGRES_URL"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
