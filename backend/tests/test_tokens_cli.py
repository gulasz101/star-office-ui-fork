"""SOU-292: tokens.py against the database — register / set-room / list / revoke / import-env.

Each test gets a fresh SQLite database at head; the CLI runs in-process (``main``)
for the logic and once as a real subprocess for the end-to-end door check.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
sys.path.insert(0, BACKEND_DIR)

import tokens  # noqa: E402


def sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _DbCase(unittest.TestCase):
    def setUp(self) -> None:
        import db
        from app import init_database  # migrates a fresh DB to head

        self.db = db
        self.workdir = tempfile.mkdtemp(prefix="sou292-")
        self.url = f"sqlite:///{os.path.join(self.workdir, 'reg.db')}"
        self._env = mock.patch.dict(os.environ, {"DATABASE_URL": self.url,
                                                 "ALEMBIC_SKIP_FILECONFIG": "1"})
        self._env.start()
        db.shutdown()
        init_database()

    def tearDown(self) -> None:
        self.db.shutdown()
        self._env.stop()
        shutil.rmtree(self.workdir, ignore_errors=True)

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            code = tokens.main(list(argv), out=out)
        return code, out.getvalue(), err.getvalue()

    def agent_row(self, name):
        with self.db.session_scope() as s:
            row = self.db.find_agent_by_name(s, name)
            return None if row is None else (row.room_type, row.hoodie)


class RegisterTest(_DbCase):
    def test_register_creates_agent_with_personality_and_valid_token(self) -> None:
        code, out, _ = self.cli("register", "builder", "--room", "garage", "--hoodie", "200,0.8,0.9")
        self.assertEqual(code, 0)
        token = out.splitlines()[1].strip()
        self.assertRegex(token, r"^[A-Za-z0-9_-]{32,}$")
        self.assertEqual(out.count(token), 1, "plaintext printed exactly once")
        self.assertNotIn(sha(token), out, "the digest is not printed")
        self.assertEqual(self.agent_row("builder"), ("garage", {"h": 200, "s": 0.8, "v": 0.9}))
        with self.db.session_scope() as s:
            self.assertEqual(self.db.agent_for_token_hash(s, sha(token)).name, "builder")

    def test_token_only_prints_just_the_token(self) -> None:
        code, out, _ = self.cli("register", "builder", "--token-only")
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^[A-Za-z0-9_-]{32,}\n$")

    def test_second_register_adds_a_token_and_keeps_the_room(self) -> None:
        self.cli("register", "builder", "--room", "writer")
        self.cli("register", "builder")
        self.assertEqual(self.agent_row("builder")[0], "writer")
        with self.db.session_scope() as s:
            self.assertEqual(sum(self.db.active_token_counts(s).values()), 2)

    def test_bad_room_lists_valid_types(self) -> None:
        code, _, err = self.cli("register", "builder", "--room", "nope")
        self.assertEqual(code, 1)
        for room in tokens.ROOM_TYPES:
            self.assertIn(room, err)
        self.assertIsNone(self.agent_row("builder"), "nothing written on a bad flag")

    def test_bad_hoodie(self) -> None:
        for value in ("1,2", "a,b,c", "400,0.5,0.5"):
            with self.subTest(value=value):
                code, _, err = self.cli("register", "builder", "--hoodie", value)
                self.assertEqual(code, 1)
                self.assertIn("--hoodie", err)


class SetRoomListRevokeTest(_DbCase):
    def test_set_room_without_token(self) -> None:
        code, out, _ = self.cli("set-room", "hermes-x", "--room", "serverroom", "--hoodie", "125,0.8,0.8")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.agent_row("hermes-x"), ("serverroom", {"h": 125, "s": 0.8, "v": 0.8}))
        with self.db.session_scope() as s:
            self.assertEqual(self.db.active_token_counts(s), {})

    def test_set_room_needs_room(self) -> None:
        self.assertEqual(self.cli("set-room", "x")[0], 1)

    def test_list_shows_no_secret(self) -> None:
        _, out, _ = self.cli("register", "builder", "--room", "garage")
        token = out.splitlines()[1].strip()
        code, listing, _ = self.cli("list")
        self.assertEqual(code, 0)
        self.assertIn("builder", listing)
        self.assertIn("garage", listing)
        self.assertNotIn(token, listing)
        self.assertIsNone(re.search(r"[0-9a-f]{64}", listing), "no digest in list output")

    def test_revoke(self) -> None:
        _, out, _ = self.cli("register", "builder")
        token = out.splitlines()[1].strip()
        code, msg, _ = self.cli("revoke", "builder")
        self.assertEqual(code, 0)
        self.assertIn("revoked 1", msg)
        with self.db.session_scope() as s:
            self.assertIsNone(self.db.agent_for_token_hash(s, sha(token)))

    def test_revoke_unknown_agent(self) -> None:
        self.assertEqual(self.cli("revoke", "ghost")[0], 1)


class ImportEnvTest(_DbCase):
    def test_import_twice(self) -> None:
        env = f"{sha('tok-a')}:alpha,{sha('tok-b')}:beta"
        with mock.patch.dict(os.environ, {"SOU_INGEST_TOKENS": env}):
            code, out, _ = self.cli("import-env")
            self.assertEqual(code, 0)
            self.assertIn("imported 2", out)
            code, out, _ = self.cli("import-env")
            self.assertIn("imported 0", out)
        with self.db.session_scope() as s:
            self.assertEqual(self.db.agent_for_token_hash(s, sha("tok-a")).name, "alpha")
            self.assertEqual(self.db.agent_for_token_hash(s, sha("tok-b")).name, "beta")

    def test_import_without_env(self) -> None:
        with mock.patch.dict(os.environ, {"SOU_INGEST_TOKENS": ""}):
            self.assertEqual(self.cli("import-env")[0], 1)


class RoomTypesPinnedTest(unittest.TestCase):
    def test_room_types_match_the_renderer(self) -> None:
        with open(os.path.join(REPO_ROOT, "frontend", "v3", "src", "rooms.js"), encoding="utf-8") as fh:
            js = fh.read()
        match = re.search(r"export const ROOM_TYPES = \[([^\]]*)\]", js)
        self.assertIsNotNone(match)
        renderer = tuple(re.findall(r"'([a-z]+)'", match.group(1)))
        self.assertEqual(tokens.ROOM_TYPES, renderer)


class EndToEndTest(unittest.TestCase):
    """A real subprocess run of the CLI, then that token through the locked door."""

    def test_registered_token_opens_the_ingest_door(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou292-e2e-")
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{os.path.join(workdir, 'e2e.db')}",
                   ALEMBIC_SKIP_FILECONFIG="1")
        env.pop("SOU_INGEST_TOKENS", None)
        try:
            subprocess.run([sys.executable, "-c", "import app"], cwd=BACKEND_DIR, env=env,
                           check=True, capture_output=True, timeout=120)
            proc = subprocess.run([sys.executable, "tokens.py", "register", "builder", "--token-only"],
                                  cwd=BACKEND_DIR, env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            token = proc.stdout.strip()
            probe = (
                "import app, json\n"
                "c = app.app.test_client()\n"
                "ev = {'version':1,'agent':'builder','event_type':'tool_call',"
                "'timestamp':'2026-10-18T10:00:00Z','payload':{'tool':'Edit','status':'ok'}}\n"
                f"print(c.post('/events', json=ev, headers={{'Authorization':'Bearer {token}'}}).status_code,"
                " c.post('/events', json=ev).status_code)\n"
            )
            run = subprocess.run([sys.executable, "-c", probe], cwd=BACKEND_DIR,
                                 env=dict(env, SOU_MODE="ingest"), capture_output=True, text=True, timeout=120)
            self.assertEqual(run.returncode, 0, run.stderr[-800:])
            self.assertEqual(run.stdout.split()[-2:], ["201", "401"])
            self.assertIn("tokens: 1 registered in the database", run.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
