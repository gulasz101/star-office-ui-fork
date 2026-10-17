#!/usr/bin/env python3
"""Create per-agent ingest tokens (SOU-219, Phase 13 P13-B).

    uv run python backend/tokens.py new hermes

Prints a fresh token **once**, plus the ``sha256(token):agent`` line to paste
into ``SOU_INGEST_TOKENS`` (or into the JSON file it points at). The plaintext
token exists only in your terminal and then in the reporter that uses it —
never in this repo, never on this machine's disk, never in a log (manager
note on SOU-219). The server only ever stores the hash, so pasting the line
into a ticket, an env file or SOPS leaks nothing usable.

Delivering the token to the agent is the owner's step, same as every other
secret in the homelab: SOPS + age, never plaintext in git.
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


def generate(agent: str) -> tuple[str, str]:
    """(plaintext token, SOU_INGEST_TOKENS line) for one agent, freshly random."""
    agent = ingest_auth.normalize_agent(agent)
    token = secrets.token_urlsafe(32)
    return token, f"{ingest_auth.hash_token(token)}:{agent}"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="tokens.py",
        description="Create per-agent ingest tokens for POST /events (SOU-219).",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    new_cmd = sub.add_parser("new", help="mint a fresh token for one agent")
    new_cmd.add_argument("agent", help="agent name, exactly as it posts events (no ':')")

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage on the bad args
        return int(exc.code or 2)

    try:
        token, line = generate(args.agent)
    except ValueError as exc:
        print(f"tokens: {exc}", file=sys.stderr)
        return 1

    print(f"one-time token for agent {args.agent!r} — shown once, keep it in your reporter:")
    print(f"  {token}")
    print()
    print("line for SOU_INGEST_TOKENS (paste as-is; the hash, not the token):")
    print(f"  {line}")
    print()
    print("docs: docs/public-lodge.md, section 'Tokens (P13-B)'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
