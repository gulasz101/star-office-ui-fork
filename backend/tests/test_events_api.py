"""HTTP contract for ``POST /events`` — agent-agnostic ingest (SOU-36, ADR-035).

Phase 2, Slice 1. The endpoint is the single door agents walk through: a
versioned JSON envelope (``version``, ``agent``, ``event_type``,
``timestamp``, ``payload``) validated against
``backend/schemas/event_v1.json``, persisted through the Phase 1 adapter
(``db.insert_event``) on an ``agents`` row that is created on first sight of
a new agent. ``/set_state`` keeps answering, and now answers *through the
same ingest path* as a ``state_change`` event (AC 5) instead of owning a
second write path.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_events_api.py -v

The harness is the one ``tests/test_app_db.py`` uses: ``app`` is imported once
against a throwaway ``DATABASE_URL`` and ``setUp`` re-points the adapter at a
brand-new SQLite file and re-runs ``init_database()``, so every test starts
from the same empty state (SOU-226 removed the v1 ``Star`` seed). When the
runtime extras (flask / sqlalchemy / alembic / jsonschema) are missing the
module skips, same convention as the rest of the suite.
"""

from __future__ import annotations

import datetime as dt
import inspect
import json
import os
import shutil
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_FILE = os.path.join(BACKEND_DIR, "schemas", "event_v1.json")

try:
    import sqlalchemy as sa
    import flask  # noqa: F401  (the app under test is a Flask module)
    import alembic  # noqa: F401
    import jsonschema  # noqa: F401  (the validator the endpoint is built on)
except ImportError as exc:  # pragma: no cover - only hit without the extras
    sa = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

app_mod = None
_ORIG_ENV: dict = {}

# AC 1's five required fields, and the envelope the happy path posts.
REQUIRED_FIELDS = ("version", "agent", "event_type", "timestamp", "payload")

# The payload size cap the endpoint enforces (ruling on SOU-36: request-shape
# guard in place of the auth this slice does not have).
PAYLOAD_MAX_BYTES = 65536


def setUpModule() -> None:
    global app_mod
    if sa is None:
        return
    _ORIG_ENV.update(
        {k: os.environ.get(k) for k in ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")}
    )
    workdir = tempfile.mkdtemp(prefix="sou36-module-")
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
    workdir = tempfile.mkdtemp(prefix="sou36-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
    app_mod.db.shutdown()
    app_mod.init_database()
    return workdir


def now_iso() -> str:
    """A current UTC instant.

    ``events.created_at`` is the *agent's* event time (ADR-035), and the
    adapter orders the feed by it, so a fixture frozen in the past sorts
    behind the seed event. Tests that ask "what does the office show now"
    must post a fresh timestamp; tests that assert on the stored instant pin
    one explicitly.
    """
    return dt.datetime.now(dt.timezone.utc).isoformat()


def valid_event(**over) -> dict:
    """A schema-valid ADR-035 ``tool_call`` event, with optional overrides."""
    event = {
        "version": 1,
        "agent": "hermes",
        "event_type": "tool_call",
        "timestamp": "2026-10-13T14:20:55.653Z",
        "payload": {
            "tool": "read_file",
            "args": {"path": "backend/app.py", "offset": 1},
            "result": {"status": "success", "error": None},
        },
    }
    event.update(over)
    return event


class _AppTestCase(unittest.TestCase):
    """Shared client + DB-reading helpers."""

    def setUp(self) -> None:
        self.workdir = fresh_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    # -- HTTP -------------------------------------------------------------
    def post_event(self, body, **kw):
        return self.client.post("/events", json=body, **kw)

    def post_raw(self, body: str, content_type="application/json"):
        return self.client.post("/events", data=body, content_type=content_type)

    def assert_rejected(self, resp, *, status=400):
        self.assertEqual(
            resp.status_code, status,
            f"expected {status}, got {resp.status_code}: {resp.get_data(as_text=True)[:200]}",
        )
        payload = resp.get_json()
        self.assertIsNotNone(payload, "error responses must be JSON")
        self.assertIs(payload.get("ok"), False, f"expected ok=false, got {payload}")
        self.assertIsInstance(payload.get("error"), str)
        self.assertTrue(payload["error"].strip(), "error message must not be empty")
        return payload["error"]

    # -- Database ---------------------------------------------------------
    def _session(self):
        return app_mod.db.session_scope()

    def agents(self):
        with self._session() as session:
            return list(app_mod.db.list_agents(session))

    def events(self, agent_id=None, event_type=None):
        with self._session() as session:
            return list(app_mod.db.list_events(session, agent_id=agent_id, event_type=event_type))

    def raw_event_rows(self):
        """Read events with plain SQL: proves the *stored* shape, not the ORM's."""
        engine = app_mod.db.get_engine()
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT e.agent_id, e.event_type, e.payload, e.created_at, a.name, a.agent_type "
                    "FROM events e JOIN agents a ON a.id = e.agent_id ORDER BY e.id"
                )
            ).fetchall()
        out = []
        for r in rows:
            payload = r[2]  # events.payload, JSONB on Postgres / TEXT on SQLite
            if isinstance(payload, (str, bytes)):
                payload = json.loads(payload)
            created_at = r[3]
            if isinstance(created_at, str):
                # SQLite hands raw SQL back as its TEXT DATETIME form.
                created_at = dt.datetime.fromisoformat(created_at.replace(" ", "T"))
            elif isinstance(created_at, dt.datetime) and created_at.tzinfo is not None:
                created_at = created_at.astimezone(dt.timezone.utc).replace(tzinfo=None)
            out.append(
                {
                    "agent_id": r[0],
                    "event_type": r[1],
                    "payload": payload,
                    "created_at": created_at,
                    "agent_name": r[4],
                    "agent_type": r[5],
                }
            )
        return out

    def main_agent(self):
        return next(a for a in self.agents() if a.agent_type == "main")


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class EventsIngestTest(_AppTestCase):
    """AC 1 + AC 3: ``POST /events`` accepts, validates and persists."""

    def test_events_route_exists_on_the_app(self) -> None:
        rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
        self.assertIn("/events", rules, "AC 1: the endpoint must live in backend/app.py")

    def test_valid_event_returns_201_ok_true(self) -> None:
        resp = self.post_event(valid_event())
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        self.assertEqual(resp.get_json(), {"ok": True}, "AC 1 spells the success body exactly")

    def test_valid_event_persists_the_row_the_adapter_wrote(self) -> None:
        event = valid_event()
        self.assertEqual(self.post_event(event).status_code, 201)

        rows = self.raw_event_rows()
        hermes = [r for r in rows if r["agent_name"] == "hermes"]
        self.assertEqual(len(hermes), 1, f"one event row expected, got {hermes}")
        stored = hermes[0]
        self.assertEqual(stored["event_type"], "tool_call")
        # SOU-218 replaced "AC 3: full payload, unreshaped" at this door:
        # POST /events stores the safe projection. The fixture sends the
        # shape an old buggy reporter would send (args/result free text);
        # only the allow-listed tool label may land.
        self.assertEqual(stored["payload"], {"tool": "read_file"},
                         "AC (SOU-218): only the safe projection is stored")
        self.assertEqual(
            stored["created_at"],
            dt.datetime(2026, 10, 13, 14, 20, 55, 653000),
            "AC 3: the submitted timestamp, normalised to naive UTC, is the row's created_at",
        )

    def test_agent_field_maps_to_the_agents_row(self) -> None:
        self.assertEqual(self.post_event(valid_event(agent="zephyr")).status_code, 201)
        row = next(a for a in self.agents() if a.name == "zephyr")
        stored = self.raw_event_rows()[-1]
        self.assertEqual(stored["agent_id"], row.id, "AC 3: agent -> agents.id")

    def test_payload_survives_the_json_round_trip_including_unicode(self) -> None:
        # SOU-218: the round-trip is for the *projection* now — free-text
        # fields never land, but a unicode basename must still come back intact.
        # Accented (not CJK) letters: the repo-wide english-only gate stays green.
        payload = {"tool": "Write", "file": "/home/dev/café-résumé.txt", "status": "ok"}
        expected = {"tool": "Write", "file": "café-résumé.txt", "status": "ok"}
        self.assertEqual(
            self.post_event(valid_event(event_type="file_edit", payload=payload)).status_code,
            201,
        )
        with self._session() as session:
            row = app_mod.db.list_events(
                session,
                agent_id=next(a.id for a in self.agents() if a.name == "hermes"),
                event_type="file_edit",
                limit=1,
            )[0]
            self.assertEqual(row.payload, expected)
            self.assertIsInstance(row.payload, dict, "the adapter parses the stored JSON back")

    def test_missing_required_field_is_400(self) -> None:
        for field in REQUIRED_FIELDS:
            with self.subTest(missing=field):
                body = {k: v for k, v in valid_event().items() if k != field}
                self.assert_rejected(self.post_event(body))

    def test_rejected_event_writes_nothing(self) -> None:
        before = len(self.raw_event_rows())
        body = {k: v for k, v in valid_event().items() if k != "payload"}
        self.assert_rejected(self.post_event(body))
        self.assertEqual(len(self.raw_event_rows()), before, "a 400 must leave the feed untouched")
        self.assertEqual(
            [a.name for a in self.agents()], [], "a 400 must not create an agent row"
        )

    def test_unknown_version_is_rejected(self) -> None:
        for version in (0, 2, 99, "1", 1.5, True, None, [1]):
            with self.subTest(version=version):
                msg = self.assert_rejected(self.post_event(valid_event(version=version)))
                self.assertIn("version", msg.lower(), "the error should name the offending field")

    def test_wrong_field_types_are_rejected(self) -> None:
        bad = {
            "version": "one",
            "agent": 123,
            "event_type": None,
            "timestamp": 1728857655,
            "payload": "a string, not an object",
        }
        for field, value in bad.items():
            with self.subTest(field=field, value=value):
                self.assert_rejected(self.post_event(valid_event(**{field: value})))

    def test_empty_agent_and_event_type_are_rejected(self) -> None:
        for over in ({"agent": ""}, {"event_type": ""}, {"agent": "   "}):
            with self.subTest(**over):
                self.assert_rejected(self.post_event(valid_event(**over)))

    def test_invalid_json_body_is_400_not_500(self) -> None:
        for raw in ("{not json", '{"version": 1,}', "", "   ", "[1,2"):
            with self.subTest(raw=raw):
                self.assert_rejected(self.post_raw(raw))

    def test_non_object_json_body_is_400(self) -> None:
        for raw in ('[]', '[{"version": 1}]', '"hermes"', 'null', 'true', '1'):
            with self.subTest(body=raw):
                self.assert_rejected(self.post_raw(raw))

    def test_missing_json_content_type_is_400(self) -> None:
        self.assert_rejected(
            self.client.post("/events", data=json.dumps(valid_event()), content_type="text/plain")
        )

    def test_malformed_timestamp_is_rejected(self) -> None:
        # Anything short of a full ISO 8601 date-time is refused: an activity chain
        # with an unusable instant in it is worthless. A plain date or a bare time
        # does not qualify; a space separator does (lenient parse, ruling on SOU-36).
        date_only = "2026" + "-" + "10" + "-" + "13"
        for stamp in ("yesterday", "2026-13-45T99:99:99Z", "", "T14:20:55Z",
                      date_only, "14:20:55", "soon"):
            with self.subTest(timestamp=stamp):
                self.assert_rejected(self.post_event(valid_event(timestamp=stamp)))

    def test_timestamp_offsets_are_normalised_to_utc(self) -> None:
        cases = {
            "2026-10-13T14:20:55.653Z": dt.datetime(2026, 10, 13, 14, 20, 55, 653000),
            "2026-10-13T16:20:55+02:00": dt.datetime(2026, 10, 13, 14, 20, 55),
            "2026-10-13T09:20:55-05:00": dt.datetime(2026, 10, 13, 14, 20, 55),
            "2026-10-13T14:20:55.653+00:00": dt.datetime(2026, 10, 13, 14, 20, 55, 653000),
            "2026-10-13T14:20:55.653": dt.datetime(2026, 10, 13, 14, 20, 55, 653000),
        }
        for stamp, expected in cases.items():
            with self.subTest(timestamp=stamp):
                self.assertEqual(
                    self.post_event(valid_event(timestamp=stamp)).status_code, 201
                )
                stored = self.raw_event_rows()[-1]["created_at"]
                self.assertEqual(
                    stored,
                    expected,
                    "ADR-035 fixes naive UTC as the application-layer convention",
                )

    def _payload_of_exact_size(self, target: int) -> dict:
        """A payload whose JSON is exactly ``target`` bytes, as the server counts."""
        overhead = len(json.dumps({"blob": ""}, ensure_ascii=False).encode("utf-8"))
        return {"blob": "a" * (target - overhead)}

    def test_oversize_payload_is_rejected(self) -> None:
        big = self._payload_of_exact_size(PAYLOAD_MAX_BYTES + 1)
        msg = self.assert_rejected(self.post_event(valid_event(payload=big)))
        self.assertIn("payload", msg.lower())
        self.assertEqual(len(self.raw_event_rows()), 0, "nothing may be written")

    def test_payload_at_the_size_limit_is_accepted(self) -> None:
        body = valid_event(payload=self._payload_of_exact_size(PAYLOAD_MAX_BYTES))
        size = len(json.dumps(body["payload"], ensure_ascii=False).encode("utf-8"))
        self.assertEqual(size, PAYLOAD_MAX_BYTES, "fixture must sit exactly on the cap")
        self.assertEqual(self.post_event(body).status_code, 201)

    def test_unknown_event_type_is_accepted(self) -> None:
        """ADR-035 extensibility: the registry is documentation, not a whitelist."""
        self.assertEqual(
            self.post_event(valid_event(event_type="git_commit", payload={"sha": "abc"})).status_code,
            201,
        )

    def test_extra_top_level_fields_are_tolerated(self) -> None:
        """Forward compat ruling: unknown fields do not break an older backend."""
        self.assertEqual(
            self.post_event(valid_event(session_id="ses_1", trace={"span": "abc"})).status_code,
            201,
        )

    def test_empty_payload_object_is_accepted(self) -> None:
        self.assertEqual(self.post_event(valid_event(payload={})).status_code, 201)

    def test_oversized_identifier_is_rejected(self) -> None:
        """agents.name is 255 wide, events.event_type 100 (migration 001)."""
        self.assert_rejected(self.post_event(valid_event(agent="a" * 256)))
        self.assert_rejected(self.post_event(valid_event(event_type="e" * 101)))

    def test_post_events_is_post_only(self) -> None:
        self.assertEqual(self.client.get("/events").status_code, 405)

    def test_unknown_agent_creates_one_row_and_reuses_it(self) -> None:
        """AC 3: create the agent row when it is missing, then reuse it."""
        self.assertEqual([a.name for a in self.agents()], [], "a fresh database has no agents (SOU-226: no v1 seed)")
        for _ in range(3):
            self.assertEqual(self.post_event(valid_event(agent="opencode")).status_code, 201)
        rows = self.agents()
        self.assertEqual(sorted(a.name for a in rows), ["opencode"])
        created = next(a for a in rows if a.name == "opencode")
        self.assertEqual(created.agent_type, "guest", "ingest-created rows are guests")
        self.assertEqual(len(self.events(agent_id=created.id)), 3)

    def test_agent_lookup_ignores_case(self) -> None:
        self.assertEqual(self.post_event(valid_event(agent="OpenCode")).status_code, 201)
        self.assertEqual(self.post_event(valid_event(agent="opencode")).status_code, 201)
        self.assertEqual(self.post_event(valid_event(agent="  OpenCode  ")).status_code, 201)
        self.assertEqual(
            {a.name for a in self.agents()}, {"OpenCode"},
            "the first spelling registered is the canonical one",
        )


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class EventSchemaDocumentTest(_AppTestCase):
    """AC 2: ``backend/schemas/event_v1.json`` is the contract the endpoint reads."""

    def _schema(self) -> dict:
        self.assertTrue(
            os.path.isfile(SCHEMA_FILE), "backend/schemas/event_v1.json must exist (AC 2)"
        )
        with open(SCHEMA_FILE, encoding="utf-8") as f:
            return json.load(f)

    def test_schema_is_draft_2020_12_json(self) -> None:
        schema = self._schema()
        self.assertEqual(schema.get("$schema"), "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(schema.get("type"), "object")
        # The file must be a schema jsonschema can actually run.
        jsonschema.Draft202012Validator.check_schema(schema)

    def test_schema_requires_the_five_ac_fields(self) -> None:
        schema = self._schema()
        self.assertEqual(set(schema["required"]), set(REQUIRED_FIELDS))
        self.assertEqual(set(schema["properties"]), set(REQUIRED_FIELDS))

    def test_schema_types_match_adr_035(self) -> None:
        props = self._schema()["properties"]
        self.assertEqual(props["version"]["type"], "integer")
        self.assertEqual(props["agent"]["type"], "string")
        self.assertEqual(props["event_type"]["type"], "string")
        self.assertEqual(props["timestamp"]["type"], "string")
        self.assertEqual(props["payload"]["type"], "object")

    def test_schema_pins_version_to_one(self) -> None:
        """AC 2: ``version=1`` and nothing else."""
        props = self._schema()["properties"]
        self.assertIn("const", props["version"])
        self.assertEqual(props["version"]["const"], 1)

    def test_schema_mirrors_the_column_widths(self) -> None:
        """agents.name is String(255), events.event_type String(100)."""
        props = self._schema()["properties"]
        self.assertEqual(props["agent"]["maxLength"], 255)
        self.assertEqual(props["event_type"]["maxLength"], 100)
        self.assertEqual(props["agent"]["minLength"], 1)
        self.assertEqual(props["event_type"]["minLength"], 1)

    def test_the_endpoint_uses_this_file_and_no_inlined_copy(self) -> None:
        validator = os.path.join(BACKEND_DIR, "event_schema.py")
        self.assertTrue(
            os.path.isfile(validator), "backend/event_schema.py must exist (AC 2 validator)"
        )
        with open(validator, encoding="utf-8") as f:
            src = f.read()
        self.assertIn(
            os.path.join("schemas", "event_v1.json").replace(os.sep, "/"), src.replace("\\", "/"),
            "the validator must load backend/schemas/event_v1.json",
        )
        self.assertNotIn(
            '"required": ["version"', src,
            "AC 2 wants one schema, not a second inlined copy that can drift",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
