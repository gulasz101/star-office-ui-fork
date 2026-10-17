"""HTTP contract for ``GET /events/recent`` — the read path (SOU-45, Phase 3).

Phase 2 opened the door (``POST /events``); this slice proves the office can
*read* back what went in. The endpoint serves the ``events`` table newest
first as a bare JSON array of ``{id, agent, event_type, payload,
created_at}`` — the shape the Phaser renderer will poll in Phase 4 — with
four query params (SOU-45 AC 2):

* ``limit``       last N events; default 50, hard cap 200, clamped 1..200
* ``agent``       registry name, case-insensitive (``find_agent_by_name``
                  contract); unknown agent -> empty array, not 404
* ``event_type``  exact type string
* ``since``       ISO 8601 instant, normalised exactly like the ingest
                  ``timestamp`` (naive UTC); inclusive floor

Bad ``since`` or bad ``limit`` -> 400 ``{"ok": false, "error": ...}``, the
same error body ``POST /events`` writes.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_events_read.py -v

Same harness as ``tests/test_events_api.py``: one import against a throwaway
``DATABASE_URL``, then ``fresh_database()`` per test so every case starts
from the seeded state (main ``Star`` agent + one seed event).
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import tempfile
import unittest
from urllib.parse import urlencode

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    import sqlalchemy as sa  # noqa: F401  (the adapter speaks it; skip-gate only)
    import flask  # noqa: F401  (the app under test is a Flask module)
    import alembic  # noqa: F401
    import jsonschema  # noqa: F401
except ImportError as exc:  # pragma: no cover - only hit without the extras
    sa = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

app_mod = None
_ORIG_ENV: dict = {}

# The exact key set AC 2 of SOU-45 spells for one feed item.
ITEM_KEYS = {"id", "agent", "event_type", "payload", "created_at"}

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# A base in the past: fixtures hang explicit offsets off it, so ordering and
# the since-floor are deterministic no matter when the suite runs. The seed
# event (created "now") sorts newest of all and is excluded whenever a test
# filters by agent.
BASE = dt.datetime(2026, 10, 12, 20, 0, 0, tzinfo=dt.timezone.utc)


def setUpModule() -> None:
    global app_mod
    if sa is None:
        return
    _ORIG_ENV.update(
        {k: os.environ.get(k) for k in ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")}
    )
    workdir = tempfile.mkdtemp(prefix="sou45-module-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'module.db')}"
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    import app  # noqa: F401  (triggers the startup path the other suites test)

    app_mod = app


def tearDownModule() -> None:
    for key, value in _ORIG_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def fresh_database() -> str:
    """Point the imported app at a brand-new SQLite file and re-seed it."""
    workdir = tempfile.mkdtemp(prefix="sou45-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
    app_mod.db.shutdown()
    app_mod.init_database()
    return workdir


def stamp(offset_seconds: int) -> str:
    """An ISO-8601 ``timestamp`` for the event at BASE + offset."""
    return (BASE + dt.timedelta(seconds=offset_seconds)).isoformat().replace(
        "+00:00", "Z"
    )


def event(agent="hermes", event_type="tool_call", offset=0, payload=None) -> dict:
    """A schema-valid ADR-035 v1 event pinned at BASE + offset seconds."""
    return {
        "version": 1,
        "agent": agent,
        "event_type": event_type,
        "timestamp": stamp(offset),
        "payload": payload
        if payload is not None
        else {"tool": "read_file", "args": {"path": "backend/app.py"}},
    }


def parse_instant(text: str) -> dt.datetime:
    """Parse a feed ``created_at`` the way the frontend must (UTC-aware)."""
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class EventsRecentTest(unittest.TestCase):
    """AC 2 + AC 5: GET /events/recent contract."""

    def setUp(self) -> None:
        self.workdir = fresh_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def push(self, *events) -> None:
        for ev in events:
            resp = self.client.post("/events", json=ev)
            self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))

    def get_recent(self, **params):
        query = urlencode(params) if params else ""
        url = "/events/recent" + (f"?{query}" if query else "")
        return self.client.get(url)

    def body(self, resp) -> list:
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertIsInstance(data, list, "AC 2: the feed is a bare JSON array")
        return data

    def assert_rejected(self, resp, *, status=400):
        self.assertEqual(
            resp.status_code, status,
            f"expected {status}, got {resp.status_code}: {resp.get_data(as_text=True)[:200]}",
        )
        payload = resp.get_json()
        self.assertIsNotNone(payload, "error responses must be JSON")
        self.assertIs(payload.get("ok"), False)
        self.assertIsInstance(payload.get("error"), str)
        self.assertTrue(payload["error"].strip())
        return payload["error"]

    # -- the route itself (AC 2) -------------------------------------------
    def test_recent_route_exists_on_the_app(self) -> None:
        rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
        self.assertIn("/events/recent", rules, "AC 2: the endpoint must live in backend/app.py")

    def test_empty_feed_is_a_json_array(self) -> None:
        data = self.body(self.get_recent())
        self.assertIsInstance(data, list)

    # -- item shape and ordering (AC 2, AC 5: schema) -----------------------
    def test_pushed_events_come_back_with_the_spelled_key_set(self) -> None:
        self.push(
            event(offset=0),
            event(event_type="file_edit", offset=10,
                  payload={"path": "backend/app.py", "lines": 12}),
        )
        mine = [e for e in self.body(self.get_recent(agent="hermes"))]
        self.assertEqual(len(mine), 2)
        for item in mine:
            self.assertEqual(
                set(item.keys()), ITEM_KEYS,
                f"AC 2 spells the item exactly: {ITEM_KEYS}",
            )
            self.assertIsInstance(item["id"], int)
            self.assertIsInstance(item["agent"], str)
            self.assertIsInstance(item["event_type"], str)
            self.assertIsInstance(item["payload"], dict)
            parse_instant(item["created_at"])  # must parse as an ISO instant
        by_id = {e["id"]: e for e in mine}
        newest, older = (mine[0], mine[1]) if by_id else (None, None)
        # newest-first: the file_edit (offset +10s) must lead the tool_call (+0s)
        self.assertEqual(newest["event_type"], "file_edit")
        self.assertEqual(older["event_type"], "tool_call")
        # SOU-218: the feed serves the stored projection — paths basenames,
        # unlisted keys gone ("lines" was never on the allow-list)
        self.assertEqual(newest["payload"], {"path": "app.py"})
        self.assertEqual(parse_instant(newest["created_at"]),
                         BASE + dt.timedelta(seconds=10))
        # ids are the database's own, and unique
        self.assertEqual(len({e["id"] for e in mine}), 2)

    def test_agent_name_comes_back_not_the_row_id(self) -> None:
        self.push(event(agent="hermes", offset=0))
        data = self.body(self.get_recent(agent="hermes"))
        self.assertEqual({e["agent"] for e in data}, {"hermes"})

    # -- filters (AC 2, AC 5: query params) ---------------------------------
    def test_agent_filter_is_case_insensitive_registry_lookup(self) -> None:
        self.push(event(agent="hermes", offset=0),
                  event(agent="OpenChamber", offset=5))
        lower = self.body(self.get_recent(agent="HERMES"))
        mixed = self.body(self.get_recent(agent="openchamber"))
        self.assertEqual({e["agent"] for e in lower}, {"hermes"})
        self.assertEqual({e["agent"] for e in mixed}, {"OpenChamber"})

    def test_unknown_agent_is_an_empty_feed_not_404(self) -> None:
        self.push(event(offset=0))
        data = self.body(self.get_recent(agent="nobody-here"))
        self.assertEqual(data, [])

    def test_event_type_filter(self) -> None:
        self.push(event(offset=0, event_type="tool_call"),
                  event(offset=5, event_type="tool_call"),
                  event(offset=10, event_type="file_edit"))
        edits = self.body(self.get_recent(event_type="file_edit", agent="hermes"))
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]["event_type"], "file_edit")
        calls = self.body(self.get_recent(event_type="tool_call", agent="hermes"))
        self.assertEqual({e["event_type"] for e in calls}, {"tool_call"})

    def test_since_is_an_inclusive_floor_normalised_like_ingest(self) -> None:
        self.push(event(offset=0), event(offset=60), event(offset=120))
        # Z form and +00:00 form must behave identically: same normalisation
        # as event_schema.parse_timestamp (naive UTC).
        for floor in (stamp(60), (BASE + dt.timedelta(seconds=60)).isoformat()):
            mine = self.body(self.get_recent(since=floor, agent="hermes"))
            self.assertEqual(
                len(mine), 2,
                f"since={floor}: offset 60 (boundary, inclusive) and 120 only",
            )
            self.assertTrue(
                all(parse_instant(e["created_at"]) >= BASE + dt.timedelta(seconds=60)
                    for e in mine)
            )

    def test_bad_since_is_400(self) -> None:
        resp = self.get_recent(since="last-tuesday")
        error = self.assert_rejected(resp)
        self.assertIn("since", error)

    # -- limit / pagination (AC 5: limit param) ------------------------------
    def test_default_limit_is_fifty_newest_first(self) -> None:
        self.push(*(event(offset=i) for i in range(60)))
        data = self.body(self.get_recent(agent="hermes"))
        self.assertEqual(len(data), DEFAULT_LIMIT, "SOU-45 AC 2: default 50")
        instants = [parse_instant(e["created_at"]) for e in data]
        self.assertEqual(instants, sorted(instants, reverse=True))
        self.assertEqual(instants[0], BASE + dt.timedelta(seconds=59))

    def test_limit_param_pages_the_feed(self) -> None:
        self.push(*(event(offset=i) for i in range(10)))
        one = self.body(self.get_recent(agent="hermes", limit=1))
        five = self.body(self.get_recent(agent="hermes", limit=5))
        self.assertEqual(len(one), 1)
        self.assertEqual(len(five), 5)
        # limit=1 is the tip of limit=5: newest-first paging is consistent
        self.assertEqual(one[0]["id"], five[0]["id"])
        # asking for more than exists returns what exists
        allm = self.body(self.get_recent(agent="hermes", limit=10))
        self.assertEqual(len(allm), 10)

    def test_limit_capped_at_two_hundred(self) -> None:
        self.push(*(event(offset=i) for i in range(205)))
        data = self.body(self.get_recent(agent="hermes", limit=500))
        self.assertEqual(len(data), MAX_LIMIT, "SOU-45 AC 2: max 200")
        self.assertEqual(
            parse_instant(data[0]["created_at"]),
            BASE + dt.timedelta(seconds=204),
            "and it is still the NEWEST 200",
        )

    def test_bad_limit_is_400(self) -> None:
        for bad in ("abc", "0", "-3"):
            resp = self.get_recent(limit=bad)
            error = self.assert_rejected(resp)
            self.assertIn("limit", error)

    # -- neighbours (Phase 2 contract must not regress) ----------------------
    def test_get_recent_does_not_shadow_post_events(self) -> None:
        self.push(event(offset=0))  # POST /events still answers through it
        data = self.body(self.get_recent(agent="hermes"))
        self.assertEqual(len(data), 1)


# The exact key set SOU-201 spells for one /events/agents entry.
AGENTS_ENTRY_KEYS = {"agent", "first_seen", "last_seen", "count", "last_event"}


def floor(seconds: int = -1) -> str:
    """A ``since`` floor at BASE + ``seconds`` (default: just before all fixtures)."""
    return (BASE + dt.timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class EventsAgentsTest(unittest.TestCase):
    """SOU-201 AC: GET /events/agents — every agent active since ``since`` with
    its newest event spelled exactly like a /events/recent row.

    The Lodge discovers rooms from this list, so a single busy agent must never
    be able to hide a quiet one the way 200 rows of /events/recent can
    (test_busy_agent_cannot_hide_quiet_agents is that bug, encoded).
    """

    def setUp(self) -> None:
        self.workdir = fresh_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    # -- helpers ----------------------------------------------------------
    def push(self, *events) -> None:
        for ev in events:
            resp = self.client.post("/events", json=ev)
            self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))

    def get_agents(self, **params):
        query = urlencode(params) if params else ""
        url = "/events/agents" + (f"?{query}" if query else "")
        return self.client.get(url)

    def body(self, resp) -> list:
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))
        data = resp.get_json()
        self.assertIsInstance(data, list, "the answer is a bare JSON array")
        return data

    def by_name(self, entries) -> dict:
        return {e["agent"]: e for e in entries}

    def assert_rejected(self, resp, *, status=400):
        self.assertEqual(
            resp.status_code, status,
            f"expected {status}, got {resp.status_code}: {resp.get_data(as_text=True)[:200]}",
        )
        payload = resp.get_json()
        self.assertIsNotNone(payload, "error responses must be JSON")
        self.assertIs(payload.get("ok"), False)
        self.assertIsInstance(payload.get("error"), str)
        self.assertTrue(payload["error"].strip())
        return payload["error"]

    # -- the route and the entry shape --------------------------------------
    def test_agents_route_exists_on_the_app(self) -> None:
        rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
        self.assertIn("/events/agents", rules, "AC 1: the endpoint must live in backend/app.py")

    def test_three_agents_come_back_with_window_scoped_stats(self) -> None:
        self.push(
            event(agent="hermes", offset=0),
            event(agent="hermes", event_type="file_edit", offset=60,
                  payload={"path": "a.py", "lines": 1}),
            event(agent="hermes", offset=120),
            event(agent="openchamber", offset=10),
            event(agent="openchamber", offset=20),
            event(agent="claude", offset=30),
        )
        entries = self.by_name(self.body(self.get_agents(since=floor())))
        self.assertTrue(
            {"hermes", "openchamber", "claude"} <= set(entries),
            f"AC 1: all three agents must be listed, got {sorted(entries)}",
        )
        for name in ("hermes", "openchamber", "claude"):
            self.assertEqual(
                set(entries[name].keys()), AGENTS_ENTRY_KEYS,
                f"AC 1 spells the entry exactly: {AGENTS_ENTRY_KEYS}",
            )
        h = entries["hermes"]
        self.assertEqual(h["count"], 3)
        self.assertEqual(parse_instant(h["first_seen"]), BASE + dt.timedelta(seconds=0))
        self.assertEqual(parse_instant(h["last_seen"]), BASE + dt.timedelta(seconds=120))
        o = entries["openchamber"]
        self.assertEqual(o["count"], 2)
        self.assertEqual(parse_instant(o["first_seen"]), BASE + dt.timedelta(seconds=10))
        self.assertEqual(parse_instant(o["last_seen"]), BASE + dt.timedelta(seconds=20))
        c = entries["claude"]
        self.assertEqual(c["count"], 1)
        self.assertEqual(parse_instant(c["first_seen"]), BASE + dt.timedelta(seconds=30))
        self.assertEqual(parse_instant(c["last_seen"]), BASE + dt.timedelta(seconds=30))

    # -- the since filter (AC 1) ---------------------------------------------
    def test_since_is_an_inclusive_floor_and_scopes_the_stats(self) -> None:
        self.push(
            event(agent="quiet", offset=0),
            event(agent="quiet", offset=60),
            event(agent="quiet", offset=100),
            event(agent="gone", offset=5),  # fully below the floor
        )
        entries = self.by_name(self.body(self.get_agents(since=stamp(60))))
        self.assertIn("quiet", entries, "the event at exactly `since` counts (inclusive)")
        self.assertEqual(entries["quiet"]["count"], 2, "window-scoped: offsets 60 and 100 only")
        self.assertEqual(parse_instant(entries["quiet"]["first_seen"]),
                         BASE + dt.timedelta(seconds=60))
        self.assertNotIn("gone", entries, "no event at/after the floor -> no entry")

    def test_default_since_is_the_last_twenty_four_hours(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        # the fixture helper pins stamps to BASE (~4 days ago); post one agent
        # NOW so the default window must include it and exclude the old one
        resp = self.client.post("/events", json={
            "version": 1, "agent": "fresh-agent", "event_type": "tool_call",
            "timestamp": now, "payload": {"tool": "now", "status": "ok"},
        })
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        self.push(event(agent="old-agent", offset=0))  # BASE, ~4 days ago
        entries = self.by_name(self.body(self.get_agents()))
        self.assertIn("fresh-agent", entries, "an agent active NOW is inside the default window")
        self.assertNotIn("old-agent", entries, "events older than 24 h are outside it")

    # -- ordering (AC 1) -------------------------------------------------------
    def test_entries_are_ordered_newest_activity_first(self) -> None:
        self.push(
            event(agent="late", offset=3600),
            event(agent="middle", offset=120),
            event(agent="early", offset=60),
            event(agent="late", offset=10),  # count 2, still led by 3600
        )
        data = self.body(self.get_agents(since=floor()))
        instants = [parse_instant(e["last_seen"]) for e in data]
        self.assertEqual(instants, sorted(instants, reverse=True),
                         "AC 1: newest activity first")
        mine = self.by_name(data)
        self.assertLess(
            [e["agent"] for e in data].index("late"),
            [e["agent"] for e in data].index("middle"),
        )
        self.assertLess(
            [e["agent"] for e in data].index("middle"),
            [e["agent"] for e in data].index("early"),
        )

    # -- last_event shape (AC 1) ----------------------------------------------
    def test_last_event_is_a_recent_feed_row(self) -> None:
        self.push(
            event(agent="hermes", offset=0),
            event(agent="hermes", event_type="file_edit", offset=5,
                  payload={"path": "backend/app.py", "lines": 9}),
        )
        entry = self.by_name(self.body(self.get_agents(since=floor())))["hermes"]
        feed = self.body(self.client.get("/events/recent?agent=hermes&limit=1"))
        self.assertEqual(len(feed), 1)
        self.assertEqual(
            entry["last_event"], feed[0],
            "AC 1: last_event equals the row /events/recent returns, exactly",
        )
        self.assertEqual(set(entry["last_event"].keys()), ITEM_KEYS)
        self.assertEqual(entry["last_event"]["event_type"], "file_edit",
                         "it is the NEWEST row, not just any row")

    # -- the SOU-201 bug, encoded (AC 1) ---------------------------------------
    def test_busy_agent_cannot_hide_quiet_agents(self) -> None:
        """250 events for one agent flood /events/recent?limit=200; the quiet
        agents must still get their room through /events/agents."""
        self.push(*(event(agent="busy-one", offset=i) for i in range(250)))
        self.push(
            event(agent="quiet-one", offset=-1000),
            event(agent="quiet-two", offset=-500),
        )
        flooded = self.body(self.client.get("/events/recent?limit=200"))
        self.assertEqual(len(flooded), 200)
        agents_in_feed = {e["agent"] for e in flooded}
        self.assertNotIn("quiet-one", agents_in_feed,
                         "the premise: 200 recent rows are monopolised, quiet agents vanish")
        self.assertNotIn("quiet-two", agents_in_feed, "same for the second quiet agent")
        entries = self.by_name(self.body(self.get_agents(since=floor(-1001))))
        for name in ("busy-one", "quiet-one", "quiet-two"):
            self.assertIn(name, entries, f"AC 1: {name} must get an entry")
        self.assertEqual(entries["busy-one"]["count"], 250)
        self.assertEqual(entries["quiet-one"]["count"], 1)
        self.assertEqual(entries["quiet-two"]["count"], 1)

    # -- nothing excluded server-side (AC 1) ------------------------------------
    def test_an_agent_called_star_is_listed_like_every_other_agent(self) -> None:
        """The server excludes nothing on this face: the renderer owns its ignore list.
        (SOU-226 removed the v1 Star seed; a reporter may still use the name.)"""
        now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        res = self.client.post("/events", json={"version": 1, "agent": "Star", "event_type": "tool_call",
                                                "timestamp": now, "payload": {"tool": "Read"}})
        self.assertEqual(res.status_code, 201)
        entries = self.by_name(self.body(self.get_agents()))
        self.assertIn("Star", entries)
        self.assertEqual(entries["Star"]["last_event"]["agent"], "Star")

    # -- validation (AC 1) -------------------------------------------------------
    def test_bad_since_is_400(self) -> None:
        for bad in ("last-tuesday", "2026-13-01T00:00:00Z", "yesterday"):
            error = self.assert_rejected(self.get_agents(since=bad))
            self.assertIn("since", error)

    def test_empty_since_falls_back_to_the_default(self) -> None:
        data = self.body(self.get_agents(since=""))
        self.assertIsInstance(data, list)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
