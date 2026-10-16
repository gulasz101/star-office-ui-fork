#!/usr/bin/env python3
"""Post a scripted agent session to the dashboard, in real time, for eyeballing v3.

usage: simulate_session.py [--url http://127.0.0.1:19000] [--agent claude-code] [--speed 1.0]
Stdlib only. Each step is (seconds to wait before, event_type, payload).
"""
import argparse, json, time, urllib.request
from datetime import datetime, timezone

SESSION = [
    (0.0, "session_start", {"source": "startup"}),
    (2.0, "state_change", {"state": "thinking"}),
    (2.5, "tool_call", {"tool": "Read", "status": "ok", "file": "app.py"}),
    (1.0, "tool_call", {"tool": "Grep", "status": "ok"}),
    (3.0, "file_edit", {"tool": "Edit", "file": "backend/app.py", "status": "ok"}),
    (1.5, "file_edit", {"tool": "Edit", "file": "frontend/v3/src/main.js", "status": "ok"}),
    (3.0, "command_run", {"command": "pytest", "status": "ok"}),
    (4.0, "command_run", {"command": "kubectl", "status": "ok"}),
    (4.0, "tool_call", {"tool": "WebSearch", "status": "ok"}),
    (4.0, "file_edit", {"tool": "Write", "file": "README.md", "status": "ok"}),
    (4.0, "code_completion", {}),
]


def post(url, agent, event_type, payload):
    body = {"version": 1, "agent": agent, "event_type": event_type,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "payload": payload}
    req = urllib.request.Request(url + "/events", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:19000")
    ap.add_argument("--agent", default="claude-code")
    ap.add_argument("--speed", type=float, default=1.0)
    a = ap.parse_args()
    for wait, et, payload in SESSION:
        time.sleep(wait / a.speed)
        print(f"{time.strftime('%H:%M:%S')} {a.agent} {et} {json.dumps(payload)} -> {post(a.url, a.agent, et, payload)}", flush=True)
