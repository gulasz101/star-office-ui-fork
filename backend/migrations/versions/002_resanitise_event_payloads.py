"""re-sanitise every stored event payload (rows from before SOU-218 are raw)

Revision ID: 002_resanitise_event_payloads
Revises: 001_initial_schema
Create Date: 2026-10-16

SOU-218 gated ``POST /events`` through ``backend/sanitize.py``; it did not touch
the rows already in the table. This is that follow-up (SOU-235): walk ``events``
and rewrite each payload through the same projection, so a pre-SOU-218 database
— the ~1200 ``hermes-szarik`` rows on the M1 Max instance above all, with full
command lines, ``/Users/…`` home paths, ``query``/``url`` fields and session ids
— is safe to back up, replicate and eventually serve.

**Data only.** No table, column, index or constraint is created, altered or
dropped, so ``backend/db.py``'s models stay equal to the schema and
``alembic check`` has nothing to complain about. The whole walk is one
transaction: either every payload is rewritten or nothing is, which is why the
runbook asks for a copy of the database rather than a checkpoint strategy.

**Idempotent.** A row is written only when its parsed payload differs from
``sanitize_payload(event_type, payload)``; ``sanitize_payload`` is itself
idempotent (asserted in ``backend/tests/test_resanitise.py``), so a second run
reports ``changed=0`` and writes nothing.

**Rows the v1 state doors wrote are left alone** (the ``Star`` roster row and
``app.save_agents_state()``): they hold v1's own dict, which ``load_agents_state``
and ``GET /status`` read back, and the projection would delete the
``detail``/``updated_at``/``joinKey``/``authStatus`` keys that round-trip needs.
Ruling, evidence and the accepted residual are on SOU-235; the door itself is
SOU-234, v1's removal is SOU-226. Skipped rows are counted, not hidden.

Usage::

    uv run alembic -c backend/alembic.ini upgrade head                    # apply
    uv run alembic -c backend/alembic.ini -x dry_run=1 upgrade head       # count, roll back
    uv run alembic -c backend/alembic.ini -x batch_size=250 upgrade head  # smaller batches
    uv run python backend/resanitise.py --dry-run                         # counts, no stamp

``--sql`` (offline) mode has no rows to read: the walk says so and skips itself.
``downgrade`` moves the version stamp only — the raw text this revision removed
is gone by design and is not handed back.

Implementation: ``backend/resanitise.py`` (shared with the CLI and the tests).
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import context, op

import resanitise

revision: str = "002_resanitise_event_payloads"
down_revision: Union[str, None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TRUE = {"", "1", "true", "yes", "y", "on"}


def _x_args() -> dict:
    """``alembic -x NAME=VALUE`` arguments, empty when none were given."""
    try:
        return context.get_x_argument(as_dictionary=True) or {}
    except Exception:  # pragma: no cover - no cmd_opts (library callers)
        return {}


def _flag(args: dict, name: str) -> bool:
    """``-x dry_run`` and ``-x dry_run=1`` both mean yes; absent means no."""
    if name not in args:
        return False
    return (args[name] or "").strip().lower() in _TRUE


def _batch_size(args: dict) -> int:
    raw = str(args.get("batch_size") or "").strip()
    if not raw:
        return resanitise.DEFAULT_BATCH_SIZE
    try:
        size = int(raw)
    except ValueError:
        raise ValueError(f"-x batch_size={raw!r} is not an integer") from None
    if size < 1:
        raise ValueError(f"-x batch_size={size} must be at least 1")
    return size


def upgrade() -> None:
    if op.get_context().as_sql:
        # Offline mode renders SQL for a database we never connect to, so there
        # are no rows to read. Say so rather than pretending the walk ran.
        print(
            "resanitise: offline (--sql) mode has no rows to read — the walk is skipped; "
            "run `alembic upgrade head` against the database to apply it",
            flush=True,
        )
        return

    args = _x_args()
    dry_run = _flag(args, "dry_run")
    summary = resanitise.resanitise_events(
        op.get_bind(), batch_size=_batch_size(args), dry_run=dry_run,
    )

    if dry_run:
        # Raising is the only way a revision can refuse its own stamp: the
        # transaction Alembic opened is rolled back, so nothing is written and
        # the database is not left marked as migrated with dirty rows in it.
        raise RuntimeError(
            "resanitise: dry run — {} row(s) scanned, {} would change, {} would be "
            "skipped as v1 state rows; nothing written and this revision is not "
            "stamped. Re-run without -x dry_run to apply.".format(
                summary["scanned"], summary["changed"], summary["skipped_v1"]
            )
        )


def downgrade() -> None:
    print(
        "resanitise: downgrade moves the version stamp only. The raw payloads this "
        "revision replaced are gone by design and are not restored here — restore a "
        "copy of the database if you need the old bytes.",
        flush=True,
    )
