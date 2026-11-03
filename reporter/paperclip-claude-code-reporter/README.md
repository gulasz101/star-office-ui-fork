# paperclip-claude-code-reporter

Puts **Claude Code**, the Paperclip PM, in the Lodge: `working` while one of its Paperclip
runs is alive, `idle` when none is.

One file, Python 3 standard library, no dependencies. GUL-827.

## Why it polls instead of hooking

Claude Code hooks are not available here. Paperclip runs the `claude_local` adapter with a
sandboxed `HOME`, so `~/.claude/settings.json` — and every hook in it — never loads
(GUL-826). Nothing inside the run can announce its own start or end.

So this reporter watches Paperclip from outside. It asks
`GET /api/companies/{company_id}/live-runs` which runs are live right now, keeps the ones
belonging to its own `agent_id`, and derives the state:

| Paperclip | Lodge |
| --- | --- |
| one or more live runs for this agent | `state_change {state: working}` |
| no live run | `state_change {state: idle}` |

An alternative was a Claude Code plugin/hook loaded from the *project* `.claude/` directory,
which does survive the sandboxed `HOME` — rejected because it would mean committing a hook
block into every repo the PM touches, and it still could not report a run that dies before
its first hook fires.

Polling also has one property the other designs lack: it re-derives the truth on every tick,
so a crashed reporter, a restarted Paperclip, or a missed POST corrects itself within one
interval instead of freezing the room at whatever it last said.

## State rules

Derived, never stored. A restart is not an event.

- A state different from the last one announced is announced immediately.
- `working` is **re-asserted every 30 s** while a run is live. The renderer falls back to
  "coffee break" after 60 s without events (`IDLE_COFFEE_MS` in `frontend/v3/activity.js`),
  so without this a long run walks off to the coffee machine halfway through.
- `idle` is announced once and then left alone. Repeating it is noise; the renderer already
  shows the character asleep on the strength of the first one, and keeps the room for 24 h
  (`ACTIVE_WINDOW_MS`).

## Configuration

`$SOU_REPORTER_CONFIG`, else `~/.config/homelab/sou-claude-code-reporter.json`. Every key
can be overridden by an environment variable, so a launchd or systemd unit can carry the
secrets instead of a file:

```json
{
  "dashboard_url": "http://192.168.1.179:30260",
  "paperclip_url": "http://127.0.0.1:3100",
  "agent": "claude-code",
  "agent_id": "97efeb2d-5b3a-4110-b893-6cbcbb29164c",
  "company_id": "0e2eb054-4cb6-4ea1-a2e7-2b1894285d8c",
  "token": "<the agent's Star Office ingest token>",
  "paperclip_token": "<a Paperclip agent API key>",
  "poll_seconds": 10,
  "reassert_seconds": 30
}
```

| JSON key | Environment | Meaning |
| --- | --- | --- |
| `dashboard_url` | `SOU_DASHBOARD_URL` | Star Office ingest base URL, no `/events` |
| `paperclip_url` | `PAPERCLIP_URL` | Paperclip API base, no `/api` |
| `agent` | `SOU_AGENT` | Agent name in the Lodge; the token must own it |
| `agent_id` | `PAPERCLIP_AGENT_ID` | Paperclip agent UUID whose runs we report |
| `company_id` | `PAPERCLIP_COMPANY_ID` | Paperclip company UUID |
| `token` | `SOU_INGEST_TOKEN` | Star Office ingest token (ADR-078 registry) |
| `paperclip_token` | `PAPERCLIP_TOKEN` | Paperclip **agent** API key |
| `poll_seconds` | `SOU_POLL_SECONDS` | Tick interval, default 10 |
| `reassert_seconds` | `SOU_REASSERT_SECONDS` | `working` repeat interval, default 30 |
| `timeout_ms` | `SOU_TIMEOUT_MS` | Per-request timeout, default 5000 |

### The two credentials, and where they come from

**The ingest token** is minted by the dashboard's own registry, which prints it exactly once:

```bash
kubectl -n star-office exec deploy/sou-ingest -- \
  python3 backend/tokens.py register claude-code --room career --token-only
```

Only its SHA-256 is stored. Re-running `register` for an existing agent adds a token rather
than creating a second agent, so rotation is the same command.

**The Paperclip key** must be an *agent* API key, and it must be minted by the **board** — an
agent key cannot mint another agent's key (`403 Board access required`):

```bash
npx paperclipai token agent create \
  --company-id <company-id> --agent 97efeb2d-5b3a-4110-b893-6cbcbb29164c \
  --name sou-claude-code-reporter
```

Neither value belongs in this repo. Both go into SOPS/age for the host that runs the
reporter; the installer decrypts them to a `0600` file.

## Usage

```bash
./paperclip-claude-code-reporter.py check          # both credentials, sends one event
./paperclip-claude-code-reporter.py once           # one tick, for cron and tests
./paperclip-claude-code-reporter.py once --dry-run # decide and log, send nothing
./paperclip-claude-code-reporter.py run            # loop; this is the daemon mode
```

`check` is the honest smoke test: it reads Paperclip, posts the real current state, and
prints neither credential. A `401` on the read means the Paperclip key is wrong; a `403` on
the write means the ingest token belongs to a different agent than `agent` — the dashboard's
door enforces that, so a token can only ever write its own room.

## Tests

```bash
python3 -m unittest discover -s reporter/paperclip-claude-code-reporter/test
```

Pure and mocked: no Paperclip, no dashboard, no network.
