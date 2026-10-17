#!/usr/bin/env python3
"""Per-agent ingest tokens (SOU-219, Phase 13 P13-B).

``POST /events`` is the door every agent walks through. ``SOU_INGEST_TOKENS``
binds each token to exactly one agent so nobody can spoof someone else's
room (docs/public-lodge.md §2). The store maps ``sha256(token) -> agent
name``: **hashes only, never plaintext**, so environment dumps, logs and the
JSON file side of the mapping leak nothing usable — a leaked digest is not a
leaked token.

Two configuration shapes, same semantics::

    # env var, comma / semicolon / newline separated pairs
    SOU_INGEST_TOKENS="<sha256hex>:hermes,<sha256hex>:claude"

    # env var pointing at a JSON file (SOPS-managed on the homelab)
    SOU_INGEST_TOKENS=/run/secrets/sou-ingest-tokens.json
    # {"<sha256hex>": "hermes", "<sha256hex>": "claude"}

Lookup is constant-time: every stored digest is compared with
``hmac.compare_digest`` and the loop never exits early, so response timing
carries no information about which entry (if any) matched. Each token gets
its own token-bucket rate limit — ``SOU_INGEST_RATE_LIMIT`` requests per
second, burst equal to the rate (default 20/s). The caller answers 401 on a
missing/unknown token, 403 on an agent mismatch, 429 when the bucket is
empty; that HTTP contract lives in ``app.py``, this module only answers
"who does this token belong to" and "may this token speak now".

Startup contract (``build_from_env``): unset/blank ``SOU_INGEST_TOKENS``
returns ``None`` — dev/LAN behaviour stays open and the caller prints the
warning; ``SOU_MODE=ingest`` without tokens is a configuration error and
raises, because the ingest face must never run unauthenticated (P13-C).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time

DEFAULT_RATE_LIMIT = 20.0  # requests per second per token; burst == rate

_TOKEN_ENV = "SOU_INGEST_TOKENS"
_RATE_ENV = "SOU_INGEST_RATE_LIMIT"
_MODE_ENV = "SOU_MODE"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
# RFC 7235: scheme name is case-insensitive; RFC 6750 spells it "Bearer".
_BEARER_RE = re.compile(r"^[Bb][Ee][Aa][Rr][Ee][Rr][ \t]+(\S+)$")


def hash_token(token: str) -> str:
    """The canonical storage form of a token: lowercase sha256 hex digest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def bearer_from_header(value):
    """The token from an ``Authorization: Bearer <token>`` header, else None."""
    if not value:
        return None
    match = _BEARER_RE.match(value.strip())
    return match.group(1) if match else None


def normalize_digest(digest):
    """Validate/normalise one stored sha256 hex digest."""
    digest = str(digest).strip().lower()
    if not _HEX64.match(digest):
        raise ValueError(
            f"token hash: expected a 64-char sha256 hex digest, got "
            f"{digest[:3] + '...' + digest[-3:] if len(digest) > 6 else digest!r}"
        )
    return digest


def normalize_agent(agent):
    """Validate/normalise one agent name (non-empty, no ':')."""
    agent = str(agent).strip()
    if not agent or ":" in agent:
        raise ValueError(f"agent: expected a non-empty name without ':', got {agent!r}")
    return agent


class TokenStore:
    """Hashes-only ``sha256(token) -> agent`` map with a per-token limiter."""

    def __init__(self, agents_by_hash, rate=DEFAULT_RATE_LIMIT, burst=None,
                 clock=time.monotonic):
        clean = {}
        for digest, agent in dict(agents_by_hash).items():
            clean[normalize_digest(digest)] = normalize_agent(agent)
        self._agents = clean
        self.rate = float(rate)
        self.burst = float(burst) if burst is not None else self.rate
        if self.rate <= 0 or self.burst <= 0:
            raise ValueError("rate limit: rate and burst must be positive")
        self._clock = clock
        # digest -> [tokens_left, last_refill]; keyed by hash, never plaintext.
        self._buckets = {}

    def mapping(self):
        """A copy of the hashes-only mapping."""
        return dict(self._agents)

    def lookup(self, token):
        """The agent owning ``token``, or None. Constant time in entry count.

        Every entry is compared, always, with ``hmac.compare_digest``: no
        early exit, no dict membership shortcut, so the time taken tells an
        attacker nothing about where (or whether) the digest matched.
        """
        if token is None:
            return None
        digest = hash_token(token)
        found = False
        agent = None
        for stored, stored_agent in self._agents.items():
            if hmac.compare_digest(digest, stored) and not found:
                found = True
                agent = stored_agent
        return agent

    def allow_request(self, token):
        """Token bucket per token: consume one allowance, True if allowed."""
        digest = hash_token(token or "")
        now = self._clock()
        tokens, ts = self._buckets.get(digest, (self.burst, now))
        tokens = min(self.burst, tokens + (now - ts) * self.rate)
        if tokens < 1.0:
            self._buckets[digest] = (tokens, now)
            return False
        tokens -= 1.0
        self._buckets[digest] = (tokens, now)
        return True


def parse_pairs(value):
    """``"hex:agent,hex:agent"`` (commas, semicolons, newlines) -> mapping."""
    entries = {}
    for chunk in re.split(r"[,;\n]+", value):
        chunk = chunk.strip()
        if not chunk:
            continue
        digest, sep, agent = chunk.partition(":")
        if not sep:
            raise ValueError(f"token line: expected '<sha256hex>:<agent>', got {chunk!r}")
        entries[digest.strip().lower()] = agent.strip()
    return entries


def parse_json_file(path):
    """``{"<sha256hex>": "<agent>", ...}`` -> mapping."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"token file: expected a JSON object of hash -> agent, got {type(data).__name__}")
    return {str(k).strip().lower(): v for k, v in data.items()}


def parse_source(value, rate=DEFAULT_RATE_LIMIT):
    """Build a TokenStore from the ``SOU_INGEST_TOKENS`` value itself.

    A path to an existing file is read as JSON; anything else is a list of
    inline ``hex:agent`` pairs.
    """
    value = value.strip()
    if os.path.isfile(value):
        entries = parse_json_file(value)
    else:
        entries = parse_pairs(value)
    if not entries:
        raise ValueError("tokens: no valid '<sha256hex>:<agent>' entries in SOU_INGEST_TOKENS")
    return TokenStore(entries, rate=rate)


def _rate_from(environ):
    raw = (environ.get(_RATE_ENV) or "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT
    try:
        rate = float(raw)
    except ValueError:
        raise ValueError(f"rate limit: SOU_INGEST_RATE_LIMIT must be a number, got {raw!r}")
    if rate <= 0:
        raise ValueError(f"rate limit: SOU_INGEST_RATE_LIMIT must be positive, got {raw!r}")
    return rate


def build_from_env(environ=None):
    """The configured TokenStore, or None when tokens are not configured.

    Raises RuntimeError when ``SOU_MODE=ingest`` demands tokens that are not
    there — that combination is a misconfigured ingest face, and the only
    honest answer is to refuse to start. Malformed entries raise ValueError:
    silence is not an option either, a typo'd digest would just reject every
    real reporter.
    """
    environ = os.environ if environ is None else environ
    value = (environ.get(_TOKEN_ENV) or "").strip()
    if not value:
        if (environ.get(_MODE_ENV) or "").strip().lower() == "ingest":
            raise RuntimeError(
                "SOU_MODE=ingest requires per-agent ingest tokens: set "
                "SOU_INGEST_TOKENS (sha256(token):agent pairs or a JSON file "
                "path); create one with `python backend/tokens.py new <agent>`"
            )
        return None
    return parse_source(value, rate=_rate_from(environ))
