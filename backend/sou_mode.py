"""The three faces of one image (SOU-220, Phase 13 P13-C).

``SOU_MODE`` picks the face a process serves:

* ``public`` — the internet-facing Lodge viewer. Only the GET surface in
  :data:`PUBLIC_RULES` exists: health, the v3 renderer and the two public
  reads. Everything else (v1, every write, ``/events/recent``, even
  ``/static``) is never registered — the route does not exist in this
  process, so it answers 404, not "403, please don't".
* ``ingest`` — the private agent door on LAN/Netbird: ``POST /events``
  plus the two debug reads and health (:data:`INGEST_RULES`). No v1, no
  frontend. Tokens are mandatory on this face (app.py refuses to boot
  without ``SOU_INGEST_TOKENS``).
* ``dev`` — the default and unset value: the v3 surface in
  :data:`DEV_ROUTES` (``/`` redirects to ``/v3/``, the ingest door and its two
  reads). Until SOU-226 this was the 42-rule master@9ea4163 snapshot with v1.

The allow-lists are rule strings from ``werkzeug.routing.Rule.rule`` (the
shape ``app.url_map`` exposes), not endpoint names: one view function can
own several rules (``/v3``, ``/v3/``, ``/v3/<path:filename>`` share one),
and the gate must speak the same language as the url_map enumeration in
``backend/tests/test_sou_mode.py``. Two endpoints on one rule (GET and
POST on one rule) are allowed or absent together.

Pure module: no Flask, no I/O, no environment reads at import. The caller
passes the raw env value; resolving is total and loud — an unknown
``SOU_MODE`` raises instead of guessing, because guessing wrong in the
"relaxed" direction would expose the dev face to the internet through a
typo. ``dev`` is the default for unset/empty.
"""

from __future__ import annotations

MODE_DEV = "dev"
MODE_PUBLIC = "public"
MODE_INGEST = "ingest"
MODES = (MODE_DEV, MODE_PUBLIC, MODE_INGEST)

MODE_ENV = "SOU_MODE"


def resolve_mode(raw) -> str:
    """Which face this process serves. Unset/empty is dev; unknown raises.

    ``ValueError`` on anything not in :data:`MODES` (case- and
    whitespace-insensitive) — fail fast at boot, never silently dev.
    """
    name = (raw or "").strip().lower()
    if name == "":
        return MODE_DEV
    if name in MODES:
        return name
    raise ValueError(
        f"{MODE_ENV}={raw!r} is not a known mode; expected one of: "
        + ", ".join(MODES)
        + " (unset means dev)"
    )


# The public face: GET only, and nothing a reporter or the v1 editor uses.
PUBLIC_RULES = frozenset({
    "/health",
    "/v3",
    "/v3/",
    "/v3/<path:filename>",
    "/public/feed",
    "/public/agents",
})

# The ingest face: the agents' door plus its debug reads and the probe.
# POST /events itself answers 401/403/429 per SOU-219; on this face the
# token store must exist at all (app.py enforces).
INGEST_RULES = frozenset({
    "/health",
    "/events",
    "/events/recent",
    "/events/agents",
})

# dev == master@9ea4163, verbatim. Each entry is one rule+endpoint+methods
# triple of the Flask url_map (methods excluding the automatic OPTIONS).
# Adding a route to app.py without touching this list means dev stops
# matching "today's app" — the url_map test then fails, on purpose.
DEV_ROUTES = (
    # SOU-226 deleted v1: dev is the whole v3 surface, the ingest door and its reads.
    {"rule": "/", "endpoint": "index", "methods": ["GET", "HEAD"]},
    {"rule": "/events", "endpoint": "events_endpoint", "methods": ["POST"]},
    {"rule": "/events/agents", "endpoint": "events_agents_endpoint", "methods": ["GET", "HEAD"]},
    {"rule": "/events/recent", "endpoint": "events_recent_endpoint", "methods": ["GET", "HEAD"]},
    {"rule": "/health", "endpoint": "health", "methods": ["GET", "HEAD"]},
    {"rule": "/v3", "endpoint": "v3_renderer", "methods": ["GET", "HEAD"]},
    {"rule": "/v3/", "endpoint": "v3_renderer", "methods": ["GET", "HEAD"]},
    {"rule": "/v3/<path:filename>", "endpoint": "v3_renderer", "methods": ["GET", "HEAD"]},
)

DEV_RULES = frozenset(entry["rule"] for entry in DEV_ROUTES)

_ALLOW_LISTS = {
    MODE_DEV: DEV_RULES,
    MODE_PUBLIC: PUBLIC_RULES,
    MODE_INGEST: INGEST_RULES,
}


def rules_for(mode: str) -> frozenset:
    """The route allow-list a face registers. Unknown mode raises."""
    try:
        return _ALLOW_LISTS[mode]
    except KeyError:
        raise ValueError(f"unknown SOU_MODE {mode!r}; expected one of: " + ", ".join(MODES))


def rule_allowed(mode: str, rule: str) -> bool:
    """True when a face may register this url_map rule."""
    return rule in rules_for(mode)
