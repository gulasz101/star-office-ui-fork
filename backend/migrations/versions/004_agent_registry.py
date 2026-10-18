"""agent registry: ingest tokens and room personality live in the database (SOU-291)

Revision ID: 004_agent_registry
Revises: 003_drop_v1_roster
Create Date: 2026-10-18

The repo knows no agent (owner ruling, 2026-10-18). A token registered here is
accepted at ``POST /events`` for exactly one agent, and the agent row carries its
room personality, which the renderer reads from the API instead of a name map:

* ``agents.room_type`` (nullable): one of the renderer's room types; null means
  the universal factory.
* ``agents.hoodie`` (nullable): ``{"h", "s", "v"}`` tint of the character.
* ``ingest_tokens``: ``sha256(token)`` hex only, never plaintext, one agent per
  token, several tokens per agent (rotation), ``revoked_at`` instead of deletes.

``tokens.py register`` writes these rows. Schema only, no data.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004_agent_registry"
down_revision: Union[str, None] = "003_drop_v1_roster"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROOM_TYPE_LENGTH = 32
IDX_TOKENS_AGENT = "idx_ingest_tokens_agent"


def _is_postgres() -> bool:
    return op.get_context().dialect.name == "postgresql"


def upgrade() -> None:
    is_postgres = _is_postgres()
    hoodie_type = postgresql.JSONB(astext_type=sa.Text()) if is_postgres else sa.Text()
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("room_type", sa.String(ROOM_TYPE_LENGTH), nullable=True))
        batch.add_column(sa.Column("hoodie", hoodie_type, nullable=True))

    sqlite_kw: dict = {} if is_postgres else {"sqlite_autoincrement": True}
    op.create_table(
        "ingest_tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "agent_id",
            sa.Integer(),
            sa.ForeignKey("agents.id", name="fk_ingest_tokens_agent_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        **sqlite_kw,
    )
    op.create_index(IDX_TOKENS_AGENT, "ingest_tokens", ["agent_id"])


def downgrade() -> None:
    op.drop_index(IDX_TOKENS_AGENT, table_name="ingest_tokens")
    op.drop_table("ingest_tokens")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("hoodie")
        batch.drop_column("room_type")
