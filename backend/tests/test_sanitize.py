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


if __name__ == "__main__":
    unittest.main()
