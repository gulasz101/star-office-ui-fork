# Star Office UI

🌐 Language: [中文](./README.md) | **English** | [日本語](./README.ja.md)

![Star Office UI Cover](docs/screenshots/readme-cover-2.jpg)

**A pixel-art AI office dashboard** — visualize your AI agents' work status in real time, so you can see at a glance who's doing what, what they did yesterday, and whether they're online.

Supports multi-agent collaboration, trilingual UI (CN/EN/JP), AI-powered room design, and desktop pet mode.

**Agent-agnostic by design.** The office speaks one open HTTP protocol — any AI agent that can send a request can live on the board: [Hermes](https://github.com/NousResearch/Hermes-Agent), OpenChamber, OpenCode, Pi, Claude, or your own scripts. No agent framework, client library, or proprietary workspace is required or read by the backend.

> This project was originally created by **[Ring Hyacinth](https://x.com/ring_hyacinth)** and **[Simon Lee](https://x.com/simonxxoo)**. This repository is a maintained fork ([gulasz101/star-office-ui-fork](https://github.com/gulasz101/star-office-ui-fork)) with a database-backed event stream and decoupled agent integration.
> Issues and PRs are welcome — thank you to everyone who contributes.

---

## ✨ Quick Start

### Option 1: Let your AI agent deploy it

Any agent that can run shell commands and HTTP requests can set this up. Send it this prompt:

```text
Deploy Star Office UI for me:
1. git clone https://github.com/gulasz101/star-office-ui-fork && cd star-office-ui-fork
2. python3 -m pip install -r backend/requirements.txt   (Python 3.10+)
3. cd backend && python3 app.py    # serves http://127.0.0.1:19000
4. Then push your own status to it: POST /events — see the Agent Integration section of README.
```

Your agent will clone, install, start the backend, and register itself on the board with its very first event.

### Option 2: 30-second manual setup

> **Requires Python 3.10+** (the codebase uses `X | Y` union type syntax, which is not supported on 3.9 or earlier)

```bash
# 1) Clone the repo
git clone https://github.com/gulasz101/star-office-ui-fork.git
cd star-office-ui-fork

# 2) Install dependencies (Python 3.10+ required)
python3 -m pip install -r backend/requirements.txt

# 3) Start the backend — the event database initializes automatically
#    (SQLite by default; set DATABASE_URL=postgresql+psycopg2://... to use Postgres)
cd backend
python3 app.py
```

Open **http://127.0.0.1:19000** and push a status change:

```bash
curl -X POST http://127.0.0.1:19000/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"hermes","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z","payload":{"state":"writing","detail":"Organizing documents"}}'
```

![Star Office UI Preview](docs/screenshots/readme-cover-1.jpg)

---

## 🤔 Who is this for?

### Users with AI agents
This is the **full experience**. Each agent (Hermes, OpenChamber, OpenCode, Pi, Claude, …) posts its own `state_change` events as it works; its pixel character walks to the matching office area in real time. Multiple agents share one board — each shows up under its own name.

### Users without an agent runtime
You can still deploy and use it:
- Push status with a `curl` one-liner or a cron job / CI step (any HTTP client works)
- Use the built-in control bar on the page (it writes through `POST /set_state`)
- Use it as a pixel-art personal status page or remote-work dashboard

---

## 📋 Features

1. **Status Visualization** — 6 states (`idle` / `writing` / `researching` / `executing` / `syncing` / `error`) mapped to different office areas with animated sprites and speech bubbles
2. **Yesterday Memo** — Automatically reads the latest daily log from `memory/*.md`, sanitizes it, and displays it as a "Yesterday Memo" card
3. **Multi-Agent Collaboration** — Invite other agents to join your office via join keys, or let them register themselves through `POST /events`; see everyone's status in real time
4. **Trilingual UI** — Switch between Chinese, English, and Japanese with one click; all UI text, bubbles, and loading messages update instantly
5. **Custom Art Assets** — Manage characters, scenes, and decorations through the sidebar; dynamic frame sync prevents flickering
6. **AI-Powered Room Design** — Connect your own Gemini API to generate new office backgrounds; core features work fine without an API
7. **Mobile-Friendly** — Open on your phone for a quick status check on the go
8. **Security Hardening** — Sidebar password protection, weak-password blocking in production, hardened session cookies
9. **Flexible Public Access** — Use Cloudflare Tunnel for instant public access, or bring your own domain / reverse proxy
10. **Desktop Pet Mode** — Optional Electron desktop wrapper that turns the office into a transparent desktop widget (see below)
11. **Event Database** — every event is stored (SQLite or PostgreSQL) and readable via `GET /events/recent`; the dashboard is a pure view over the event stream

---

## 🚀 Detailed Setup Guide

### 1) Install dependencies

```bash
cd star-office-ui-fork
python3 -m pip install -r backend/requirements.txt
```

### 2) Choose your database (optional)

No state files to copy: the backend stores agent state and events in a database. It falls back to a local SQLite file automatically; to use PostgreSQL instead, export e.g. `DATABASE_URL=postgresql+psycopg2://user:pass@host/star_office` and start normally — migrations run on startup.

### 3) Start the backend

```bash
cd backend
python3 app.py
```

Open `http://127.0.0.1:19000`

> ✅ For local development you can start with the defaults; in production, copy `.env.example` to `.env` and set strong random values for `FLASK_SECRET_KEY` and `ASSET_DRAWER_PASS` to avoid weak passwords and session leaks.
>
> 🏷️ Want a custom office name on the plaque? Start with `OFFICE_NAME="Wojtek's Office"` — the backend never reads any agent's workspace files for it.

### 4) Switch states

Post an event (the agent-facing door, see next section):

```bash
curl -X POST http://127.0.0.1:19000/events -H 'Content-Type: application/json' -d '{
  "version": 1, "agent": "hermes", "event_type": "state_change",
  "timestamp": "2026-10-13T14:20:55Z",
  "payload": {"state": "executing", "detail": "Running the test suite"}
}'
```

Or use the built-in page control bar (writes through `POST /set_state`, the main agent only).

### 5) Public access (optional)

```bash
cloudflared tunnel --url http://127.0.0.1:19000
```

Share the `https://xxx.trycloudflare.com` link with anyone.

### 6) Verify your installation (optional)

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:19000
```

If all checks report `OK`, your deployment is good to go.

---

## 🤖 Agent Integration (any agent, one protocol)

The office has exactly one ingest door: **`POST /events`**. There is no agent-side client to install, no workspace files to share, and no framework to configure — if your agent can send an HTTP POST, it can join.

### The event envelope (schema v1)

Every event is a small JSON envelope (`backend/schemas/event_v1.json`):

| Field | Type | Notes |
|-------|------|-------|
| `version` | int | always `1` |
| `agent` | string | your agent's display name, e.g. `hermes`, `openchamber`, `opencode`, `pi`, `claude`. The **first event from an unknown name auto-registers** that agent on the board. |
| `event_type` | string | e.g. `state_change`, `tool_call`, `file_edit`, `command_run`, `session_start`, `session_end` — not a closed list; unknown types are stored as sent |
| `timestamp` | string | ISO-8601 instant (offsets honoured, normalised to UTC) |
| `payload` | object | type-specific data; for `state_change` use `{"state": "<one of the 6 states>", "detail": "<short text>"}` |

```bash
curl -X POST https://your.office.host/events \
  -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"claude","event_type":"state_change",
       "timestamp":"2026-10-13T14:20:55Z",
       "payload":{"state":"researching","detail":"Reading the codebase"}}'
```

- `201 {"ok": true}` — stored and visible on the board within one poll cycle
- `400 {"ok": false, "error": "<field>: <what to fix>"}` — the message names the offending field, so a hook can self-correct

**6 states → 3 office areas:**

| State | Office Area | When to use |
|-------|-------------|-------------|
| `idle` | 🛋 Breakroom (sofa) | Standing by / task complete |
| `writing` | 💻 Workspace (desk) | Writing code or docs |
| `researching` | 💻 Workspace | Searching / researching |
| `executing` | 💻 Workspace | Running commands / tasks |
| `syncing` | 💻 Workspace | Syncing data / pushing |
| `error` | 🐛 Bug Corner | Error / debugging |

### Wiring common agents

The pattern is always the same — emit one `curl` (or `python3 -c "import requests…"`) when the agent's state changes:

| Agent | Where to hook |
|-------|----------------|
| Hermes | On task start/finish in its action loop: POST `state_change` (`executing` → `idle`) |
| OpenChamber | In a session hook or the Itsaplan bridge runner: `session_start` + `state_change` |
| OpenCode | In `opencode.json` custom commands / plugin events: `tool_call` per tool, `state_change` idle/work |
| Pi | On state transitions in the scheduler driving it |
| Claude (Code) | A `Stop`/`PostToolUse` hook, or a rule in `CLAUDE.md`: "before starting a task POST state_change executing; when finished POST idle" |

Add the rule once, e.g. in whatever instruction file your agent honours:

```markdown
## Star Office status sync
- Before starting a task: POST /events with state "executing" and a one-line detail
- When finishing: POST /events with state "idle"
Only the state word and a short description leave your machine — never file contents.
```

### Read path

`GET /events/recent` returns the newest stored events as a bare JSON array
(`limit` ≤ 200, `agent`, `event_type`, `since` filters — see the API table below).
The dashboard itself polls it; external widgets can poll the same door.

### Invite guest agents (join-key roster door)

For controlled guest access with approval, presence TTL and concurrency caps, keep the classic join ceremony:

**Step 1: Prepare join keys**

When you start the backend for the first time, if there is no `join-keys.json` in the project root, the service creates one from `join-keys.sample.json` (which contains an example key such as `ocj_example_team_01`). Edit the generated `join-keys.json` to add, change, or remove keys; by default each key allows up to 3 concurrent guests.

**Step 2: The guest registers and pushes**

```bash
# join once — returns {"ok":true,"agentId":"42","authStatus":"approved"}
curl -X POST https://your.office.host/join-agent -H 'Content-Type: application/json' \
  -d '{"name":"AliceBot","joinKey":"ocj_starteam02","state":"idle","detail":"just joined"}'

# then push status whenever it changes (every ~15s is a good default)
curl -X POST https://your.office.host/agent-push -H 'Content-Type: application/json' \
  -d '{"agentId":"42","joinKey":"ocj_starteam02","state":"writing","detail":"drafting the report"}'
```

The guest appears on the dashboard and moves between areas with its state; 5 minutes of silence marks it offline, and `POST /leave-agent` removes it.

**Step 3 (optional): give the guest the onboarding doc**

`frontend/join-office-skill.md` is a paste-ready, agent-agnostic instruction sheet — any agent can follow it with plain `curl`.

---

## 📡 API Reference

| Endpoint | Description |
|----------|-------------|
| `POST /events` | **Ingest one agent event (schema v1 envelope above); auto-registers unknown agents** |
| `GET /events/recent` | Read the feed: newest-first array of `{id, agent, event_type, payload, created_at}`; params `limit` (≤200), `agent` (case-insensitive), `event_type`, `since` (ISO) |
| `GET /health` | Health check |
| `GET /status` | Main agent status (`officeName` included when the `OFFICE_NAME` env is set) |
| `POST /set_state` | Set main agent status (built-in page control bar; writes a `state_change` event) |
| `GET /agents` | List all agents (roster with presence/auth) |
| `POST /join-agent` | Guest joins the office with a join key |
| `POST /agent-push` | Guest pushes status (agentId + joinKey) |
| `POST /leave-agent` | Guest leaves |
| `GET /yesterday-memo` | Get yesterday's memo |
| `GET /config/gemini` | Get Gemini API config |
| `POST /config/gemini` | Set Gemini API config |
| `GET /assets/generate-rpg-background/poll` | Poll image generation progress |

---

## 🖥 Desktop Pet Mode (Optional)

The `desktop-pet/` directory contains a **Electron**-based desktop wrapper that turns the pixel office into a transparent desktop widget.

```bash
cd desktop-pet
npm install
npm run dev
```

- Auto-launches the Python backend on startup
- Window points to `http://127.0.0.1:19000/?desktop=1` by default
- Customizable via environment variables for project path and Python path

> ⚠️ This is an **optional, experimental feature**, primarily developed and tested on macOS. See [`desktop-pet/README.md`](./desktop-pet/README.md) for details.
>
> 🙏 The desktop pet module was independently developed by [@Zhaohan-Wang](https://github.com/Zhaohan-Wang) — thank you for this contribution!

---

## 🎨 Art Assets & License

### Asset Attribution

Guest character animations use free assets by **LimeZu**:
- [Animated Mini Characters 2 (Platformer) [FREE]](https://limezu.itch.io/animated-mini-characters-2-platform-free)

Please keep attribution when redistributing or demoing, and follow the original license terms.

### License

- **Code / Logic: MIT** (see [`LICENSE`](./LICENSE))
- **Art Assets: Non-commercial use only** (learning / demo / sharing)

> For commercial use, replace all art assets with your own original artwork (this fork is regenerating the full art pack on Apache-2.0 models — see the project board).

---

## 📝 Changelog

| Date | Summary | Details |
|------|---------|---------|
| 2026-10 | 🌱 **Fork program** — Phases 1–4: event database (SQLite/Postgres), `POST /events` ingest + `GET /events/recent` read path, and full OpenClaw decoupling (`SKILL.md`, `set_state.py`, `office-agent-push.py` removed; agent-agnostic integration documented here) | Issues SOU-18 … SOU-46 on the fork board |
| 2026-03-06 | 🔌 Default port updated — backend default port changed from 18791 to 19000 to avoid conflicts with a local browser-control service; synced scripts, desktop shells, and docs defaults | [`docs/CHANGELOG_2026-03.md`](./docs/CHANGELOG_2026-03.md) |
| 2026-03-05 | 📱 Stability fixes — CDN cache fix, async image generation, mobile sidebar UX, join key expiration & concurrency | [`docs/UPDATE_REPORT_2026-03-05.md`](./docs/UPDATE_REPORT_2026-03-05.md) |
| 2026-03-04 | 🔒 P0/P1 Security hardening — weak password blocking, backend refactor, stale-state auto-idle, skeleton loading | [`docs/UPDATE_REPORT_2026-03-04_P0_P1.md`](./docs/UPDATE_REPORT_2026-03-04_P0_P1.md) |
| 2026-03-03 | 📋 Open-source release checklist completed | [`docs/OPEN_SOURCE_RELEASE_CHECKLIST.md`](./docs/OPEN_SOURCE_RELEASE_CHECKLIST.md) |
| 2026-03-01 | 🎉 **v2 Rebuild** — Trilingual support, asset management system, AI room design, full art asset overhaul | [`docs/FEATURES_NEW_2026-03-01.md`](./docs/FEATURES_NEW_2026-03-01.md) |

---

## 📁 Project Structure

```text
star-office-ui-fork/
├── backend/            # Flask backend
│   ├── app.py          # routes: /events, /events/recent, /status, /agents, ...
│   ├── db.py           # SQLite/Postgres event adapter (fork Phase 1)
│   ├── schemas/event_v1.json  # the POST /events contract
│   ├── migrations/     # Alembic schema
│   ├── requirements.txt
│   └── run.sh
├── frontend/           # Frontend pages & assets
│   ├── index.html
│   ├── join.html
│   ├── invite.html
│   ├── join-office-skill.md   # paste-ready guest instructions (agent-agnostic)
│   └── layout.js
├── desktop-pet/        # Electron desktop wrapper (optional)
├── docs/               # Documentation & screenshots
│   └── screenshots/
├── scripts/            # smoke test, security check, dev helpers
├── state.sample.json     # legacy state template (state now lives in the DB)
├── join-keys.sample.json # Join key template (runtime generates join-keys.json)
└── LICENSE               # MIT License
```

---

## ⭐ Star History

[![Star History Chart](https://api.star-history.com/image?repos=ringhyacinth/Star-Office-UI&type=date&legend=top-left)](https://www.star-history.com/?repos=ringhyacinth%2FStar-Office-UI&type=date&legend=top-left)
