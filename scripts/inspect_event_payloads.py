#!/usr/bin/env python3
"""Report what sensitive-looking content a live events database still holds.

The SOU-235 evidence gate: before the migration the sanitisable rows are full
of the shapes the old reporters pushed (secret markers, host-looking strings,
URLs with credentials, absolute home paths); after it, none of them may be
left in any row the sanitiser is allowed to touch — only in the v1 state-door
rows the ruling excludes, which must be *intact*.  Also prints a digest of the
whole table so a dry run can be proven not to have written anything.

Everything it looks for is fixture-shaped (CANARY markers, ``.example``
hosts, ``/Users/dev`` paths); a real deployment is checked the same way by
reading what ``sanitize`` would drop.

Usage:
    uv run python scripts/inspect_event_payloads.py --database sqlite:///sou.db
    uv run python scripts/inspect_event_payloads.py --database postgresql+psycopg2://... --expect-clean
"""

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402

from backend import resanitise  # noqa: E402

PATTERNS = {
    "canary": re.compile(r"CANARY"),
    "host": re.compile(r"[A-Za-z0-9._-]+\.(?:example|local|internal|lan|corp|home)(?::\d+)?"),
    "userinfo_url": re.compile(r"://[^/\s:@]+:[^@\s/]+@"),
    "home_path": re.compile(r"/Users/[A-Za-z0-9._-]+|/home/[a-z0-9_.-]+|C:\\\\Users"),
    "keep_marker": re.compile(r"KEEPME-[A-Z]+"),
}


def scan(database):
    engine = create_engine(database)
    dialect = engine.dialect.name
    from sqlalchemy import inspect as sqlalchemy_inspect
    cols = {c["name"] for c in sqlalchemy_inspect(engine).get_columns("events")}
    payload_column = next(c for c in ("payload", "data", "payload_json", "event_data") if c in cols)
    counts = {name: 0 for name in PATTERNS}
    counts_sanitisable = {name: 0 for name in PATTERNS}
    rows_total = 0
    v1_rows = 0
    digest = hashlib.sha256()
    with engine.connect() as conn:
        for row_id, event_type, raw in conn.execute(
            text(f"SELECT id, event_type, {payload_column}::text FROM events ORDER BY created_at, id"
                 if dialect == "postgresql" else
                 f"SELECT id, event_type, {payload_column} FROM events ORDER BY created_at, id")
        ):
            rows_total += 1
            digest.update(f"{row_id}|{event_type}|{raw}\n".encode())
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError):
                payload = None
            is_v1 = isinstance(payload, dict) and resanitise.is_v1_state_row(event_type, payload)
            if is_v1:
                v1_rows += 1
            blob = json.dumps(payload, ensure_ascii=False) if payload is not None else str(raw)
            for name, pat in PATTERNS.items():
                hits = len(pat.findall(blob))
                counts[name] += hits
                if not is_v1:
                    counts_sanitisable[name] += hits
    return {
        "dialect": dialect,
        "rows": rows_total,
        "v1_state_rows": v1_rows,
        "digest": digest.hexdigest(),
        "all_rows": counts,
        "sanitisable_rows": counts_sanitisable,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--database", required=True)
    ap.add_argument("--expect-clean", action="store_true",
                    help="exit 1 unless no sanitisable row carries canary/host/userinfo/home-path content")
    args = ap.parse_args(argv)

    report = scan(args.database)
    print(f"inspect: {report['rows']} events in {report['dialect']}, "
          f"v1 state rows (excluded by ruling): {report['v1_state_rows']}")
    print(f"inspect: table digest {report['digest']}")
    print("inspect: matches over ALL rows        : " + ", ".join(
        f"{k}={v}" for k, v in report["all_rows"].items()))
    print("inspect: matches over sanitisable rows: " + ", ".join(
        f"{k}={v}" for k, v in report["sanitisable_rows"].items()))

    if args.expect_clean:
        leftover = {k: v for k, v in report["sanitisable_rows"].items()
                    if k in ("canary", "host", "userinfo_url", "home_path") and v}
        if leftover:
            print(f"inspect: FAIL — leftovers in sanitisable rows: {leftover}")
            return 1
        print("inspect: PASS — no sanitisable row carries canary/host/userinfo/home-path content")
    return 0


if __name__ == "__main__":
    sys.exit(main())
