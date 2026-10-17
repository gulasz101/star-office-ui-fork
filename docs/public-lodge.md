# The Lodge goes public: design (Phase 13)

Status: proposal, 2026-10-16. Owner question: "deploy this dashboard publicly, but the endpoints
we write data from agents must be protected… or poll Prometheus? …make sure we are not
exposing anything sensitive."

## Today's exposure (audit, master @ b4d40f8)

- `backend/app.py` serves **40 routes**; v3 needs 5 of them (`/v3/*`, `/events/recent`,
  `/events/agents`, `POST /events`, `/health`).
- The other 35 are the legacy v1 surface:
  - writes: `/set_state`, `/agent-push`, `/join-agent`, `/agent-approve`, `/assets/upload`,
    `/assets/positions`, `/config/gemini`…
  - process spawning: `os.system` / `subprocess` around image tooling
  - a drawer password defaulting to `1234` (SOU-3, SOU-4)
- `POST /events` has **no authentication**. Anyone who can reach it can write any agent name.
- `/events/recent` returns **raw payloads**. The Claude Code reporter sends program names and
  basenames only. The Hermes reporter sends full command lines and home paths (SOU-192),
  plus `cwd` basenames (project names) and session ids.

The LAN hides all of this today. None of it may face the internet.

## Decision: public viewer, private ingest, sanitise at the door

```
 agents (laptop, Mac, t460)                        internet
   │  POST /events + per-agent token                  │  GET only
   ▼  (LAN / Netbird, never routed publicly)          ▼
 ┌────────── ingest face ─────────┐        ┌─────── public face ────────┐
 │ token → agent check            │        │ Cloudflare Tunnel           │
 │ server-side sanitiser          │──DB──▶ │ /v3/*, /public/feed,        │
 │ (stores the safe projection    │        │ /public/agents, /health     │
 │  only, never the raw payload)  │        │ everything else: 404        │
 └────────────────────────────────┘        └─────────────────────────────┘
```

1. **Two faces, one image.** Pick the face with `SOU_MODE=ingest|public|dev`.
   - **Public mode** registers only `GET /v3/*`, `GET /public/*` and `GET /health`. Every v1
     route, every write, and `/events/recent` don't exist in that process.
   - Defence in depth: the tunnel/ingress also routes only those paths.
   - The ingest face is reachable only on LAN/Netbird (internal hostname), never through the
     tunnel.
2. **Per-agent ingest tokens.** `SOU_INGEST_TOKENS` holds `sha256(token) → agent name`.
   - A token may only write events for its own agent, so no spoofing someone else's room.
   - Requests go `Authorization: Bearer <token>` (stdlib in every reporter).
   - Constant-time compare, 401 on mismatch, rate limit per token.
   - Tokens live in SOPS like every other homelab secret, never in the repo.
3. **Sanitise at the door, not in the browser.** The backend applies the reporter privacy
   rules itself, and stores only the result:
   - `command`: program + one allow-listed subcommand
   - file/path/cwd fields: basename only, or nothing
   - dropped: free text, outputs, errors and session ids
   - Reporters can be old or buggy; the server is the gate. A DB dump then leaks nothing,
     because the raw payload never lands.
4. **Public read projection.** `/public/feed` and `/public/agents` return exactly what the
   renderer needs: `agent, event_type, created_at` plus the sanitised label fields.
   - `/events/recent` stays on the ingest face for debugging.
   - v3 picks the public endpoints when the page is served in public mode.
5. **v1 goes away.** Public mode never loads it. Deleting it (SOU-3/SOU-4 close with it) is the
   cleanup once v3 has parity, which it effectively does now.

## Tokens (P13-B)

`POST /events` learns who may write from `SOU_INGEST_TOKENS`: a mapping of
`sha256(token) → agent name`. The server only ever stores hashes — a leaked line,
env dump or JSON file is not a leaked token. Two configuration shapes:

```bash
# inline pairs, comma / semicolon / newline separated
SOU_INGEST_TOKENS="<sha256hex>:hermes,<sha256hex>:claude"

# or a path to a JSON file (SOPS-managed, like every other homelab secret)
SOU_INGEST_TOKENS=/run/secrets/sou-ingest-tokens.json
# {"<sha256hex>": "hermes", "<sha256hex>": "claude"}
```

Mint a token for one agent — it prints the plaintext **once** and the line to add:

```bash
uv run python backend/tokens.py new hermes
```

Hand the plaintext to that agent's reporter (SOPS, never git, never a ticket);
paste the `sha256hex:agent` line into the mapping above.

Request contract on `POST /events`, checked before the body (SOU-219):

| Situation | Answer |
|---|---|
| No `Authorization` header, or not `Bearer`, or unknown token | `401` |
| Token's agent ≠ envelope `agent` (case-exact) | `403`, nothing written |
| Token exceeds its rate limit (`SOU_INGEST_RATE_LIMIT`, default 20 req/s burst per token) | `429` + `Retry-After: 1` |
| Everything fine | the existing ADR-035 contract: `201` / `400` |

Token lookup is constant-time (`hmac.compare_digest` over every entry).

**Unset `SOU_INGEST_TOKENS`** = today's dev/LAN behaviour: untokened reporters keep
working and the backend prints a startup warning. That is only acceptable on the
private box. **`SOU_MODE=ingest` (P13-C) makes tokens mandatory** — the backend
refuses to start with no tokens on the ingest face.

Tests: `backend/tests/test_ingest_tokens.py` (store, 401/403/429/ok, CLI round-trip).
Reporter token support arrives in P13-D/E/F; until then run the reporters on LAN
against an untokened dev instance, or set their tokens once implemented.

## Why not Prometheus as the source

- **It's the wrong shape.** Agent activity is a stream of discrete, labelled events (a tool
  call, its label, a session boundary). Prometheus stores numeric samples on a 15–30 s scrape.
  Bubbles, ordering and session starts would be lost, and per-tool labels are the
  high-cardinality anti-pattern.
- **Pull doesn't fit the agents.** Every agent would have to expose an HTTP endpoint and be
  scrapeable. A laptop behind NAT can't be.
- **Where it does fit is ambience,** both ways:
  - Read: pull coarse homelab metrics (node load, GPU utilisation, pod restarts) and let them
    drive the scenery. Andrzej's rack LEDs blink with the real cluster load; the token den
    hums when the M1 GPU is busy. Only bucketed values (low/medium/high) reach the public page,
    never hostnames.
  - Write: export `/metrics` (events per agent per minute, active sessions) for Grafana.
  - Optional, later (Backlog).

## Deployment sketch (homelab, Andrzej's lane)

- k3s via Flux (`homelab-gitops`). The image already builds with uv (SOU-170). Postgres primary.
- Two Deployments from the same image:
  - `sou-ingest`: ClusterIP; exposed on the Netbird/LAN hostname only.
  - `sou-public`: exposed through the existing Cloudflare Tunnel on a public hostname.
- Both use the same DB; `sou-public` has a read-only DB role.
- Owner decides: the public hostname, whether to put Cloudflare Access in front at first
  (private beta), and whether the M1 Max instance stays as the dev/LAN dashboard.

## Go-live checklist

- [ ] Public mode serves only the allow-listed routes (test enumerates the URL map).
- [ ] `POST /events` without/with a wrong token → 401; agent mismatch → 403.
- [ ] A canary sweep: events posted with secrets/paths in every field never appear in the DB
      or in `/public/*`.
- [ ] Every reporter sends its token (Claude Code plugin, Hermes plugin, OpenChamber).
- [ ] `/events/recent` and every v1 route return 404 through the tunnel (curl from outside).
- [ ] No secrets in git history of the deployed revision (gitleaks).
