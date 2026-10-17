#!/usr/bin/env python3
"""Star Office UI - the Lodge backend (v3).

What is left after the v1 renderer and its routes were deleted (SOU-226):
- ``GET /v3/*`` serves the Phaser renderer; ``GET /`` redirects there.
- ``POST /events`` is the one ingest door (ADR-035 schema v1, sanitised at the
  door, per-agent tokens).
- ``GET /events/recent`` and ``GET /events/agents`` feed the renderer on the
  dev and ingest faces; ``GET /public/feed`` and ``GET /public/agents`` are the
  stricter projection for the public face.
- ``GET /health``.
Which of these exist in a process is decided by ``SOU_MODE`` (backend/sou_mode.py).
"""

from flask import Flask, jsonify, send_from_directory, request, redirect
from datetime import datetime, timedelta, timezone
import os
import sys
import db
import event_schema
import ingest_auth
import sanitize
import sou_mode

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
ALEMBIC_INI = os.path.join(BACKEND_DIR, "alembic.ini")
FRONTEND_DIR = os.path.join(ROOT_DIR, "frontend")
FRONTEND_V3_DIR = os.path.join(FRONTEND_DIR, "v3")

SOU_MODE = sou_mode.resolve_mode(os.environ.get(sou_mode.MODE_ENV))


class FaceFlask(Flask):
    """Flask app that registers only its own face's routes (SOU-220).

    Rules outside the face's allow-list never reach ``url_map`` — the
    public face enumerates six GET rules, the ingest face the door's
    four, so a probe and a design doc agree, and an unregistered path
    answers 404 because it does not exist in this process (not a 403
    that would confirm it does). The view functions stay in the module;
    they are simply unreachable.
    """

    def add_url_rule(self, rule, endpoint=None, view_func=None, **options):
        if not sou_mode.rule_allowed(SOU_MODE, rule):
            return
        super().add_url_rule(rule, endpoint=endpoint, view_func=view_func, **options)


app = FaceFlask(__name__, static_folder=None)

# Per-agent ingest tokens (SOU-219, docs/public-lodge.md §2): sha256(token) ->
# agent. Configured -> POST /events answers 401/403/429 before touching the
# body; unset -> today's dev/LAN behaviour with exactly one loud warning, so
# old untokened reporters keep working on the private box. SOU_MODE=ingest
# refuses to start without tokens (build_from_env raises; SOU-220 wired the
# face, this line is where it fires). The public face has no /events at all,
# so it neither warns nor needs tokens. Tests reassign app.INGEST_STORE;
# the endpoint reads this global per request.
INGEST_STORE = ingest_auth.build_from_env()
if INGEST_STORE is None and SOU_MODE == sou_mode.MODE_DEV:
    print(
        "WARNING: SOU_INGEST_TOKENS is not set - POST /events accepts unauthenticated writes. "
        "Fine on the dev/LAN box, never on a public or ingest face: create tokens with "
        "`python backend/tokens.py new <agent>` and set SOU_INGEST_TOKENS (or SOU_MODE=ingest "
        "to make this fatal). See docs/public-lodge.md.",
        file=sys.stderr,
    )


# /public/* reads (SOU-220): short enough that the Lodge still feels live
# behind the CDN, long enough that a polling viewer does not stampede the
# DB. Pinned as a literal in tests — operators wanting a different number
# change one line here.
PUBLIC_CACHE_MAX_AGE_SECONDS = 5


@app.after_request
def add_no_cache_headers(response):
    """Apply cache policy by path:
    - /static assets (2xx only): long cache (filenames are versioned with ?v=VERSION_TIMESTAMP)
    - /public/* reads (2xx only): short public cache (SOU-220) — the viewer
      polls, and Cloudflare in front of the public face should absorb that
      without stampeding the Lodge DB. Errors stay no-store.
    - everything else (HTML/API/state): no-cache (always fresh)
    - non-2xx static (e.g. 404): no-cache to prevent CDN from caching errors

    CORS same-origin (SOU-220): the app emits no Access-Control-Allow-*
    header on any face — a public page is only ever read by scripts
    served from the very same origin under /v3/*, never cross-origin.
    """
    path = (request.path or "")
    if path.startswith('/static/') and 200 <= response.status_code < 300:
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        response.headers.pop("Pragma", None)
        response.headers.pop("Expires", None)
    elif path.startswith('/public/') and 200 <= response.status_code < 300:
        response.headers["Cache-Control"] = (
            f"public, max-age={PUBLIC_CACHE_MAX_AGE_SECONDS}, must-revalidate"
        )
        response.headers.pop("Pragma", None)
        response.headers.pop("Expires", None)
    else:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.route("/", methods=["GET"])
def index():
    """The Lodge is the only renderer left (SOU-226): send visitors to it."""
    return redirect("/v3/", code=302)


@app.route("/v3", methods=["GET"])
@app.route("/v3/", methods=["GET"])
@app.route("/v3/<path:filename>", methods=["GET"])
def v3_renderer(filename="index.html"):
    """Serve the v3 top-down renderer (SOU-153). Not under /static, so it is never cached
    as immutable: edits show up on reload without a backend restart."""
    if request.path == "/v3":
        return redirect("/v3/", code=301)
    return send_from_directory(FRONTEND_V3_DIR, filename)


# ---------------------------------------------------------------------------
# Database bootstrap (SOU-19, ADR-034): Alembic owns the schema, the adapter
# (backend/db.py) owns every read and write. Importing the app against a
# fresh database runs `alembic upgrade head` here, so `run.sh` / gunicorn
# never need a manual migration step.
# ---------------------------------------------------------------------------

def _database_is_ready(engine) -> bool:
    """True when the migrated schema is already in place."""
    import sqlalchemy as sa

    names = set(sa.inspect(engine).get_table_names())
    return {"agents", "events", "sessions", "alembic_version"} <= names


def _alembic_upgrade_head(url: str) -> None:
    """Run `alembic upgrade head` through the Python API against `url`."""
    from alembic import command
    from alembic.config import Config

    config = Config(ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


def init_database():
    """Migrate the schema when missing. Idempotent: every worker import may call it.

    (Until SOU-226 this also seeded the v1 main agent "Star"; v3 has no
    privileged agent, so nothing is seeded any more.)
    """
    os.environ.setdefault("ALEMBIC_SKIP_FILECONFIG", "1")
    engine = db.get_engine()
    if not _database_is_ready(engine):
        _alembic_upgrade_head(db.resolve_database_url())

    return engine


if SOU_MODE == sou_mode.MODE_PUBLIC:
    # The public face never writes to the database (SOU-220 AC5): no
    # migration run, just the shared DB opened with the
    # read-only guard from db.py. An unmigrated DB is an operator error —
    # the reads then fail loudly while /health keeps telling the truth.
    # dev and ingest migrate if needed.
    db.init(read_only=True)
else:
    init_database()


# ---------------------------------------------------------------------------
# Event ingest (SOU-36, ADR-035): the one door agents walk through.
#
# POST /events takes a versioned envelope, checks it against
# backend/schemas/event_v1.json, and appends it to the feed through the Phase 1
# adapter. The `agent` field is the registry key: an unknown agent is
# registered as a guest row on its first event, so a new agent needs no
# join ceremony to start reporting activity.
# ---------------------------------------------------------------------------

INGEST_AGENT_TYPE = "guest"


def find_agent_by_name(session, name: str):
    """The registry row whose name matches, ignoring case and padding.

    Migration 001 puts no unique index on ``agents.name``, so reuse is a
    lookup contract, not a database one: the first spelling registered is the
    canonical one and later events must find it, not create a twin.
    """
    wanted = (name or "").strip().lower()
    for row in db.list_agents(session):
        if (row.name or "").strip().lower() == wanted:
            return row
    return None


def ingest_event(data, dedupe: bool = False, safe_projection: bool = True):
    """Validate one ADR-035 envelope and persist it; returns the event row.

    ``safe_projection`` (SOU-218): the reporter door stores only
    ``sanitize.sanitize_payload(event_type, payload)`` — the ticket's rules,
    ported from the Claude Code reporter. Reporters can be old or buggy, so
    the server is the gate and a DB dump leaks nothing. Validation runs
    *first* so every existing 400 contract keeps its exact message.
    ``dedupe`` skips the write when the newest event of that type for that
    agent already carries an identical payload — the no-op-on-refresh rule the
    SOU-19 state paths grew, now shared by every door. Raises
    ``event_schema.EventValidationError`` for anything the agent must be told
    about; the caller turns that into a 400.
    """
    envelope = event_schema.validate_event(data)
    payload = (
        sanitize.sanitize_payload(envelope.event_type, envelope.payload)
        if safe_projection
        else envelope.payload
    )
    with db.session_scope() as session:
        agent = find_agent_by_name(session, envelope.agent)
        if agent is None:
            agent = db.insert_agent(session, name=envelope.agent, agent_type=INGEST_AGENT_TYPE)
        if dedupe:
            latest = db.list_events(
                session, agent_id=agent.id, event_type=envelope.event_type, limit=1
            )
            if latest and latest[0].payload == payload:
                return latest[0]
        return db.insert_event(
            session,
            agent.id,
            envelope.event_type,
            payload,
            created_at=envelope.created_at,
        )


@app.route("/health", methods=["GET"])
def health():
    """Health check"""
    return jsonify({
        "status": "ok",
        "service": "star-office-ui",
        "timestamp": datetime.now().isoformat(),
    })


@app.route("/events", methods=["POST"])
def events_endpoint():
    """Ingest one agent event (POST /events, ADR-035 event schema v1).

    Agent-agnostic by design: any agent posts its own envelope and the office
    works out who it is from the ``agent`` field. ``{"ok": true}`` with 201 on
    success, ``{"ok": false, "error": "<what to fix>"}`` with 400 when the body
    is not a valid v1 event — the message names the offending field, because
    the agent reading it is a machine running a hook, not a person at a form.

    With ``SOU_INGEST_TOKENS`` configured (SOU-219) the door also has a lock,
    checked before the body: ``Authorization: Bearer <token>`` missing or
    unknown -> 401; the token's rate bucket empty -> 429 with ``Retry-After``;
    the token belongs to a different agent than the envelope claims -> 403 and
    nothing is written. A token can only ever write its own agent's room.
    """
    store = INGEST_STORE
    token_agent = None
    if store is not None:
        token = ingest_auth.bearer_from_header(request.headers.get("Authorization"))
        token_agent = store.lookup(token) if token is not None else None
        if token_agent is None:
            return (
                jsonify({"ok": False, "error": "authorization: a valid Bearer ingest token is required"}),
                401,
            )
        if not store.allow_request(token):
            response = jsonify(
                {"ok": False, "error": f"rate limit: this token sends more than {store.rate:g} events/s"}
            )
            response.headers["Retry-After"] = "1"
            return response, 429
    if not request.is_json:
        return (
            jsonify({"ok": False, "error": "event: expected Content-Type application/json"}),
            400,
        )
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"ok": False, "error": "event: request body is not valid JSON"}), 400
    if store is not None and isinstance(data, dict):
        # The token decides identity, exactly: "Hermes" is not "hermes". The
        # claim is checked before schema validation, so a wrong-agent envelope
        # is a 403 even if it would also fail validation for another reason.
        claimed = data.get("agent")
        if isinstance(claimed, str) and claimed != token_agent:
            return (
                jsonify({
                    "ok": False,
                    "error": f"agent: this token writes the room of {token_agent!r}, not {claimed!r}",
                }),
                403,
            )
    try:
        ingest_event(data)
    except event_schema.EventValidationError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except Exception as exc:  # pragma: no cover - adapter failure, not agent error
        return jsonify({"ok": False, "error": f"ingest failed: {exc}"}), 500
    return jsonify({"ok": True}), 201


EVENTS_RECENT_DEFAULT_LIMIT = 50
EVENTS_RECENT_MAX_LIMIT = 200

# GET /events/agents window when no `since` is given; matches ACTIVE_WINDOW_MS
# in frontend/v3/src/feed.js, so the endpoint's reach equals the renderer's.
EVENTS_AGENTS_DEFAULT_WINDOW_HOURS = 24


def _iso_instant(value) -> str:
    """Stored naive UTC (ADR-035) spelled back as a Z-suffixed ISO instant."""
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_feed_limit(raw_limit):
    """``limit`` for the feed-shaped reads: default 50, capped 200.

    Returns ``(limit, error)``; ``error`` is a ``(body, status)`` tuple to
    return straight away, or None. The 400 body is the one
    GET /events/recent has always written (SOU-45 contract) — every read
    door answers the same way, so a reporter or the renderer can treat
    them as one family.
    """
    if raw_limit in (None, ""):
        return EVENTS_RECENT_DEFAULT_LIMIT, None
    try:
        parsed = int(raw_limit)
    except ValueError:
        parsed = -1
    if parsed < 1:
        return None, (jsonify({
            "ok": False,
            "error": f"limit: must be a positive integer, got {raw_limit!r}",
        }), 400)
    return min(parsed, EVENTS_RECENT_MAX_LIMIT), None


def _parse_feed_since(raw_since):
    """Inclusive ISO 8601 floor for the feed-shaped reads, normalised
    through the same ``parse_timestamp`` as ingest (SOU-45). Returns
    ``(since, error)`` with the same shared 400 body."""
    if raw_since in (None, ""):
        return None, None
    try:
        return event_schema.parse_timestamp(raw_since), None
    except event_schema.EventValidationError as exc:
        return None, (jsonify({
            "ok": False,
            "error": str(exc).replace("timestamp:", "since", 1),
        }), 400)


def feed_item(row, name):
    """One Event row in the GET /events/recent item shape (the SOU-45
    contract): ``{id, agent, event_type, payload, created_at}``.

    Both read endpoints serialise through here, so GET /events/agents can
    promise "exactly the shape /events/recent returns" by construction
    instead of by copy-paste (SOU-201).
    """
    return {
        "id": row.id,
        "agent": name,
        "event_type": row.event_type,
        "payload": row.payload,
        "created_at": _iso_instant(row.created_at),
    }


@app.route("/events/recent", methods=["GET"])
def events_recent_endpoint():
    """Read the feed back: the newest agent events (GET /events/recent, SOU-45).

    Phase 2 opened the door (POST /events); this is the matching read path —
    a bare JSON array, newest first, of ``{id, agent, event_type, payload,
    created_at}``. ``limit`` defaults to 50 and is capped at 200; ``agent``
    is a case-insensitive registry name (unknown agent -> empty feed, this
    is a read endpoint, not a lookup oracle); ``event_type`` is exact;
    ``since`` is an inclusive ISO 8601 floor, normalised through the same
    ``parse_timestamp`` as ingest so the two ends of the stream agree.
    """
    limit, error = _parse_feed_limit(request.args.get("limit"))
    if error is not None:
        return error
    since, error = _parse_feed_since(request.args.get("since"))
    if error is not None:
        return error

    event_type = request.args.get("event_type") or None
    agent_name = request.args.get("agent") or None

    with db.session_scope() as session:
        agent_id = None
        if agent_name is not None:
            agent = find_agent_by_name(session, agent_name)
            if agent is None:
                return jsonify([])
            agent_id = agent.id
        rows = db.list_events(
            session, agent_id=agent_id, event_type=event_type, limit=limit, since=since
        )
        names = {a.id: a.name for a in db.list_agents(session)}
        feed = [feed_item(row, names.get(row.agent_id)) for row in rows]
    return jsonify(feed)


@app.route("/events/agents", methods=["GET"])
def events_agents_endpoint():
    """Agents seen since a floor, each with its newest event (SOU-201).

    The v3 Lodge gives every agent active in the last 24 h a room on page
    load, but ``GET /events/recent?limit=200`` can be monopolised by one busy
    agent, so quieter agents never surface for discovery. This is the
    discovery-shaped read: one entry per agent with at least one event at or
    after ``since`` (ISO 8601, normalised through the same
    ``parse_timestamp`` as ``/events/recent``; default now − 24 h), newest
    activity first, as a bare JSON array of ``{agent, first_seen, last_seen,
    count, last_event}``. ``last_event`` is that agent's newest row in
    exactly the feed item shape (``feed_item`` above), so
    ``FeedState.ingest(rows.map(r => r.last_event), {initial: true})``
    consumes it as is. The window stats — ``first_seen``, ``last_seen``,
    ``count`` — cover the window only: this answers "who has been seen since
    T", not lifetime totals. Nothing is excluded server-side; the ignore
    list (``Star``) belongs to the renderer, not the feed. Bad ``since`` is
    a 400 with the same body ``/events/recent`` writes.
    """
    since, error = _parse_feed_since(request.args.get("since"))
    if error is not None:
        return error
    if since is None:
        # naive UTC, the storage convention db.agent_activity compares against
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=EVENTS_AGENTS_DEFAULT_WINDOW_HOURS
        )

    with db.session_scope() as session:
        names = {a.id: a.name for a in db.list_agents(session)}
        entries = []
        for agent_id, first_seen, last_seen, count, newest_row in db.agent_activity(
            session, since
        ):
            name = names.get(agent_id)
            if name is None:  # pragma: no cover - FK cascade makes this unreachable
                continue
            entries.append({
                "agent": name,
                "first_seen": _iso_instant(first_seen),
                "last_seen": _iso_instant(last_seen),
                "count": count,
                "last_event": feed_item(newest_row, name),
            })
    return jsonify(entries)


# The public face hides a little more than the ingest projection (SOU-221, docs §4): no
# project names (``cwd``), task ids only as a per-process salted digest (enough for the
# renderer to pair a background start with its end, useless to anyone else), and none of
# the v1 roster rows (``Star``), which the renderer ignores anyway.
PUBLIC_HIDDEN_KEYS = frozenset({"cwd"})
PUBLIC_HIDDEN_AGENTS = frozenset({"Star"})
_PUBLIC_TASK_SALT = __import__("secrets").token_bytes(16)


def _public_payload(event_type, payload):
    out = sanitize.sanitize_payload(event_type, payload)
    for key in PUBLIC_HIDDEN_KEYS:
        out.pop(key, None)
    task = out.get("task_id")
    if isinstance(task, str) and task:
        out["task_id"] = __import__("hashlib").sha256(_PUBLIC_TASK_SALT + task.encode()).hexdigest()[:12]
    return out


def public_feed_item(row, name):
    """One Event row in the **public projection shape** (SOU-220,
    docs/public-lodge.md §4): the SOU-45 feed item minus its ``id``, with
    the payload run back through the P13-A sanitiser at this door.

    Two deliberate differences from ``feed_item``:

    * no ``id`` — the public face hands out nothing enumerable. Paging
      walks the ``created_at`` timeline through ``since`` (the inclusive
      floor), which is the only cursor by design: "no id-guessing beyond
      paging".
    * re-sanitised payload — rows written by a pre-SOU-218 backend (or
      poked straight into the DB) get projected here too. The ingest door
      is the promise, this read is the guarantee behind it.
    """
    return {
        "agent": name,
        "event_type": row.event_type,
        "payload": _public_payload(row.event_type, row.payload),
        "created_at": _iso_instant(row.created_at),
    }


@app.route("/public/feed", methods=["GET"])
def public_feed_endpoint():
    """The viewer's feed: sanitised projection, newest first (SOU-220).

    GET /public/feed?since=&limit= — two knobs, that is the whole surface.
    ``limit`` defaults 50, capped 200 (the shared feed-door numbers);
    ``since`` is the inclusive ISO floor parsed by the same
    ``parse_timestamp`` as ingest; both answer 400 with the family's one
    body. No ``agent``/``event_type`` filter: fewer probes to bang on,
    and the v3 renderer already filters (its ignore list is a renderer
    concern, SOU-201). 2xx gets ``Cache-Control: public, max-age=5`` from
    the path policy; CORS headers exist on no face at all — the page is
    read same-origin from under /v3/*. Registered only on the public
    face: on dev and ingest this rule is not in ``url_map``.
    """
    limit, error = _parse_feed_limit(request.args.get("limit"))
    if error is not None:
        return error
    since, error = _parse_feed_since(request.args.get("since"))
    if error is not None:
        return error

    with db.session_scope() as session:
        rows = db.list_events(session, limit=limit, since=since)
        names = {a.id: a.name for a in db.list_agents(session)}
        feed = [public_feed_item(row, names.get(row.agent_id)) for row in rows
                if names.get(row.agent_id) not in PUBLIC_HIDDEN_AGENTS]
    return jsonify(feed)


@app.route("/public/agents", methods=["GET"])
def public_agents_endpoint():
    """The viewer's discovery read: agents seen since a floor, projected.

    The public-face twin of GET /events/agents (SOU-201): one entry per
    agent with activity at or after ``since`` (default now − 24 h),
    newest first, ``{agent, first_seen, last_seen, count, last_event}`` —
    and ``last_event`` is a ``public_feed_item``, not a ``feed_item``:
    no id, payload projected here at the door. Same cache and CORS
    contract as /public/feed.
    """
    since, error = _parse_feed_since(request.args.get("since"))
    if error is not None:
        return error
    if since is None:
        # naive UTC, the storage convention db.agent_activity compares against
        since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=EVENTS_AGENTS_DEFAULT_WINDOW_HOURS
        )

    with db.session_scope() as session:
        names = {a.id: a.name for a in db.list_agents(session)}
        entries = []
        for agent_id, first_seen, last_seen, count, newest_row in db.agent_activity(
            session, since
        ):
            name = names.get(agent_id)
            if name is None or name in PUBLIC_HIDDEN_AGENTS:
                continue
            entries.append({
                "agent": name,
                "first_seen": _iso_instant(first_seen),
                "last_seen": _iso_instant(last_seen),
                "count": count,
                "last_event": public_feed_item(newest_row, name),
            })
    return jsonify(entries)


if __name__ == "__main__":
    raw_port = os.environ.get("STAR_BACKEND_PORT", "19000")
    try:
        backend_port = int(raw_port)
    except ValueError:
        backend_port = 19000
    if backend_port <= 0:
        backend_port = 19000

    print("=" * 50)
    print("Star Office UI - the Lodge backend")
    print("=" * 50)
    print(f"Database: {db.get_engine().url.render_as_string(hide_password=True)}")
    print(f"Face: {SOU_MODE} (SOU_MODE; see docs/public-lodge.md)")
    print(f"Listening on: http://0.0.0.0:{backend_port}  (STAR_BACKEND_PORT overrides)")
    print("=" * 50)

    app.run(host="0.0.0.0", port=backend_port, debug=False)
