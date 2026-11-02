#!/usr/bin/env python3
"""paperclip-claude-code-reporter — put the Paperclip PM in the Lodge (GUL-827).

Claude Code under Paperclip runs on the ``claude_local`` adapter. Claude Code hooks
are not an option here: Paperclip runs it with a sandboxed ``HOME``, so
``~/.claude/settings.json`` never loads (GUL-826). Nothing inside the run can tell us
it started or ended, so this reporter watches Paperclip from the outside instead.

Two credentials, two directions:

* **read** — ``GET {paperclip_url}/companies/{company_id}/live-runs`` with a Paperclip
  *agent* API key. It answers "which runs are live right now"; we keep only the runs of
  our own ``agent_id``. That is the whole trigger: a live run means ``working``, no live
  run means ``idle``.
* **write** — ``POST {dashboard_url}/events`` with that agent's Star Office ingest token
  (ADR-078 registry, minted by ``backend/tokens.py register``). The token decides the
  identity, so the envelope's ``agent`` must equal the token's agent or the door answers
  403 — this is checked on the first successful post, not assumed.

Why a poller and not an event: Paperclip can only be read here, and reading it needs a
key the board mints (an agent key cannot mint another agent's key). A poller needs no
part of the run to cooperate, survives a Claude Code restart, and is the only trigger
that also works if the PM moves adapter.

State is derived, never stored: a restart re-derives it from Paperclip. The one thing
worth remembering is *when we last said something*, so a long run is not re-announced
every tick — see ``decide``.

The renderer falls back on its own after 60 s without events (``IDLE_COFFEE_MS`` in
``frontend/v3/activity.js``), so ``working`` is re-asserted while a run is live and the
character stays at the desk instead of drifting to the coffee machine mid-run.

Usage:
    paperclip-claude-code-reporter.py run            # loop, the daemon mode
    paperclip-claude-code-reporter.py once           # one tick, for cron and tests
    paperclip-claude-code-reporter.py once --dry-run # decide, print, send nothing
    paperclip-claude-code-reporter.py check          # validate config + both doors

Config comes from a JSON file (``$SOU_REPORTER_CONFIG``, else
``~/.config/homelab/sou-claude-code-reporter.json``); environment variables of the same
name win, so a systemd/launchd unit can carry them instead. The token is never logged
and never printed, not even in ``check``.

Nothing here knows the agent's name: ``agent`` comes from config, and the token must
agree with it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PROG = "paperclip-claude-code-reporter"

# Paperclip run statuses that mean "this run is alive". Everything else (succeeded,
# failed, cancelled, timed_out, interrupted) is done, whatever it finished as.
LIVE_STATUSES = frozenset({"running", "starting", "pending"})

# Re-assert `working` this often while a run is live. Well under the renderer's 60 s
# IDLE_COFFEE_MS, with room for one lost POST.
DEFAULT_REASSERT_SECONDS = 30
DEFAULT_POLL_SECONDS = 10

DEFAULTS = {
    "dashboard_url": "http://127.0.0.1:19000",
    "paperclip_url": "http://127.0.0.1:3100",
    "agent": "claude-code",
    "poll_seconds": DEFAULT_POLL_SECONDS,
    "reassert_seconds": DEFAULT_REASSERT_SECONDS,
    "timeout_ms": 5000,
}

CONFIG_ENV = {
    "dashboard_url": "SOU_DASHBOARD_URL",
    "paperclip_url": "PAPERCLIP_URL",
    "agent": "SOU_AGENT",
    "token": "SOU_INGEST_TOKEN",
    "agent_id": "PAPERCLIP_AGENT_ID",
    "company_id": "PAPERCLIP_COMPANY_ID",
    "paperclip_token": "PAPERCLIP_TOKEN",
    "poll_seconds": "SOU_POLL_SECONDS",
    "reassert_seconds": "SOU_REASSERT_SECONDS",
    "timeout_ms": "SOU_TIMEOUT_MS",
}

# Never echo these, whatever happens to the process.
SECRET_KEYS = ("token", "paperclip_token")


class ReporterError(Exception):
    """A user-facing failure: printed without a traceback, exit 1."""


# --------------------------------------------------------------------------- config


def default_config_path(env=None):
    env = os.environ if env is None else env
    explicit = env.get("SOU_REPORTER_CONFIG")
    if explicit:
        return explicit
    home = env.get("HOME") or os.path.expanduser("~")
    return os.path.join(home, ".config", "homelab", "sou-claude-code-reporter.json")


def _as_int(value, name, minimum):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ReporterError(f"{name}: expected an integer, got {value!r}")
    if parsed < minimum:
        raise ReporterError(f"{name}: must be >= {minimum}, got {parsed}")
    return parsed


def load_config(path=None, env=None):
    """File first, then environment. Environment wins; it is what a unit file carries."""
    env = os.environ if env is None else env
    path = path or default_config_path(env)

    cfg = dict(DEFAULTS)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                on_disk = json.load(handle)
        except (OSError, ValueError) as exc:
            raise ReporterError(f"config {path}: {exc}")
        if not isinstance(on_disk, dict):
            raise ReporterError(f"config {path}: expected a JSON object")
        cfg.update({k: v for k, v in on_disk.items() if v is not None})

    for key, var in CONFIG_ENV.items():
        raw = env.get(var)
        if raw is not None and str(raw).strip() != "":
            cfg[key] = raw

    for key in ("agent", "agent_id", "company_id", "token", "paperclip_token"):
        value = cfg.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ReporterError(
                f"config: {key} is missing — set it in {path} or via {CONFIG_ENV[key]}"
            )
        cfg[key] = value.strip()

    cfg["dashboard_url"] = str(cfg["dashboard_url"]).rstrip("/")
    cfg["paperclip_url"] = str(cfg["paperclip_url"]).rstrip("/")
    cfg["poll_seconds"] = _as_int(cfg["poll_seconds"], "poll_seconds", 1)
    cfg["reassert_seconds"] = _as_int(cfg["reassert_seconds"], "reassert_seconds", 1)
    cfg["timeout_ms"] = _as_int(cfg["timeout_ms"], "timeout_ms", 200)
    return cfg


# ------------------------------------------------------------------------- pure logic


def utc_now_iso(now=None):
    """The envelope instant: ISO 8601, UTC, Z-suffixed (ADR-035)."""
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + (
        "%03dZ" % (moment.astimezone(timezone.utc).microsecond // 1000)
    )


def build_event(agent, state, now=None):
    """The v1 envelope. `state` is the renderer's `state_change` vocabulary."""
    return {
        "version": 1,
        "agent": agent,
        "event_type": "state_change",
        "timestamp": utc_now_iso(now),
        "payload": {"state": state},
    }


def is_running(runs, agent_id):
    """True when `agent_id` has at least one live run in a live-runs payload.

    Tolerates both shapes Paperclip has answered with (a bare list, or {runs|items: [...]})
    and skips anything that is not a dict, because a reporter must not die on a feed.
    """
    if isinstance(runs, dict):
        runs = runs.get("runs") if "runs" in runs else runs.get("items")
    if not isinstance(runs, list):
        return False
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("agentId") or "").strip().lower() != str(agent_id).strip().lower():
            continue
        if str(run.get("status") or "").strip().lower() in LIVE_STATUSES:
            return True
    return False


def decide(state, now, last_sent_at, last_sent_state, reassert_seconds):
    """Should this tick post, and what?

    `state` is "working" or "idle". Returns None for "say nothing".

    The rules, in order:
      * a state we have not announced yet is always announced — a change is the point;
      * `working` is re-asserted once per `reassert_seconds`, or the renderer's 60 s idle
        fallback moves the character to the coffee machine during a long run;
      * `idle` is announced once and then left alone: repeating it would be noise, and
        the renderer already shows "asleep" on the strength of the first one.
    """
    if state != last_sent_state:
        return state
    if state != "working":
        return None
    if last_sent_at is None:
        return state
    if now - last_sent_at >= reassert_seconds:
        return state
    return None


# ----------------------------------------------------------------------------- I/O


def _request(method, url, timeout_s, headers=None, body=None):
    data = None
    hdrs = {"Accept": "application/json", "User-Agent": PROG}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise ReporterError(f"{method} {url}: {exc.reason}")
    except OSError as exc:
        raise ReporterError(f"{method} {url}: {exc}")


def fetch_live_runs(cfg):
    """The Paperclip runs that are live right now, as the API returns them."""
    url = f"{cfg['paperclip_url']}/api/companies/{cfg['company_id']}/live-runs"
    status, text = _request(
        "GET", url, cfg["timeout_ms"] / 1000, {"Authorization": f"Bearer {cfg['paperclip_token']}"}
    )
    if status == 401:
        raise ReporterError(
            "paperclip: 401 — paperclip_token is not accepted. It must be an agent API key "
            "for the agent whose runs you are reporting; a board key is not wanted here."
        )
    if status != 200:
        raise ReporterError(f"paperclip: GET live-runs answered {status}: {text[:200]}")
    try:
        return json.loads(text)
    except ValueError:
        raise ReporterError("paperclip: live-runs did not answer JSON")


def post_event(cfg, state, now=None):
    """POST one state_change. Returns the HTTP status."""
    url = f"{cfg['dashboard_url']}/events"
    body = build_event(cfg["agent"], state, now)
    status, text = _request(
        "POST", url, cfg["timeout_ms"] / 1000,
        {"Authorization": f"Bearer {cfg['token']}"}, body,
    )
    if status == 401:
        raise ReporterError("dashboard: 401 — the ingest token is missing or unknown")
    if status == 403:
        raise ReporterError(
            f"dashboard: 403 — this token does not write agent {cfg['agent']!r}: {text[:200]}"
        )
    if status == 429:
        raise ReporterError("dashboard: 429 — this token is sending faster than the rate bucket")
    if status not in (200, 201):
        raise ReporterError(f"dashboard: POST /events answered {status}: {text[:200]}")
    return status


def log(message):
    print(f"{datetime.now().strftime('%Y-%m-%dT%H:%M:%S')} {PROG}: {message}", flush=True)


# ---------------------------------------------------------------------------- modes


def tick(cfg, state, now, last_sent_at, last_sent_state, dry_run=False):
    """One decision-and-send. Returns the (last_sent_at, last_sent_state) to carry on."""
    wanted = decide(state, now, last_sent_at, last_sent_state, cfg["reassert_seconds"])
    if wanted is None:
        return last_sent_at, last_sent_state
    if dry_run:
        log(f"[dry-run] would post state_change {wanted}")
        return now, wanted
    post_event(cfg, wanted)
    log(f"posted state_change {wanted}")
    return now, wanted


def cmd_once(cfg, dry_run=False):
    running = is_running(fetch_live_runs(cfg), cfg["agent_id"])
    state = "working" if running else "idle"
    log(f"live run: {'yes' if running else 'no'} -> {state}")
    tick(cfg, state, time.monotonic(), None, None, dry_run=dry_run)
    return 0


def cmd_run(cfg):
    log(f"watching agent {cfg['agent_id']} for {cfg['agent']!r}, "
        f"every {cfg['poll_seconds']}s, re-asserting working every {cfg['reassert_seconds']}s")
    last_sent_at, last_sent_state = None, None
    while True:
        try:
            running = is_running(fetch_live_runs(cfg), cfg["agent_id"])
            state = "working" if running else "idle"
            last_sent_at, last_sent_state = tick(
                cfg, state, time.monotonic(), last_sent_at, last_sent_state
            )
        except ReporterError as exc:
            # Transient by assumption: a Paperclip restart or a dropped POST must not end
            # the daemon, because then the room silently freezes at whatever it last said.
            # The next tick re-derives the truth from Paperclip and corrects itself.
            log(f"error: {exc}")
            last_sent_state = None
        time.sleep(cfg["poll_seconds"])


def cmd_check(cfg):
    """Prove both credentials work, without ever printing either of them."""
    running = is_running(fetch_live_runs(cfg), cfg["agent_id"])
    log(f"paperclip: ok — agent {cfg['agent_id']} is {'running' if running else 'idle'}")
    state = "working" if running else "idle"
    status = post_event(cfg, state)
    log(f"dashboard: ok — {status} for state_change {state} as {cfg['agent']!r}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog=PROG, description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("run", "once", "check"))
    parser.add_argument("--dry-run", action="store_true", help="decide and log, send nothing")
    parser.add_argument("--config", help="path to the JSON config")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        if args.mode == "run":
            return cmd_run(cfg)
        if args.mode == "check":
            return cmd_check(cfg)
        return cmd_once(cfg, dry_run=args.dry_run)
    except ReporterError as exc:
        log(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
