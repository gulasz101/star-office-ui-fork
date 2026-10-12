"""initial schema: agents, events, sessions

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-10-12

One schema, two backends (ADR-034). Postgres is the homelab primary, SQLite the
local-dev fallback, so the dialect split lives here and nowhere else:

* row id      one portable ``Integer`` primary key: SQLAlchemy renders it as
  ``SERIAL`` on Postgres, and as ``INTEGER PRIMARY KEY AUTOINCREMENT`` on SQLite
  through the ``sqlite_autoincrement`` table flag
* payload     Postgres ``JSONB``  ->  SQLite ``TEXT`` holding the JSON string,
  parsed by the adapter on read (SOU-18). JSONB does not exist on SQLite, so
  this is the only genuinely split type in the schema.
* timestamps  ``TIMESTAMP`` without time zone on both. UTC is an
  application-layer contract (ADR-035); switching to ``TIMESTAMPTZ`` is a
  deliberate later migration, not a side effect of this one.

``events`` and ``sessions`` reference ``agents`` and cascade on delete, so
dropping an agent never leaves orphan activity behind. The two indexes back the
only reads Phase 1 needs: the latest activity of one agent, and the activity
chain of one session.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NAME_LENGTH = 255
AGENT_TYPE_LENGTH = 50
EVENT_TYPE_LENGTH = 100

IDX_EVENTS_AGENT_CREATED = "idx_events_agent_created"
IDX_SESSIONS_AGENT_STARTED = "idx_sessions_agent_started"


def _is_postgres() -> bool:
    """Dialect of the run, in online and in --sql (offline) mode alike."""
    return op.get_context().dialect.name == "postgresql"


def _row_id() -> sa.Column:
    """SERIAL on Postgres, INTEGER PRIMARY KEY AUTOINCREMENT on SQLite."""
    return sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True)


def _payload(is_postgres: bool) -> sa.Column:
    type_ = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.Text()
    return sa.Column("payload", type_, nullable=False)


def _agent_id(table: str) -> sa.Column:
    return sa.Column(
        "agent_id",
        sa.Integer(),
        sa.ForeignKey("agents.id", name=f"fk_{table}_agent_id", ondelete="CASCADE"),
        nullable=False,
    )


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(),
        server_default=sa.text("CURRENT_TIMESTAMP"),
        nullable=False,
    )


def upgrade() -> None:
    is_postgres = _is_postgres()
    # SQLite needs the table-level flag to emit a real AUTOINCREMENT column;
    # Postgres ignores it because the SERIAL rendering already owns a sequence.
    sqlite_kw: dict = {} if is_postgres else {"sqlite_autoincrement": True}

    op.create_table(
        "agents",
        _row_id(),
        sa.Column("name", sa.String(NAME_LENGTH), nullable=False),
        sa.Column("agent_type", sa.String(AGENT_TYPE_LENGTH), nullable=False),
        _created_at(),
        **sqlite_kw,
    )

    op.create_table(
        "events",
        _row_id(),
        _agent_id("events"),
        sa.Column("event_type", sa.String(EVENT_TYPE_LENGTH), nullable=False),
        _payload(is_postgres),
        _created_at(),
        **sqlite_kw,
    )
    op.create_index(
        IDX_EVENTS_AGENT_CREATED, "events", ["agent_id", sa.text("created_at DESC")]
    )

    op.create_table(
        "sessions",
        _row_id(),
        _agent_id("sessions"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        **sqlite_kw,
    )
    op.create_index(
        IDX_SESSIONS_AGENT_STARTED,
        "sessions",
        ["agent_id", sa.text("started_at DESC")],
    )


def downgrade() -> None:
    # Reverse dependency order: the children first, then the agents they point at.
    op.drop_index(IDX_SESSIONS_AGENT_STARTED, table_name="sessions")
    op.drop_table("sessions")
    op.drop_index(IDX_EVENTS_AGENT_CREATED, table_name="events")
    op.drop_table("events")
    op.drop_table("agents")
