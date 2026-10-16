#!/usr/bin/env python3
"""Build a pre-sanitiser events database, the way SOU-218 left it behind.

SOU-235 has to be proven against a database that looks like a real deployment,
not against hand-written rows: reporter payloads from before ``/events`` put
the sanitiser on the door (git history: ``reporter/openchamber``,
``reporter/opencode``, the Hermes reporter as Szarik pushed it in phase 9)
carry free text, absolute home paths, URLs with credentials, join keys and
pushed auth status.  This script writes those shapes into a fresh SQLite file
or into PostgreSQL, so the re-sanitising migration can be exercised on
something realistic before it touches ``~/sou-deploy/backend/sou.db`` on the
M1 Max.

Everything it generates is invented.  Secret-shaped strings are flagged with
``CANARY`` so a grep proves what the migration removed, host-looking strings
end in ``.example`` (RFC 6761), paths use ``/Users/dev``, and the v1 rows use
inventoried roster names (SOU-218 ruling: the test DB is the inventory —
never reuse a real username).  Nothing here is a credential, and nothing here
is real data.

Usage:
    uv run python scripts/legacy_event_fixture.py --database sqlite:///sou.db --rows 1200
    uv run python scripts/legacy_event_fixture.py --database postgresql+psycopg2://... --rows 1200 --force

It refuses to write into a file that already exists and into a Postgres
schema that already has events, unless ``--force`` says otherwise.
"""

import argparse
import getpass
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402

# Host-looking values may only ever use this TLD (RFC 6761 reserved, never resolves).
FAKE_TLD = ".example"
HOME_PATH = "/Users/dev"
LOCAL_USER = getpass.getuser()

# Secret-shaped markers. Anything in a stored payload that should not survive
# the migration carries one of these, so `grep -c CANARY` is the evidence.
CANARY_TOKENS = [
    "CANARY-SECRET-TOKEN",
    "CANARY-PUSH-TOKEN",
    "CANARY-JOIN-KEY",
    "CANARY-USER-TOKEN",
    "CANARY-ACCESS-KEY",
    "CANARY-PASSWORD",
    "CANARY-SSH-KEY",
]

# Host-looking strings the migration must remove from sanitisable rows.
FAKE_HOSTS = [
    f"build.internal{FAKE_TLD}",
    f"dashboard.home{FAKE_TLD}",
    f"ci.internal{FAKE_TLD}",
    f"host.local{FAKE_TLD}",
    f"vpn.corp{FAKE_TLD}",
]

# Strings the v1 state rows deliberately keep (the migration excludes them by
# the SOU-235 ruling) — their continued presence proves the exclusion works.
KEEP_MARKERS = ["KEEPME-ROSTER", "KEEPME-DETAIL", "KEEPME-JOINKEY"]

AGENTS = ["claude", "hermes", "pi", "openclaw", "openchamber", "hermes-szarik"]

# Same schema the migration walks (backend/resanitise.py).
PAYLOAD_COLUMNS = {
    "postgresql": ("payload", "data", "payload_json", "event_data"),
    "sqlite": ("payload",),
}


def _host(n):
    return FAKE_HOSTS[n % len(FAKE_HOSTS)]


def _canary(n):
    return CANARY_TOKENS[n % len(CANARY_TOKENS)]


# --- raw pre-SOU-218 payload shapes -----------------------------------------

def session_start_payload(i):
    return {
        "model": "anthropic/claude-opus-4.6",
        "tokens_in": random.randint(50, 400),
        "tokens_out": random.randint(1, 60),
        "cost_usd": round(random.uniform(0.001, 0.2), 3),
        "cwd": f"{HOME_PATH}/star-office",
        "state": "idle",
        "source": "hermes-sou",
        "homePath": HOME_PATH,
        "userToken": f"{_canary(i)}-user",
        "title": f"session {i} on {_host(i)} for {LOCAL_USER}",
    }


def session_end_payload(i):
    return {
        "state": "idle",
        "source": "hermes-sou",
        "authStatus": "ok",
        "authExpiresAt": 1790000000 + i,
        "lastPushAt": "2026-10-15T07:00:00Z",
        "joinKey": f"{_canary(i)}-join",
        "detail": f"pushed to {_host(i)} by {LOCAL_USER}: {_canary(i)}",
    }


def tool_call_payload(i):
    tool = random.choice(["read", "write", "edit", "bash", "grep", "web_search", "browser"])
    return {
        "tool": tool,
        "area": random.choice(["desk", "rack", "garage", "bookshelf", "coffee"]),
        "command": f"curl -s http://127.0.0.1:19000/events -H 'token: {_canary(i)}'",
        "detail": f"{tool} while {LOCAL_USER} watched from {_host(i)} ({_canary(i)})",
        "status": random.choice(["ok", "error"]),
        "durationMs": random.randint(1, 9000),
        "url": f"http://admin:{_canary(i)}@{_host(i)}/jobs/{i}",
        "session_id": f"ses_{i:05d}",
        "permission_mode": "bypassPermissions",
    }


def file_edit_payload(i):
    return {
        "path": f"{HOME_PATH}/star-office/backend/app.py",
        "filename": "app.py",
        "diff": f"- TOKEN = 'x'\n+ TOKEN = '{_canary(i)}'\n # {LOCAL_USER} @ {_host(i)}",
        "status": "ok",
        "lines_added": random.randint(1, 40),
        "lines_removed": random.randint(0, 12),
        "api_key": _canary(i),
    }


def command_run_payload(i):
    return {
        "command": f"ssh deploy@{_host(i)} 'cd {HOME_PATH}/srv && ./deploy.sh {_canary(i)}'",
        "exit": random.choice([0, 0, 0, 1]),
        "output": f"connected to {_host(i)} as {LOCAL_USER}",
        "duration_ms": random.randint(50, 30000),
        "password": _canary(i),
    }


def code_completion_payload(i):
    return {
        "file": f"{HOME_PATH}/star-office/frontend/v3/game.js",
        "accepted": random.random() < 0.7,
        "suggestion": f"const HOST = '{_host(i)}'; // {_canary(i)}",
        "tokens": random.randint(5, 200),
    }


def near_miss_state_payload(i):
    """A v1-shaped key set *without* the door marker — must be sanitised."""
    return {
        "state": random.choice(["idle", "working", "thinking"]),
        "area": "desk",
        "detail": f"not a door write, still carries {_canary(i)} for {LOCAL_USER}",
    }


# --- v1 state-door rows (excluded by the ruling; must survive untouched) ----

def v1_star_seed_row(i):
    """``init_database`` seeds exactly this for the `Star` roster entry."""
    return {
        "state": "idle",
        "area": "home",
        "detail": {
            "name": "Star",
            "source": "user:1337",
            "authStatus": None,
            "authExpiresAt": None,
            "joinKey": f"{_canary(i)}-seed",
            "lastPushAt": None,
        },
        "updated_at": "2026-10-16T00:00:00.000000",
    }


def v1_set_state_row(i):
    """A browser ``POST /set_state`` with the door marker the v1 frontends send.
    This is the row class the SOU-235 ruling protects."""
    return {
        "state": "working",
        "area": "roam",
        "notification_type": "null",
        "detail": f"KEEPME-DETAIL browser poll {i} from {_host(i)} ({LOCAL_USER})",
        "updated_at": f"2026-10-15T{7 + i % 12:02d}:00:00.000000",
    }


def v1_save_agents_state_row(i):
    """What ``save_agents_state`` writes: the whole roster snapshot."""
    return {
        "state": "working",
        "updated_at": f"2026-10-15T{9 + i % 12:02d}:30:00.000000",
        "detail": {
            "Star": {
                "name": "Star", "source": "user:1337", "authStatus": "ok",
                "authExpiresAt": 1790000000, "joinKey": "KEEPME-JOINKEY",
                "lastPushAt": "2026-10-15T07:00:00Z",
            },
            "Alice": {
                "name": "Alice", "source": LOCAL_USER, "authStatus": "need_auth",
                "authExpiresAt": None, "joinKey": _canary(i), "lastPushAt": None,
            },
        },
    }


# --- already-sanitised rows (the no-op case) --------------------------------

def clean_tool_call(i):
    return {"tool": random.choice(["read", "write", "bash"]), "status": "ok"}


def clean_state_change(i):
    # exactly what /events leaves behind now: allowed keys only, no empty values
    return {"state": "working", "area": "desk"}


def clean_command_run(i):
    return {"command": "pytest", "status": "ok", "exit_code": 0}


BUILDERS = {
    "session_start": session_start_payload,
    "session_end": session_end_payload,
    "tool_call": tool_call_payload,
    "file_edit": file_edit_payload,
    "command_run": command_run_payload,
    "code_completion": code_completion_payload,
}
CLEAN_BUILDERS = {
    "tool_call": clean_tool_call,
    "state_change": clean_state_change,
    "command_run": clean_command_run,
}


def build_rows(total):
    """Interleave raw reporter rows, v1 state rows, near-misses and clean rows."""
    rows = []
    t0 = datetime(2026, 10, 2, 6, 0, tzinfo=timezone.utc)
    for i in range(total):
        created = t0 + timedelta(minutes=i, seconds=random.randint(0, 59))
        agent = AGENTS[i % len(AGENTS)]
        r = random.Random(i * 7919 + 13)  # deterministic per index
        roll = i % 40
        if roll == 36:
            event_type, payload = "state_change", v1_star_seed_row(i)
        elif roll == 37:
            event_type, payload = "state_change", v1_save_agents_state_row(i)
        elif roll == 38:
            event_type, payload = "state_change", v1_set_state_row(i)
        elif roll == 39:
            event_type, payload = "state_change", near_miss_state_payload(i)
        elif roll in (30, 31, 32, 33, 34, 35):
            event_type = r.choice(list(CLEAN_BUILDERS))
            payload = CLEAN_BUILDERS[event_type](i)
        else:
            event_type = r.choice(list(BUILDERS))
            payload = BUILDERS[event_type](i)
        rows.append({
            "id": str(uuid_uuid()),
            "agent_id": f"{agent}-fixture",
            "agent": agent,
            "event_type": event_type,
            "payload": payload,
            "created_at": created,
        })
    return rows


_counter = 0


def uuid_uuid():
    """Deterministic-ish unique ids without importing uuid's randomness."""
    global _counter
    _counter += 1
    return f"fedbc0de-0000-4000-8000-{_counter:012d}"


def ddl(dialect):
    json_type = "JSONB" if dialect == "postgresql" else "TEXT"
    ts_type = "TIMESTAMP" if dialect == "postgresql" else "DATETIME"
    return [
        f"""CREATE TABLE IF NOT EXISTS agents (
            id VARCHAR(36) NOT NULL,
            name VARCHAR(128) NOT NULL,
            token VARCHAR(255) NOT NULL,
            role VARCHAR(32) DEFAULT 'member',
            owner VARCHAR(128),
            data {json_type},
            created_at {ts_type} DEFAULT CURRENT_TIMESTAMP,
            last_seen_at {ts_type},
            PRIMARY KEY (id),
            UNIQUE (token)
        )""",
        f"""CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(36) NOT NULL,
            agent_id VARCHAR(36),
            name VARCHAR(128),
            status VARCHAR(32) DEFAULT 'active',
            created_at {ts_type} DEFAULT CURRENT_TIMESTAMP,
            last_event_at {ts_type},
            updated_at {ts_type} DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id)
        )""",
        f"""CREATE TABLE IF NOT EXISTS events (
            id VARCHAR(36) NOT NULL,
            agent_id VARCHAR(36),
            session_id VARCHAR(36),
            event_type VARCHAR(64),
            payload {json_type},
            created_at {ts_type} DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id)
        )""",

        """CREATE TABLE IF NOT EXISTS alembic_version (
            version_num VARCHAR(32) NOT NULL,
            PRIMARY KEY (version_num)
        )""",
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database", required=True, help="SQLAlchemy URL (sqlite:///… or postgresql+psycopg2://…)")
    ap.add_argument("--rows", type=int, default=1200, help="number of event rows (default 1200)")
    ap.add_argument("--force", action="store_true", help="write even if the target already has events")
    args = ap.parse_args(argv)

    dialect = "postgresql" if args.database.startswith("postgres") else "sqlite"

    engine = create_engine(args.database)
    if _stored_events(engine) and not args.force:
        sys.exit("events already stored — pass --force to add to them")
    with engine.begin() as c:
        for statement in ddl(dialect):
            c.execute(text(statement))
        if not c.execute(text("SELECT 1 FROM alembic_version LIMIT 1")).fetchone():
            c.execute(text("INSERT INTO alembic_version (version_num) VALUES ('001_initial_schema')"))

    rows = build_rows(args.rows)
    with engine.begin() as conn:
        cols = [c for c in ("id", "name", "token", "data") if c in _agent_columns(conn, dialect)]
        if not {"id", "name", "token"} <= set(cols):
            cols = []  # an agents table from another era; the events are what matter here
        if cols and dialect == "postgresql":
            agent_stmt = (f"INSERT INTO agents ({', '.join(cols)}) "
                          f"VALUES ({', '.join('CAST(:' + c + ' AS jsonb)' if c == 'data' else ':' + c for c in cols)}) "
                          "ON CONFLICT (id) DO NOTHING")
        elif cols:
            agent_stmt = (f"INSERT OR IGNORE INTO agents ({', '.join(cols)}) "
                          f"VALUES ({', '.join(':' + c for c in cols)})")
        payload_col = "CAST(:payload AS jsonb)" if dialect == "postgresql" else ":payload"
        for row in rows:
            if cols:
                conn.execute(text(agent_stmt), {
                    "id": row["agent_id"], "name": row["agent"], "token": f"fx-{row['agent_id']}",
                    "data": json.dumps({"source": "fixture"})})
            payload = json.dumps(row["payload"], ensure_ascii=False)
            conn.execute(
                text("INSERT INTO events (id, agent_id, event_type, payload, created_at) "
                     f"VALUES (:id, :agent_id, :event_type, {payload_col}, :created_at)"),
                {"id": row["id"], "agent_id": row["agent_id"], "event_type": row["event_type"],
                 "payload": payload, "created_at": row["created_at"]},
            )

    stored = _stored_events(engine)
    v1 = sum(1 for r in rows if r["event_type"] == "state_change" and "updated_at" in r["payload"])
    print(f"fixture: {len(rows)} rows built, {stored} events stored in {dialect}")
    print(f"fixture: rows carrying CANARY markers: {sum(1 for r in rows if 'CANARY' in json.dumps(r['payload']))}")
    print(f"fixture: v1 state rows (excluded by the SOU-235 ruling, must survive): {v1}")
    return 0


def _stored_events(engine):
    try:
        with engine.connect() as conn:
            return conn.execute(text("SELECT count(*) FROM events")).scalar()
    except Exception:
        return 0


def _agent_columns(conn, dialect):
    if dialect == "postgresql":
        rows = conn.execute(text("SELECT column_name FROM information_schema.columns "
                                 "WHERE table_name = 'agents'")).fetchall()
    else:
        rows = conn.execute(text("PRAGMA table_info(agents)")).fetchall()
    return {r[0] if dialect == "postgresql" else r[1] for r in rows}


if __name__ == "__main__":
    sys.exit(main())
