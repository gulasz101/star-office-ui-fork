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

import hashlib
import json
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


# ---------------------------------------------------------------------------
# S3 (SOU-252): face boots. Real `app` imports in subprocesses, so every
# face is proved under the same import-time conditions a deploy sees.
# ---------------------------------------------------------------------------

PROBE_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, os.getcwd())
import app

plan = json.loads(os.environ.get("SOU_PROBE") or "{}")
rules = []
for r in app.app.url_map.iter_rules():
    rules.append({
        "rule": str(r.rule),
        "endpoint": r.endpoint,
        "methods": sorted(m for m in r.methods if m != "OPTIONS"),
    })
rules.sort(key=lambda d: (d["rule"], d["endpoint"], d["methods"]))
client = app.app.test_client()
out = {"mode": getattr(app, "SOU_MODE", None), "rules": rules, "probes": {}}

def snap(label, resp):
    out["probes"][label] = {
        "status": resp.status_code,
        "cache_control": resp.headers.get("Cache-Control"),
        "cors_headers": {k: v for k, v in resp.headers.items()
                         if k.lower().startswith("access-control")},
        "json": resp.get_json(silent=True),
    }

for i, path in enumerate(plan.get("get", [])):
    snap("GET %d %s" % (i, path), client.get(path))
for i, entry in enumerate(plan.get("post", [])):
    snap("POST %d %s" % (i, entry["path"]),
         client.post(entry["path"], json=entry.get("body"), headers=entry.get("headers")))
print(json.dumps(out))
"""

FACES_DB = {}  # workdir + url, built once per run


def _faces_db_url() -> str:
    """One throwaway SQLite file shared by the face subprocesses.

    Migrated + seeded by the first dev boot; a deliberately RAW row is
    written straight through the adapter (bypassing the ingest door) —
    the way a pre-SOU-218 backend stored it — because the public reads
    must project it away migration or not.
    """
    if "url" in FACES_DB:
        return FACES_DB["url"]
    import tempfile

    import sqlalchemy as sa
    from sqlalchemy.orm import sessionmaker

    import db as db_mod

    workdir = tempfile.mkdtemp(prefix="sou220-faces-")
    url = f"sqlite:///{os.path.join(workdir, 'faces.db')}"
    FACES_DB["url"] = url
    FACES_DB["workdir"] = workdir

    # migrate + seed exactly like a dev boot does
    proc = _boot(None, {})
    assert proc.returncode == 0, proc.stderr[-2000:]

    engine = db_mod.create_db_engine(url)
    maker = sessionmaker(bind=engine)
    with maker() as session:
        canary = db_mod.insert_agent(session, name="canary", agent_type="external")
        db_mod.insert_event(session, canary.id, "tool_call", {
            "tool": "Read",
            "detail": "reading /home/operator/notes.md",
            "url": "https://internal.example/api?key=sk-canary-secret",
            "session_id": "sess-canary-42",
            "file": "/home/operator/projects/homelab/private-notes.md",
            "command": "cat /home/operator/.ssh/id_ed25519",
        })
        db_mod.insert_event(session, canary.id, "state_change", {
            "state": "writing",
            "detail": "reviewing id_ed25519 for /home/operator",
            "progress": 3,
        })
        session.commit()
    engine.dispose()
    return url


def _boot(mode, probe, tokens=None):
    """Import backend/app.py in a subprocess under ``SOU_MODE`` and probe it."""
    import subprocess

    env = dict(os.environ)
    env["DATABASE_URL"] = _faces_db_url()
    env["ALEMBIC_SKIP_FILECONFIG"] = "1"
    env["SOU_PROBE"] = json.dumps(probe)
    for drop in ("SOU_MODE", "SOU_INGEST_TOKENS", "OFFICE_NAME"):
        env.pop(drop, None)
    if mode is not None:
        env["SOU_MODE"] = mode
    if tokens is not None:
        env["SOU_INGEST_TOKENS"] = tokens
    return subprocess.run(
        [sys.executable, "-c", PROBE_SCRIPT],
        env=env, cwd=BACKEND_DIR, capture_output=True, text=True, timeout=180,
    )


def _boot_ok(mode, probe, tokens=None):
    proc = _boot(mode, probe, tokens)
    assert proc.returncode == 0, (
        f"{mode} boot failed (rc={proc.returncode}):\nstdout tail: "
        f"{proc.stdout[-1500:]}\nstderr tail: {proc.stderr[-3000:]}"
    )
    # the probe prints its report as the final stdout line; startup chatter
    # (standalone snapshot, resanitise log) rides above it
    return json.loads(proc.stdout.splitlines()[-1])


def valid_envelope() -> dict:
    """A schema-valid ADR-035 tool_call envelope for the hermes token."""
    return {
        "version": 1,
        "agent": "hermes",
        "event_type": "tool_call",
        "timestamp": "2026-10-17T07:00:00Z",
        "payload": {"tool": "Bash", "status": "ok"},
    }


def _rule_triples(report):
    return sorted((d["rule"], d["endpoint"], tuple(d["methods"]))
                  for d in report["rules"])


class PublicFaceTest(unittest.TestCase):
    """AC1 + AC2: the public face is its allow-list and nothing else."""

    GETS = ["/health", "/v3/", "/v3/src/main.js", "/public/feed",
            "/public/agents", "/", "/status", "/agents", "/events/recent",
            "/events/agents", "/static/game.js", "/public/feed?limit=1"]
    POSTS = ["/events", "/set_state", "/agent-push", "/join-agent",
             "/assets/upload"]

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _boot_ok(
            "public",
            {"get": cls.GETS, "post": [{"path": p, "body": {}} for p in cls.POSTS]},
        )
        cls.probes = cls.report["probes"]

    def get(self, path) -> dict:
        return self.probes[f"GET {self.GETS.index(path)} {path}"]

    def post(self, path) -> dict:
        return self.probes[f"POST {self.POSTS.index(path)} {path}"]

    def test_url_map_is_exactly_the_public_allow_list(self) -> None:
        self.assertEqual(self.report["mode"], "public")
        self.assertEqual(sorted(d["rule"] for d in self.report["rules"]),
                         sorted(sou_mode.PUBLIC_RULES))
        for d in self.report["rules"]:
            self.assertLessEqual(set(d["methods"]), {"GET", "HEAD"},
                                 f"{d['rule']} exposes writes on the public face")

    def test_everything_else_is_404_not_forbidden(self) -> None:
        for path in ("/", "/status", "/agents", "/events/recent",
                     "/events/agents", "/static/game.js"):
            self.assertEqual(self.get(path)["status"], 404, path)
        for path in self.POSTS:
            self.assertEqual(self.post(path)["status"], 404, path)

    def test_allowed_reads_work(self) -> None:
        health = self.get("/health")
        self.assertEqual(health["status"], 200)
        self.assertEqual(health["json"]["status"], "ok")
        self.assertEqual(self.get("/v3/")["status"], 200)
        self.assertEqual(self.get("/v3/src/main.js")["status"], 200)

    def test_feed_projection_has_no_secrets_no_id(self) -> None:
        feed = self.get("/public/feed")["json"]
        self.assertIsInstance(feed, list)
        text = json.dumps(feed)
        for leak in ("sk-canary-secret", "sess-canary-42", "/home", "id_ed25519",
                     "internal.example", '"detail"', '"progress"', '"url"', '"id"'):
            self.assertNotIn(leak, text, f"public feed leaked {leak!r}")
        canary = [item for item in feed if item["agent"] == "canary"]
        self.assertTrue(canary, "canary row missing from /public/feed")
        self.assertGreaterEqual(len(canary), 2)
        self.assertIn("tool_call", {item["event_type"] for item in canary})
        for item in canary:
            self.assertEqual(set(item), {"agent", "event_type", "created_at",
                                         "payload"})
            for key in item["payload"]:
                self.assertIn(key, sanitize_allowed(item["event_type"]),
                              f"payload key {key!r} outside the P13-A projection")
        # newest first: paging walks the created_at cursor, no ids needed
        times = [item["created_at"] for item in feed]
        self.assertEqual(times, sorted(times, reverse=True))
        paged = self.get("/public/feed?limit=1")["json"]
        self.assertEqual(len(paged), 1)
        self.assertEqual(paged[0], feed[0])

    def test_cache_headers_and_same_origin_cors(self) -> None:
        feed = self.get("/public/feed")
        self.assertEqual(feed["status"], 200)
        self.assertEqual(feed["cache_control"],
                         "public, max-age=5, must-revalidate")
        self.assertEqual(self.get("/public/agents")["cache_control"],
                         "public, max-age=5, must-revalidate")
        # errors and non-allow-listed paths stay no-store like today
        self.assertIn("no-store",
                      (self.get("/events/recent")["cache_control"] or "").lower())
        for label, probe in self.probes.items():
            self.assertEqual(probe["cors_headers"], {},
                             f"{label} emitted CORS headers")


class PublicAgentsShapeTest(unittest.TestCase):
    """AC2, agents half: /public/agents mirrors /events/agents minus ids."""

    GETS = ["/public/agents", "/public/agents?since=not-a-date"]

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _boot_ok("public", {"get": cls.GETS})
        cls.probes = cls.report["probes"]

    def get(self, path) -> dict:
        return self.probes[f"GET {self.GETS.index(path)} {path}"]

    def test_entries_projected(self) -> None:
        entries = self.get("/public/agents")["json"]
        self.assertIsInstance(entries, list)
        self.assertIn("canary", {e["agent"] for e in entries})
        for e in entries:
            self.assertEqual(set(e), {"agent", "first_seen", "last_seen",
                                      "count", "last_event"})
            self.assertEqual(set(e["last_event"]), {"agent", "event_type",
                                                    "created_at", "payload"})
        text = json.dumps(entries)
        for leak in ('"id"', "sk-canary-secret", "sess-canary-42", "/home"):
            self.assertNotIn(leak, text)

    def test_bad_since_is_400(self) -> None:
        resp = self.get("/public/agents?since=not-a-date")
        self.assertEqual(resp["status"], 400)
        self.assertIs(resp["json"]["ok"], False)
        self.assertIn("since", resp["json"]["error"])


def sanitize_allowed(event_type) -> set:
    """The P13-A key contract, read from its single source (the sanitiser),
    never a copy in the test."""
    import sanitize

    return set(sanitize.allowed_keys(event_type))


class IngestFaceTest(unittest.TestCase):
    """AC3: the ingest face registers the door, and tokens are mandatory."""

    def test_untokened_boot_is_refused(self) -> None:
        proc = _boot("ingest", {})
        self.assertNotEqual(proc.returncode, 0,
                            "ingest face started without tokens!")
        self.assertIn("SOU_INGEST_TOKENS", proc.stderr)

    def test_url_map_and_token_gate(self) -> None:
        token = "sou220-face-token"
        digest = hashlib.sha256(token.encode()).hexdigest()
        gets = ["/health", "/", "/v3/", "/public/feed", "/static/game.js",
                "/events/recent", "/events/agents"]
        posts = [
            {"path": "/events", "body": valid_envelope(), "headers": None},
            {"path": "/events", "body": valid_envelope(),
             "headers": {"Authorization": f"Bearer {token}"}},
            {"path": "/set_state", "body": {}, "headers": None},
        ]
        report = _boot_ok("ingest", {"get": gets, "post": posts},
                          tokens=f"{digest}:hermes")
        self.assertEqual(report["mode"], "ingest")
        self.assertEqual(sorted(d["rule"] for d in report["rules"]),
                         sorted(sou_mode.INGEST_RULES))
        probes = report["probes"]

        def get(path):
            return probes[f"GET {gets.index(path)} {path}"]

        self.assertEqual(get("/health")["status"], 200)
        self.assertEqual(get("/events/recent")["status"], 200)
        self.assertEqual(get("/events/agents")["status"], 200)
        for path in ("/", "/v3/", "/public/feed", "/static/game.js"):
            self.assertEqual(get(path)["status"], 404, path)
        self.assertEqual(probes["POST 0 /events"]["status"], 401)
        self.assertIs(probes["POST 0 /events"]["json"]["ok"], False)
        self.assertEqual(probes["POST 1 /events"]["status"], 201)
        self.assertEqual(probes["POST 2 /set_state"]["status"], 404)


class DevFaceTest(unittest.TestCase):
    """AC4: dev (unset or explicit) is the 42-rule master app, unchanged."""

    GETS_UNSET = ["/status", "/health", "/public/feed"]
    GETS_DEV = ["/"]

    @classmethod
    def setUpClass(cls) -> None:
        cls.unset = _boot_ok(None, {"get": cls.GETS_UNSET})
        cls.explicit = _boot_ok("dev", {"get": cls.GETS_DEV})

    def test_rules_equal_master_snapshot(self) -> None:
        expected = sorted((d["rule"], d["endpoint"], tuple(d["methods"]))
                          for d in MASTER_42)
        self.assertEqual(_rule_triples(self.unset), expected)
        self.assertEqual(_rule_triples(self.explicit), expected)

    def test_today_behaviour_kept_and_public_reads_absent(self) -> None:
        probes = self.unset["probes"]
        self.assertEqual(self.unset["mode"], "dev")
        self.assertEqual(probes[f"GET 1 /health"]["status"], 200)
        self.assertEqual(probes[f"GET 0 /status"]["status"], 200)
        self.assertEqual(probes[f"GET 2 /public/feed"]["status"], 404)
        self.assertEqual(
            self.explicit["probes"]["GET 0 /"]["status"], 200)


class UnknownModeTest(unittest.TestCase):
    """A typo'd face name must never boot a silent dev."""

    def test_unknown_mode_refuses_to_start(self) -> None:
        proc = _boot("pubic", {})
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("SOU_MODE", proc.stderr)


class IngestWritesThenPublicReadsTest(unittest.TestCase):
    """The Phase 13 story end to end: a tokened write on the ingest door
    shows up on the public face already projected — raw paths never
    resurface."""

    def test_round_trip(self) -> None:
        token = "sou220-e2e-token"
        digest = hashlib.sha256(token.encode()).hexdigest()
        wrote = _boot_ok("ingest", {"post": [{
            "path": "/events",
            "body": {
                "version": 1,
                "agent": "hermes",
                "event_type": "tool_call",
                "timestamp": "2026-10-17T08:00:00Z",
                "payload": {
                    "tool": "Edit",
                    "file": "/home/operator/vault/passwords.kdbx",
                    "detail": "editing the vault file at /home/operator",
                    "command": "grep -r secret /home/operator/vault",
                },
            },
            "headers": {"Authorization": f"Bearer {token}"},
        }]}, tokens=f"{digest}:hermes")
        self.assertEqual(wrote["probes"]["POST 0 /events"]["status"], 201)

        read = _boot_ok("public", {"get": ["/public/feed"]})
        feed = read["probes"]["GET 0 /public/feed"]["json"]
        hers = [item for item in feed if item["agent"] == "hermes"]
        self.assertTrue(hers, "tokened ingest did not reach the public feed")
        mine = hers[0] if hers[0]["payload"].get("file") else hers[-1]
        self.assertEqual(
            mine["payload"],
            {"tool": "Edit", "file": "passwords.kdbx", "command": "grep"})
        text = json.dumps(feed)
        for leak in ("/home", '"detail"', "vault/", '"id"', '"url"',
                     '"session_id"'):
            self.assertNotIn(leak, text)


if __name__ == "__main__":
    unittest.main()
