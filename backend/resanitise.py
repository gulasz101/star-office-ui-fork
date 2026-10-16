"""One-off re-sanitising of every stored event payload (SOU-235, Phase 13).

SOU-218 made ``POST /events`` store only ``sanitize.sanitize_payload(event_type,
payload)`` — the safe projection (docs/public-lodge.md §3). It fixed the *door*,
not the *room*: every row written before it is still raw. The M1 Max dashboard
DB alone holds ~1200 ``hermes-szarik`` rows with full command lines, ``/Users/…``
home paths, ``query``/``url`` fields and session ids. A public face must never
serve, back up or replicate those, and a ``pg_dump`` / ``cp sou.db`` is exactly
what an operator does before touching a database.

So: walk ``events``, run every payload through the sanitiser again, and write
back only what differs. This module is that walk — one implementation shared by
the Alembic revision (``backend/migrations/versions/002_resanitise_event_payloads.py``),
the command line (``python backend/resanitise.py``) and the tests
(``backend/tests/test_resanitise.py``).

What it guarantees, and what it deliberately does not:

* **The projection, not a new one.** A processed row ends up exactly where a
  fresh ingest would have put it: ``sanitize.sanitize_payload(event_type,
  payload)``. Nothing is invented here, so the two can never drift apart.
* **Idempotent.** ``sanitize_payload`` is idempotent (tested), a row is written
  only when its parsed payload differs from the projection, and a second run
  therefore reports ``changed=0``.
* **Never destructive.** No ``DELETE``, no new rows, no touch on ``id``,
  ``agent_id``, ``event_type`` or ``created_at``. Payloads that are not JSON
  objects (junk text, a list, a bare string) become ``{}`` — the same answer the
  door gives a non-dict — and are counted as ``corrupt``.
* **Bounded memory.** Rows are read in keyset batches (``WHERE id > :after
  ORDER BY id LIMIT :batch``), never with an offset, never all at once.
* **One transaction, owned by the caller.** The walk issues no ``COMMIT``: inside
  Alembic it rides the migration's transaction (all payloads rewritten or
  nothing), and a dry run rides a rollback.
* **The v1 state doors are skipped.** ``Star``'s roster row and
  ``app.save_agents_state()`` write v1's own dict and read it back; sanitising
  them would delete ``detail``/``updated_at``/``joinKey``/``authStatus`` from the
  rows ``load_agents_state()`` and ``GET /status`` round-trip. Ruling and
  evidence on SOU-235; the door itself is SOU-234, v1's removal is SOU-226.

Run it (see ``docs/resanitise-migration.md`` for the M1 Max procedure)::

    uv run python backend/resanitise.py --database-url sqlite:////tmp/copy.db --dry-run
    uv run python backend/resanitise.py --database-url sqlite:////tmp/copy.db
    uv run alembic -c backend/alembic.ini upgrade head   # the stamped run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterator, Mapping

try:  # package-style import (tests) and flat import (backend/ on sys.path)
    from . import sanitize
except ImportError:  # pragma: no cover - the repo runs backend/ on sys.path
    import sanitize

import sqlalchemy as sa

DEFAULT_BATCH_SIZE = 500

#: The last summary the walk produced, so a caller that drove it through Alembic
#: (where the return value is out of reach) can still print the counts.
LAST_SUMMARY: dict | None = None

# --- the v1 state-door exclusion (the ruling on SOU-235) ---------------------
# ``updated_at`` is written by every v1 state door and by no reporter:
# app.save_state() stamps it, /set_state stamps it, /agent-push stamps it, the
# roster dict carries it, and init_database()'s seed row carries it. A state row
# with a key outside this vocabulary came through the event door instead, and is
# sanitised — the narrow exclusion, chosen so the privacy-safe direction wins
# whenever a row is ambiguous.
V1_STATE_MARKER = "updated_at"
V1_STATE_KEYS = frozenset({
    "agentId", "isMain", "name", "avatar", "state", "detail", "progress",
    "updated_at", "ttl_seconds", "area", "source", "position", "joinKey",
    "authStatus", "authApprovedAt", "authExpiresAt", "authRejectedAt",
    "lastPushAt",
})
#: Excluded rows whose content is the reason the exclusion has a cost: free text
#: and a join key. Counted so whoever runs the migration can see the residual.
V1_SENSITIVE_KEYS = ("detail", "joinKey")


def is_v1_state_row(event_type, payload: Any) -> bool:
    """Is this a row the v1 state doors wrote (and must therefore not touch)?"""
    if sanitize.text_of(event_type, 100).strip().lower() != "state_change":
        return False
    if not isinstance(payload, dict):
        return False
    keys = {key for key in payload if isinstance(key, str)}
    if len(keys) != len(payload):  # a non-string key is not v1's dict
        return False
    if V1_STATE_MARKER not in keys:
        return False
    return keys <= (V1_STATE_KEYS | sanitize.allowed_keys("state_change"))


def _is_sensitive_v1(payload) -> bool:
    """Does an excluded v1 row still visibly carry free text or a credential?
    The roster snapshot nests ``joinKey`` inside ``detail``, so the walk is
    recursive — the point of the count is to not understate the residual."""

    def scan(node) -> bool:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key in V1_SENSITIVE_KEYS and (
                    (isinstance(value, str) and value.strip())
                    or (key == "joinKey" and value not in (None, ""))
                ):
                    return True
                if scan(value):
                    return True
        elif isinstance(node, (list, tuple)):
            return any(scan(item) for item in node)
        return False

    return scan(payload)


# --- dialect split: one JSON column, two ways to spell it -------------------
# Postgres keeps ``events.payload`` as JSONB (read it as text, write it back with
# a cast); SQLite keeps it as TEXT holding the JSON string (ADR-034). Everything
# else below is the same code on both.

def dialect_name(bind) -> str:
    dialect = getattr(bind, "dialect", None)
    name = getattr(dialect, "name", "")
    if name not in ("sqlite", "postgresql"):
        raise ValueError(
            f"resanitise supports sqlite or postgresql, not {name or bind!r} (ADR-034)"
        )
    return name


def _payload_text_column(dialect: str) -> str:
    return "payload::text" if dialect == "postgresql" else "payload"


def _payload_write(dialect: str) -> str:
    return "CAST(:payload AS jsonb)" if dialect == "postgresql" else ":payload"


def _select_batch(dialect: str, first: bool) -> sa.text:
    """Keyset pagination needs no predicate on the first batch: ids are VARCHAR(36)
    in the 001 schema (INTEGER only on legacy files), and there is no value that
    is safely ``below every id`` for both types on both backends."""
    where = "" if first else "WHERE id > :after "
    return sa.text(
        "SELECT id, event_type, " + _payload_text_column(dialect) + " AS payload "
        f"FROM events {where}ORDER BY id LIMIT :batch"
    )


def _update_payload(dialect: str) -> sa.text:
    return sa.text(
        f"UPDATE events SET payload = {_payload_write(dialect)} WHERE id = :id"
    )


def _batches(bind, batch_size: int, select) -> Iterator[list]:
    """Keyset pagination by id: no OFFSET, no full-table load, no repeats.
    ``select`` is a callable taking ``first`` (bool) so the first batch can skip
    the ``WHERE`` — the id column is opaque (VARCHAR on modern schemas)."""
    if batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    after = None
    while True:
        rows = (bind.execute(select(first=True), {"batch": batch_size}) if after is None
                else bind.execute(select(first=False), {"after": after, "batch": batch_size})).mappings().all()
        if not rows:
            return
        after = rows[-1]["id"]
        yield rows


def _parse(stored: Any) -> tuple[bool, Any]:
    """The payload as Python, and whether it was a usable JSON document.

    Postgres hands JSONB back already parsed; SQLite hands the JSON string back.
    """
    if isinstance(stored, (str, bytes)):
        try:
            return True, json.loads(stored)
        except ValueError:
            return False, None
    return True, stored


def resanitise_events(bind, *, batch_size: int = DEFAULT_BATCH_SIZE,
                      dry_run: bool = False, quiet: bool = False) -> dict:
    """Rewrite every stored payload through ``sanitize.sanitize_payload``.

    Returns the counts; ``written`` is the number of UPDATEs actually executed,
    which is 0 in a dry run and equals ``changed`` otherwise. The caller owns the
    transaction — nothing here commits.
    """
    global LAST_SUMMARY
    dialect = dialect_name(bind)
    select, update = lambda first: _select_batch(dialect, first), _update_payload(dialect)

    summary = {
        "scanned": 0, "changed": 0, "unchanged": 0,
        "skipped_v1": 0, "skipped_v1_sensitive": 0, "corrupt": 0,
        "written": 0, "dry_run": dry_run, "batch_size": batch_size,
        "by_event_type": {},
    }

    for batch in _batches(bind, batch_size, select):
        writes = []
        for row in batch:
            summary["scanned"] += 1
            event_type = row["event_type"]
            parsed, payload = _parse(row["payload"])
            if not parsed or not isinstance(payload, dict):
                summary["corrupt"] += 1
            if parsed and is_v1_state_row(event_type, payload):
                summary["skipped_v1"] += 1
                if _is_sensitive_v1(payload):
                    summary["skipped_v1_sensitive"] += 1
                continue
            clean = sanitize.sanitize_payload(event_type, payload)
            if parsed and isinstance(payload, dict) and payload == clean:
                summary["unchanged"] += 1
                continue
            summary["changed"] += 1
            summary["by_event_type"][str(event_type)] = \
                summary["by_event_type"].get(str(event_type), 0) + 1
            if not dry_run:
                writes.append({"id": row["id"], "payload": json.dumps(clean)})
        if writes:
            bind.execute(update, writes)
            summary["written"] += len(writes)

    LAST_SUMMARY = summary
    if not quiet:
        print(format_summary(summary), flush=True)
    return summary


def format_summary(summary: Mapping) -> str:
    """The count block: what an operator reads, and what the ticket records."""
    mode = "dry-run, nothing written" if summary["dry_run"] else "written"
    lines = [
        "resanitise ({}): scanned={} changed={} unchanged={} skipped_v1={} "
        "skipped_v1_sensitive={} corrupt={} updates={} batch_size={}".format(
            mode, summary["scanned"], summary["changed"], summary["unchanged"],
            summary["skipped_v1"], summary["skipped_v1_sensitive"],
            summary["corrupt"], summary["written"], summary["batch_size"],
        )
    ]
    if summary["by_event_type"]:
        per_type = ", ".join(
            f"{name}={count}" for name, count in sorted(summary["by_event_type"].items())
        )
        lines.append(f"  changed by event type: {per_type}")
    else:
        lines.append("  changed by event type: (nothing)")
    if summary["skipped_v1"]:
        lines.append(
            f"  note: {summary['skipped_v1']} v1 state rows left exactly as they are "
            f"(SOU-235 ruling; {summary['skipped_v1_sensitive']} still carrying free "
            f"text or a joinKey — closed by SOU-234 and SOU-226, not here)"
        )
    if summary["corrupt"]:
        lines.append(
            f"  note: {summary['corrupt']} payloads were not a JSON object and became "
            f"{{}} — the same answer POST /events gives a non-dict payload"
        )
    return "\n".join(lines)


# --- command line -------------------------------------------------------------

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
REVISION = "002_resanitise_event_payloads"


def _parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="resanitise",
        description="Re-run every stored event payload through the SOU-218 ingest "
                    "sanitiser (SOU-235). Dry-run writes nothing at all.",
    )
    parser.add_argument("--database-url", default=None,
                        help="Target database (default: DATABASE_URL, else backend/sou.db)")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                        help=f"Rows per keyset batch (default {DEFAULT_BATCH_SIZE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Count rows scanned / changed / unchanged without writing")
    parser.add_argument("--re-scan", action="store_true",
                        help="Walk the table directly instead of applying the revision "
                             "(for rows that arrived after the stamp); idempotent")
    return parser.parse_args(argv)


def _has_sou_schema(engine) -> bool:
    """Are the SOU tables even in this database?"""
    names = set(sa.inspect(engine).get_table_names())
    return {"events", "agents"} <= names


def main(argv=None) -> int:
    """Apply the migration, or report what it would do. Exits 0 on success."""
    args = _parse_args(argv)
    try:  # backend/ on the path when run from inside backend/, as the other modules assume
        from db import create_db_engine, resolve_database_url  # adapter owns URL rules
    except ImportError:  # or `python -m backend.resanitise` from the repo root
        from backend.db import create_db_engine, resolve_database_url

    url = resolve_database_url(args.database_url)
    engine = create_db_engine(url)
    try:
        if not _has_sou_schema(engine):
            print("resanitise: this database has no SOU schema (nothing to walk) — "
                  "start the backend once, or run `alembic upgrade head`", file=sys.stderr)
            return 2

        if args.dry_run:
            with engine.connect() as conn:
                summary = resanitise_events(conn, batch_size=args.batch_size, dry_run=True)
                conn.rollback()  # a dry run must not even keep a write in flight
            return 0 if summary["written"] == 0 else 1

        if args.re_scan:
            with engine.connect() as conn:
                summary = resanitise_events(conn, batch_size=args.batch_size)
                conn.commit()
            print(f"resanitise: {summary['written']} payload(s) committed (no stamp change)")
            return 0

        from alembic import command
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext

        config = Config(ALEMBIC_INI)
        config.set_main_option("sqlalchemy.url", url)
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        if current == REVISION:
            print(f"resanitise: revision {REVISION} is already applied; "
                  f"use --re-scan to walk the table again")
            return 0
        if current is None:
            print("resanitise: this database has no SOU schema (nothing to migrate) — "
                  "start the backend once, or run `alembic upgrade head`", file=sys.stderr)
            return 2
        command.upgrade(config, "head")
        summary = LAST_SUMMARY or {}
        print(f"resanitise: revision {REVISION} applied; "
              f"{summary.get('written', 0)} payload(s) rewritten")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
