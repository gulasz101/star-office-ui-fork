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
import shutil
import subprocess
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


# ---------------------------------------------------------------------------
# Slice S2 (SOU-247): the HTTP contract on POST /events.
# ---------------------------------------------------------------------------

app_mod = None


def load_app_module():
    """Import ``app`` once against a throwaway DATABASE_URL (the established
    harness of tests/test_app_db.py / tests/test_events_api.py)."""
    global app_mod
    if app_mod is not None:
        return app_mod
    workdir = tempfile.mkdtemp(prefix="sou219-module-")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{os.path.join(workdir, 'module.db')}")
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    import app  # noqa: F401  (module import runs the startup path under test)
    app_mod = app
    return app_mod


def valid_event(**over) -> dict:
    """A schema-valid ADR-035 ``tool_call`` envelope, with optional overrides."""
    event = {
        "version": 1,
        "agent": "hermes",
        "event_type": "tool_call",
        "timestamp": "2026-10-16T14:20:55.653Z",
        "payload": {"tool": "read_file", "args": {"path": "backend/app.py"}},
    }
    event.update(over)
    return event


class _HttpAuthTestCase(unittest.TestCase):
    """Client + per-test database + a swapped-in INGEST_STORE."""

    def setUp(self) -> None:
        app = load_app_module()
        self.app_mod = app
        self.workdir = tempfile.mkdtemp(prefix="sou219-")
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(self.workdir, 'app.db')}"
        app.db.shutdown()
        app.init_database()
        self.client = app.app.test_client()
        self._orig_store = getattr(app, "INGEST_STORE", None)
        self.clock = Clock()
        self.store = ingest_auth.TokenStore(
            {sha(HERMES_TOKEN): "hermes", sha(CLAUDE_TOKEN): "claude"},
            rate=2.0, burst=2.0, clock=self.clock,
        )
        self.seed_count = self.stored_event_count()

    def tearDown(self) -> None:
        self.app_mod.INGEST_STORE = self._orig_store
        self.app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def enforce(self, store) -> None:
        self.app_mod.INGEST_STORE = store

    def post(self, body=None, *, token=None, header=None):
        headers = {}
        if header is not None:
            headers["Authorization"] = header
        elif token is not None:
            headers["Authorization"] = f"Bearer {token}"
        return self.client.post("/events", json=body if body is not None else valid_event(),
                               headers=headers)

    def stored_event_count(self):
        with self.app_mod.db.session_scope() as session:
            return len(list(self.app_mod.db.list_events(session)))

    def written_by_test(self):
        """Events beyond the one the fresh install seeds (init_database)."""
        return self.stored_event_count() - self.seed_count

    def assert_json_error(self, resp, status):
        self.assertEqual(
            resp.status_code, status,
            f"expected {status}, got {resp.status_code}: {resp.get_data(as_text=True)[:200]}",
        )
        payload = resp.get_json()
        self.assertIs(payload.get("ok"), False, f"expected ok=false, got {payload}")
        self.assertIsInstance(payload.get("error"), str)
        self.assertTrue(payload["error"].strip())
        return payload["error"]


class UnauthorizedTest(_HttpAuthTestCase):
    """AC 3: missing or unknown Bearer token -> 401, before anything else."""

    def test_unauthorized_no_header(self) -> None:
        self.enforce(self.store)
        self.assert_json_error(self.post(), 401)
        self.assertEqual(self.written_by_test(), 0)

    def test_unauthorized_unknown_token(self) -> None:
        self.enforce(self.store)
        resp = self.post(token="no-such-token")
        self.assert_json_error(resp, 401)
        # The error must not hint which part of the token matched.
        self.assertNotIn("no-such", resp.get_data(as_text=True))

    def test_unauthorized_wrong_scheme(self) -> None:
        self.enforce(self.store)
        self.assert_json_error(
            self.post(header="Basic dXNlcjpwYXNz"), 401
        )

    def test_unauthorized_precedes_body_checks(self) -> None:
        # A broken body from an unauthed caller is still a 401, not a 400:
        # authentication happens before the endpoint looks at the payload.
        self.enforce(self.store)
        resp = self.client.post(
            "/events", data="not json at all", content_type="application/json"
        )
        self.assertEqual(resp.status_code, 401)


class ForbiddenTest(_HttpAuthTestCase):
    """AC 4: token's agent != envelope agent -> 403, nothing written."""

    def test_forbidden_agent_mismatch(self) -> None:
        self.enforce(self.store)
        error = self.assert_json_error(
            self.post(valid_event(agent="claude"), token=HERMES_TOKEN), 403
        )
        self.assertIn("claude", error)
        self.assertEqual(self.written_by_test(), 0)

    def test_forbidden_is_case_sensitive(self) -> None:
        # The token decides identity exactly; "Hermes" is not "hermes".
        self.enforce(self.store)
        self.assert_json_error(
            self.post(valid_event(agent="Hermes"), token=HERMES_TOKEN), 403
        )


class OkTest(_HttpAuthTestCase):
    """AC 5: valid token + matching agent -> 201 and the event lands; the
    pre-existing 400 contract is untouched for authenticated callers."""

    def test_ok_matching_token_stores_event(self) -> None:
        self.enforce(self.store)
        resp = self.post(token=HERMES_TOKEN)
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))
        self.assertIs(resp.get_json().get("ok"), True)
        self.assertEqual(self.written_by_test(), 1)

    def test_ok_second_agent_own_event(self) -> None:
        self.enforce(self.store)
        resp = self.post(valid_event(agent="claude"), token=CLAUDE_TOKEN)
        self.assertEqual(resp.status_code, 201)

    def test_ok_bad_body_still_400_when_authed(self) -> None:
        # Ruling: a 403 decision needs the agent field; a body the schema
        # rejects never reaches validation, so an authenticated malformed body
        # keeps the historical 400 with its exact message.
        self.enforce(self.store)
        resp = self.post(token=HERMES_TOKEN, body={"agent": "hermes"})
        self.assert_json_error(resp, 400)

    def test_ok_non_json_still_400_when_authed(self) -> None:
        self.enforce(self.store)
        resp = self.client.post(
            "/events", data="hello", content_type="text/plain",
            headers={"Authorization": f"Bearer {HERMES_TOKEN}"},
        )
        self.assert_json_error(resp, 400)


class RateLimitHttpTest(_HttpAuthTestCase):
    """AC 6 (enforcement half): over the per-token bucket -> 429 + Retry-After."""

    def test_rate_limit_429_with_retry_after(self) -> None:
        self.enforce(self.store)  # burst 2 at rate 2/s
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        resp = self.post(token=HERMES_TOKEN)
        self.assert_json_error(resp, 429)
        self.assertEqual(resp.headers.get("Retry-After"), "1")
        self.clock.advance(0.5)  # one token refilled
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        self.clock.advance(60)  # capped at burst
        for _ in range(2):
            self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 429)

    def test_rate_limit_is_per_token_over_http(self) -> None:
        self.enforce(self.store)
        self.post(token=HERMES_TOKEN)
        self.post(token=HERMES_TOKEN)
        self.assert_json_error(self.post(token=HERMES_TOKEN), 429)
        self.assertEqual(self.post(valid_event(agent="claude"), token=CLAUDE_TOKEN).status_code, 201)

    def test_rate_limit_denied_request_writes_nothing(self) -> None:
        self.enforce(self.store)
        before = self.stored_event_count()
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        self.assertEqual(self.post(token=HERMES_TOKEN).status_code, 201)
        self.assertEqual(self.stored_event_count(), before + 2)
        self.assert_json_error(self.post(token=HERMES_TOKEN), 429)
        self.assertEqual(self.stored_event_count(), before + 2)


class OpenBehaviourTest(_HttpAuthTestCase):
    """AC 7: unset SOU_INGEST_TOKENS -> today's dev/LAN behaviour + warning."""

    def test_open_when_unset_accepts_untokened(self) -> None:
        self.enforce(None)
        resp = self.post()
        self.assertEqual(resp.status_code, 201, resp.get_data(as_text=True))

    def test_open_when_unset_ignores_stray_bearer(self) -> None:
        # Manager note on SOU-219: old reporters must keep working; a stray
        # header on an open server is accepted, not judged.
        self.enforce(None)
        resp = self.post(token=HERMES_TOKEN)
        self.assertEqual(resp.status_code, 201)

    def test_open_startup_warning_when_unset(self) -> None:
        # A real startup: import app in a clean subprocess, tokens unset.
        env = os.environ.copy()
        env.pop("SOU_INGEST_TOKENS", None)
        env.pop("SOU_MODE", None)
        workdir = tempfile.mkdtemp(prefix="sou219-boot-")
        env["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'boot.db')}"
        env["ALEMBIC_SKIP_FILECONFIG"] = "1"
        try:
            proc = subprocess.run(
                [sys.executable, "-c", "import app"],
                cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            self.assertIn("WARNING", proc.stderr)
            self.assertIn("SOU_INGEST_TOKENS", proc.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
