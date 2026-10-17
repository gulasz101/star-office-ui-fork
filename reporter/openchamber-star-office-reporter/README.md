# openchamber-star-office-reporter

An [OpenCode](https://opencode.ai) / OpenChamber **plugin** that reports your coding
agent's activity to a [Star Office UI](https://github.com/gulasz101/star-office-ui-fork)
dashboard ("the Lodge") as it happens: the agent gets its own room and a pixel character that
works in it. One file, zero dependencies, standard-library only. Your
agent keeps working — the office feed lights up with tool calls, file edits, commands
run, and sessions started/ended.

It hooks the agent engine (not a wrapper script), so everything the model does through
OpenCode's tools is captured automatically, including work done inside OpenChamber
sessions.

## What it reports

Every event is one
[schema-v1 envelope](https://github.com/gulasz101/star-office-ui-fork/blob/master/backend/schemas/event_v1.json)
`POST`ed to the dashboard's `/events` endpoint:

| `event_type`     | when                                        | `payload` fields                                                      |
|------------------|---------------------------------------------|-----------------------------------------------------------------------|
| `session_start`  | plugin loads (engine/server came up)        | `directory`, `engine`, `engine_version`, `plugin`, `pid`              |
| `tool_call`      | every tool execution, success **or** error  | `tool`, `status`, `session_id`, `duration_ms`, `error` (on failure)   |
| `file_edit`      | write/edit/patch-class tools                | `tool`, `file`, `session_id`, `status`, `duration_ms`                 |
| `command_run`    | bash/shell-class tools                      | `command`, `cwd`, `session_id`, `status`, `exit_code`, `duration_ms`, `output` |
| `code_completion`| a session goes idle after editing files     | `session_id`, `files_touched`, `edits`                                |
| `session_end`    | session deleted, plugin unloaded, SIGINT/TERM | `session_id`, `reason`, `duration_seconds`, `events_logged`         |

The dashboard decides what the character does with each event. **It also sanitises every
payload at its door** (command lines reduced to the program, paths and `cwd` to basenames, `output`,
`error` and `directory` dropped; the public page hides `cwd` too), so fields this reporter sends are never stored in full or
shown on a public page.

Siblings: [`claude-code-star-office-reporter`](https://github.com/gulasz101/claude-code-star-office-reporter)
and [`hermes-star-office-reporter`](https://github.com/gulasz101/hermes-star-office-reporter).

## Install

**Requires:** OpenCode ≥ 2.0 (or OpenChamber, which embeds it) and a reachable
Star Office UI dashboard (`backend/app.py`, default port `19000`).

Pick one — do **not** do both, or every event is sent twice:

### A. Global (recommended — every project reports)

Copy the file into OpenCode's global plugins directory:

```bash
cp openchamber-star-office-reporter.js ~/.config/opencode/plugins/
```

### B. Per project

```bash
mkdir -p .opencode/plugins
cp openchamber-star-office-reporter.js .opencode/plugins/
```

### C. From a published npm package (future)

```jsonc
// opencode.json
{ "plugin": ["openchamber-star-office-reporter"] }
```

Restart the editor/session afterwards; plugins load at startup. There is no build
step — the plugin is plain ESM JavaScript.

## Configure

All configuration is optional; defaults point at a local dashboard and the
`openchamber` agent name. **Environment variables override config files.**

| Setting          | Env var                 | Config-file key  | Default                        |                                             |
|------------------|-------------------------|------------------|--------------------------------|---------------------------------------------|
| Dashboard URL    | `SOU_DASHBOARD_URL`     | `dashboardUrl`   | `http://127.0.0.1:19000`       | Base URL of the Star Office UI backend      |
| Agent name       | `SOU_AGENT_NAME`        | `agent`          | `openchamber`                  | Feed identity; register/name your agents to match your office UI setup (`opencode` also works if that is your registered name) |
| Ingest token     | `SOU_TOKEN`             | `token`          | —                              | Sent as `Authorization: Bearer …` on every `POST /events`; required once the dashboard runs with `SOU_INGEST_TOKENS` (SOU-219). Mint with `python backend/tokens.py new <agent>` — the **same name as `SOU_AGENT_NAME`**, or the door answers 403. Never printed to logs, never in the event body. |
| Enable/disable   | `SOU_REPORTER_ENABLED`  | `enabled`        | `true`                         | `0`/`false`/`no`/`off` to silence the reporter |
| Timeout          | `SOU_TIMEOUT_MS`        | `timeoutMs`      | `5000`                         | Per-event HTTP timeout, clamped to 200–30000 |
| Debug mirror     | `SOU_DEBUG`             | `debug`          | `false`                        | Also append warn lines to a log file        |
| Debug log path   | `SOU_REPORTER_LOG`      | —                | `$XDG_STATE_HOME/sou-reporter/reporter.log` | Where debug mirror lines go      |
| Config file path | `SOU_REPORTER_CONFIG`   | —                | —                              | Explicit file, skipping discovery           |

Config-file candidates (first one found wins, `{"dashboardUrl": …}` JSON):

1. `$SOU_REPORTER_CONFIG`
2. `$XDG_CONFIG_HOME/star-office-reporter.json`
3. `$XDG_CONFIG_HOME/opencode/star-office-reporter.json`
4. `~/.config/opencode/star-office-reporter.json`

### Enable / disable examples

Point one machine's agents at the office dashboard on your LAN:

```bash
SOU_DASHBOARD_URL=http://dashboard.lan:19000 \
  opencode run "fix the flaky test"
```

Permanently, without env spam — write the file:

```bash
cat > ~/.config/opencode/star-office-reporter.json <<'EOF'
{
  "dashboardUrl": "http://dashboard.lan:19000",
  "agent": "openchamber",
  "timeoutMs": 5000,
  "enabled": true
}
EOF
```

On a token-gated dashboard add `"token": "<your one-time ingest token>"` to that file (or
`SOU_TOKEN=…` in the environment) — `chmod 600` it: the config file is where a plaintext
secret lives on your machine, the way every other homelab reporter secret does. It never
belongs in git, a ticket or a log.

Take a demo / offline day silently (reporter stays installed, sends nothing):

```bash
SOU_REPORTER_ENABLED=0 opencode        # this shell only
```

```jsonc
// ~/.config/opencode/star-office-reporter.json  — globally quiet
{ "enabled": false }
```

Flip the file back to `"enabled": true` (or drop the env var) to resume. Disabling is
one knob; nothing else about the plugin changes.

### For OpenChamber users

Same story — OpenChamber embeds the OpenCode engine, so the global install above
applies. Because OpenChamber runs the engine inside a server process, the plugin's
`console` warnings can be swallowed; run with `SOU_DEBUG=1` (see
`SOU_REPORTER_LOG`) if you need to see reporter diagnostics.

## Privacy & safety (by design)

- **Credentials never leave.** Payloads are scanned before sending: `sk-…`/`rk-…`/`ghp_…`/
  `github_pat_…`/AWS `AKIA…`/Slack `xox…`/JWTs/`Bearer …`, plus any value stored under a
  secret-looking key (`token`, `api_key`, `password`, `secret`, …) are replaced with
  `[redacted]`. Your ingest token is scrubbed even when it appears verbatim in command
  output. When `SOU_TOKEN`/`token` is configured (SOU-223), the reporter sends exactly one
  authenticated header — `Authorization: Bearer <token>` on `POST /events`, the door
  contract from SOU-219 — and the plaintext token is redacted from every reporter log line
  and debug-file entry. It is never part of the event body or URL.
- **Long output is truncated.** Every string field is capped (4 KB) and the whole
  payload stays under the dashboard's 64 KB limit (the schema validator would drop it
  otherwise); overlong values keep a visible `…[truncated N chars]` tail.
- **Never blocks the agent.** Each event is fire-and-forget with a 5 s timeout
  (configurable). Unreachable dashboard → one warn line per failure, a drop counter,
  and the agent doesn't notice. No retries, no queue backpressure, no thrown errors
  into tool pipelines or hook paths.
- **Tool errors are reported as events**, not swallowed; reporting failures can never
  break a session.

## Verify it works

Fire one run at your dashboard, then read the feed back:

```bash
curl -s "http://127.0.0.1:19000/events/recent?agent=openchamber&limit=20" | json_pp
```

You should see `session_start`, `tool_call`, … rows. If the feed is empty:

- `dashboardUrl` typo / wrong port → the debug log says `dashboard unreachable, dropped <type>`
- `HTTP 401` in the debug log → the dashboard was started with `SOU_INGEST_TOKENS`
  (token-gated door, SOU-219) but no `SOU_TOKEN` is configured; set the token, or run the
  reporter against an untokened dev instance.
- `HTTP 403` → your `SOU_TOKEN` was minted for a different agent than `SOU_AGENT_NAME`
  claims; a token only ever writes its own agent's room. Re-mint with
  `python backend/tokens.py new <agent>` using the reporter's exact agent name.
- agent name not registered in the office → `POST /events` accepts it (agents
  auto-register), but double-check `GET /events/recent?agent=<name>` uses the same
  spelling (case-insensitive).
- plugin not loaded → start OpenCode with `--print-logs --log-level DEBUG` and look
  for plugin loading lines; confirm the file is in one of the two plugin directories.

## Development

Plain Node ≥ 18, no dependencies to install:

```bash
node --test test/reporter.test.mjs test/adapter.test.mjs   # unit suites (37)
E2E=1 node --test test/e2e-live-run.mjs                    # real headless opencode run
                                                          # vs a local capture server
E2E=1 E2E_LIVE=1 node --test test/e2e-live-run.mjs         # against the live dashboard
```

The end-to-end harness installs the plugin the documented way (global plugins dir),
runs `opencode run --standalone` in a throwaway `HOME`/project sandbox, and validates
every delivered envelope with the dashboard's own `backend/event_schema.py` (a
Star-Office-UI checkout; point `SOU_REPO_ROOT` at one if it isn't the default path).

## Provenance

Built for the Star-Office-UI fork. The source of truth is
`reporter/openchamber-star-office-reporter/` in that repo; this repository is its
standalone mirror under the same MIT licence. It ships no artwork.
