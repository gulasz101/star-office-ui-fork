"""Server-side ingest sanitiser (SOU-218, Phase 13).

Reporters can be old or buggy, so ``POST /events`` is the gate: the backend
stores only a **safe projection** of each payload, never the raw one, and a
DB dump then leaks nothing (docs/public-lodge.md §3). The rules are ported
from the Claude Code reporter (SOU-157/SOU-173,
``reporter/claude-code/star_office_hook.py``), which stays the reference
implementation for what may leave a machine.

Slice S1 (SOU-228): pure unit tests for ``backend/sanitize.py`` — the
ticket's privacy examples plus the per-event-type allow-list. Slice S2
(SOU-229) appends the canary sweep through the real Flask test client.

Run from the repo root::

    uv run pytest backend/tests/test_sanitize.py -v
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import unittest

import sanitize


class CommandLabelTest(unittest.TestCase):
    """`command` -> program plus at most one allow-listed bare subcommand."""

    def test_ticket_examples(self) -> None:
        # The three examples the ticket spells out, verbatim expectations.
        self.assertEqual(
            sanitize.command_label("git -c user.password=X push"), "git push"
        )
        self.assertEqual(
            sanitize.command_label("cd /secret && kubectl get secret foo -o yaml"),
            "kubectl get",
        )
        self.assertEqual(
            sanitize.command_label("curl -H 'Authorization: Bearer X' https://host/p?q"),
            "curl",
        )

    def test_runner_programs_keep_two_tokens(self) -> None:
        self.assertEqual(sanitize.command_label("npm run build"), "npm run build")
        self.assertEqual(sanitize.command_label("pnpm run lint"), "pnpm run lint")
        self.assertEqual(sanitize.command_label("yarn run test"), "yarn run test")
        # a bare install verb is one token: still no arguments
        self.assertEqual(sanitize.command_label("npm i left-pad"), "npm i")

    def test_python_dash_m_reports_the_module(self) -> None:
        self.assertEqual(sanitize.command_label("python -m pytest -q"), "pytest")
        self.assertEqual(sanitize.command_label("python3.11 -m venv .venv"), "venv")

    def test_wrappers_env_prefixes_and_flags_are_skipped(self) -> None:
        # exact entries from the reporter's reference table (test_subcommands.py)
        self.assertEqual(sanitize.command_label("sudo -n git push"), "git push")
        self.assertEqual(
            sanitize.command_label("  /usr/bin/env FOO=bar npm run build "), "npm run build"
        )
        self.assertEqual(sanitize.command_label("timeout 300 git status"), "git status")
        self.assertEqual(sanitize.command_label("timeout 5m git pull"), "git pull")
        self.assertEqual(sanitize.command_label("FOO=bar /usr/local/bin/git status"), "git status")
        self.assertEqual(sanitize.command_label("cd build && make test"), "make test")

    def test_subcommand_must_be_a_bare_lowercase_identifier(self) -> None:
        # value-ish tokens are skipped, never echoed; only bare verbs survive
        self.assertEqual(sanitize.command_label("git config user.email a@b.c"), "git config")
        self.assertEqual(
            sanitize.command_label("kubectl CANARY-namespace get pods"), "kubectl get"
        )
        self.assertEqual(sanitize.command_label("git -c a=b -c c=d"), "git")

    def test_non_allowlisted_programs_get_program_only(self) -> None:
        self.assertEqual(sanitize.command_label("rsync -av /home/secret backup.host:/x"), "rsync")
        self.assertEqual(sanitize.command_label("docker build -t img ."), "docker build")

    def test_degenerate_commands_stay_safe(self) -> None:
        self.assertEqual(sanitize.command_label(""), "")
        self.assertEqual(sanitize.command_label(None), "")
        self.assertEqual(sanitize.command_label("cd /only/dir"), "cd")
        self.assertEqual(sanitize.command_label("!!!"), "shell")


class BasenameTest(unittest.TestCase):
    """File-ish values keep the last segment only."""

    def test_ticket_example_full_paths_become_basenames(self) -> None:
        self.assertEqual(sanitize.basename("/home/dev/project/app.py"), "app.py")

    def test_windows_paths_and_trailing_separators(self) -> None:
        self.assertEqual(sanitize.basename("C:\\Users\\dev\\proj\\main.rs"), "main.rs")
        self.assertEqual(sanitize.basename("/tmp/"), "tmp")

    def test_unusable_paths_are_dropped(self) -> None:
        self.assertEqual(sanitize.basename("/"), "")
        self.assertEqual(sanitize.basename("../../.."), "")
        self.assertEqual(sanitize.basename(None), "")
        self.assertEqual(sanitize.basename(17), "")


class TextRuleTest(unittest.TestCase):
    """Values are single-line, <= 120 chars, no control characters."""

    def test_control_characters_and_newlines_collapse(self) -> None:
        cleaned = sanitize.text_of("one\ntwo\x07three\x00")
        self.assertNotIn("\n", cleaned)
        self.assertNotIn("\x00", cleaned)
        self.assertNotIn("\x07", cleaned)

    def test_length_cap(self) -> None:
        self.assertLessEqual(len(sanitize.text_of("x" * 500)), 120)


class SanitizePayloadTest(unittest.TestCase):
    """The per-event-type allow-list: anything not listed is dropped."""

    def test_tool_call_keeps_label_fields_drops_the_rest(self) -> None:
        out = sanitize.sanitize_payload("tool_call", {
            "tool": "Edit",
            "detail": "free text CANARY",
            "message": "CANARY",
            "session_id": "sess-CANARY",
            "url": "https://host/CANARY",
            "args": {"path": "CANARY"},
        })
        self.assertEqual(out, {"tool": "Edit"})

    def test_file_edit_keeps_basename_drops_output(self) -> None:
        out = sanitize.sanitize_payload("file_edit", {
            "tool": "Write",
            "file": "/home/dev/project/app.py",
            "session_id": "x",
            "output": "CANARY file contents",
            "duration_ms": 250,
        })
        self.assertEqual(out, {"tool": "Write", "file": "app.py", "duration_ms": 250})

    def test_path_aliases_are_all_basename_fields(self) -> None:
        out = sanitize.sanitize_payload("file_edit", {
            "path": "/a/b/main.go",
            "target_file": "/c/d/skill.md",
            "file_path": "/e/f/note.ipynb",
            "notebook_path": "/g/h/secret.ipynb",
        })
        self.assertEqual(out, {
            "path": "main.go",
            "target_file": "skill.md",
            "file_path": "note.ipynb",
            "notebook_path": "secret.ipynb",
        })

    def test_command_run_sanitises_command_and_cwd(self) -> None:
        out = sanitize.sanitize_payload("command_run", {
            "command": "git -c credential.helper='!echo CANARY' push",
            "cwd": "/srv/CANARY-proj/api",
            "exit_code": 0,
            "duration_ms": 1234,
            "output": "CANARY stdout",
            "error": "CANARY stderr",
        })
        self.assertEqual(out, {
            "command": "git push",
            "cwd": "api",
            "exit_code": 0,
            "duration_ms": 1234,
        })

    def test_state_change_keeps_state_area_and_drops_the_rest(self) -> None:
        out = sanitize.sanitize_payload("state_change", {
            "state": "thinking",
            "detail": "CANARY prompt text",
            "prompt": "CANARY",
            "session_id": "CANARY",
            "progress": 42,
        })
        self.assertEqual(out, {"state": "thinking"})
        out = sanitize.sanitize_payload("state_change", {
            "state": "waiting_for_user",
            "area": "coffee",
            "notification_type": "permission_required",
        })
        self.assertEqual(
            out,
            {"state": "waiting_for_user", "area": "coffee", "notification_type": "permission_required"},
        )

    def test_state_change_state_must_be_enum_shaped(self) -> None:
        self.assertEqual(sanitize.sanitize_payload("state_change", {"state": "CANARY state"}), {})
        self.assertEqual(sanitize.sanitize_payload("state_change", {"state": "../escape"}), {})
        self.assertEqual(sanitize.sanitize_payload("state_change", {"state": "Thinking"}), {})

    def test_session_events(self) -> None:
        out = sanitize.sanitize_payload("session_start", {
            "cwd": "/x/CANARY-project/backend",
            "source": "startup",
            "session_id": "CANARY",
            "engine_version": "2.0",
            "pid": 1234,
        })
        self.assertEqual(out, {"cwd": "backend", "source": "startup"})
        self.assertEqual(
            sanitize.sanitize_payload("session_end", {"reason": "logout", "detail": "CANARY"}),
            {"reason": "logout"},
        )

    def test_code_completion_drops_session_id_and_file_lists(self) -> None:
        out = sanitize.sanitize_payload("code_completion", {
            "session_id": "CANARY",
            "files_touched": ["/a/CANARY.py"],
            "edits": 12,
        })
        self.assertEqual(out, {})

    def test_background_events_keep_task_fields(self) -> None:
        out = sanitize.sanitize_payload("background_start", {
            "task_id": "ses_01ABC",
            "kind": "shell",
            "label": "sleep 60",
            "session_id": "CANARY",
        })
        self.assertEqual(out, {"task_id": "ses_01ABC", "kind": "shell", "label": "sleep"})
        out = sanitize.sanitize_payload("background_end", {
            "task_id": "ses_01ABC",
            "kind": "agent",
            "label": "npm run CANARY-secret-build",
            "status": "ok",
            "exit_code": 0,
        })
        self.assertEqual(
            out, {"task_id": "ses_01ABC", "kind": "agent", "label": "npm run", "status": "ok", "exit_code": 0}
        )

    def test_itsaplan_transition_keeps_only_label_fields(self) -> None:
        out = sanitize.sanitize_payload("itsaplan_issue_transition", {
            "issue": "CANARY-42",
            "title": "CANARY title",
            "kind": "board",
            "status": "ok",
            "session_id": "CANARY",
        })
        self.assertEqual(out, {"kind": "board", "status": "ok"})

    def test_unknown_event_type_keeps_common_labels_only(self) -> None:
        out = sanitize.sanitize_payload("widget_frobnicated", {
            "CANARY_key": {"nested": "CANARY"},
            "secret": "CANARY",
            "tool": "widget",
            "note": "CANARY free text",
        })
        self.assertEqual(out, {"tool": "widget"})

    def test_numeric_fields_reject_strings_and_junk(self) -> None:
        out = sanitize.sanitize_payload("command_run", {
            "command": "ls",
            "duration_ms": "1500",   # string, not a number -> dropped
            "exit_code": True,       # bool is not an exit code -> dropped
        })
        self.assertEqual(out, {"command": "ls"})
        out = sanitize.sanitize_payload("tool_call", {"tool": "Bash", "duration_ms": 10**12})
        self.assertEqual(out, {"tool": "Bash"})

    def test_status_must_be_enum_shaped(self) -> None:
        self.assertEqual(
            sanitize.sanitize_payload("tool_call", {"tool": "Bash", "status": "started"}),
            {"tool": "Bash", "status": "started"},
        )
        self.assertEqual(
            sanitize.sanitize_payload("tool_call", {
                "tool": "Bash",
                "status": "failed: could not connect to postgres://user:pw@host",
            }),
            {"tool": "Bash"},
        )

    def test_tool_name_is_capped_and_cleaned(self) -> None:
        out = sanitize.sanitize_payload("tool_call", {"tool": "mcp__slack__send_message"})
        self.assertEqual(out, {"tool": "mcp__slack__send_message"})
        out = sanitize.sanitize_payload("tool_call", {"tool": "T" * 300})
        self.assertLessEqual(len(out["tool"]), 120)
        self.assertNotIn("CANARY", out["tool"])

    def test_empty_after_sanitisation_drops_the_key(self) -> None:
        out = sanitize.sanitize_payload("file_edit", {"file": "/", "tool": "   "})
        self.assertEqual(out, {})

    def test_non_dict_payload_produces_empty_projection(self) -> None:
        for junk in (None, "CANARY", 5, ["CANARY"]):
            self.assertEqual(sanitize.sanitize_payload("tool_call", junk), {})


# ---------------------------------------------------------------------------
# Slice S2 (SOU-229): the canary sweep through the real Flask test client.
# ---------------------------------------------------------------------------
import shutil
import tempfile

try:
    import sqlalchemy as sa
    import flask  # noqa: F401  (the app under test is a Flask module)
    import alembic  # noqa: F401
    import jsonschema  # noqa: F401
except ImportError as exc:  # pragma: no cover - only hit without the extras
    sa = None
    _IMPORT_ERROR = str(exc)
else:
    _IMPORT_ERROR = None

app_mod = None
_ORIG_ENV: dict = {}


def setUpModule() -> None:
    """Same throwaway-database harness as tests/test_events_api.py."""
    global app_mod
    if sa is None:
        return
    _ORIG_ENV.update({k: os.environ.get(k) for k in ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")})
    workdir = tempfile.mkdtemp(prefix="sou218-module-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'module.db')}"
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    import app  # noqa: F401  (triggers the startup path the other suites test)

    app_mod = app


def tearDownModule() -> None:
    for key, value in _ORIG_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

CANARY = "CANARY"


def sweep_events(now_iso: str) -> list:
    """One envelope per event type, CANARY planted in every sensitive field.

    The canary sits where secrets sit in the wild: path *directories* (the
    basename is the allow-listed signal, the road to it is not), command
    flags and values, ``session_id`` / ``url`` / free-text keys, keys whose
    very *name* is the canary, nested dicts and lists under unknown keys.
    Allow-listed identifier fields carry clean realistic values — they are
    stored by design (ruling on the ticket: the sweep proves what is DROPPED
    and TRANSFORMED, not that allow-listed labels are scrubbed of content,
    which would contradict the ticket's own allow-list).
    """
    def env(event_type, payload):
        return {"version": 1, "agent": "sweep", "event_type": event_type,
                "timestamp": now_iso, "payload": payload}

    return [
        env("tool_call", {
            "tool": "Bash", "status": "started", "session_id": f"{CANARY}-sess",
            "detail": f"{CANARY} detail", "args": {"secret": CANARY},
            f"{CANARY}_key": {"nested": [CANARY, {"deep": CANARY}]},
        }),
        env("file_edit", {
            "tool": "Edit", "file": f"/home/dev/{CANARY}-project/app.py",
            "status": "ok", "duration_ms": 250, "session_id": f"{CANARY}-sess",
            "output": f"{CANARY} file contents", "note": CANARY,
        }),
        env("command_run", {
            "tool": "Bash",
            "command": f"git -c credential.helper='!echo {CANARY}' push https://{CANARY}.example/x",
            "cwd": f"/srv/{CANARY}-host/api", "status": "ok", "exit_code": 0,
            "duration_ms": 1234, "output": f"{CANARY} stdout", "error": f"{CANARY} stderr",
        }),
        env("state_change", {
            "state": "thinking", "detail": f"{CANARY} prompt text",
            "prompt": f"{CANARY} user said", "message": f"{CANARY} notification",
            "progress": 42,
        }),
        env("session_start", {
            "cwd": f"/x/{CANARY}-repo/backend", "source": "startup",
            "session_id": f"{CANARY}-sess", "engine_version": "2.0", "pid": 1234,
            "url": f"https://{CANARY}.example/boot",
        }),
        env("session_end", {
            "reason": "logout", "session_id": f"{CANARY}-sess",
            "detail": CANARY, "url": f"https://{CANARY}",
        }),
        env("code_completion", {
            "session_id": f"{CANARY}-sess", "files_touched": [f"/a/{CANARY}.py"],
            "edits": 12, "message": f"{CANARY} summary",
        }),
        env("itsaplan_issue_transition", {
            "issue": f"{CANARY}-42", "title": f"{CANARY} title",
            "kind": "board", "status": "ok", "session_id": f"{CANARY}-sess",
            "detail": f"{CANARY} body",
        }),
        env("background_start", {
            "task_id": "ses_01ABCdef", "kind": "shell", "label": f"sleep {CANARY}",
            "session_id": f"{CANARY}-sess",
        }),
        env("background_end", {
            "task_id": "ses_01ABCdef", "kind": "shell", "label": f"npm run {CANARY}-script",
            "status": "ok", "exit_code": 0,
            "error": f"{CANARY} tail",
        }),
        env("widget_frobnicated", {
            f"{CANARY}_key": {"nested": CANARY}, "secret": CANARY,
            "tool": "widget", "note": f"{CANARY} free text",
        }),
    ]


class CanarySweepTest(unittest.TestCase):
    """POST every event type through the real Flask client; nothing but the
    projection may land. (SOU-218 AC 1: a DB dump then leaks nothing.)"""

    def setUp(self) -> None:
        if sa is None:
            self.skipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        workdir = tempfile.mkdtemp(prefix="sou218-")
        os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
        self.workdir = workdir
        app_mod.db.shutdown()
        app_mod.init_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        app_mod.db.shutdown()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def post_all(self) -> None:
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        for event in sweep_events(now):
            resp = self.client.post("/events", json=event)
            self.assertEqual(resp.status_code, 201,
                             f"{event['event_type']}: {resp.get_json()}")

    def sweep_rows(self) -> list:
        with app_mod.db.session_scope() as session:
            sweep_id = next(a.id for a in app_mod.db.list_agents(session) if a.name == "sweep")
            return [r for r in app_mod.db.list_events(session, limit=200)
                    if r.agent_id == sweep_id]

    def test_sweep_posts_are_accepted_and_stored_as_projections(self) -> None:
        self.post_all()
        stored = {r.event_type: r.payload for r in self.sweep_rows()}
        # the safe signal survives...
        self.assertEqual(stored["command_run"], {
            "tool": "Bash", "command": "git push", "cwd": "api",
            "status": "ok", "exit_code": 0, "duration_ms": 1234,
        })
        self.assertEqual(stored["file_edit"], {
            "tool": "Edit", "file": "app.py", "status": "ok", "duration_ms": 250,
        })
        self.assertEqual(stored["state_change"], {"state": "thinking"})
        self.assertEqual(stored["background_end"], {
            "task_id": "ses_01ABCdef", "kind": "shell", "label": "npm run",
            "status": "ok", "exit_code": 0,
        })
        # ...and every sweep row is a dict we can audit
        for event_type, payload in stored.items():
            self.assertIsInstance(payload, dict, event_type)

    def test_no_canary_in_stored_payloads(self) -> None:
        self.post_all()
        for row in self.sweep_rows():
            blob = json.dumps(row.payload).lower()
            self.assertNotIn(CANARY.lower(), blob,
                             f"{CANARY} leaked into the {row.event_type} payload")

    def test_no_canary_in_the_raw_database_dump(self) -> None:
        """Even a straight dump of the SQLite files leaks nothing."""
        self.post_all()
        conn = sqlite3.connect(os.path.join(self.workdir, "app.db"))
        try:
            for table in ("events", "agents"):
                for row in conn.execute(f"SELECT * FROM {table}"):
                    blob = str(row).lower()
                    self.assertNotIn(CANARY.lower(), blob,
                                     f"{CANARY} leaked into the {table} table")
        finally:
            conn.close()

    def test_no_canary_in_the_read_endpoints(self) -> None:
        self.post_all()
        for path in ("/events/recent?limit=200", "/events/agents"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            text = resp.get_data(as_text=True).lower()
            self.assertNotIn(CANARY.lower(), text, f"{CANARY} served by GET {path}")

if __name__ == "__main__":
    unittest.main()