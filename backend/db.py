"""SOU database adapter — one API, two backends (SOU-18, ADR-034).

``backend/db.py`` is the only place application code touches the database.
It speaks to Postgres (the homelab primary) and SQLite (the local-dev
fallback) through the same SQLAlchemy models and CRUD functions, on top of
the schema owned by the Alembic migrations in ``backend/migrations/``
(SOU-17). The models here mirror migration ``001_initial_schema``; the
migrations stay the source of truth for what the database looks like.

Backend selection follows ADR-034 and ``migrations/env.py`` exactly:

* ``DATABASE_URL`` set  -> use it (e.g. ``postgresql+psycopg2://sou@host/sou``)
* ``DATABASE_URL`` unset/empty -> fall back to ``sqlite:///sou.db``, anchored
  at this directory so the file never depends on the current directory

Dialect split (ADR-034): ``events.payload`` is native ``JSONB`` on Postgres
and ``TEXT`` holding the JSON string on SQLite. The adapter expresses it as
one portable type — :class:`db.PayloadJSON`, a ``sa.JSON`` subclass pinned to
compile exactly like migration 001 on each dialect — so the split lives in
the type system, not in scattered ``if`` branches: writes serialise, reads
parse, on both backends.

Usage::

    import db

    db.init()                      # optional; get_session() self-initialises
    with db.session_scope() as session:
        agent = db.insert_agent(session, name="hermes", agent_type="hermes")
        db.insert_event(session, agent.id, "state_change", {"state": "writing"})

    with db.get_session() as session:
        for event in db.list_events(session, agent_id=agent.id, limit=10):
            print(event.created_at, event.payload)

The test suite (``backend/tests/test_db.py``) is the contract; UTC is an
application-layer timestamp convention on both backends (ADR-035).
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional, Sequence

import sqlalchemy as sa
from sqlalchemy import create_engine as _create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.event import listens_for
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

# The ADR-034 fallback. A relative SQLite path is anchored at backend/ (the
# same rule migrations/env.py applies) so Alembic and this adapter always
# open the very same file.
DEFAULT_SQLITE_URL = "sqlite:///sou.db"
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# sqlite:///sou.db and sqlite+driver:///sou.db, but not sqlite:////abs/path.
_RELATIVE_SQLITE = re.compile(
    r"^(?P<scheme>sqlite(?:\+\w+)?:///)(?P<path>[^/].*?)(?P<query>\?[^?]*)?$"
)

NAME_LENGTH = 255
AGENT_TYPE_LENGTH = 50
EVENT_TYPE_LENGTH = 100

# Active-agent window in minutes; an agent is "active" if it produced any
# event inside it. Sized for the dashboard's "who is working now" column.
ACTIVE_WINDOW_MINUTES = 5


def _utcnow() -> datetime:
    """Naive UTC — the timestamp convention ADR-035 fixes for both dialects."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Connection detection and engine construction
# ---------------------------------------------------------------------------


def resolve_database_url(url: Optional[str] = None) -> str:
    """Return the URL to open: ``DATABASE_URL``, else the SQLite fallback.

    ``url`` overrides the environment (used by tests and by callers that
    already know their target). Relative ``sqlite:///`` paths are anchored at
    backend/; anything else is passed through untouched, so a Postgres
    connection string — credentials included — reaches SQLAlchemy verbatim.
    """
    if url is None:
        url = os.environ.get("DATABASE_URL") or DEFAULT_SQLITE_URL

    match = _RELATIVE_SQLITE.match(url)
    if match:
        path = match.group("path")
        if not os.path.isabs(path):
            path = os.path.join(BACKEND_DIR, path)
        url = f"{match.group('scheme')}{path}{match.group('query') or ''}"
    return url


def backend_name(url: str) -> str:
    """Which backend a URL selects: ``"postgres"`` or ``"sqlite"``."""
    dialect = make_url(url).get_backend_name()
    if dialect == "postgresql":
        return "postgres"
    if dialect == "sqlite":
        return "sqlite"
    raise ValueError(f"unsupported database backend: {dialect!r} (ADR-034 allows postgres or sqlite)")


def create_db_engine(url: Optional[str] = None, read_only: bool = False, **engine_kw: Any) -> Engine:
    """Build an Engine for either backend, with the per-dialect pragmas.

    SQLite gets ``PRAGMA foreign_keys=ON`` (off by default in SQLite, yet the
    schema declares ON DELETE CASCADE and the tests rely on it) and is opened
    across threads because Flask serves the dashboard in parallel. Postgres
    needs neither and keeps SQLAlchemy's pooling defaults.

    ``read_only=True`` adds the in-app write guard (SOU-220, AC5): every
    connection runs SQLite's ``PRAGMA query_only=ON`` or sets Postgres's
    session GUC ``default_transaction_read_only`` before use, so a face
    that only reads (the public viewer) cannot write even if a bug — or a
    tempting route — asks it to. "when the URL allows it": the durable
    guarantee is a read-only role granted by the operator; this flag is
    the defence-in-depth layer that works on any URL.
    """
    resolved = resolve_database_url(url)
    if backend_name(resolved) == "sqlite":
        engine = _create_engine(resolved, connect_args={"check_same_thread": False}, **engine_kw)

        @listens_for(engine, "connect")
        def _sqlite_guards(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            if read_only:
                cursor.execute("PRAGMA query_only=ON")
            cursor.close()

        return engine
    engine = _create_engine(resolved, **engine_kw)
    if read_only:
        @listens_for(engine, "connect")
        def _postgres_read_only(dbapi_conn, _record):  # pragma: no cover - needs server
            cursor = dbapi_conn.cursor()
            cursor.execute("SET default_transaction_read_only = on")
            cursor.close()
    return engine


# ---------------------------------------------------------------------------
# ORM models mirroring migration 001_initial_schema
# ---------------------------------------------------------------------------

# The two activity indexes the migration creates. Declared on the models too,
# so ``alembic check`` and autogenerate see model == schema and never propose
# dropping them; ``(agent_id, <time> DESC)`` serves the dashboard's two reads
# (latest activity of one agent, chain of one session).
IDX_EVENTS_AGENT_CREATED = "idx_events_agent_created"
IDX_SESSIONS_AGENT_STARTED = "idx_sessions_agent_started"

# One portable JSON column: serialises and parses everywhere, but compiles
# to exactly migration 001's DDL per dialect — native JSONB on Postgres,
# TEXT on SQLite. Plain sa.JSON would render "JSON" on SQLite (NUM affinity,
# a hazard if its DDL were ever applied) and "JSON" on Postgres (not JSONB),
# so the two dialect renderings are pinned here instead.
class PayloadJSON(sa.JSON):
    """``events.payload``: JSONB on Postgres, TEXT on SQLite, JSON behaviour
    on both (writes dump, reads parse — the ADR-034 dialect split)."""


@compiles(PayloadJSON, "postgresql")
def _payload_postgres(type_, compiler, **kw):  # pragma: no cover - DDL string
    return "JSONB"


@compiles(PayloadJSON, "sqlite")
def _payload_sqlite(type_, compiler, **kw):  # pragma: no cover - DDL string
    return "TEXT"


class Base(DeclarativeBase):
    """Declarative base for the adapter's models (schema lives in Alembic)."""


class Agent(Base):
    __tablename__ = "agents"
    # Mirrors migration 001's per-dialect table flags: real AUTOINCREMENT ids
    # on SQLite, ignored by Postgres (whose SERIAL already owns a sequence).
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(sa.String(NAME_LENGTH), nullable=False)
    agent_type: Mapped[str] = mapped_column(sa.String(AGENT_TYPE_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    # Mirrors the database-level ON DELETE CASCADE (migration 001) so an ORM
    # delete never leaves orphan activity behind on either backend.
    events: Mapped[list["Event"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )
    sessions: Mapped[list["AgentSession"]] = relationship(
        back_populates="agent", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Agent id={self.id} name={self.name!r} type={self.agent_type!r}>"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        sa.Index(IDX_EVENTS_AGENT_CREATED, "agent_id", sa.text("created_at DESC")),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        sa.Integer,
        sa.ForeignKey("agents.id", name="fk_events_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(sa.String(EVENT_TYPE_LENGTH), nullable=False)
    # The one genuinely split type: JSONB on Postgres, TEXT on SQLite, JSON
    # (de)serialisation behaviour on both — see PayloadJSON above.
    payload: Mapped[dict] = mapped_column(PayloadJSON(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime, nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")
    )

    agent: Mapped[Agent] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Event id={self.id} agent_id={self.agent_id} type={self.event_type!r}>"


class AgentSession(Base):
    """A run of an agent; named to stay clear of SQLAlchemy's own ``Session``."""

    __tablename__ = "sessions"
    __table_args__ = (
        sa.Index(IDX_SESSIONS_AGENT_STARTED, "agent_id", sa.text("started_at DESC")),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[int] = mapped_column(
        sa.Integer,
        sa.ForeignKey("agents.id", name="fk_sessions_agent_id", ondelete="CASCADE"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(sa.DateTime, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(sa.DateTime, nullable=True)

    agent: Mapped[Agent] = relationship(back_populates="sessions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentSession id={self.id} agent_id={self.agent_id}>"


# ---------------------------------------------------------------------------
# Module-level engine + session factory
# ---------------------------------------------------------------------------

_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None
_default_read_only: bool = False


def init(
    url: Optional[str] = None,
    engine: Optional[Engine] = None,
    read_only: Optional[bool] = None,
) -> Engine:
    """Point the adapter at ``url`` (or ``DATABASE_URL`` / SQLite fallback).

    Pass an ``engine`` to adopt one built elsewhere. Re-init disposes the
    previous engine, so tests can move the whole module between databases.

    ``read_only`` (SOU-220) builds the engine with the write guard; passing
    ``None`` keeps the module's previous default, so the self-initialising
    ``get_engine``/``get_session`` path after a ``shutdown()`` reopens the
    same face the app started with. Explicit ``False`` resets it (dev).
    """
    global _engine, _session_factory, _default_read_only
    shutdown()
    if read_only is not None:
        _default_read_only = bool(read_only)
    _engine = engine if engine is not None else create_db_engine(url, read_only=_default_read_only)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def shutdown() -> None:
    """Dispose the current engine and forget the factory."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


def get_engine() -> Engine:
    """The active engine, self-initialising from the environment on first use."""
    if _engine is None:
        init()
    assert _engine is not None
    return _engine


def get_session() -> Session:
    """A new Session bound to the active engine. Caller owns (closes) it."""
    if _session_factory is None:
        init()
    assert _session_factory is not None
    return _session_factory()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional unit of work: commit on success, roll back on error."""
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# CRUD — the six functions of the SOU-18 contract, plus the registry
# helpers SOU-19 added for the app refactor (update_agent / list_agents /
# delete_agent): the dashboard's approve/reject/leave flows must reach
# agents outside the activity window, which list_agents_active never shows.
# ---------------------------------------------------------------------------


def insert_agent(db: Session, name: str, agent_type: str) -> Agent:
    """Register an agent. ``created_at`` is filled by the database default."""
    agent = Agent(name=name, agent_type=agent_type)
    db.add(agent)
    db.flush()
    return agent


def insert_event(
    db: Session,
    agent_id: int,
    event_type: str,
    payload: dict,
    created_at: Optional[datetime] = None,
) -> Event:
    """Append one agent event; ``payload`` becomes JSONB (Postgres) or TEXT (SQLite).

    Flushes immediately so a missing ``agent_id`` raises ``IntegrityError``
    here rather than at commit time.
    """
    event = Event(
        agent_id=agent_id,
        event_type=event_type,
        payload=payload,
        created_at=created_at if created_at is not None else _utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def insert_session(
    db: Session,
    agent_id: int,
    started_at: Optional[datetime] = None,
    ended_at: Optional[datetime] = None,
) -> AgentSession:
    """Record an agent run; omit ``started_at`` to stamp it with UTC now."""
    row = AgentSession(
        agent_id=agent_id,
        started_at=started_at if started_at is not None else _utcnow(),
        ended_at=ended_at,
    )
    db.add(row)
    db.flush()
    return row


def get_agent(db: Session, agent_id: int) -> Optional[Agent]:
    """The agent with this id, or ``None``."""
    return db.get(Agent, agent_id)


def update_agent(
    db: Session,
    agent_id: int,
    name: Optional[str] = None,
    agent_type: Optional[str] = None,
) -> Optional[Agent]:
    """Rename or retype an agent; only the fields passed are touched.

    Returns the updated row, or ``None`` when there is no such agent. A call
    with no fields is a pure read-through of the current row.
    """
    agent = db.get(Agent, agent_id)
    if agent is None:
        return None
    if name is not None:
        agent.name = name
    if agent_type is not None:
        agent.agent_type = agent_type
    db.flush()
    return agent


def list_agents(db: Session) -> Sequence[Agent]:
    """The whole registry in id order — including agents outside the window.

    The counterpart of :func:`list_agents_active`: identity flows (approve,
    reject, leave, the dashboard roster) need every row, not just the recent
    ones; presence reads use the window instead.
    """
    query = sa.select(Agent).order_by(Agent.id)
    return db.execute(query).scalars().all()


def delete_agent(db: Session, agent_id: int) -> bool:
    """Remove an agent; ``ON DELETE CASCADE`` takes its events and sessions.

    Returns ``True`` when a row was deleted, ``False`` when there was none —
    leaving an unknown agent is not an error (the JSON roster behaved the
    same way).
    """
    agent = db.get(Agent, agent_id)
    if agent is None:
        return False
    db.delete(agent)
    db.flush()
    return True


def list_events(
    db: Session,
    agent_id: Optional[int] = None,
    event_type: Optional[str] = None,
    limit: Optional[int] = None,
    since: Optional[datetime] = None,
) -> Sequence[Event]:
    """Events newest-first — the order ``idx_events_agent_created`` serves.

    ``agent_id`` / ``event_type`` narrow the feed; ``limit`` caps it;
    ``since`` is an inclusive floor on ``created_at`` (naive UTC, the
    ADR-035 convention — GET /events/recent, SOU-45).
    ``id`` breaks created_at ties so paging stays deterministic on both
    backends (SQLite timestamps are second-granular via SQL, sub-second via
    the adapter — never assume created_at alone is unique).
    """
    query = sa.select(Event)
    if agent_id is not None:
        query = query.where(Event.agent_id == agent_id)
    if event_type is not None:
        query = query.where(Event.event_type == event_type)
    if since is not None:
        query = query.where(Event.created_at >= since)
    query = query.order_by(Event.created_at.desc(), Event.id.desc())
    if limit is not None:
        query = query.limit(limit)
    return db.execute(query).scalars().all()


def agent_activity(
    db: Session,
    since: datetime,
) -> list[tuple]:
    """Per-agent activity summary for events at or after ``since`` (SOU-201).

    Returns ``(agent_id, first_seen, last_seen, event_count, newest_event)``
    ordered newest activity first (``last_seen`` DESC, agent id DESC) —
    built for GET /events/agents. Two statements, both set-based, on either
    dialect: a GROUP BY for the window stats, and a ``row_number() OVER
    (PARTITION BY agent_id ORDER BY created_at DESC, id DESC)`` ranking that
    keeps each agent's rn = 1 row. The window's ordering mirrors
    :func:`list_events`, so when timestamps tie the two reads agree on which
    row is "newest". SQLite has had window functions since 3.25 (bundled
    everywhere modern); Postgres has had them far longer; there is no
    per-agent or full-table Python loop.
    """
    first_seen = sa.func.min(Event.created_at).label("first_seen")
    last_seen = sa.func.max(Event.created_at).label("last_seen")
    event_count = sa.func.count().label("event_count")
    stats = (
        sa.select(
            Event.agent_id.label("agent_id"),
            first_seen,
            last_seen,
            event_count,
        )
        .where(Event.created_at >= since)
        .group_by(Event.agent_id)
        .order_by(last_seen.desc(), Event.agent_id.desc())
    )
    rn = (
        sa.func.row_number()
        .over(
            partition_by=Event.agent_id,
            order_by=[Event.created_at.desc(), Event.id.desc()],
        )
        .label("rn")
    )
    ranked = (
        sa.select(Event.id.label("event_id"), rn)
        .where(Event.created_at >= since)
        .subquery()
    )
    newest_ids = sa.select(ranked.c.event_id).where(ranked.c.rn == 1)
    newest = {
        event.agent_id: event
        for event in db.execute(
            sa.select(Event).where(Event.id.in_(newest_ids))
        ).scalars().all()
    }
    return [
        (agent_id, first, last, count, newest[agent_id])
        for agent_id, first, last, count in db.execute(stats)
        if agent_id in newest
    ]


def list_agents_active(
    db: Session,
    window_minutes: int = ACTIVE_WINDOW_MINUTES,
    now: Optional[datetime] = None,
) -> Sequence[Agent]:
    """Agents that produced at least one event inside the window, by id.

    "Active" is activity-based, not state-based (ADR-035): any event counts,
    so a tool call keeps an agent as alive as a state_change would.
    """
    cutoff = (now if now is not None else _utcnow()) - timedelta(minutes=window_minutes)
    query = (
        sa.select(Agent)
        .join(Event, Event.agent_id == Agent.id)
        .where(Event.created_at >= cutoff)
        .distinct()
        .order_by(Agent.id)
    )
    return db.execute(query).scalars().all()
