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

## Modes (P13-C)

`SOU_MODE` picks the face a process serves — one image, three faces:

| Face | Its whole `app.url_map` | Notes |
|---|---|---|
| `public` | `GET /` (302 to `/v3/`), `GET /health`, `GET /v3`, `GET /v3/`, `GET /v3/<path:filename>`, `GET /public/feed`, `GET /public/agents` | everything else simply does not exist in the process: 404 |
| `ingest` | `GET /health`, `POST /events`, `GET /events/recent`, `GET /events/agents` | refuses to start without `SOU_INGEST_TOKENS` |
| `dev` (default, unset) | today's 42 routes, unchanged | the LAN box |

- An unknown value aborts the boot (`resolve_mode` raises): a typo'd
  `pubic` must never quietly become an open dev face.
- Gating happens at `add_url_rule` (`app.py`'s `FaceFlask`), so a
  disallowed surface is an **enumerable absence**, not a request-time 403 —
  `app.url_map` per face is exactly its allow-list
  (`backend/sou_mode.py` holds the lists; `backend/tests/test_sou_mode.py`
  boots real processes and enumerates them). Even `/static` is absent on
  public: the v1 bundle never crosses the tunnel.
- **Public never writes.** It does not run Alembic, does not seed the main
  agent, and opens the DB with the write guard (`db.init(read_only=True)`):
  SQLite `PRAGMA query_only=ON`, Postgres session
  `default_transaction_read_only=on`. "When the URL allows it" — the
  durable guarantee is a read-only role (the deploy's half, SOU-224); the
  in-app guard holds on any URL. Migrate the DB with a dev/ingest process
  before first public boot; reads fail loudly on an unmigrated DB while
  `/health` stays honest.
- **Public reads** (`GET /public/feed?since=&limit=`,
  `GET /public/agents?since=`): the P13-A projection at the door —
  `{agent, event_type, created_at, payload}` and, for agents, the
  discovery shape `{agent, first_seen, last_seen, count, last_event}` with
  the same projected `last_event`.
  - no `id` field anywhere: paging walks the `created_at` cursor via
    `since` (the inclusive ISO floor), the only cursor that exists;
  - payloads pass through `sanitize.sanitize_payload` **again on read**,
    so rows an older backend stored raw still come back projected;
  - 2xx carries `Cache-Control: public, max-age=5, must-revalidate`
    (the viewer polls; Cloudflare absorbs it; errors stay no-store);
  - CORS: the app sends no `Access-Control-*` header on any face — the
    page is only ever read same-origin under `/v3/*`;
  - only `since` and `limit` exist as knobs: no `agent`/`event_type`
    filter, fewer probes to bang on; the renderer filters client-side.
- v3 switching its reads to `/public/*` when served publicly is SOU-221
  (P13-D); until then the public face simply 404s what v1 used.

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
- One image, two roles (SOU-270). `sou-ingest` runs the entrypoint's default: `SOU_MIGRATE=1`,
  i.e. `alembic upgrade head` before gunicorn, exactly as before. `sou-public` sets
  `SOU_MIGRATE=0`, which skips migrations and says so in its log
  (`[entrypoint] SOU_MIGRATE=0: skipping migrations`) — the public face is read-only, so it must
  never be the process that writes a migration. The image's `HEALTHCHECK` probes `/health`, the
  one path all three faces register (`/events/recent` 404s on public, so the old probe would have
  kept a public container permanently unhealthy).
- Owner decides: the public hostname, whether to put Cloudflare Access in front at first
  (private beta), and whether the M1 Max instance stays as the dev/LAN dashboard.

### Flux image automation

`:latest` cannot be ordered, so `docker-image.yml` publishes a sortable tag beside it: a master
push pushes `:latest` **and** `:master-<sha7>-<epoch>` (e.g. `master-cfec67b-1760668800`, the
epoch taken once per run), and a `v*` tag push pushes `:vX.Y.Z` **and** `:latest`. The image
policy below follows the master tags; the `sou-ingest` / `sou-public` Deployments then reference
the tag the policy selects (an `ImageUpdateAutomation` marker, or `flux get image policy sou`).

```yaml
apiVersion: image.toolkit.fluxcd.io/v1
kind: ImagePolicy
metadata:
  name: sou
  namespace: flux-system
spec:
  imageRepositoryRef:
    name: sou
  filterTags:
    pattern: '^master-[a-f0-9]+-(?P<ts>[0-9]+)$'
    extract: '$ts'
  policy:
    numerical:
      order: asc
```

The release alternative — follow the semver tags instead of the master ones:

```yaml
apiVersion: image.toolkit.fluxcd.io/v1
kind: ImagePolicy
metadata:
  name: sou-release
  namespace: flux-system
spec:
  imageRepositoryRef:
    name: sou
  policy:
    semver:
      range: ">=2.0.0"
```

Both need the image automation controllers in the cluster (`image-reflector-controller` and
`image-automation-controller`, plus an `ImageRepository` for the GHCR repo). homelab-2nd
currently installs source/kustomize/helm/notification only, so that is a step of the SOU-224
deployment, not something this sketch assumes.

## Go-live checklist

- [x] Public mode serves only the allow-listed routes (test enumerates the URL map) — `backend/tests/test_sou_mode.py`, SOU-220.
- [ ] `POST /events` without/with a wrong token → 401; agent mismatch → 403.
- [ ] A canary sweep: events posted with secrets/paths in every field never appear in the DB
      or in `/public/*`.
- [ ] Every reporter sends its token (Claude Code plugin, Hermes plugin, OpenChamber).
- [ ] `/events/recent` and every v1 route return 404 through the tunnel (curl from outside).
- [ ] No secrets in git history of the deployed revision (gitleaks).
