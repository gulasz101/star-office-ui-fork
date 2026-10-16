"""One-off re-sanitising of every stored event payload (SOU-235, Phase 13).

SOU-218 gated ``POST /events`` through ``backend/sanitize.py``, but rows written
**before** it are still raw: full command lines, ``/Users/…`` home paths,
``query``/``url`` fields and session ids — on the M1 Max instance alone, ~1200
``hermes-szarik`` rows. A public face must never serve, back up or replicate
those, so the fix is a migration that runs the stored payloads through the same
projection the door now uses.

This module tests the walk (``backend/resanitise.py``) on SQLite, and the same
contract on Postgres when a server is reachable:

    scripts/dev_postgres.sh start
    TEST_POSTGRES_URL=postgresql+psycopg2://postgres@127.0.0.1:55432/sou \\
        uv run pytest backend/tests/test_resanitise.py -v

The rules the suite pins (the ticket's, restated as checks):

* every payload the migration processes becomes exactly
  ``sanitize.sanitize_payload(event_type, payload)`` — the door's function, so
  the row ends up where a fresh ingest would have put it;
* no canary survives in any row it processes, and — per the ruling on the
  ticket — the v1 state-door rows it skips keep their own bytes;
* running it twice changes nothing (the sanitiser is idempotent, tested);
* dry-run reports the counts and writes nothing at all;
* it never deletes a row and never rewrites ``agent_id``/``event_type``/
  ``created_at``.

Canary placement follows SOU-218's ruling: a canary sits where a secret sits in
the wild (path *directories*, flags, dropped keys), never in the basename the
allow-list keeps on purpose.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:  # same convention as the other backend suites
    sys.path.insert(0, BACKEND_DIR)

try:
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
except ImportError as exc:  # pragma: no cover - only hit without the DB extras
    sa = None
    command = None
    Config = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

import resanitise  # noqa: E402  (the module under test)
import sanitize  # noqa: E402  (the projection it must match)

ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
REVISION_001 = "001_initial_schema"

CANARY = "CANARY"          # planted in reporter-shaped rows: must not survive
KEEPME = "KEEPME"          # planted in v1 state rows: must survive untouched
HOME_PATH = "/Users/dev"  # the leak this migration exists to remove

FIRST_CREATED = dt.datetime(2026, 9, 1, 8, 0, 0)


# ---------------------------------------------------------------------------
# Raw rows: what a pre-SOU-218 database actually holds.
# ---------------------------------------------------------------------------
def reporter_rows() -> list:
    """Pre-SOU-218 reporter payloads — the shapes SOU-218's sweep posted.

    Keys and value shapes are the ones the Hermes and OpenChamber reporters sent
    before the door existed: full command lines, home paths, ``query``/``url``,
    ``session_id``, free text.
    """
    return [
        ("hermes-szarik", "command_run", {
            "command": f"rsync -av {HOME_PATH}/Music/gulasz.backup nas.example:/vol1/music",
            "cwd": f"{HOME_PATH}/dev/star-office-ui-fork/backend",
            "exit_code": 0,
            "duration_ms": 812,
            "output": f"{CANARY} stdout of the transfer",
            "session_id": f"sess-{CANARY}",
        }),
        ("hermes-szarik", "tool_call", {
            "tool": "search_files",
            "query": f"{HOME_PATH}/.ssh/id_rsa {CANARY}",
            "path": f"{HOME_PATH}/dev/{CANARY}-proj/src",
            "url": f"https://{CANARY}.example/api?token=***",
            "detail": f"{CANARY} matched 12 files",
            "session_id": f"sess-{CANARY}",
        }),
        ("hermes-szarik", "file_edit", {
            "tool": "Write",
            "file": f"{HOME_PATH}/dev/{CANARY}-proj/backend/app.py",
            "diff": f"- old {CANARY}\n+ new {CANARY}",
            "note": CANARY,
        }),
        ("hermes-szarik", "session_start", {
            "cwd": f"/srv/{CANARY}-host/api",
            "session_id": f"sess-{CANARY}",
            "model": "claude-opus-4-1",
            "prompt": f"{CANARY} what the user typed",
        }),
        ("hermes-szarik", "state_change", {
            "state": "thinking",
            "detail": f"{CANARY} prompt text",
            "prompt": CANARY,
            "progress": 42,
            "session_id": f"sess-{CANARY}",
        }),
        ("hermes-szarik", "itsaplan_issue_transition", {
            "issue": f"{CANARY}-42",
            "title": f"{CANARY} issue title",
            "kind": "board",
            "status": "ok",
            "url": f"https://plan.example/{CANARY}",
            "session_id": f"sess-{CANARY}",
        }),
        ("openchamber", "command_run", {
            "command": f"git -c credential.helper='!echo {CANARY}' push https://{CANARY}.example/x",
            "cwd": f"/storage/workspaces/{CANARY}-host/ops",
            "status": "ok",
            "exit_code": 0,
            "duration_ms": 1234,
            "error": f"{CANARY} stderr tail",
        }),
        ("openchamber", "background_start", {
            "task_id": "ses_01ABCdef",
            "kind": "shell",
            "label": f"sleep {CANARY}",
            "session_id": f"sess-{CANARY}",
        }),
        ("claude", "code_completion", {
            "session_id": f"sess-{CANARY}",
            "files_touched": [f"{HOME_PATH}/{CANARY}.py"],
            "edits": 12,
            "message": f"{CANARY} summary of the completion",
        }),
        ("opencode", "widget_frobnicated", {
            f"{CANARY}_key": {"nested": [CANARY, {"deep": CANARY}]},
            "secret": CANARY,
            "tool": "widget",
            "note": f"{CANARY} free text",
        }),
    ]


def v1_state_rows() -> list:
    """Rows the v1 state doors wrote: ``Star`` roster + ``save_agents_state``.

    The payload is v1's own dict — the one ``load_agents_state()`` reads back and
    ``/status`` round-trips — so it carries ``updated_at``, which no reporter
    sends and which is the marker the exclusion uses. ``KEEPME`` marks what must
    survive the run.
    """
    return [
        ("Star", "state_change", {
            "name": "Star",
            "state": "idle",
            "detail": f"{KEEPME} v1 main state",
            "updated_at": "2026-10-16T12:00:00",
            "area": "breakroom",
            "source": "local",
            "joinKey": None,
            "authStatus": "approved",
            "authExpiresAt": None,
            "lastPushAt": None,
        }),
        ("Star", "state_change", {
            "state": "writing",
            "detail": f"{KEEPME} phase13 v1",
            "progress": 40,
            "updated_at": "2026-10-16T12:05:00",
        }),
        ("hermes-szarik", "state_change", {
            "name": "hermes-szarik",
            "state": "building",
            "detail": f"{KEEPME} pushed from the join-key door",
            "updated_at": "2026-10-16T12:10:00",
            "area": "garage",
            "source": "remote-push",
            "joinKey": f"jk-{KEEPME}",
            "authStatus": "approved",
            "authExpiresAt": "2026-10-17T12:10:00",
            "lastPushAt": "2026-10-16T12:10:00",
        }),
    ]


def clean_rows() -> list:
    """Rows a post-SOU-218 door already stored: the walk must not touch them."""
    return [
        ("openchamber", "command_run", {"command": "git push", "cwd": "backend", "exit_code": 0}),
        ("openchamber", "tool_call", {"tool": "Edit", "status": "ok", "duration_ms": 250}),
        ("claude", "state_change", {"state": "waiting_for_user", "area": "coffee",
                                    "notification_type": "permission_required"}),
        ("claude", "session_start", {"cwd": "star-office-ui-fork", "source": "startup"}),
    ]


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
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


def _config(url: str) -> "Config":
    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    return config


def _upgrade(url: str, revision: str, **kw) -> None:
    with _alembic_env(url):
        command.upgrade(_config(url), revision, **kw)


def _downgrade(url: str, revision: str) -> None:
    with _alembic_env(url):
        command.downgrade(_config(url), revision)


def _payload_text(dialect: str) -> str:
    """The column expression that hands back the stored payload as text."""
    return "payload::text" if dialect == "postgresql" else "payload"


def _cast(dialect: str) -> str:
    """How a JSON string is written into the payload column."""
    return "CAST(:payload AS jsonb)" if dialect == "postgresql" else ":payload"


def _connect(url: str):
    engine = sa.create_engine(url)
    if engine.dialect.name == "sqlite":
        @sa.event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


class _Harness:
    """Fixture shared by both backends and both suites; subclasses fix the URL.

    ``dialect`` and the URL are all a subclass has to provide: the walk suite and
    the revision suite then assert the same contract on SQLite and on Postgres.
    """

    dialect = ""
    supports_raw_json_text = True  # SQLite lets us plant corrupt payload text

    # -- fixture ---------------------------------------------------------------

    def setUp(self) -> None:
        self.workdir = tempfile.mkdtemp(prefix="sou235-")
        self.url = self.database_url(self.workdir)
        _downgrade(self.url, "base")
        _upgrade(self.url, REVISION_001)
        self.engine = _connect(self.url)
        self.conn = self.engine.connect()
        self.agents: dict = {}

    def tearDown(self) -> None:
        self.conn.close()
        self.engine.dispose()
        with contextlib.suppress(Exception):
            _downgrade(self.url, "base")
        shutil.rmtree(self.workdir, ignore_errors=True)

    def database_url(self, workdir: str) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- seeding and reading ---------------------------------------------------

    def insert_returning(self, sql: str, params: dict) -> int:
        """Insert one row and hand back its id, whichever way the dialect does it."""
        if self.dialect == "postgresql":
            return self.conn.execute(sa.text(sql + " RETURNING id"), params).scalar()
        return self.conn.execute(sa.text(sql), params).lastrowid

    def agent_id(self, name: str, agent_type: str = "guest") -> int:
        if name not in self.agents:
            self.agents[name] = self.insert_returning(
                "INSERT INTO agents (name, agent_type) VALUES (:name, :type)",
                {"name": name, "type": agent_type},
            )
        return self.agents[name]

    def seed(self, rows: list) -> list:
        """Insert ``(agent, event_type, payload)`` rows and return their ids.

        ``payload`` goes in as written — straight through the raw column, not the
        adapter — because these are the rows the *old* reporters left behind, and
        a dict may be given as text on purpose (the corrupt-payload cases).
        """
        insert = (
            "INSERT INTO events (agent_id, event_type, created_at, payload) "
            f"VALUES (:agent_id, :event_type, :created_at, {_cast(self.dialect)})"
        )
        ids = []
        for index, (agent, event_type, payload) in enumerate(rows):
            text = payload if isinstance(payload, str) else json.dumps(payload)
            ids.append(self.insert_returning(insert, {
                "agent_id": self.agent_id(agent),
                "event_type": event_type,
                # A plain literal: both dialects accept it for a TIMESTAMP column,
                # and it keeps Python 3.12's sqlite3 datetime-adapter deprecation
                # out of the suite output.
                "created_at": (FIRST_CREATED + dt.timedelta(seconds=index)).isoformat(sep=" "),
                "payload": text,
            }))
        self.ids = ids
        self.conn.commit()
        return ids

    def rows(self) -> list:
        """Every event row, oldest first, with the payload parsed."""
        found = self.conn.execute(sa.text(
            "SELECT id, agent_id, event_type, created_at, "
            f"{_payload_text(self.dialect)} AS payload FROM events ORDER BY id"
        )).mappings().all()
        out = []
        for row in found:
            payload = row["payload"]
            if isinstance(payload, (str, bytes)):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {"__unparseable__": str(payload)}
            out.append({
                "id": row["id"],
                "agent_id": row["agent_id"],
                "event_type": row["event_type"],
                "created_at": str(row["created_at"]),
                "payload": payload,
            })
        # SQLite keeps a read transaction open after a SELECT, and Alembic needs
        # the write lock in the revision tests: end it here instead of blocking.
        self.conn.commit()
        return out

    def fingerprint(self) -> str:
        """A hash over every column of every event row — any write changes it.

        The payload is normalised (sorted keys) so the hash reports writes and
        row content, never a dialect's own JSON spacing.
        """
        dump = json.dumps([
            {**row, "payload": json.dumps(row["payload"], sort_keys=True)}
            for row in self.rows()
        ], sort_keys=True, default=str)
        return hashlib.sha256(dump.encode("utf-8")).hexdigest()

    def counts(self) -> dict:
        found = {
            "events": self.conn.execute(sa.text("SELECT count(*) FROM events")).scalar(),
            "agents": self.conn.execute(sa.text("SELECT count(*) FROM agents")).scalar(),
        }
        self.conn.commit()
        return found

    def stamp(self) -> str | None:
        """The revision this database sits at."""
        from alembic.runtime.migration import MigrationContext

        with _alembic_env(self.url):
            with self.engine.connect() as conn:
                return MigrationContext.configure(conn).get_current_revision()

    def upgrade_head(self, **kw) -> None:
        _upgrade(self.url, "head", **kw)


class _WalkContract(_Harness):
    """The walk itself (SOU-235 S1)."""

    def walk(self, *, dry_run: bool = False, batch_size: int = 500) -> dict:
        summary = resanitise.resanitise_events(
            self.conn, batch_size=batch_size, dry_run=dry_run,
        )
        if not dry_run:
            self.conn.commit()
        return summary

    # -- AC 1 / 4: the projection is the door's projection --------------------

    def test_processed_rows_are_the_door_projection(self) -> None:
        rows = reporter_rows()
        self.seed(rows)
        self.walk()
        stored = {row["id"]: row["payload"] for row in self.rows()}
        for index, (_agent, event_type, payload) in enumerate(rows):
            expected = sanitize.sanitize_payload(event_type, payload)
            self.assertEqual(
                stored[self.ids[index]], expected,
                f"{event_type}: the migration stored something other than "
                f"sanitize_payload(event_type, payload)",
            )

    def test_no_canary_in_any_processed_row(self) -> None:
        """AC 1, the sweep: after the run no canary marker is in any row."""
        self.seed(reporter_rows())
        self.walk()
        for row in self.rows():
            blob = json.dumps(row["payload"], default=str).lower()
            self.assertNotIn(CANARY.lower(), blob, f"{CANARY} survived in {row['event_type']}")
            self.assertNotIn("/users/", blob, f"a home path survived in {row['event_type']}")
            self.assertNotIn("/storage/", blob, f"an absolute path survived in {row['event_type']}")

    def test_row_count_and_identity_columns_are_untouched(self) -> None:
        """AC: it never deletes events; it only rewrites payloads."""
        self.seed(reporter_rows() + v1_state_rows() + clean_rows())
        before = self.rows()
        before_counts = self.counts()
        self.walk()
        after = self.rows()
        self.assertEqual(self.counts(), before_counts, "a row was deleted or added")
        self.assertEqual(
            [(r["id"], r["agent_id"], r["event_type"], r["created_at"]) for r in after],
            [(r["id"], r["agent_id"], r["event_type"], r["created_at"]) for r in before],
            "the walk changed a row's identity, not just its payload",
        )

    def test_it_reaches_every_row_across_batches(self) -> None:
        ids = self.seed(reporter_rows() * 3)
        summary = self.walk(batch_size=4)
        self.assertEqual(summary["scanned"], len(ids), "keyset batching lost or repeated rows")
        self.assertEqual(summary["written"], summary["changed"])
        self.assertEqual(summary["changed"], len(ids), "every seeded row was raw")
        for row in self.rows():
            self.assertNotIn(CANARY.lower(), json.dumps(row["payload"], default=str).lower())

    # -- AC 6: idempotent ------------------------------------------------------

    def test_second_run_is_a_no_op(self) -> None:
        self.seed(reporter_rows() + clean_rows())
        self.walk()
        fingerprint = self.fingerprint()
        summary = self.walk()
        self.assertEqual(summary["changed"], 0, f"second run rewrote {summary['changed']} rows")
        self.assertEqual(summary["written"], 0)
        self.assertGreater(summary["unchanged"], 0)
        self.assertEqual(self.fingerprint(), fingerprint, "a no-op run still wrote bytes")

    def test_the_sanitiser_itself_is_idempotent(self) -> None:
        """The no-op property the migration leans on, tested at the source."""
        for _agent, event_type, payload in reporter_rows() + v1_state_rows() + clean_rows():
            once = sanitize.sanitize_payload(event_type, payload)
            twice = sanitize.sanitize_payload(event_type, once)
            self.assertEqual(twice, once, f"{event_type}: sanitize_payload is not idempotent")

    def test_clean_rows_are_not_rewritten(self) -> None:
        ids = self.seed(clean_rows())
        summary = self.walk()
        self.assertEqual(summary["changed"], 0, "already-sanitised rows were rewritten")
        self.assertEqual(summary["unchanged"], len(ids))

    # -- AC 8: dry run writes nothing -----------------------------------------

    def test_dry_run_reports_and_writes_nothing(self) -> None:
        self.seed(reporter_rows())
        fingerprint = self.fingerprint()
        summary = self.walk(dry_run=True)
        self.assertGreater(summary["changed"], 0, "nothing to do: the dry run proves nothing")
        self.assertEqual(summary["written"], 0, "the dry run executed an UPDATE")
        self.assertEqual(summary["scanned"], summary["changed"] + summary["unchanged"]
                         + summary["skipped_v1"])
        self.assertEqual(self.fingerprint(), fingerprint, "the dry run wrote to the table")
        # and the real run afterwards still finds the work — the dry run was a look,
        # not a partial apply
        self.assertEqual(self.walk()["changed"], summary["changed"])

    def test_dry_run_output_lists_the_counts(self) -> None:
        self.seed(reporter_rows() + v1_state_rows() + clean_rows())
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            summary = self.walk(dry_run=True)
        text = printed.getvalue()
        for key in ("scanned", "changed", "unchanged", "skipped_v1"):
            self.assertIn(key, text, f"dry-run output has no {key} count")
        self.assertIn("dry-run", text.lower())
        self.assertIn(str(summary["scanned"]), text)

    # -- AC 5: the v1 state doors are excluded (ruling) -----------------------

    def test_v1_state_rows_are_left_byte_identical(self) -> None:
        ids = self.seed(v1_state_rows())
        before = {row["id"]: row["payload"] for row in self.rows()}
        summary = self.walk()
        after = {row["id"]: row["payload"] for row in self.rows()}
        self.assertEqual(summary["skipped_v1"], len(ids))
        for event_id in ids:
            self.assertEqual(after[event_id], before[event_id],
                             f"v1 state row {event_id} was sanitised")

    def test_v1_rows_carry_their_marker_and_are_counted_as_residual(self) -> None:
        """The ruling's cost, made visible instead of hidden."""
        self.seed(v1_state_rows())
        summary = self.walk()
        self.assertEqual(summary["skipped_v1_sensitive"], len(v1_state_rows()),
                         "every v1 row here carries detail or a joinKey")
        kept = [row for row in self.rows() if row["event_type"] == "state_change"]
        self.assertTrue(any(KEEPME in json.dumps(r["payload"], default=str) for r in kept),
                        "the excluded rows should still hold their own bytes")

    def test_a_state_change_without_the_v1_marker_is_sanitised(self) -> None:
        """The exclusion is narrow: a reporter state_change is still cleaned."""
        self.seed([("hermes-szarik", "state_change", {
            "state": "thinking", "detail": f"{CANARY} prompt text",
            "session_id": f"sess-{CANARY}", "progress": 40,
        })])
        summary = self.walk()
        self.assertEqual(summary["skipped_v1"], 0)
        self.assertEqual(summary["changed"], 1)
        self.assertEqual(self.rows()[0]["payload"], {"state": "thinking"})

    def test_is_v1_state_row(self) -> None:
        yes = [
            {"state": "idle", "detail": "Idle", "updated_at": "2026-10-16T12:00:00"},
            {"name": "Star", "state": "idle", "detail": "x", "updated_at": "…",
             "joinKey": "jk", "authStatus": "approved", "lastPushAt": "…"},
        ]
        no = [
            {"state": "thinking", "session_id": "sess-1"},
            {"state": "waiting_for_user", "area": "coffee"},
            {"updated_at": "2026-10-16T12:00:00", "command": "git push /Users/x/y"},
            "not a dict",
        ]
        for payload in yes:
            self.assertTrue(resanitise.is_v1_state_row("state_change", payload),
                            f"{payload} is a v1 state row")
        for payload in no:
            self.assertFalse(resanitise.is_v1_state_row("state_change", payload),
                             f"{payload} is not a v1 state row")
        self.assertFalse(resanitise.is_v1_state_row(
            "command_run", {"updated_at": "…", "detail": "…"}))

    def test_sanitising_a_v1_row_would_break_the_v1_read_path(self) -> None:
        """Evidence for the ruling, not prose: the projection deletes the keys
        ``load_agents_state()`` and ``GET /status`` read back."""
        roster_keys_needed = {
            "detail", "updated_at", "joinKey", "authStatus", "authExpiresAt", "lastPushAt",
            "name", "source",
        }
        for _agent, _event_type, payload in v1_state_rows():
            projection = sanitize.sanitize_payload("state_change", payload)
            lost = roster_keys_needed & set(payload) - set(projection)
            self.assertTrue(lost, f"{payload} would survive the sanitiser untouched")

    # -- the payload shapes the walk must not choke on ------------------------

    def test_non_object_and_unparseable_payloads_become_the_empty_projection(self) -> None:
        if not self.supports_raw_json_text:
            self.skipTest("Postgres JSONB cannot hold an unparseable payload")
        ids = self.seed([
            ("hermes-szarik", "tool_call", '"just a string"'),
            ("hermes-szarik", "tool_call", '["a", "list"]'),
            ("hermes-szarik", "tool_call", '{not json at all'),
            ("hermes-szarik", "tool_call", 'null'),
        ])
        summary = self.walk()
        self.assertEqual(summary["corrupt"], len(ids))
        self.assertEqual(summary["changed"], len(ids))
        for row in self.rows():
            self.assertEqual(row["payload"], {}, f"{row['id']} kept its junk payload")

    def test_an_empty_database_is_a_clean_no_op(self) -> None:
        summary = self.walk()
        self.assertEqual(summary["scanned"], 0)
        self.assertEqual(summary["changed"], 0)
        self.assertEqual(summary["written"], 0)
        self.assertEqual(summary["unchanged"], 0)

    def test_summary_shape(self) -> None:
        self.seed(reporter_rows())
        summary = self.walk()
        for key in ("scanned", "changed", "unchanged", "skipped_v1",
                    "skipped_v1_sensitive", "corrupt", "written", "dry_run"):
            self.assertIn(key, summary)
        self.assertIsInstance(summary["by_event_type"], dict)

    def test_the_walk_owns_no_transaction(self) -> None:
        """A rolled-back caller must leave nothing behind (Alembic's transaction)."""
        self.seed(reporter_rows())
        resanitise.resanitise_events(self.conn, dry_run=False)
        self.conn.rollback()
        self.assertIn(CANARY, json.dumps(self.rows()[0]["payload"], default=str),
                      "the walk committed by itself")


REVISION_002 = "002_resanitise_event_payloads"


class _RevisionContract(_Harness):
    """The Alembic revision and the CLI that drives it (SOU-235 S2).

    Same contract as the walk, but reached the way an operator reaches it:
    ``alembic upgrade head``, with the stamp, the version table and the
    command line in the picture.
    """

    def test_upgrade_head_applies_the_revision_and_stamps_it(self) -> None:
        raw, v1, clean = reporter_rows(), v1_state_rows(), clean_rows()
        self.seed(raw + v1 + clean)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            self.upgrade_head()
        summary = resanitise.LAST_SUMMARY or {}
        self.assertEqual(self.stamp(), REVISION_002, "the revision did not stamp")
        self.assertEqual(summary["scanned"], len(raw) + len(v1) + len(clean))
        self.assertEqual(summary["changed"], len(raw), "raw rows were not rewritten")
        self.assertEqual(summary["unchanged"], len(clean))
        self.assertEqual(summary["skipped_v1"], len(v1))
        self.assertEqual(summary["written"], summary["changed"])
        self.assertIn("resanitise", printed.getvalue(),
                      "the run printed no counts (AC 2 needs them pasted)")
        for row in self.rows():
            blob = json.dumps(row["payload"], default=str)
            self.assertNotIn(CANARY, blob)
            self.assertNotIn("/Users/", blob)
            if KEEPME in blob:
                self.assertIn("updated_at", blob, "only a v1 row may keep its bytes")

    def test_a_second_upgrade_head_runs_nothing(self) -> None:
        self.seed(reporter_rows())
        self.upgrade_head()
        fingerprint = self.fingerprint()
        resanitise.LAST_SUMMARY = None
        self.upgrade_head()
        self.assertIsNone(resanitise.LAST_SUMMARY, "the revision ran a second time")
        self.assertEqual(self.stamp(), REVISION_002)
        self.assertEqual(self.fingerprint(), fingerprint, "a repeat upgrade wrote bytes")

    def test_downgrade_moves_the_stamp_only(self) -> None:
        """A data migration cannot hand the raw text back, and must not try."""
        self.seed(reporter_rows() + v1_state_rows())
        self.upgrade_head()
        fingerprint, counts = self.fingerprint(), self.counts()
        _downgrade(self.url, REVISION_001)
        self.assertEqual(self.stamp(), REVISION_001)
        self.assertEqual(self.counts(), counts, "a downgrade removed rows")
        self.assertEqual(self.fingerprint(), fingerprint,
                         "downgrade rewrote payloads instead of just moving the stamp")

    def test_reapplying_after_a_downgrade_finds_no_work(self) -> None:
        """Idempotent across stamping, not just within one process."""
        self.seed(reporter_rows() + v1_state_rows())
        self.upgrade_head()
        fingerprint = self.fingerprint()
        _downgrade(self.url, REVISION_001)
        self.upgrade_head()
        self.assertEqual(self.stamp(), REVISION_002)
        self.assertEqual(resanitise.LAST_SUMMARY["changed"], 0,
                         "re-running the walk on clean rows did work")
        self.assertEqual(self.fingerprint(), fingerprint)

    def test_the_revision_changes_no_table_shape(self) -> None:
        """Data only: the adapter's models must stay equal to the schema."""
        insp = sa.inspect(self.engine)
        self.upgrade_head()
        insp = sa.inspect(self.engine)
        self.assertEqual(set(insp.get_table_names()),
                         {"agents", "events", "sessions", "alembic_version"})
        self.assertEqual(
            sorted(c["name"] for c in insp.get_columns("events")),
            ["agent_id", "created_at", "event_type", "id", "payload"],
        )
        self.assertIn("idx_events_agent_created",
                      {i["name"] for i in insp.get_indexes("events")})

    def test_offline_sql_mode_skips_the_walk_instead_of_crashing(self) -> None:
        """``--sql`` has no rows to read; it must say so and print DDL only."""
        self.seed(reporter_rows())
        fingerprint = self.fingerprint()
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            _upgrade(self.url, "head", sql=True)
        text = printed.getvalue()
        self.assertIn("resanitise", text.lower(), "offline mode said nothing about the walk")
        self.assertNotIn("UPDATE events", text.upper(), "offline mode emitted payload writes")
        self.assertEqual(self.stamp(), REVISION_001, "an offline run stamped the database")
        self.assertEqual(self.fingerprint(), fingerprint, "an offline run wrote rows")

    def test_alembic_x_dry_run_writes_and_stamps_nothing(self) -> None:
        """``alembic -x dry_run=1 upgrade head`` may not leave a stamped, dirty DB."""
        self.seed(reporter_rows())
        fingerprint = self.fingerprint()
        config = _config(self.url)
        config.cmd_opts = argparse.Namespace(x=["dry_run=1"])
        with self.assertRaises(Exception) as caught:
            with _alembic_env(self.url):
                command.upgrade(config, "head")
        self.assertIn("dry run", str(caught.exception).lower())
        self.assertEqual(self.stamp(), REVISION_001, "a dry run stamped the revision")
        self.assertEqual(self.fingerprint(), fingerprint, "a dry run wrote rows")

    # -- the command line an operator actually types --------------------------

    def test_cli_applies_the_revision_and_stamps(self) -> None:
        self.seed(reporter_rows() + v1_state_rows())
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), _alembic_env(self.url):
            code = resanitise.main(["--database-url", self.url])
        self.assertEqual(code, 0)
        self.assertEqual(self.stamp(), REVISION_002)
        self.assertIn("applied", printed.getvalue())
        for row in self.rows():
            if row["event_type"] == "command_run":
                self.assertNotIn(CANARY, json.dumps(row["payload"], default=str))

    def test_cli_dry_run_writes_nothing_and_leaves_the_stamp(self) -> None:
        self.seed(reporter_rows())
        fingerprint = self.fingerprint()
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), _alembic_env(self.url):
            code = resanitise.main(["--database-url", self.url, "--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("dry-run", printed.getvalue())
        self.assertIn("changed", printed.getvalue())
        self.assertEqual(self.stamp(), REVISION_001, "the CLI dry run stamped the DB")
        self.assertEqual(self.fingerprint(), fingerprint)

    def test_cli_says_so_when_the_revision_is_already_applied(self) -> None:
        self.seed(reporter_rows())
        with _alembic_env(self.url):
            self.assertEqual(resanitise.main(["--database-url", self.url]), 0)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), _alembic_env(self.url):
            code = resanitise.main(["--database-url", self.url])
        self.assertEqual(code, 0)
        self.assertIn("already applied", printed.getvalue())

    def test_cli_refuses_a_database_with_no_schema(self) -> None:
        url = f"sqlite:///{os.path.join(self.workdir, 'empty.db')}"
        printed = io.StringIO()
        with contextlib.redirect_stderr(printed), _alembic_env(url):
            code = resanitise.main(["--database-url", url, "--dry-run"])
        self.assertEqual(code, 2)
        self.assertIn("no SOU schema", printed.getvalue())


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
class SqliteWalkTest(_WalkContract, unittest.TestCase):
    """SQLite fallback backend: payload is TEXT holding the JSON string."""

    dialect = "sqlite"

    def database_url(self, workdir: str) -> str:
        return f"sqlite:///{os.path.join(workdir, 'sou235.db')}"


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
class SqliteRevisionTest(_RevisionContract, unittest.TestCase):
    """The revision applied on SQLite, the M1 Max instance's backend."""

    dialect = "sqlite"

    def database_url(self, workdir: str) -> str:
        return f"sqlite:///{os.path.join(workdir, 'sou235-rev.db')}"


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "set TEST_POSTGRES_URL to run the Postgres leg of the migration "
    "(scripts/dev_postgres.sh start)",
)
class PostgresWalkTest(_WalkContract, unittest.TestCase):
    """Postgres primary backend: payload is JSONB."""

    dialect = "postgresql"
    supports_raw_json_text = False

    def database_url(self, _workdir: str) -> str:
        return os.environ["TEST_POSTGRES_URL"]


@unittest.skipIf(sa is None, f"database extras not installed: {_IMPORT_ERROR}")
@unittest.skipUnless(
    os.environ.get("TEST_POSTGRES_URL"),
    "set TEST_POSTGRES_URL to run the Postgres leg of the migration "
    "(scripts/dev_postgres.sh start)",
)
class PostgresRevisionTest(_RevisionContract, unittest.TestCase):
    """The revision applied on Postgres, the homelab primary."""

    dialect = "postgresql"
    supports_raw_json_text = False

    def database_url(self, _workdir: str) -> str:
        return os.environ["TEST_POSTGRES_URL"]


if __name__ == "__main__":
    unittest.main(verbosity=2)
