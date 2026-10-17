#!/usr/bin/env python3
"""Star Office UI smoke test (non-destructive): the Lodge (v3) and its event door.

Usage:
  python3 scripts/smoke_test.py --base-url http://127.0.0.1:19000

Optional env:
  SOU_SMOKE_TOKEN=xxxx   # an ingest token for the agent "smoke-test" when the
                         # backend sets SOU_INGEST_TOKENS (POST /events is then gated)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REQUIRED = [
    ("GET", "/health", 200),
    ("GET", "/v3/", 200),
    ("GET", "/events/recent?limit=1", 200),
    ("GET", "/events/agents", 200),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # report the 302 instead of following it
        return None


def req(method: str, url: str, body: dict | None = None, token: str = "") -> tuple[int, str]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=10) as resp:
            return resp.status, resp.read(2000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(2000).decode("utf-8", "replace")
    except Exception as exc:  # connection refused etc.
        return 0, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:19000")
    base = parser.parse_args().base_url.rstrip("/")
    token = os.environ.get("SOU_SMOKE_TOKEN", "")
    failures = []

    for method, path, expected in REQUIRED:
        code, body = req(method, base + path)
        (failures.append(f"{method} {path} -> {code}, expected {expected}") if code != expected
         else print(f"  OK  {method} {path} -> {code}"))

    code, _ = req("GET", base + "/")
    (failures.append(f"GET / -> {code}, expected 302 to /v3/") if code != 302 else print("  OK  GET / -> 302 (to /v3/)"))

    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    event = {"version": 1, "agent": "smoke-test", "event_type": "tool_call", "timestamp": now,
             "payload": {"tool": "Read", "status": "ok"}}
    code, body = req("POST", base + "/events", event, token=token)
    (failures.append(f"POST /events -> {code}, body={body[:200]}") if code != 201 else print("  OK  POST /events -> 201"))

    if failures:
        print("\nSMOKE FAILED:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nsmoke ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
