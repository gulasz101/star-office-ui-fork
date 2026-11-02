"""Unit tests for paperclip-claude-code-reporter (GUL-827).

Run with:  python3 -m unittest discover -s reporter/paperclip-claude-code-reporter/test

Everything here is pure or mocked: no Paperclip, no dashboard, no network. The one
thing worth writing tests for is `decide`, because a wrong rule there is invisible in
production — the character simply sits in the wrong place.
"""

import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(HERE, "..", "paperclip-claude-code-reporter.py")

_spec = importlib.util.spec_from_file_location("reporter", MODULE_PATH)
reporter = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reporter)


class EnvelopeTest(unittest.TestCase):
    def test_envelope_is_v1_and_carries_the_state(self):
        event = reporter.build_event("claude-code", "working")
        self.assertEqual(event["version"], 1)
        self.assertEqual(event["agent"], "claude-code")
        self.assertEqual(event["event_type"], "state_change")
        self.assertEqual(event["payload"], {"state": "working"})

    def test_timestamp_is_utc_z_suffixed_iso8601(self):
        moment = datetime(2026, 11, 2, 21, 21, 8, 123000, tzinfo=timezone.utc)
        self.assertEqual(reporter.utc_now_iso(moment), "2026-11-02T21:21:08.123Z")

    def test_timestamp_converts_an_offset_to_utc(self):
        # 22:21 CET is 21:21Z; the schema normalises, but we send UTC already.
        from datetime import timedelta

        cet = timezone(timedelta(hours=1))
        moment = datetime(2026, 11, 2, 22, 21, 8, tzinfo=cet)
        self.assertEqual(reporter.utc_now_iso(moment), "2026-11-02T21:21:08.000Z")

    def test_the_five_required_keys_are_present(self):
        required = {"version", "agent", "event_type", "timestamp", "payload"}
        self.assertEqual(set(reporter.build_event("a", "idle").keys()), required)


class IsRunningTest(unittest.TestCase):
    AGENT = "97efeb2d-5b3a-4110-b893-6cbcbb29164c"

    def test_bare_list_shape(self):
        runs = [{"agentId": self.AGENT, "status": "running"}]
        self.assertTrue(reporter.is_running(runs, self.AGENT))

    def test_wrapped_shape(self):
        runs = {"runs": [{"agentId": self.AGENT, "status": "running"}]}
        self.assertTrue(reporter.is_running(runs, self.AGENT))

    def test_another_agents_live_run_is_not_ours(self):
        runs = [{"agentId": "e12b834d-5e42-491a-9cd6-f4ddef2e97f8", "status": "running"}]
        self.assertFalse(reporter.is_running(runs, self.AGENT))

    def test_finished_run_is_not_live(self):
        for status in ("succeeded", "failed", "cancelled", "timed_out", "interrupted"):
            runs = [{"agentId": self.AGENT, "status": status}]
            self.assertFalse(reporter.is_running(runs, self.AGENT), status)

    def test_live_statuses_are_live(self):
        for status in ("running", "starting", "pending"):
            runs = [{"agentId": self.AGENT, "status": status}]
            self.assertTrue(reporter.is_running(runs, self.AGENT), status)

    def test_agent_id_match_is_case_insensitive(self):
        runs = [{"agentId": self.AGENT.upper(), "status": "running"}]
        self.assertTrue(reporter.is_running(runs, self.AGENT))

    def test_junk_never_raises(self):
        for junk in (None, [], {}, "nope", 3, [None, 7, "x"], {"runs": "nope"}):
            self.assertFalse(reporter.is_running(junk, self.AGENT), junk)

    def test_one_live_run_among_many_counts(self):
        runs = [
            {"agentId": self.AGENT, "status": "succeeded"},
            {"agentId": "someone-else", "status": "running"},
            {"agentId": self.AGENT, "status": "running"},
        ]
        self.assertTrue(reporter.is_running(runs, self.AGENT))


class DecideTest(unittest.TestCase):
    REASSERT = 30

    def decide(self, state, now, last_sent_at, last_sent_state):
        return reporter.decide(state, now, last_sent_at, last_sent_state, self.REASSERT)

    def test_first_tick_always_announces(self):
        self.assertEqual(self.decide("working", 100.0, None, None), "working")
        self.assertEqual(self.decide("idle", 100.0, None, None), "idle")

    def test_working_into_idle_is_announced_at_once(self):
        self.assertEqual(self.decide("idle", 100.5, 100.0, "working"), "idle")

    def test_idle_into_working_is_announced_at_once(self):
        self.assertEqual(self.decide("working", 100.5, 100.0, "idle"), "working")

    def test_idle_is_announced_once_then_left_alone(self):
        self.assertIsNone(self.decide("idle", 100.0, 90.0, "idle"))
        self.assertIsNone(self.decide("idle", 10_000.0, 90.0, "idle"))

    def test_working_is_reasserted_before_the_renderer_goes_for_coffee(self):
        # 60 s is IDLE_COFFEE_MS in frontend/v3/activity.js. A 45 s gap must already have
        # produced a re-assert, or a long run drifts to the coffee machine mid-work.
        self.assertEqual(self.decide("working", 145.0, 100.0, "working"), "working")

    def test_working_is_not_spammed_every_tick(self):
        self.assertIsNone(self.decide("working", 105.0, 100.0, "working"))
        self.assertIsNone(self.decide("working", 129.9, 100.0, "working"))

    def test_reassert_interval_is_under_the_renderer_idle_window(self):
        self.assertLess(reporter.DEFAULT_REASSERT_SECONDS, 60)

    def test_unknown_previous_state_counts_as_a_change(self):
        self.assertEqual(self.decide("working", 100.0, 99.0, "wibble"), "working")


class ConfigTest(unittest.TestCase):
    def write(self, payload):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    BASE = {
        "agent": "claude-code",
        "agent_id": "97efeb2d-5b3a-4110-b893-6cbcbb29164c",
        "company_id": "0e2eb054-4cb6-4ea1-a2e7-2b1894285d8c",
        "token": "ingest-token",
        "paperclip_token": "paperclip-token",
    }

    def test_reads_the_file(self):
        cfg = reporter.load_config(self.write(dict(self.BASE)), env={})
        self.assertEqual(cfg["agent"], "claude-code")
        self.assertEqual(cfg["poll_seconds"], 10)

    def test_environment_wins_over_the_file(self):
        path = self.write(dict(self.BASE))
        cfg = reporter.load_config(path, env={"SOU_DASHBOARD_URL": "http://example:19000/"})
        self.assertEqual(cfg["dashboard_url"], "http://example:19000")

    def test_a_missing_credential_is_named_not_ignored(self):
        payload = dict(self.BASE)
        del payload["token"]
        with self.assertRaises(reporter.ReporterError) as ctx:
            reporter.load_config(self.write(payload), env={})
        self.assertIn("token", str(ctx.exception))

    def test_a_missing_config_file_names_the_path(self):
        with self.assertRaises(reporter.ReporterError) as ctx:
            reporter.load_config("/nonexistent/nope.json", env={})
        self.assertIn("/nonexistent/nope.json", str(ctx.exception))

    def test_blank_env_does_not_clobber_the_file(self):
        cfg = reporter.load_config(self.write(dict(self.BASE)), env={"SOU_AGENT": "  "})
        self.assertEqual(cfg["agent"], "claude-code")

    def test_bad_numbers_are_refused_with_the_key_name(self):
        payload = dict(self.BASE, poll_seconds="soon")
        with self.assertRaises(reporter.ReporterError) as ctx:
            reporter.load_config(self.write(payload), env={})
        self.assertIn("poll_seconds", str(ctx.exception))

    def test_secrets_are_never_part_of_a_config_repr_we_print(self):
        # Guard against a future `print(cfg)`: the token keys stay out of messages.
        self.assertEqual(set(reporter.SECRET_KEYS), {"token", "paperclip_token"})


if __name__ == "__main__":
    unittest.main()
