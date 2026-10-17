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
* ``dev`` — the default and unset value: today's whole app, the exact
  42-rule master@9ea4163 surface in :data:`DEV_ROUTES`, unchanged.

The allow-lists are rule strings from ``werkzeug.routing.Rule.rule`` (the
shape ``app.url_map`` exposes), not endpoint names: one view function can
own several rules (``/v3``, ``/v3/``, ``/v3/<path:filename>`` share one),
and the gate must speak the same language as the url_map enumeration in
``backend/tests/test_sou_mode.py``. Two endpoints on one rule (GET and
POST ``/assets/positions``) are allowed or absent together — that only
ever happens on dev, which allows the whole snapshot.

Pure module: no Flask, no I/O, no environment reads at import. The caller
passes the raw env value; resolving is total and loud — an unknown
``SOU_MODE`` raises instead of guessing, because guessing wrong in the
"relaxed" direction would expose the dev face to the internet through a
typo. ``dev`` is the default for unset/empty, which keeps every existing
deployment byte-for-byte on today's behaviour.
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
    {"rule": "/", "endpoint": "index", "methods": ["GET", "HEAD"]},
    {"rule": "/agent-approve", "endpoint": "agent_approve", "methods": ["POST"]},
    {"rule": "/agent-push", "endpoint": "agent_push", "methods": ["POST"]},
    {"rule": "/agent-reject", "endpoint": "agent_reject", "methods": ["POST"]},
    {"rule": "/agents", "endpoint": "get_agents", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/auth", "endpoint": "assets_auth", "methods": ["POST"]},
    {"rule": "/assets/auth/status", "endpoint": "assets_auth_status", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/defaults", "endpoint": "assets_defaults_get", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/defaults", "endpoint": "assets_defaults_set", "methods": ["POST"]},
    {"rule": "/assets/generate-rpg-background", "endpoint": "assets_generate_rpg_background", "methods": ["POST"]},
    {"rule": "/assets/generate-rpg-background/poll", "endpoint": "assets_generate_rpg_background_poll", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/home-favorites/apply", "endpoint": "assets_home_favorites_apply", "methods": ["POST"]},
    {"rule": "/assets/home-favorites/delete", "endpoint": "assets_home_favorites_delete", "methods": ["POST"]},
    {"rule": "/assets/home-favorites/file/<path:filename>", "endpoint": "assets_home_favorites_file", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/home-favorites/list", "endpoint": "assets_home_favorites_list", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/home-favorites/save-current", "endpoint": "assets_home_favorites_save_current", "methods": ["POST"]},
    {"rule": "/assets/list", "endpoint": "assets_list", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/positions", "endpoint": "assets_positions_get", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/positions", "endpoint": "assets_positions_set", "methods": ["POST"]},
    {"rule": "/assets/restore-default", "endpoint": "assets_restore_default", "methods": ["POST"]},
    {"rule": "/assets/restore-last-generated-background", "endpoint": "assets_restore_last_generated_background", "methods": ["POST"]},
    {"rule": "/assets/restore-prev", "endpoint": "assets_restore_prev", "methods": ["POST"]},
    {"rule": "/assets/restore-reference-background", "endpoint": "assets_restore_reference_background", "methods": ["POST"]},
    {"rule": "/assets/template.zip", "endpoint": "assets_template_download", "methods": ["GET", "HEAD"]},
    {"rule": "/assets/upload", "endpoint": "assets_upload", "methods": ["POST"]},
    {"rule": "/config/gemini", "endpoint": "gemini_config_get", "methods": ["GET", "HEAD"]},
    {"rule": "/config/gemini", "endpoint": "gemini_config_set", "methods": ["POST"]},
    {"rule": "/electron-standalone", "endpoint": "electron_standalone_page", "methods": ["GET", "HEAD"]},
    {"rule": "/events", "endpoint": "events_endpoint", "methods": ["POST"]},
    {"rule": "/events/agents", "endpoint": "events_agents_endpoint", "methods": ["GET", "HEAD"]},
    {"rule": "/events/recent", "endpoint": "events_recent_endpoint", "methods": ["GET", "HEAD"]},
    {"rule": "/health", "endpoint": "health", "methods": ["GET", "HEAD"]},
    {"rule": "/invite", "endpoint": "invite_page", "methods": ["GET", "HEAD"]},
    {"rule": "/join", "endpoint": "join_page", "methods": ["GET", "HEAD"]},
    {"rule": "/join-agent", "endpoint": "join_agent", "methods": ["POST"]},
    {"rule": "/leave-agent", "endpoint": "leave_agent", "methods": ["POST"]},
    {"rule": "/set_state", "endpoint": "set_state_endpoint", "methods": ["POST"]},
    {"rule": "/static/<path:filename>", "endpoint": "static", "methods": ["GET", "HEAD"]},
    {"rule": "/status", "endpoint": "get_status", "methods": ["GET", "HEAD"]},
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
