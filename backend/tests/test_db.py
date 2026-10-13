"""Dual-backend contract for the database adapter (SOU-18, ADR-034).

The adapter in ``backend/db.py`` must behave identically on Postgres (the
homelab primary) and SQLite (the local-dev fallback). Like the SOU-17 schema
contract in ``tests/test_migrations.py``, one shared test class runs against
both backends; only the *storage* of the JSON payload differs, and asserting
that difference is the whole point of the dual-backend slice:

* Postgres: ``events.payload`` is a real ``JSONB`` column, verified natively
  through jsonb operators.
* SQLite:   ``events.payload`` is ``TEXT`` holding the JSON string, parsed
  back into a dict by the adapter on read.

Run from ``backend/``::

    python3 -m pytest tests.test_db -v                     # SQLite leg
    python3 -m unittest tests.test_db -v                   # same tests, no pytest

The Postgres leg needs a reachable server and is skipped when
``TEST_POSTGRES_URL`` is unset (start one with ``scripts/dev_postgres.sh``)::

    TEST_POSTGRES_URL=postgresql+psycopg2://postgres@127.0.0.1:55432/sou \
        python3 -m pytest tests.test_db -v

When the database extras (alembic / SQLAlchemy / psycopg2) are not installed
— the Flask-only install path — the module skips itself instead of erroring.
Once they are installed, importing ``db`` is deliberate and unguarded: that
import failing IS the signal that the adapter does not exist yet.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")

try:
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.orm import Session as SASession
except ImportError as exc:  # pragma: no cover - only hit without the DB extras
    sa = None
    command = None
    Config = None
    postgresql = None
    IntegrityError = None
    SASession = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None
    # The adapter under test. With the extras present, a missing backend/db.py
    # must fail collection loudly — that is the test, not an accident.
    import db as sou_db  # noqa: E402  (the module under test)

# Guarded because the class bodies below are evaluated at import time, before
# any skipIf can run: without the DB extras these stay empty and the module
# still imports, so the suite reports a clean skip instead of an ImportError.
TEXT_TYPE = (sa.Text,) if sa is not None else ()
JSONB_TYPE = (postgresql.JSONB,) if postgresql is not None else ()

# ADR-035 state_change payload, unicode included: what events must survive.
SAMPLE_PAYLOAD = {
    "state": "writing",
    "detail": "正在整理文档",
    "progress": 0.5,
    "nested": {"tools": ["read", "edit"], "ok": True, "nothing": None},
}

BASE_TIME = dt.datetime(2026, 10, 13, 9, 0, 0)


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


def _alembic_config(url: str) -> "Config":
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    return config


def _run_alembic(url: str, action: str, revision: str) -> None:
    """Drive Alembic through its Python API with one URL, whatever the CWD."""
    with _alembic_env(url):
        getattr(command, action)(_alembic_config(url), revision)


@contextlib.contextmanager
def _env(**values):
    """Temporarily set (or with None, clear) environment variables."""
    previous = {key: os.environ.get(key) for key in values}
    for key, value in values.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class _AdapterContract:
    """Assertions shared by both backends; subclasses fix the URL and dialect."""

    dialect: str = ""
    json_payload_type: tuple = ()

    # -- fixture: schema from the migrations, adapter fresh against it --------

    def setUp(self) -> None:
        self.workdir = tempfile.mkdtemp(prefix="sou-db-")
        self.url = self.database_url(self.workdir)
        _run_alembic(self.url, "downgrade", "base")
        _run_alembic(self.url, "upgrade", "head")
        self.engine = sou_db.init(self.url)
        self.db = sou_db.get_session()
        self.insp = sa.inspect(self.engine)

    def tearDown(self) -> None:
        self.db.close()
        sou_db.shutdown()
        self.engine.dispose()
        with contextlib.suppress(Exception):
            _run_alembic(self.url, "downgrade", "base")
        shutil.rmtree(self.workdir, ignore_errors=True)

    def database_url(self, workdir: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def raw(self):
        """A plain DBAPI-level connection: no ORM type magic, storage as written."""
        return self.engine.connect()

    # -- AC1: connection detection ---------------------------------------------

    def test_falls_back_to_sqlite_without_database_url(self) -> None:
        with _env(DATABASE_URL=None):
            url = sou_db.resolve_database_url()
        self.assertTrue(url.startswith("sqlite:///"), f"got {url!r}")
        path = url[len("sqlite:///") :]
        self.assertTrue(
            os.path.isabs(path) and path == os.path.join(BACKEND_DIR, "sou.db"),
            f"fallback must anchor sqlite:///sou.db at backend/, got {url!r}",
        )
        self.assertEqual(sou_db.backend_name(url), "sqlite")

    def test_database_url_env_wins_and_is_passed_through(self) -> None:
        candidate = "postgresql+psycopg2://sou:sou@db.internal:5432/sou"
        with _env(DATABASE_URL=candidate):
            self.assertEqual(sou_db.resolve_database_url(), candidate)
            self.assertEqual(sou_db.backend_name(sou_db.resolve_database_url()), "postgres")

    def test_relative_sqlite_in_database_url_is_anchored_at_backend(self) -> None:
        # Same rule as migrations/env.py, so Alembic and the adapter open the
        # very same file no matter the current directory.
        with _env(DATABASE_URL="sqlite:///sou.db"):
            url = sou_db.resolve_database_url()
        self.assertEqual(
            url, f"sqlite:///{os.path.join(BACKEND_DIR, 'sou.db')}"
        )

    def test_empty_database_url_falls_back_like_unset(self) -> None:
        with _env(DATABASE_URL=""):
            self.assertTrue(sou_db.resolve_database_url().startswith("sqlite:///"))

    # -- AC1: engine + session factory ------------------------------------------

    def test_engine_and_session_factory_are_live(self) -> None:
        engine = sou_db.get_engine()
        self.assertIsInstance(engine, sa.engine.Engine)
        self.assertEqual(sa.engine.url.make_url(str(engine.url)), sa.engine.url.make_url(self.url))
        self.assertIsInstance(self.db, SASession)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute(sa.text("SELECT 1")).scalar(), 1)

    def _agent_names(self, session) -> list:
        rows = session.execute(sa.select(sou_db.Agent)).scalars().all()
        return [row.name for row in rows]

    def test_session_scope_commits_on_success(self) -> None:
        with sou_db.session_scope() as scope:
            sou_db.insert_agent(scope, name="scoped", agent_type="opencode")
        self.assertIn("scoped", self._agent_names(self.db), "session_scope() did not commit its work")

    def test_session_scope_rolls_back_on_exception(self) -> None:
        with contextlib.suppress(RuntimeError), sou_db.session_scope() as scope:
            sou_db.insert_agent(scope, name="ghost", agent_type="opencode")
            raise RuntimeError("boom")
        self.assertNotIn(
            "ghost", self._agent_names(self.db),
            "session_scope() committed despite the exception",
        )

    # -- AC1/AC2: insert_agent + get_agent ---------------------------------------

    def test_insert_agent_returns_row_with_id_and_defaults(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        self.db.commit()
        self.assertIsInstance(agent.id, int)
        self.assertGreater(agent.id, 0)

        fetched = sou_db.get_agent(self.db, agent.id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.name, "hermes")
        self.assertEqual(fetched.agent_type, "hermes")
        self.assertIsInstance(fetched.created_at, dt.datetime)

    def test_get_agent_missing_returns_none(self) -> None:
        self.assertIsNone(sou_db.get_agent(self.db, 10_999))

    # -- AC2: insert_event + payload dialect handling ----------------------------

    def test_event_roundtrip_survives_nested_unicode_payload(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        event = sou_db.insert_event(
            self.db, agent.id, "state_change", SAMPLE_PAYLOAD, created_at=BASE_TIME
        )
        self.db.commit()

        (stored,) = sou_db.list_events(self.db, agent_id=agent.id)
        self.assertEqual(stored.id, event.id)
        self.assertEqual(stored.event_type, "state_change")
        self.assertEqual(stored.payload, SAMPLE_PAYLOAD)
        self.assertIsInstance(stored.created_at, dt.datetime)

    def test_payload_storage_matches_the_backend(self) -> None:
        """The split ADR-034 exists for: JSONB on Postgres, TEXT on SQLite."""
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        sou_db.insert_event(self.db, agent.id, "state_change", SAMPLE_PAYLOAD, created_at=BASE_TIME)
        self.db.commit()

        column = {c["name"]: c for c in self.insp.get_columns("events")}["payload"]
        self.assertIsInstance(column["type"], self.json_payload_type)

        with self.raw() as conn:
            if self.dialect == "postgresql":
                native = conn.execute(
                    sa.text(
                        "SELECT payload ->> 'detail' FROM events WHERE agent_id = :agent_id"
                    ),
                    {"agent_id": agent.id},
                ).scalar()
                self.assertEqual(
                    native,
                    SAMPLE_PAYLOAD["detail"],
                    "->> only works on a real jsonb column",
                )
            else:
                raw_value = conn.execute(
                    sa.text("SELECT payload FROM events WHERE agent_id = :agent_id"),
                    {"agent_id": agent.id},
                ).scalar()
                self.assertIsInstance(
                    raw_value, str, "SQLite leg must store the JSON as TEXT"
                )
                self.assertEqual(json.loads(raw_value), SAMPLE_PAYLOAD)

    # -- AC2: insert_session -------------------------------------------------------

    def test_session_row_roundtrip_and_explicit_times(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        row = sou_db.insert_session(
            self.db, agent.id, started_at=BASE_TIME, ended_at=BASE_TIME + dt.timedelta(minutes=42)
        )
        self.db.commit()

        loaded = self.db.get(type(row), row.id)
        self.assertEqual(loaded.agent_id, agent.id)
        self.assertEqual(loaded.started_at, BASE_TIME)
        self.assertEqual(loaded.ended_at, BASE_TIME + dt.timedelta(minutes=42))

    def test_session_defaults_started_at_and_allows_open_ended(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        row = sou_db.insert_session(self.db, agent.id)
        self.db.commit()
        loaded = self.db.get(type(row), row.id)
        self.assertIsInstance(loaded.started_at, dt.datetime)
        self.assertLess(abs(loaded.started_at - dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)), dt.timedelta(minutes=5))
        self.assertIsNone(loaded.ended_at)

    # -- AC1: list_events ------------------------------------------------------------

    def test_list_events_newest_first_with_filter_and_limit(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        other = sou_db.insert_agent(self.db, name="szarik", agent_type="opencode")
        sou_db.insert_event(self.db, agent.id, "state_change", {"state": "idle"}, created_at=BASE_TIME)
        sou_db.insert_event(self.db, agent.id, "tool_call", {"tool": "read"}, created_at=BASE_TIME + dt.timedelta(hours=1))
        sou_db.insert_event(self.db, agent.id, "file_edit", {"path": "db.py"}, created_at=BASE_TIME + dt.timedelta(hours=2))
        sou_db.insert_event(self.db, other.id, "tool_call", {"tool": "edit"}, created_at=BASE_TIME + dt.timedelta(hours=3))
        self.db.commit()

        ordered = sou_db.list_events(self.db, agent_id=agent.id)
        self.assertEqual(
            [e.event_type for e in ordered], ["file_edit", "tool_call", "state_change"]
        )
        self.assertEqual(
            [e.event_type for e in sou_db.list_events(self.db, agent_id=agent.id, event_type="tool_call")],
            ["tool_call"],
        )
        self.assertEqual(
            [e.event_type for e in sou_db.list_events(self.db, agent_id=agent.id, limit=1)],
            ["file_edit"],
        )
        every = sou_db.list_events(self.db)
        self.assertEqual(len(every), 4, "no agent filter must return every agent's events")
        self.assertEqual(every[0].agent_id, other.id, "global list must be newest first too")
        self.assertEqual(
            sou_db.list_events(self.db, agent_id=10_999), [], "unknown agent has no events"
        )

    def test_list_events_descends_before_created_at(self) -> None:
        # The idx_events_agent_created index is (agent_id, created_at DESC);
        # the read path must match it without an ORDER BY hint from the caller.
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        for hours in (0, 5, 11):
            sou_db.insert_event(
                self.db, agent.id, "tool_call", {"n": hours}, created_at=BASE_TIME + dt.timedelta(hours=hours)
            )
        self.db.commit()
        stamps = [e.created_at for e in sou_db.list_events(self.db, agent_id=agent.id)]
        self.assertEqual(stamps, sorted(stamps, reverse=True))

    # -- AC1: list_agents_active ------------------------------------------------------

    def test_list_agents_active_uses_event_window(self) -> None:
        busy = sou_db.insert_agent(self.db, name="busy-hermes", agent_type="hermes")
        stale = sou_db.insert_agent(self.db, name="stale-hermes", agent_type="hermes")
        quiet = sou_db.insert_agent(self.db, name="quiet-hermes", agent_type="hermes")
        now = BASE_TIME
        sou_db.insert_event(self.db, busy.id, "state_change", {"state": "writing"}, created_at=now - dt.timedelta(minutes=1))
        sou_db.insert_event(self.db, busy.id, "tool_call", {"tool": "read"}, created_at=now - dt.timedelta(minutes=3))
        sou_db.insert_event(self.db, stale.id, "state_change", {"state": "idle"}, created_at=now - dt.timedelta(minutes=30))
        self.db.commit()

        active = sou_db.list_agents_active(self.db, window_minutes=5, now=now)
        names = [a.name for a in active]
        self.assertEqual(names, ["busy-hermes"], "active set drifted from the window")
        # An agent kept alive by two recent events is listed once.
        self.assertEqual(len(active), len(set(a.id for a in active)))
        # Wider window: both agents with recent-enough activity, in id order.
        self.assertEqual(
            [a.name for a in sou_db.list_agents_active(self.db, window_minutes=60, now=now)],
            [busy.name, stale.name],
        )
        self.assertEqual(sou_db.list_agents_active(self.db, window_minutes=0, now=now), [])
        self.assertNotIn("quiet-hermes", names, "agent with no events is never active")

    # -- referential behaviour on the adapter's own tables ------------------------------

    def test_insert_event_for_unknown_agent_is_rejected(self) -> None:
        with self.assertRaises(IntegrityError):
            sou_db.insert_event(self.db, 10_999, "state_change", {"state": "idle"})
            self.db.commit()
        self.db.rollback()

    def test_deleting_an_agent_removes_its_events_and_sessions(self) -> None:
        agent = sou_db.insert_agent(self.db, name="hermes", agent_type="hermes")
        sou_db.insert_event(self.db, agent.id, "state_change", {"state": "idle"})
        sou_db.insert_session(self.db, agent.id, started_at=BASE_TIME)
        self.db.commit()
        agent_id = agent.id

        self.db.delete(agent)
        self.db.commit()

        self.assertEqual(sou_db.list_events(self.db, agent_id=agent_id), [])
        left = self.db.execute(
            sa.text("SELECT count(*) FROM sessions WHERE agent_id = :i"), {"i": agent_id}
        ).scalar()
        self.assertEqual(left, 0, "cascade must reach sessions too")


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
class SqliteAdapterTest(_AdapterContract, unittest.TestCase):
    """SQLite fallback backend: payload stored as TEXT, parsed on read."""

    dialect = "sqlite"
    json_payload_type = TEXT_TYPE

    def database_url(self, workdir: str) -> str:
        return f"sqlite:///{os.path.join(workdir, 'sou.db')}"


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "set TEST_POSTGRES_URL to run the Postgres leg (JSONB payloads)",
)
class PostgresAdapterTest(_AdapterContract, unittest.TestCase):
    """Postgres primary backend: payload stored as native JSONB."""

    dialect = "postgresql"
    json_payload_type = JSONB_TYPE

    def database_url(self, _workdir: str) -> str:
        return os.environ["TEST_POSTGRES_URL"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
