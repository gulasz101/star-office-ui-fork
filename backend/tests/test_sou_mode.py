"""SOU_MODE faces (SOU-220, Phase 13 P13-C — docs/public-lodge.md §1).

One process, one image, three faces:

* ``SOU_MODE=public`` — the internet-facing viewer. Registers **only**
  ``GET /health``, ``GET /v3/*``, ``GET /public/feed`` and
  ``GET /public/agents``. Every v1 route, every write and
  ``/events/recent`` are never registered: they answer 404 because they
  do not exist in this process. The public reads return the P13-A
  projection (no ``id``, payload re-sanitised at the door), cache
  headers on 2xx, and no CORS headers at all (same-origin).
* ``SOU_MODE=ingest`` — the private agent door. ``POST /events`` (tokens
  mandatory: the face refuses to start with no ``SOU_INGEST_TOKENS``),
  ``/events/recent``, ``/events/agents``, ``/health``. No v1.
* ``SOU_MODE=dev`` (default, and unset) — today's app, unchanged: the
  42-rule master@9ea4163 snapshot, rule-for-rule and method-for-method.

Slices:
  S1 (SOU-250): pure ``backend/sou_mode.py`` — mode resolution and the
      three route allow-lists. Unit-tested here, in-process.
  S2 (SOU-251): ``backend/db.py`` ``read_only`` engine — the public face
      never writes to the DB.
  S3 (SOU-252): ``app.py`` wiring — gated registration per face,
      ``/public/feed`` + ``/public/agents``, enforcement and behaviour,
      proved by booting real ``app`` instances in subprocesses and
      enumerating ``app.url_map`` against the allow-lists.

Run from the repo root::

    uv run pytest backend/tests/test_sou_mode.py -v
"""

from __future__ import annotations

import os
import sys
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import sou_mode


class ModeResolutionTest(unittest.TestCase):
    """AC4: dev is the default; unknown values fail fast, never silently dev."""

    def test_unset_or_empty_is_dev(self) -> None:
        for raw in (None, "", "   ", "\n"):
            self.assertEqual(sou_mode.resolve_mode(raw), "dev", repr(raw))

    def test_known_values_normalise(self) -> None:
        self.assertEqual(sou_mode.resolve_mode("public"), "public")
        self.assertEqual(sou_mode.resolve_mode("INGEST"), "ingest")
        self.assertEqual(sou_mode.resolve_mode(" Dev "), "dev")

    def test_unknown_value_raises(self) -> None:
        # A typo like "pubic" must never quietly open the dev face to the
        # internet: the process refuses to guess.
        with self.assertRaises(ValueError) as ctx:
            sou_mode.resolve_mode("pubic")
        self.assertIn("SOU_MODE", str(ctx.exception))
        for mode in sou_mode.MODES:
            self.assertIn(mode, str(ctx.exception))


class AllowListTest(unittest.TestCase):
    """AC1/AC3: the public and ingest faces are exactly their allow-lists."""

    def test_public_rules(self) -> None:
        self.assertEqual(
            set(sou_mode.PUBLIC_RULES),
            {
                "/health",
                "/v3",
                "/v3/",
                "/v3/<path:filename>",
                "/public/feed",
                "/public/agents",
            },
        )

    def test_ingest_rules(self) -> None:
        self.assertEqual(
            set(sou_mode.INGEST_RULES),
            {"/health", "/events", "/events/recent", "/events/agents"},
        )

    def test_faces_share_only_health_and_v3_reads_exist_on_public_only(self) -> None:
        self.assertEqual(set(sou_mode.PUBLIC_RULES) & set(sou_mode.INGEST_RULES), {"/health"})
        for rule in ("/public/feed", "/public/agents", "/v3/<path:filename>"):
            self.assertIn(rule, sou_mode.PUBLIC_RULES)
            self.assertNotIn(rule, sou_mode.INGEST_RULES)
        for rule in ("/events", "/events/recent", "/events/agents"):
            self.assertIn(rule, sou_mode.INGEST_RULES)
            self.assertNotIn(rule, sou_mode.PUBLIC_RULES)

    def test_rule_allowed(self) -> None:
        self.assertTrue(sou_mode.rule_allowed("public", "/public/feed"))
        self.assertFalse(sou_mode.rule_allowed("public", "/status"))
        self.assertFalse(sou_mode.rule_allowed("public", "/events"))
        self.assertFalse(sou_mode.rule_allowed("public", "/static/<path:filename>"))
        self.assertTrue(sou_mode.rule_allowed("ingest", "/events"))
        self.assertFalse(sou_mode.rule_allowed("ingest", "/public/feed"))
        self.assertFalse(sou_mode.rule_allowed("ingest", "/"))
        # dev registers today's surface and nothing newer (AC4)
        self.assertTrue(sou_mode.rule_allowed("dev", "/status"))
        self.assertTrue(sou_mode.rule_allowed("dev", "/static/<path:filename>"))
        self.assertFalse(sou_mode.rule_allowed("dev", "/public/feed"))
        self.assertFalse(sou_mode.rule_allowed("dev", "/public/agents"))

    def test_dev_rules_are_the_master_snapshot(self) -> None:
        # AC4 proof part 1: sou_mode's dev list equals the url_map snapshot
        # captured from master@9ea4163 (SOU-220 S3 proves the running app
        # equals this list rule-for-rule, method-for-method).
        self.assertEqual(
            [d["rule"] for d in sou_mode.DEV_ROUTES],
            [d["rule"] for d in MASTER_42],
        )
        self.assertEqual(
            {(d["rule"], d["endpoint"], tuple(d["methods"])) for d in sou_mode.DEV_ROUTES},
            {(d["rule"], d["endpoint"], tuple(d["methods"])) for d in MASTER_42},
        )
        self.assertEqual(len(sou_mode.DEV_ROUTES), 42)


# The url_map of master@9ea4163 (b4d40f8 audit said 40 routes; v3/agents
# work grew it to 42 rule+endpoint pairs over 39 distinct rules). Captured
# with `app.url_map.iter_rules()`, methods excluding the automatic OPTIONS.
# This literal is the AC4 contract: SOU_MODE=dev must still produce exactly
# this map. If a ticket deliberately adds a route, it updates this list in
# the same commit — that friction is the point.
MASTER_42 = [
    {"rule": '/', "endpoint": 'index', "methods": ['GET', 'HEAD']},
    {"rule": '/agent-approve', "endpoint": 'agent_approve', "methods": ['POST']},
    {"rule": '/agent-push', "endpoint": 'agent_push', "methods": ['POST']},
    {"rule": '/agent-reject', "endpoint": 'agent_reject', "methods": ['POST']},
    {"rule": '/agents', "endpoint": 'get_agents', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/auth', "endpoint": 'assets_auth', "methods": ['POST']},
    {"rule": '/assets/auth/status', "endpoint": 'assets_auth_status', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/defaults', "endpoint": 'assets_defaults_get', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/defaults', "endpoint": 'assets_defaults_set', "methods": ['POST']},
    {"rule": '/assets/generate-rpg-background', "endpoint": 'assets_generate_rpg_background', "methods": ['POST']},
    {"rule": '/assets/generate-rpg-background/poll', "endpoint": 'assets_generate_rpg_background_poll', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/home-favorites/apply', "endpoint": 'assets_home_favorites_apply', "methods": ['POST']},
    {"rule": '/assets/home-favorites/delete', "endpoint": 'assets_home_favorites_delete', "methods": ['POST']},
    {"rule": '/assets/home-favorites/file/<path:filename>', "endpoint": 'assets_home_favorites_file', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/home-favorites/list', "endpoint": 'assets_home_favorites_list', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/home-favorites/save-current', "endpoint": 'assets_home_favorites_save_current', "methods": ['POST']},
    {"rule": '/assets/list', "endpoint": 'assets_list', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/positions', "endpoint": 'assets_positions_get', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/positions', "endpoint": 'assets_positions_set', "methods": ['POST']},
    {"rule": '/assets/restore-default', "endpoint": 'assets_restore_default', "methods": ['POST']},
    {"rule": '/assets/restore-last-generated-background', "endpoint": 'assets_restore_last_generated_background', "methods": ['POST']},
    {"rule": '/assets/restore-prev', "endpoint": 'assets_restore_prev', "methods": ['POST']},
    {"rule": '/assets/restore-reference-background', "endpoint": 'assets_restore_reference_background', "methods": ['POST']},
    {"rule": '/assets/template.zip', "endpoint": 'assets_template_download', "methods": ['GET', 'HEAD']},
    {"rule": '/assets/upload', "endpoint": 'assets_upload', "methods": ['POST']},
    {"rule": '/config/gemini', "endpoint": 'gemini_config_get', "methods": ['GET', 'HEAD']},
    {"rule": '/config/gemini', "endpoint": 'gemini_config_set', "methods": ['POST']},
    {"rule": '/electron-standalone', "endpoint": 'electron_standalone_page', "methods": ['GET', 'HEAD']},
    {"rule": '/events', "endpoint": 'events_endpoint', "methods": ['POST']},
    {"rule": '/events/agents', "endpoint": 'events_agents_endpoint', "methods": ['GET', 'HEAD']},
    {"rule": '/events/recent', "endpoint": 'events_recent_endpoint', "methods": ['GET', 'HEAD']},
    {"rule": '/health', "endpoint": 'health', "methods": ['GET', 'HEAD']},
    {"rule": '/invite', "endpoint": 'invite_page', "methods": ['GET', 'HEAD']},
    {"rule": '/join', "endpoint": 'join_page', "methods": ['GET', 'HEAD']},
    {"rule": '/join-agent', "endpoint": 'join_agent', "methods": ['POST']},
    {"rule": '/leave-agent', "endpoint": 'leave_agent', "methods": ['POST']},
    {"rule": '/set_state', "endpoint": 'set_state_endpoint', "methods": ['POST']},
    {"rule": '/static/<path:filename>', "endpoint": 'static', "methods": ['GET', 'HEAD']},
    {"rule": '/status', "endpoint": 'get_status', "methods": ['GET', 'HEAD']},
    {"rule": '/v3', "endpoint": 'v3_renderer', "methods": ['GET', 'HEAD']},
    {"rule": '/v3/', "endpoint": 'v3_renderer', "methods": ['GET', 'HEAD']},
    {"rule": '/v3/<path:filename>', "endpoint": 'v3_renderer', "methods": ['GET', 'HEAD']},
]


class ReadonlyEngineTest(unittest.TestCase):
    """AC5: the public face's engine (SOU-251, S2 half).

    ``db.create_db_engine(url, read_only=True)`` must make every
    connection refuse writes while reading normally: SQLite gets
    ``PRAGMA query_only=ON``, Postgres gets a session-level
    ``default_transaction_read_only = on``. The URL can also arrive
    pointing at a read-only role — that half is the operator's; this is
    the in-app guard for when it doesn't.
    """

    def setUp(self) -> None:
        import tempfile
        import db

        self.db = db
        self.workdir = tempfile.mkdtemp(prefix="sou220-ro-")
        self.url = f"sqlite:///{os.path.join(self.workdir, 'ro.db')}"
        self.orig_env = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = self.url

    def tearDown(self) -> None:
        # leave no sticky read-only default behind for other tests
        self.db.init(self.db.resolve_database_url(), read_only=False)
        self.db.shutdown()
        if self.orig_env is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.orig_env

    def _sql(self, engine, text):
        import sqlalchemy as sa

        with engine.connect() as conn:
            return conn.execute(sa.text(text)).fetchall()

    def _execute(self, engine, text):
        """Run a statement that returns nothing (DDL/DML), committing it."""
        import sqlalchemy as sa

        with engine.begin() as conn:
            conn.execute(sa.text(text))

    def test_sqlite_read_only_refuses_writes_and_still_reads(self) -> None:
        engine = self.db.create_db_engine(self.url, read_only=True)
        try:
            self.assertEqual(self._sql(engine, "SELECT 1"), [(1,)])
            with self.assertRaises(Exception) as ctx:
                self._sql(engine, "CREATE TABLE canary (x INTEGER)")
            self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            engine.dispose()
        # nothing was actually written — not even the failed table
        import sqlalchemy as sa

        self.assertNotIn("canary", sa.inspect(engine).get_table_names())

    def test_default_engine_still_writable(self) -> None:
        import sqlalchemy as sa

        engine = self.db.create_db_engine(self.url)
        try:
            self._execute(engine, "CREATE TABLE writable_check (x INTEGER)")
            self.assertIn("writable_check", sa.inspect(engine).get_table_names())
        finally:
            engine.dispose()

    def test_init_read_only_is_sticky_for_lazy_reinit(self) -> None:
        import sqlalchemy as sa

        # real schema so the ORM write path (not "no such table") is what
        # the read-only guard refuses
        writable = self.db.create_db_engine(self.url)
        self.db.Base.metadata.create_all(writable)
        writable.dispose()

        self.db.init(self.url, read_only=True)
        try:
            with self.db.session_scope() as session:
                self.assertEqual(session.execute(sa.text("SELECT 1")).scalar(), 1)
            with self.assertRaises(Exception) as ctx:
                with self.db.session_scope() as session:
                    self.db.insert_agent(session, name="canary", agent_type="external")
            self.assertIn("readonly", str(ctx.exception).lower())
        finally:
            self.db.shutdown()
        # shutdown() + lazy get_engine() must reopen the same read-only guard
        engine = self.db.get_engine()
        with self.assertRaises(Exception):
            self._execute(engine, "CREATE TABLE sticky_check (x INTEGER)")
        # and an explicit read_only=False resets the default (dev face)
        self.db.init(self.url, read_only=False)
        self.db.shutdown()
        engine = self.db.get_engine()
        self._execute(engine, "CREATE TABLE writable_again (x INTEGER)")
        self.assertIn("writable_again", sa.inspect(engine).get_table_names())

    @unittest.skipIf(
        not os.environ.get("TEST_POSTGRES_URL"),
        "no live Postgres (set TEST_POSTGRES_URL to run)",
    )
    def test_postgres_read_only_guard(self) -> None:  # pragma: no cover - needs server
        import sqlalchemy as sa

        url = os.environ["TEST_POSTGRES_URL"]
        engine = self.db.create_db_engine(url, read_only=True)
        try:
            rows = self._sql(engine, "SELECT current_setting('default_transaction_read_only')")
            self.assertEqual(rows, [("on",)])
            with self.assertRaises(sa.exc.InternalError):
                self._sql(engine, "CREATE TABLE sou220_canary (x INTEGER)")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
