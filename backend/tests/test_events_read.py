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
        # the payload round-trips byte-for-byte through the feed
        self.assertEqual(newest["payload"], {"path": "backend/app.py", "lines": 12})
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

    def test_main_seed_event_is_readable_through_the_feed(self) -> None:
        """init_database seeds Star's state_change; /status reads it, so
        must /events/recent (same table)."""
        seeds = self.body(self.get_recent(agent="Star"))
        self.assertGreaterEqual(len(seeds), 1)
        self.assertEqual(seeds[0]["event_type"], "state_change")
        self.assertIn("state", seeds[0]["payload"])

    def test_seed_then_push_matches_the_status_read_path(self) -> None:
        """The renderer's contract (AC 3's proof): a state_change pushed via
        POST /events is what GET /status serves, and it is also in the feed."""
        state = {
            "state": "writing",
            "detail": "phase3 smoke",
            "progress": 42,
            "ttl_seconds": 300,
            "updated_at": stamp(0),
        }
        self.push(event(agent="Star", event_type="state_change", payload=state))
        served = self.client.get("/status")
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.get_json()["detail"], "phase3 smoke")
        feed = self.body(self.get_recent(agent="Star", event_type="state_change"))
        self.assertEqual(feed[0]["payload"]["detail"], "phase3 smoke")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
