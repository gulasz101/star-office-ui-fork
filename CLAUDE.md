# Star Office UI fork (SOU) — agent instructions

`AGENTS.md` is a symlink to this file. Claude Code, the OpenChamber worker and Szarik
all read the same text: if it is wrong for one of you, fix it here, never in a copy.

## What we are building

An **agent-agnostic, anime pixel-art dashboard** that shows what the owner's AI agents
(Hermes, OpenChamber, OpenCode, Pi, Claude Code, …) are doing right now. Agents `POST`
events to the backend; a Phaser scene turns them into little characters walking around
their offices, sitting at desks, waiting for coffee, building things in the garage.

It is a **fun project**, not a product. Optimise for "does it look and feel alive on
screen", not for ceremony.

### The picture we are aiming at

- **Top-down 3/4 view** (Pokémon / JRPG style), square tiles, characters with
  4-direction walk cycles. No isometric — that route (SOU-122) is closed.
- **Every agent gets its own office.** When a new agent joins, a new office module is
  appended to the building, laid out so the whole thing grows in a visually pleasing way
  (a neighbourhood or office floor that expands, not a stack of identical boxes).
- **Each office reflects its agent**: the desk, the props and the character come from the
  agent's own personality, e.g. a homelab rack, a bass guitar, a boxy 90s van in a garage
  nook, a cat in a box.
- **Activity is visible**: tool calls move the character to a zone (desk = editing,
  rack = shell/ops, garage = long builds and deploys, bookshelf = reading/search,
  coffee = waiting, couch = idle) with a speech bubble saying what it is doing.

### Current milestone — M1 (Phase 11, see the board)

One office, one agent, live: the character moves between zones and works, driven by real
events, and you can tell from the screen what it is doing. M2 (multiple offices growing
next to each other) comes after M1 is accepted by the owner.

## Architecture

| Part | Where | Status |
|---|---|---|
| Backend (Flask) | `backend/app.py`, `backend/db.py`, `backend/event_schema.py` | Keep. Postgres primary, SQLite for local dev. |
| Event ingest | `POST /events`, schema `backend/schemas/event_v1.json` (ADR-035) | Keep. Extra fields allowed. |
| Event feed | `GET /events/recent?agent=&event_type=&since=&limit=` | Keep. Newest first. |
| **v3 renderer** | `frontend/v3/` — Phaser **4.2.1**, served by Flask | **New, all new frontend work goes here.** |
| v1 renderer | `frontend/index.html`, `frontend/game.js`, `layout.js`, `nav.js` | Legacy upstream code. Do not extend. Deleted once v3 reaches parity. |
| Reporters | `reporter/` (OpenChamber/OpenCode), Hermes plugin `gulasz101/hermes-star-office-reporter` | Keep, add more (Claude Code hooks). |
| Art pipeline | FLUX.2-klein-4B on the M1 Max (MPS), pixelize post-process | Keep. Mac-side only. |

Event types in use (non-normative, new ones are allowed without a schema change):
`session_start`, `session_end`, `tool_call`, `file_edit`, `command_run`,
`code_completion`, `state_change`, `itsaplan_issue_transition`.

### v3 conventions

- Phaser 4.2.1, vendored (no CDN at runtime). Plain ES modules, no bundler unless a
  ticket says so.
- Maps are **Tiled JSON (orthogonal)**, authored as data, loaded with Phaser's tilemap
  loader. Positions are tile coordinates; pixel maths stays inside the renderer.
- One office = one `OfficeModule`: its own map chunk, zones, props and character, placed
  at an origin offset in the world. Never hardcode a single office; M2 depends on this.
- Zones are named rectangles or points in the Tiled object layer (`work`, `ops`, `build`,
  `read`, `wait`, `rest`, `entry`). The event → zone/activity mapping is plain data.
- Depth: sort by y (feet position). Pixel art: `pixelArt: true`, integer zoom, no
  smoothing.
- Use the 28 vendored official Phaser skills in `.opencode/skills/` (`tilemaps`, `sprites-and-images`, `animations`, `v4-new-features`, …) (also exposed to Claude
  Code via `.claude/skills`) and the docs MCP when you have it. Phaser 4 is not Phaser 3:
  do not guess APIs from memory.

## Art rules (licence: not negotiable)

- Art comes from **our FLUX pipeline** (FLUX.2-klein-4B, Apache-2.0) on the M1 Max. The
  owner likes the existing generated style (the boxy van, the cat in a box, the CRT desk):
  stay in that look, just in top-down 3/4 perspective.
- Coherence beats detail: one shared palette, one pixel scale and one light direction
  across a whole office. Generate as sets and pixelize to the same palette; never ship one
  prop that clearly comes from a different game.
- Never: Qwen-Image-2.1 (research licence); img2img/inpaint/training on Star-Office or
  LimeZu art; non-commercial weights; paid image APIs (Gemini etc.); anime, game or vehicle
  brand marks and readable text in generated art.
- Generated art lives under `assets/generated/` with a manifest next to each file (model,
  prompt, seed, steps, post-process). Never overwrite shipped files in place.

## How work flows (Itsaplan board, project `SOU`)

The board at `plan.voitech.dev` (MCP `https://plan-api.voitech.dev/mcp`) is the record.
The owner reads it later instead of chat logs. **The board is public and so is this repo:
never put secrets, tokens or keys in a ticket, comment, commit or file.**

Roles:
- **Claude Code** — manager and visual lead. Plans, files tickets, builds the renderer
  core, reviews every delivery by running it and looking at it.
- **OpenChamber worker** (t460, `@openchamber`) — delegated implementation tickets:
  reporters, data modules, tests, backend work, chores.
- **Szarik** (Hermes on the M1 Max, `@szarik`) — Mac-side work: FLUX generation,
  anything that needs the Mac GPU or Hermes.

Rules for everyone:
1. **Work on a ticket or it didn't happen.** Move it to In Progress when you start;
   comment as you go; Done only with evidence.
2. **AC are the contract.** Done needs per-AC evidence: the exact command plus the
   observed result. For anything visual that means a **screenshot of the real page in a
   real browser** (not a headless canvas dump that renders black, not a numbers table).
   "Tests pass" while the screen looks wrong is a failed ticket.
3. **Rulings, not stalls.** If something is ambiguous, decide, then comment
   `Ruling: <what> - <why> - <cost if wrong>`.
4. **Branches:** one branch per ticket, named `sou-<number>-<slug>`, branched from
   `master` unless the ticket names another base. Atomic commits, push the branch, put
   the SHAs on the ticket. Only Claude Code merges to `master`.
5. **Stay in scope.** Follow-ups go to Backlog as new tickets, they don't get folded in.
6. Don't edit files another in-flight ticket owns (check the board first).

## Running it

```bash
# backend + v1 (port 19000, STAR_BACKEND_PORT overrides), SQLite by default
uv venv && uv pip install -r backend/requirements.txt
backend/run.sh            # = .venv/bin/python backend/app.py, loads .env if present
# v3 renderer
open http://127.0.0.1:19000/v3/
# push a test event
curl -s -X POST http://127.0.0.1:19000/events -H 'Content-Type: application/json' \
  -d '{"version":1,"agent":"claude","event_type":"tool_call","timestamp":"2026-10-16T12:00:00Z","payload":{"tool":"Edit","status":"ok"}}'
```

Tests: `.venv/bin/python -m pytest backend/tests` (Python), `node --test "frontend/tests/*.test.js" "frontend/v3/tests/*.test.mjs"` (JS; quote the globs — the bare directory form fails on Node 22/26).

## History, in brief

The fork started from `ringhyacinth/Star-Office-UI` (Flask + Phaser 3, OpenClaw-only).
Phases 0–7 made the backend agent-agnostic (DB layer, `/events`, reporters, OpenClaw
decoupling, Docker, English-only). Phases 8–10 tried to get good visuals by patching the
v1 renderer, then by a Python-generated isometric pipeline. Both were dropped on
2026-10-16 in favour of the v3 top-down renderer. That work stays in git history and on
the `sou-*` branches; the FLUX pipeline and the generated assets carry over.
