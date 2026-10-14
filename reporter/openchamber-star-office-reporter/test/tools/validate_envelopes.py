#!/usr/bin/env python3
"""Validate captured event envelopes with the dashboard's own validator.

Usage: python3 validate_envelopes.py <ndjson-file> <repo-root>

Imports backend/event_schema.py (ADR-035 schema v1) exactly as POST /events
does, with third-party deps from the repo's .tooling/site target install.
Prints "VALID <n>" and exits 0 when every envelope passes; exits 1 otherwise.
"""
import json
import sys

ndjson, root = sys.argv[1], sys.argv[2]
sys.path.insert(0, f"{root}/backend")
sys.path.insert(0, f"{root}/.tooling/site")
import event_schema  # noqa: E402

failures = 0
count = 0
for line in open(ndjson, encoding="utf-8"):
    if not line.strip():
        continue
    count += 1
    try:
        event_schema.validate_event(json.loads(line))
    except event_schema.EventValidationError as exc:
        failures += 1
        print(f"INVALID line {count}: {exc}", file=sys.stderr)

if failures:
    print(f"FAILED: {failures}/{count} envelopes invalid", file=sys.stderr)
    sys.exit(1)
print(f"VALID {count}")
