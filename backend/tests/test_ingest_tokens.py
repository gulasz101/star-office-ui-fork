"""Per-agent ingest tokens (SOU-219, Phase 13 P13-B).

``POST /events`` is the agents' door. Until now anyone who could reach it
could write any agent name (docs/public-lodge.md §2): ``SOU_INGEST_TOKENS``
maps ``sha256(token) -> agent name`` — hashes only, never plaintext — and a
request may only write events for the agent its token belongs to. Missing or
unknown token -> 401; agent mismatch -> 403; a per-token rate limit answers
429 over the burst. Unset ``SOU_INGEST_TOKENS`` keeps today's dev/LAN
behaviour with a startup warning; ``SOU_MODE=ingest`` (P13-C) makes tokens
mandatory.

Slice S1 (SOU-246): pure ``backend/ingest_auth.py`` — store parsing,
constant-time lookup, rate limiter, env build. Slice S2 (SOU-247) appends the
HTTP enforcement through the real Flask test client. Slice S3 (SOU-248)
appends the ``backend/tokens.py`` CLI round-trip.

Run from the repo root::

    uv run pytest backend/tests/test_ingest_tokens.py -v
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import ingest_auth


def sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# A known vector so the digest convention is pinned, not merely consistent
# with itself: sha256("abc") is published everywhere.
ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

HERMES_TOKEN = "hermes-t…-aaa"
CLAUDE_TOKEN = "claude-t…-bbb"


class Clock:
    """Injectable monotonic clock: tests advance it, nothing sleeps."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class StorageTest(unittest.TestCase):
    """AC 1/2: hashes-only storage, sha256 digest convention, constant time."""

    def test_storage_sha256_vector(self) -> None:
        self.assertEqual(ingest_auth.hash_token("abc"), ABC_SHA256)

    def test_storage_holds_hashes_only(self) -> None:
        store = ingest_auth.TokenStore({sha(HERMES_TOKEN): "hermes"})
        # Keys are 64-char lowercase hex digests, values are agent names.
        for key in store.mapping():
            self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertEqual(store.mapping(), {sha(HERMES_TOKEN): "hermes"})
        # The plaintext token appears nowhere in the store's own state.
        self.assertNotIn(HERMES_TOKEN, repr(store.mapping()))
        self.assertNotIn(HERMES_TOKEN, str(vars(store)))

    def test_storage_parses_inline_pairs(self) -> None:
        value = f"{sha(HERMES_TOKEN)}:hermes,{sha(CLAUDE_TOKEN)}:claude"
        store = ingest_auth.parse_source(value)
        self.assertEqual(store.mapping()[sha(HERMES_TOKEN)], "hermes")
        self.assertEqual(store.mapping()[sha(CLAUDE_TOKEN)], "claude")

    def test_storage_parses_semicolons_and_newlines(self) -> None:
        value = f"{sha(HERMES_TOKEN)}:hermes; {sha(CLAUDE_TOKEN)}:claude\n"
        store = ingest_auth.parse_source(value)
        self.assertEqual(len(store.mapping()), 2)

    def test_storage_parses_json_file(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou219-")
        path = os.path.join(workdir, "tokens.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({sha(HERMES_TOKEN): "hermes"}, fh)
        store = ingest_auth.parse_source(path)
        self.assertEqual(store.lookup(HERMES_TOKEN), "hermes")

    def test_storage_rejects_malformed_entries(self) -> None:
        for bad in (
            "notahex:hermes",                       # not a digest
            "deadbeef:hermes",                     # too short
            f"{sha(HERMES_TOKEN)}:",               # empty agent
            f"{sha(HERMES_TOKEN)}:hermes:oops",    # digest must have no colon
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ingest_auth.parse_source(bad)

    def test_storage_normalises_uppercase_hashes(self) -> None:
        value = f"{sha(HERMES_TOKEN).upper()}:hermes"
        store = ingest_auth.parse_source(value)
        self.assertEqual(store.lookup(HERMES_TOKEN), "hermes")

    def test_storage_constant_time_lookup(self) -> None:
        # The compare itself must be timing-safe, not a dict membership test.
        source = inspect.getsource(ingest_auth.TokenStore.lookup)
        self.assertIn("compare_digest", source)
        store = ingest_auth.TokenStore(
            {sha(HERMES_TOKEN): "hermes", sha(CLAUDE_TOKEN): "claude"}
        )
        self.assertEqual(store.lookup(HERMES_TOKEN), "hermes")
        self.assertEqual(store.lookup(CLAUDE_TOKEN), "claude")
        # Wrong/unknown tokens are uniformly rejected.
        self.assertIsNone(store.lookup("no-such-token"))
        self.assertIsNone(store.lookup(""))

    def test_storage_last_entry_not_special(self) -> None:
        # compare_digest over ALL entries: a first-entry token still matches
        # after more entries exist (guards against position-dependent logic).
        store = ingest_auth.TokenStore(
            {sha(HERMES_TOKEN): "hermes", sha(CLAUDE_TOKEN): "claude"}
        )
        self.assertEqual(store.lookup(HERMES_TOKEN), "hermes")


class RateLimitTest(unittest.TestCase):
    """AC 6 (limiter half): per-token token bucket, default 20 req/s burst."""

    def _store(self, rate: float = 2.0, burst: float = 2.0) -> tuple:
        clock = Clock()
        store = ingest_auth.TokenStore(
            {sha(HERMES_TOKEN): "hermes", sha(CLAUDE_TOKEN): "claude"},
            rate=rate,
            burst=burst,
            clock=clock,
        )
        return store, clock

    def test_rate_burst_then_block(self) -> None:
        store, _clock = self._store(rate=2, burst=2)
        self.assertTrue(store.allow_request(HERMES_TOKEN))
        self.assertTrue(store.allow_request(HERMES_TOKEN))
        self.assertFalse(store.allow_request(HERMES_TOKEN))

    def test_rate_refill_restores(self) -> None:
        store, clock = self._store(rate=2, burst=2)
        store.allow_request(HERMES_TOKEN)
        store.allow_request(HERMES_TOKEN)
        self.assertFalse(store.allow_request(HERMES_TOKEN))
        clock.advance(0.5)  # 2 req/s refills one token in half a second
        self.assertTrue(store.allow_request(HERMES_TOKEN))
        clock.advance(100)  # never refill above the burst
        self.assertTrue(store.allow_request(HERMES_TOKEN))
        self.assertTrue(store.allow_request(HERMES_TOKEN))
        self.assertFalse(store.allow_request(HERMES_TOKEN))

    def test_rate_is_per_token(self) -> None:
        store, _clock = self._store(rate=2, burst=2)
        store.allow_request(HERMES_TOKEN)
        store.allow_request(HERMES_TOKEN)
        self.assertFalse(store.allow_request(HERMES_TOKEN))
        # claude's bucket is untouched by hermes burning its own.
        self.assertTrue(store.allow_request(CLAUDE_TOKEN))

    def test_rate_buckets_keyed_by_hash_not_plaintext(self) -> None:
        store, _clock = self._store()
        store.allow_request(HERMES_TOKEN)
        self.assertNotIn(HERMES_TOKEN, str(store._buckets))
        self.assertIn(sha(HERMES_TOKEN), store._buckets)

    def test_rate_default_is_20_per_second(self) -> None:
        store = ingest_auth.TokenStore({sha(HERMES_TOKEN): "hermes"})
        self.assertEqual(store.rate, 20.0)
        self.assertEqual(store.burst, 20.0)


class EnvBuildTest(unittest.TestCase):
    """AC 7/8 build contract: unset -> None (caller warns), ingest mode -> required."""

    def test_storage_open_when_unset_returns_none(self) -> None:
        for value in (None, "", "   "):
            env = {"SOU_INGEST_TOKENS": value} if value is not None else {}
            self.assertIsNone(ingest_auth.build_from_env(env))

    def test_requires_tokens_in_ingest_mode(self) -> None:
        for env in (
            {"SOU_MODE": "ingest"},
            {"SOU_MODE": "ingest", "SOU_INGEST_TOKENS": "  "},
        ):
            with self.subTest(env=env):
                with self.assertRaises(RuntimeError):
                    ingest_auth.build_from_env(env)

    def test_requires_only_about_tokens(self) -> None:
        with self.assertRaises(RuntimeError) as ctx:
            ingest_auth.build_from_env({"SOU_MODE": "ingest"})
        self.assertIn("SOU_INGEST_TOKENS", str(ctx.exception))

    def test_rate_env_override(self) -> None:
        env = {
            "SOU_INGEST_TOKENS": f"{sha(HERMES_TOKEN)}:hermes",
            "SOU_INGEST_RATE_LIMIT": "5",
        }
        store = ingest_auth.build_from_env(env)
        self.assertEqual(store.rate, 5.0)

    def test_build_from_real_environment_by_default(self) -> None:
        saved = os.environ.pop("SOU_INGEST_TOKENS", None)
        saved_mode = os.environ.pop("SOU_MODE", None)
        try:
            self.assertIsNone(ingest_auth.build_from_env())
            os.environ["SOU_INGEST_TOKENS"] = f"{sha(HERMES_TOKEN)}:hermes"
            store = ingest_auth.build_from_env()
            self.assertEqual(store.lookup(HERMES_TOKEN), "hermes")
        finally:
            if saved is not None:
                os.environ["SOU_INGEST_TOKENS"] = saved
            if saved_mode is not None:
                os.environ["SOU_MODE"] = saved_mode

    def test_bearer_header_parsing(self) -> None:
        self.assertEqual(
            ingest_auth.bearer_from_header(f"Bearer {HERMES_TOKEN}"), HERMES_TOKEN
        )
        # RFC 7235: the scheme is case-insensitive.
        self.assertEqual(
            ingest_auth.bearer_from_header(f"bearer {HERMES_TOKEN}"), HERMES_TOKEN
        )
        self.assertIsNone(ingest_auth.bearer_from_header(None))
        self.assertIsNone(ingest_auth.bearer_from_header(""))
        self.assertIsNone(ingest_auth.bearer_from_header("Basic dXNlcjpwYXNz"))
        self.assertIsNone(ingest_auth.bearer_from_header("Bearer"))
        self.assertIsNone(ingest_auth.bearer_from_header("Bearer two tokens"))


if __name__ == "__main__":
    unittest.main()
