> **Superseded (SOU-210):** install the Claude Code plugin instead —
> [`gulasz101/claude-code-star-office-reporter`](https://github.com/gulasz101/claude-code-star-office-reporter)
> (`/plugin marketplace add gulasz101/claude-code-star-office-reporter`). It ships this hook plus
> background-task and subagent events. This copy stays for its tests and history.

# claude-code — Claude Code hooks reporter for Star Office UI

A [Claude Code](https://docs.claude.com/en/docs/claude-code/hooks) **command hook**
that reports what the agent is doing to a [Star Office UI](../../README.md)
dashboard, the way `reporter/openchamber-star-office-reporter/` does for
OpenCode. One Python file, standard library only, no install step. Claude Code
hands the hook event to the script as JSON on stdin; the script turns it into
one [ADR-035 schema-v1 envelope](../../backend/schemas/event_v1.json) and `POST`s
it to `/events`.

It hooks Claude Code itself, not a wrapper, so terminal sessions, IDE
extensions and background sessions all report.

## What it reports

| Claude Code hook event | `event_type` | `payload` |
|---|---|---|
| `SessionStart` | `session_start` | `session_id`, `cwd` (basename only), `source` (`startup`/`resume`/`clear`/`compact`/`fork`) |
| `UserPromptSubmit` | `state_change` | `state: "thinking"`, `session_id` |
| `PreToolUse` | `tool_call` | `tool`, `status: "started"`, `session_id` |
| `PostToolUse` (Edit, Write, MultiEdit, NotebookEdit) | `file_edit` | `tool`, `file` (basename only), `status: "ok"`, `session_id`, `duration_ms` |
| `PostToolUse` (Bash, PowerShell) | `command_run` | `tool`, `command` (**program name plus one allow-listed subcommand**, see below), `status: "ok"`, `session_id`, `duration_ms` |
| `PostToolUse` (any other tool, incl. `mcp__server__tool`) | `tool_call` | `tool`, `status: "ok"`, `session_id`, `duration_ms` |
| `Stop` | `code_completion` | `session_id` |
| `Notification` | `state_change` | `state: "waiting_for_user"`, `session_id`, `notification_type` |
| `SessionEnd` | `session_end` | `session_id`, `reason` |

Any other hook event (`PreCompact`, `SubagentStop`, `FileChanged`, …) sends
nothing: the event set above is what the office renderer knows how to place.
Payload keys are the ones the v3 activity map reads (`tool`, `status`, `file`,
`command`, `state`), so an edit puts the character at the desk, a command at
the rack, a long build in the garage and a notification at the coffee machine.

**The command label (SOU-173).** `command` carries the program name, plus for
an allow-list of CLI families the *first bare subcommand token*:
`git, npm, pnpm, yarn, docker, kubectl, cargo, go, helm, flux, make, uv, pip,
poetry`. That list mirrors `frontend/v3/activity.js`, which decides desk /
rack / garage from the verb — `git push`, `docker build` and `kubectl apply`
go to the garage, `git status` stays at the rack. The subcommand must match
`^[a-z][a-z0-9-]{0,20}$`, so flags, `key=value` pairs, paths, URLs and values
are skipped and never echoed: `git -c user.password=SECRET push` is sent as
`git push`. `npm`/`pnpm`/`yarn` keep `run <script>` as two tokens because
activity.js reads the runner verb from the token after `run`. Every other
program is sent as the bare name, as before.

## Privacy — what never leaves the machine

The script is written so it *cannot* forward prompt text, file contents,
tool output (`tool_response`), error text, a full command or its arguments,
absolute paths and directory prefixes, the transcript path, and
`last_assistant_message`. Only names, statuses, one basename, one program name
plus (for the allow-listed CLI families) one or two bare subcommand tokens,
the session id and a handful of documented enum values are sent — never a
flag, value, path or URL from a command line. The test suite proves this with
canary values in every forbidden field (`tests/test_privacy.py`,
`tests/test_subcommands.py`).

## It cannot break your session

Exit code 2 blocks a tool call or rejects a prompt, and plain-text stdout is
added to Claude's context on `UserPromptSubmit` and `SessionStart`. So this
hook always prints nothing on stdout and always `exit 0`, whatever happens:
no dashboard, a refused event, undecodable stdin, an unreachable host. The POST
is capped at 2 s (`STAR_OFFICE_TIMEOUT`), which is why `settings.example.json`
gives every handler `"timeout": 5` — Claude Code's own default budget for
`SessionEnd` hooks is 1.5 s, and a per-hook timeout raises it.

## Install

Requires a reachable dashboard (`backend/app.py`, default port `19000`) and
`python3` on `PATH`. Claude Code reads hooks from JSON settings files; put the
`hooks` block of [`settings.example.json`](settings.example.json) in **one** of
these (hook entries merge across levels, so installing it twice sends every
event twice):

**A. User-level — every project reports** (`~/.claude/settings.json`)

Replace `${CLAUDE_PROJECT_DIR}` with the absolute path of this checkout:

```jsonc
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "python3",
            "args": ["/absolute/path/to/star-office-ui-fork/reporter/claude-code/star_office_hook.py"],
            "timeout": 5
          }
        ]
      }
    ]
    // …and the same handler under SessionStart, UserPromptSubmit, PreToolUse, Stop, Notification, SessionEnd
  }
}
```

**Why `args` and `statusMessage` are valid here (SOU-173 verification).** The
current hooks reference (docs.claude.com/en/docs/claude-code/hooks, "Command
hook fields") documents `args` on command hooks: *"Argument list. When
present, `command` is resolved as an executable and spawned directly with
`args` as the argument vector, with no shell involved."*, and its guidance is
*"Prefer exec form for any hook that references a path placeholder"* — exactly
what `${CLAUDE_PROJECT_DIR}` is. `statusMessage` is documented under "Common
fields": *"Custom spinner message displayed while the hook runs."* So the
example keeps the exec form. If your Claude Code build predates `args`, the
equivalent shell form is `"command": "python3 /abs/path/star_office_hook.py"`
(quote the path if it contains spaces); everything else is unchanged.

**B. Project-level — only this repo** (`.claude/settings.json`, committable)

Copy `settings.example.json` to `.claude/settings.json`; `${CLAUDE_PROJECT_DIR}`
already points at the project root the session started in. Note that
`UserPromptSubmit`, `Stop` and `SessionEnd` have no matcher support, so their
groups omit it.

Restart the session (or run `/hooks` to see what Claude Code picked up). To
silence it temporarily, set `STAR_OFFICE_URL=""` for the hook, delete the
entry, or set `disableAllHooks` in your settings — do not edit the script to
fail: a hook that exits non-zero shows error notices in the transcript.

## Configure

All optional, all environment variables (Claude Code passes the parent
environment to hook commands; put them in your shell or in the `"env"` block of
your settings file):

| Setting | Env var | Default | Notes |
|---|---|---|---|
| Dashboard URL | `STAR_OFFICE_URL` | `http://127.0.0.1:19000` | base URL; `/events` is appended. **Set it to an empty value to switch reporting off** without uninstalling the hook; unset still means the local dashboard |
| Agent name | `STAR_OFFICE_AGENT` | `claude-code` | one token, no spaces; registers the agent on first event |
| Network cap (s) | `STAR_OFFICE_TIMEOUT` | `2.0` | clamped to 0.25–10 |
| Debug echo | `STAR_OFFICE_DEBUG` | off | writes the envelope to **stderr** only, never stdout |

## Test it without Claude Code

The hook is just a stdin filter, so a recorded event drives it directly — start
the backend and watch the feed:

```bash
backend/run.sh &                                              # dashboard on :19000
python3 star_office_hook.py < tests/fixtures/post_tool_use_edit.json
curl -s "http://127.0.0.1:19000/events/recent?agent=claude-code&limit=3"
```

The suite covers the mapping table, envelope validation against
`backend/schemas/event_v1.json` (through `jsonschema` *and* the backend's own
validator), the privacy canaries, the fail-open/exit-0 rules and this file's
wiring:

```bash
uv run pytest reporter/claude-code/tests -q
```

## Known limits (deliberate, out of this ticket's scope)

* Failed tool calls are not reported: `PostToolUseFailure` is not wired, so a
  call that dies mid-flight leaves a `started` bubble and no result.
* The `command` label carries a subcommand only for the allow-listed CLI
  families, and only the first bare identifier: wrappers outside the stripped
  set (`timeout 30 git push` labels as `timeout`), second commands after a
  `&&` chain, and everything a non-listed program points at stay invisible —
  the full command is forbidden, not just redacted.
* A `MultiEdit`/`NotebookEdit` whose path is not under `file_path` /
  `notebook_path` / `path` falls back to a plain `tool_call` instead of
  guessing.
* Subagent events (`SubagentStart`, `SubagentStop`) are not reported, so a
  subagent's work is invisible until its own ticket exists.
