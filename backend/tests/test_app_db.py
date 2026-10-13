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

# In-process app import (AC1/AC2 roster + state tests below). The module is
# imported once against a throwaway DATABASE_URL; per-test setUp re-points
# the adapter at a fresh file and re-runs init_database(), which is
# idempotent by design.
app_mod = None
_APP_IMPORT_URL = None
_ORIG_ENV = {}


def setUpModule() -> None:
    global app_mod, _APP_IMPORT_URL
    if sa is None:
        return
    _ORIG_ENV.update(
        {k: os.environ.get(k) for k in ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")}
    )
    workdir = tempfile.mkdtemp(prefix="sou19-module-")
    _APP_IMPORT_URL = f"sqlite:///{os.path.join(workdir, 'module.db')}"
    os.environ["DATABASE_URL"] = _APP_IMPORT_URL
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    if BACKEND_DIR not in sys.path:
        sys.path.insert(0, BACKEND_DIR)
    import app  # noqa: F401  (triggers the startup path under test)
    app_mod = app


def tearDownModule() -> None:
    for key, value in _ORIG_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _fresh_app_database() -> str:
    """Point the imported app at a brand-new SQLite file and re-seed it."""
    workdir = tempfile.mkdtemp(prefix="sou19-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
    app_mod.db.shutdown()
    app_mod.init_database()
    return workdir


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


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class AgentsRosterDbTest(unittest.TestCase):
    """AC1 (SOU-19): the agents roster is served by the adapter.

    Replaces the agents-state.json read/write behind load_agents_state()/
    save_agents_state(); every roster endpoint (agents, join-agent,
    agent-approve, agent-push, leave-agent, agent-reject) must keep its
    HTTP contract while the JSON file stops existing as a code path.
    """

    def setUp(self) -> None:
        self.workdir = _fresh_app_database()
        self.client = app_mod.app.test_client()
        self._orig_join_keys_file = app_mod.JOIN_KEYS_FILE
        self.join_keys_file = os.path.join(self.workdir, "join-keys.json")
        with open(self.join_keys_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "keys": [
                        {
                            "key": "k1",
                            "used": False,
                            "reusable": True,
                            "maxConcurrent": 3,
                            "usedBy": None,
                            "usedByAgentId": None,
                            "usedAt": None,
                        }
                    ]
                },
                f,
            )
        app_mod.JOIN_KEYS_FILE = self.join_keys_file

    def tearDown(self) -> None:
        app_mod.JOIN_KEYS_FILE = self._orig_join_keys_file
        app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _agents(self):
        return self.client.get("/agents").get_json()

    def _db_names(self):
        with app_mod.db.session_scope() as session:
            return sorted(a.name for a in app_mod.db.list_agents(session))

    def _state_events(self, agent_id: str):
        with app_mod.db.session_scope() as session:
            row = next(
                (a for a in app_mod.db.list_agents(session) if str(a.id) == agent_id),
                None,
            )
            if row is None:
                return []
            return list(
                app_mod.db.list_events(session, agent_id=row.id, event_type="state_change")
            )

    def test_app_source_drops_the_agents_json_file(self) -> None:
        with open(os.path.join(BACKEND_DIR, "app.py"), encoding="utf-8") as f:
            src = f.read()
        for needle in (
            "agents-state.json",
            "AGENTS_STATE_FILE",
            "_store_load_agents_state",
            "_store_save_agents_state",
        ):
            self.assertNotIn(needle, src, f"app.py must not keep {needle}")

    def test_roster_roundtrip_join_push_approve_leave(self) -> None:
        agents = self._agents()
        self.assertEqual([a["agentId"] for a in agents], ["star"])
        self.assertEqual(agents[0]["state"], "idle")
        self.assertTrue(agents[0]["isMain"])

        joined = self.client.post(
            "/join-agent",
            json={"name": "hermes", "joinKey": "k1", "state": "writing", "detail": "drafting"},
        ).get_json()
        self.assertTrue(joined["ok"], joined)
        self.assertEqual(joined["authStatus"], "approved")
        agent_id = joined["agentId"]

        hermes = next(a for a in self._agents() if a["name"] == "hermes")
        self.assertEqual(hermes["agentId"], agent_id)
        self.assertEqual(hermes["state"], "writing")
        self.assertEqual(hermes["area"], "writing")
        self.assertEqual(hermes["authStatus"], "approved")
        self.assertFalse(hermes["isMain"])
        self.assertEqual(self._db_names(), ["Star", "hermes"])

        pushed = self.client.post(
            "/agent-push",
            json={"agentId": agent_id, "joinKey": "k1", "state": "executing", "detail": "running"},
        ).get_json()
        self.assertTrue(pushed["ok"], pushed)
        hermes = next(a for a in self._agents() if a["name"] == "hermes")
        self.assertEqual(hermes["state"], "executing")
        self.assertIsNotNone(hermes["lastPushAt"])

        approved = self.client.post("/agent-approve", json={"agentId": agent_id}).get_json()
        self.assertTrue(approved["ok"], approved)

        left = self.client.post("/leave-agent", json={"agentId": agent_id}).get_json()
        self.assertTrue(left["ok"], left)
        self.assertEqual(self._db_names(), ["Star"], "leave must remove the registry row")
        self.assertNotIn("hermes", {a["name"] for a in self._agents()})

        keys = json.load(open(self.join_keys_file, encoding="utf-8"))["keys"][0]
        self.assertFalse(keys["used"], "leave must free the join key as before")

    def test_reject_removes_the_row(self) -> None:
        joined = self.client.post(
            "/join-agent",
            json={"name": "szarik", "joinKey": "k1", "state": "idle", "detail": ""},
        ).get_json()
        self.assertTrue(joined["ok"], joined)
        rejected = self.client.post(
            "/agent-reject", json={"agentId": joined["agentId"]}
        ).get_json()
        self.assertTrue(rejected["ok"], rejected)
        self.assertEqual(self._db_names(), ["Star"])

    def test_page_refresh_does_not_grow_the_event_feed(self) -> None:
        # GET /agents still persists (auto-cleanup), but the DB write must be
        # a no-op when the roster did not change: one join => one state_change,
        # however many refreshes follow.
        joined = self.client.post(
            "/join-agent",
            json={"name": "quiet", "joinKey": "k1", "state": "idle", "detail": ""},
        ).get_json()
        self.assertTrue(joined["ok"], joined)
        for _ in range(3):
            self._agents()
        events = self._state_events(joined["agentId"])
        self.assertEqual(len(events), 1, [e.payload for e in events])
        self.assertEqual(events[0].payload["state"], "idle")

    def test_state_changes_do_write_events(self) -> None:
        joined = self.client.post(
            "/join-agent",
            json={"name": "pushy", "joinKey": "k1", "state": "idle", "detail": "a"},
        ).get_json()
        self.client.post(
            "/agent-push",
            json={"agentId": joined["agentId"], "joinKey": "k1", "state": "writing", "detail": "b"},
        )
        self.client.post(
            "/agent-push",
            json={"agentId": joined["agentId"], "joinKey": "k1", "state": "executing", "detail": "c"},
        )
        events = self._state_events(joined["agentId"])
        self.assertEqual(len(events), 3, [e.payload for e in events])
        self.assertEqual(events[0].payload["state"], "executing", "newest first")


if __name__ == "__main__":
    unittest.main(verbosity=2)
