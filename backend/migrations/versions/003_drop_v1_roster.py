"""drop the v1 roster: the rows the v1 state paths wrote, and their agents (SOU-226)

Revision ID: 003_drop_v1_roster
Revises: 002_resanitise_event_payloads
Create Date: 2026-10-17

SOU-226 deleted the v1 renderer and every v1 route (``/status``, ``/agents``,
``/set_state``, ``/agent-push``, ``/join-agent``…). The rows those paths wrote are
now dead data, and 002 deliberately left them raw (``detail`` free text,
``joinKey``) because v1 still read them back. Nothing reads them any more, so this
revision removes them:

* every event that ``resanitise.is_v1_state_row`` recognises (the v1 dict: an
  ``updated_at`` marker plus only v1/state_change keys), and
* the agents those rows belonged to when nothing else is left of them: the v1
  main agent (``agent_type = 'main'``, "Star") always, and v1 join-flow guests
  that have no other events.

Agents that also reported through ``POST /events`` keep their row and every
non-v1 event. **Data only**, one transaction, idempotent (a second run finds
nothing). ``-x dry_run=1`` counts and rolls back. ``downgrade`` moves the stamp
only: deleted rows are gone by design; restore the backup the runbook asks for.

Usage::

    uv run alembic -c backend/alembic.ini -x dry_run=1 upgrade head   # count, roll back
    uv run alembic -c backend/alembic.ini upgrade head                # apply
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import context, op

import resanitise

revision: str = "003_drop_v1_roster"
down_revision: Union[str, None] = "002_resanitise_event_payloads"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _dry_run() -> bool:
    try:
        args = context.get_x_argument(as_dictionary=True) or {}
    except Exception:  # pragma: no cover - library callers without cmd_opts
        args = {}
    return "dry_run" in args and (args["dry_run"] or "").strip().lower() in {"", "1", "true", "yes", "y", "on"}


def drop_v1_roster(bind, *, batch_size: int = resanitise.DEFAULT_BATCH_SIZE) -> dict:
    """Delete the v1 state rows and the agents left empty by that. Returns counts.

    The caller owns the transaction. Shared with the tests.
    """
    dialect = resanitise.dialect_name(bind)
    column = resanitise._payload_text_column(dialect)

    def select(first: bool):
        where = "" if first else "WHERE id > :after "
        return sa.text(
            f"SELECT id, agent_id, event_type, {column} AS payload FROM events {where}ORDER BY id LIMIT :batch"
        )

    v1_event_ids, touched_agents = [], set()
    scanned = 0
    for rows in resanitise._batches(bind, batch_size, select):
        for row in rows:
            scanned += 1
            ok, payload = resanitise._parse(row["payload"])
            if ok and resanitise.is_v1_state_row(row["event_type"], payload):
                v1_event_ids.append(row["id"])
                touched_agents.add(row["agent_id"])

    for event_id in v1_event_ids:
        bind.execute(sa.text("DELETE FROM events WHERE id = :id"), {"id": event_id})

    mains = {r[0] for r in bind.execute(sa.text("SELECT id FROM agents WHERE agent_type = 'main'"))}
    dropped_agents = 0
    for agent_id in sorted(touched_agents | mains, key=str):
        left = bind.execute(sa.text("SELECT COUNT(*) FROM events WHERE agent_id = :a"), {"a": agent_id}).scalar()
        if agent_id in mains or not left:
            bind.execute(sa.text("DELETE FROM events WHERE agent_id = :a"), {"a": agent_id})
            bind.execute(sa.text("DELETE FROM agents WHERE id = :a"), {"a": agent_id})
            dropped_agents += 1
    return {"scanned": scanned, "v1_events": len(v1_event_ids), "agents": dropped_agents}


def upgrade() -> None:
    if context.is_offline_mode():
        print("drop_v1_roster: offline (--sql) mode has no rows to read; skipped")
        return
    bind = op.get_bind()
    summary = drop_v1_roster(bind)
    dry = _dry_run()
    print(
        f"drop_v1_roster ({'dry-run, rolled back' if dry else 'applied'}): "
        f"scanned={summary['scanned']} v1_events={summary['v1_events']} agents={summary['agents']}"
    )
    if dry:
        raise RuntimeError("dry run: rolled back on purpose (nothing written, stamp unchanged)")


def downgrade() -> None:
    """Stamp only: the v1 rows are not restored (use the pre-upgrade backup)."""
