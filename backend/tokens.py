#!/usr/bin/env python3
"""Register agents for the Lodge: ingest tokens and room personality (SOU-292).

The repo knows no agent. Who may write, and how each room looks, is data in the
database ``DATABASE_URL`` points at (migration 004, SOU-291)::

    uv run python backend/tokens.py register builder --room garage --hoodie 200,0.8,0.9
    uv run python backend/tokens.py set-room builder --room serverroom
    uv run python backend/tokens.py list
    uv run python backend/tokens.py revoke builder
    uv run python backend/tokens.py import-env        # SOU_INGEST_TOKENS -> database

``register`` prints a fresh token **once** and stores only its sha256. The
plaintext lives in your terminal and then in the reporter that uses it: never in
this repo, never in a log. ``list`` shows no token and no hash. ``set-room``
needs no token, for dev boxes that run with tokens off.

``new <agent>`` is the old offline form (no database): it prints a token plus a
``sha256:agent`` line for the deprecated ``SOU_INGEST_TOKENS``.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

# Same convention as the backend suites: importable when run as a script
# (sys.path[0] is already this directory) and when imported from tests.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ingest_auth

# The renderer's room maps (frontend/v3/src/rooms.js ROOM_TYPES); a test pins
# the two lists together.
ROOM_TYPES = ("garage", "serverroom", "factory", "mystery", "tokenden", "career", "writer")
AGENT_TYPE = "guest"  # what POST /events registers unknown agents as


class CliError(Exception):
    """A user-facing error: printed as ``tokens: <message>``, exit 1."""


def generate(agent: str) -> tuple[str, str]:
    """(plaintext token, SOU_INGEST_TOKENS line) for one agent, freshly random."""
    agent = ingest_auth.normalize_agent(agent)
    token = secrets.token_urlsafe(32)
    return token, f"{ingest_auth.hash_token(token)}:{agent}"


def parse_room(value):
    if value is None:
        return None
    room = value.strip().lower()
    if room not in ROOM_TYPES:
        raise CliError(f"--room: {value!r} is not a room type; pick one of {', '.join(ROOM_TYPES)}")
    return room


def parse_hoodie(value):
    """``"H,S,V"`` -> ``{"h": H, "s": S, "v": V}``; hue 0-360, saturation/value 0-1.5."""
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",")]
    try:
        h, s, v = (float(p) for p in parts)
    except ValueError:
        raise CliError(f"--hoodie: expected H,S,V numbers like 125,0.8,0.8, got {value!r}")
    if not (0 <= h <= 360 and 0 <= s <= 1.5 and 0 <= v <= 1.5):
        raise CliError(f"--hoodie: hue 0-360, saturation and value 0-1.5, got {value!r}")
    return {"h": h, "s": s, "v": v}


def _open_db():
    import db
    import sqlalchemy as sa

    engine = db.get_engine()
    if "ingest_tokens" not in sa.inspect(engine).get_table_names():
        raise CliError(
            "the database has no agent registry yet: run "
            "`uv run alembic -c backend/alembic.ini upgrade head` (or start the backend once)"
        )
    return db


def _agent(db, session, name, create):
    name = ingest_auth.normalize_agent(name)
    row = db.find_agent_by_name(session, name)
    if row is None and create:
        row = db.insert_agent(session, name=name, agent_type=AGENT_TYPE)
    if row is None:
        raise CliError(f"no agent named {name!r}")
    return row


def cmd_register(args, out):
    room, hoodie = parse_room(args.room), parse_hoodie(args.hoodie)
    db = _open_db()
    token = secrets.token_urlsafe(32)
    with db.session_scope() as session:
        row = _agent(db, session, args.agent, create=True)
        if room is not None or hoodie is not None:
            db.set_agent_room(session, row.id, room if room is not None else row.room_type,
                              hoodie if hoodie is not None else row.hoodie)
        db.insert_token(session, row.id, ingest_auth.hash_token(token))
        name = row.name
    if args.token_only:
        print(token, file=out)
        return 0
    print(f"one-time token for agent {name!r} — shown once, keep it in your reporter:", file=out)
    print(f"  {token}", file=out)
    print(file=out)
    print("registered in the database (only its sha256 is stored).", file=out)
    return 0


def cmd_set_room(args, out):
    room, hoodie = parse_room(args.room), parse_hoodie(args.hoodie)
    db = _open_db()
    with db.session_scope() as session:
        row = _agent(db, session, args.agent, create=True)
        db.set_agent_room(session, row.id, room, hoodie if hoodie is not None else row.hoodie)
        print(f"{row.name}: room {room}, hoodie {_hoodie_text(row.hoodie)}", file=out)
    return 0


def _hoodie_text(hoodie):
    if not hoodie:
        return "-"
    return ",".join(f"{hoodie[k]:g}" for k in ("h", "s", "v"))


def cmd_list(args, out):
    db = _open_db()
    with db.session_scope() as session:
        counts = db.active_token_counts(session)
        rows = db.list_agents(session)
        print(f"{'agent':<24} {'room':<11} {'hoodie':<14} {'tokens':>6}  last token", file=out)
        for row in rows:
            created = [t.created_at for t in row.tokens if t.revoked_at is None]
            last = max(created).strftime("%Y-%m-%d") if created else "-"
            print(f"{row.name:<24} {row.room_type or '-':<11} {_hoodie_text(row.hoodie):<14} "
                  f"{counts.get(row.id, 0):>6}  {last}", file=out)
    return 0


def cmd_revoke(args, out):
    db = _open_db()
    with db.session_scope() as session:
        row = _agent(db, session, args.agent, create=False)
        n = db.revoke_tokens(session, row.id)
        print(f"{row.name}: revoked {n} token(s)", file=out)
    return 0


def cmd_import_env(args, out):
    value = (os.environ.get("SOU_INGEST_TOKENS") or "").strip()
    if not value:
        raise CliError("SOU_INGEST_TOKENS is not set: nothing to import")
    entries = ingest_auth.parse_source(value).mapping()
    db = _open_db()
    imported = skipped = 0
    with db.session_scope() as session:
        for digest, agent in sorted(entries.items(), key=lambda kv: kv[1]):
            if db.token_hash_exists(session, digest):
                skipped += 1
                continue
            row = _agent(db, session, agent, create=True)
            db.insert_token(session, row.id, digest)
            imported += 1
    print(f"imported {imported}, already there {skipped}", file=out)
    return 0


def cmd_new(args, out):
    token, line = generate(args.agent)
    print(f"one-time token for agent {args.agent!r} — shown once, keep it in your reporter:", file=out)
    print(f"  {token}", file=out)
    print(file=out)
    print("line for SOU_INGEST_TOKENS (deprecated; prefer `register`, which stores it in the database):", file=out)
    print(f"  {line}", file=out)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="tokens.py",
        description="Register agents (ingest tokens + room personality) in the Lodge database.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def personality(p):
        p.add_argument("--room", help=f"room type: {', '.join(ROOM_TYPES)}")
        p.add_argument("--hoodie", help="character tint H,S,V, e.g. 125,0.8,0.8")

    p = sub.add_parser("register", help="create the agent if needed and mint a token for it")
    p.add_argument("agent", help="agent name, exactly as it posts events (no ':')")
    personality(p)
    p.add_argument("--token-only", action="store_true",
                   help="print only the token, for piping into a secret store")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("set-room", help="set an agent's room personality (no token)")
    p.add_argument("agent")
    personality(p)
    p.set_defaults(func=cmd_set_room)

    sub.add_parser("list", help="agents, rooms and active token counts").set_defaults(func=cmd_list)

    p = sub.add_parser("revoke", help="revoke every active token of an agent")
    p.add_argument("agent")
    p.set_defaults(func=cmd_revoke)

    sub.add_parser("import-env", help="copy SOU_INGEST_TOKENS entries into the database") \
        .set_defaults(func=cmd_import_env)

    p = sub.add_parser("new", help="(deprecated, offline) mint a token + SOU_INGEST_TOKENS line")
    p.add_argument("agent", help="agent name, exactly as it posts events (no ':')")
    p.set_defaults(func=cmd_new)
    return parser


def main(argv=None, out=None) -> int:
    out = sys.stdout if out is None else out
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage on the bad args
        return int(exc.code or 2)
    if args.command == "set-room" and args.room is None:
        print("tokens: set-room needs --room", file=sys.stderr)
        return 1
    try:
        return args.func(args, out)
    except (CliError, ValueError) as exc:
        print(f"tokens: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
