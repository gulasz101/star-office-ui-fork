"""English-only UI contract (SOU-79, Phase 7).

Machine gate for the fork's product decision: the dashboard is English-only,
with no language switcher and no memo feature. These tests are the RED-first
contract that SOU-81 introduces and SOU-82..SOU-87 turn green:

* ``scripts/check_english_only.py`` (committed checker) must exit 0 — every
  tracked text file outside ``docs/i18n/**`` and ``dist/**`` is ASCII-safe
  (AC1/AC2/AC3 proof by exhaustive character sweep).
* The memo feature must be gone: no ``backend/memo_utils.py``, no
  ``/yesterday-memo`` route, no ``frontend/memo-bg.webp`` (AC4).
* A fresh instance answers ``GET /status`` with ``state == "idle"`` and
  ``detail == "Idle"`` byte-exact — the acceptance-criteria string (AC3).
* The served dashboard HTML contains no CJK characters and no language
  switcher markup (AC1/AC2).

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_english_only.py -v

Harness follows ``tests/test_events_api.py``: one ``app`` import against a
throwaway ``DATABASE_URL``; per-test ``fresh_database()`` re-points at a new
SQLite file and re-seeds. Skips cleanly when the runtime extras are absent.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
CHECKER = os.path.join(REPO_ROOT, "scripts", "check_english_only.py")

CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")

try:
    import sqlalchemy as sa
    import flask  # noqa: F401
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
    global app_mod
    if sa is None:
        return
    _ORIG_ENV.update({k: os.environ.get(k) for k in ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")})
    workdir = tempfile.mkdtemp(prefix="sou79-module-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'module.db')}"
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    sys.path.insert(0, BACKEND_DIR)
    import app  # noqa: F401

    app_mod = app


def tearDownModule() -> None:
    for key, value in _ORIG_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def fresh_database() -> str:
    workdir = tempfile.mkdtemp(prefix="sou79-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
    app_mod.db.shutdown()
    app_mod.init_database()
    return workdir


@unittest.skipIf(sa is None, f"runtime extras missing: {_IMPORT_ERROR}")
class EnglishOnlyContract(unittest.TestCase):
    def setUp(self) -> None:
        self._workdir = fresh_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self._workdir, ignore_errors=True)

    # --- AC1/AC2/AC3: exhaustive sweep via the committed checker ------------
    def test_checker_gate_passes_repo_wide(self) -> None:
        proc = subprocess.run(
            [sys.executable, CHECKER, "-q"], capture_output=True, text=True, cwd=REPO_ROOT
        )
        self.assertEqual(
            proc.returncode, 0, msg=f"english-only checker reported:\n{proc.stdout}{proc.stderr}"
        )

    def test_served_index_has_no_cjk_and_no_switcher(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        found = CJK_RE.findall(html)
        self.assertFalse(found, f"served / contains {len(found)} non-English chars")
        self.assertNotIn("lang-toggle-group", html)
        self.assertNotIn("setUILanguage", html)

    # --- AC3: the literal byte-exact proof string ---------------------------
    def test_fresh_status_is_idle_in_english(self) -> None:
        resp = self.client.get("/status")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["state"], "idle")
        self.assertEqual(body["detail"], "Idle")
        self.assertNotRegex(resp.get_data(as_text=True), r"\\u[0-9a-fA-F]{4}")

    def test_default_state_and_seed_strings_are_english(self) -> None:
        self.assertEqual(app_mod.DEFAULT_STATE["detail"], "Idle")
        star = app_mod.DEFAULT_AGENTS[0]
        self.assertEqual(star["detail"], "Idle")
        for value in (app_mod.DEFAULT_STATE["detail"], star["detail"], star["name"]):
            self.assertFalse(CJK_RE.search(value), f"CJK in seed: {value!r}")

    # --- AC4: memo feature fully gone ---------------------------------------
    def test_memo_files_absent(self) -> None:
        for rel in ("backend/memo_utils.py", "frontend/memo-bg.webp"):
            full = os.path.join(REPO_ROOT, rel)
            self.assertFalse(os.path.exists(full), f"{rel} must not exist (AC4)")
        self.assertFalse(
            any(
                os.path.basename(root) == "__pycache__" and "memo_utils" in f
                for root, _, files in os.walk(os.path.join(REPO_ROOT, "backend"))
                for f in files
                if f.endswith(".py")  # source, not .pyc
            )
        )

    def test_memo_route_not_registered(self) -> None:
        rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
        self.assertNotIn("/yesterday-memo", rules)

    def test_memo_gone_from_smoke_script(self) -> None:
        with open(os.path.join(REPO_ROOT, "scripts", "smoke_test.py"), encoding="utf-8") as fh:
            self.assertNotIn("yesterday-memo", fh.read())

    # --- AC6 guard: Gemini stays (removal here would be the real regression)
    def test_gemini_config_routes_remain(self) -> None:
        rules = {r.rule for r in app_mod.app.url_map.iter_rules()}
        self.assertIn("/config/gemini", rules)
        self.assertIn("/assets/generate-rpg-background", rules)


if __name__ == "__main__":
    unittest.main()
