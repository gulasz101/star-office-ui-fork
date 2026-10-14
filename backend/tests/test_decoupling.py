"""OpenClaw decoupling contract (SOU-46, Phase 4 — ADR-036 ruthless divergence).

The office must speak one agent-agnostic protocol: agents POST their own
events (``POST /events``), nobody ships OpenClaw-shaped clients, and the
backend never reaches into an agent's private workspace for files like
``IDENTITY.md`` or configuration like ``OPENCLAW_WORKSPACE``.

These tests pin the decoupled shape BEFORE the code changes (SOU-46 AC1):

* ``backend/app.py`` has no ``OPENCLAW_WORKSPACE`` / ``IDENTITY_FILE``
  module attributes and no ``get_office_name_from_identity`` — the office
  name is a plain ``OFFICE_NAME`` env var, read from the environment at
  request time (no file reading at all).
* ``GET /status`` never leaks an office name sourced from an
  ``IDENTITY.md`` sitting in an OpenClaw workspace, and does serve
  ``officeName`` when ``OFFICE_NAME`` is set.
* The roster source marker is ``remote-push``, not ``remote-openclaw``.
* No ``/openclaw/*`` route exists anywhere in the url map (regression
  lock: the upstream app never grew one, the AC asks to remove any).
* The OpenClaw-era files ``SKILL.md``, ``set_state.py`` and
  ``office-agent-push.py`` (root *and* the ``/static`` frontend copy)
  are gone from the tracked tree.
* No live frontend file mentions ``openclaw`` (case-insensitive).

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_decoupling.py -v

Same harness as ``tests/test_events_read.py``: one import against a
throwaway ``DATABASE_URL``, then ``fresh_database()`` per test.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)

try:
    import sqlalchemy as sa  # noqa: F401  (skip-gate only)
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

# Env vars the decoupled backend owns the name-space of; nothing from an
# agent's private workspace may be consulted.
_ENV_KEYS = ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG", "OFFICE_NAME", "OPENCLAW_WORKSPACE")

# Files that must no longer exist in the tracked tree (SOU-46 AC1).
DEAD_FILES = (
    "SKILL.md",
    "set_state.py",
    "office-agent-push.py",
    os.path.join("frontend", "office-agent-push.py"),
)


def setUpModule() -> None:
    global app_mod
    if sa is None:
        return
    _ORIG_ENV.update({k: os.environ.get(k) for k in _ENV_KEYS})
    workdir = tempfile.mkdtemp(prefix="sou46-module-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'module.db')}"
    os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
    os.environ.pop("OFFICE_NAME", None)
    os.environ.pop("OPENCLAW_WORKSPACE", None)
    import app  # noqa: F401

    app_mod = app


def tearDownModule() -> None:
    for key, value in _ORIG_ENV.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def fresh_database() -> str:
    """Point the imported app at a brand-new SQLite file and re-seed it."""
    workdir = tempfile.mkdtemp(prefix="sou46-")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(workdir, 'app.db')}"
    app_mod.db.shutdown()
    app_mod.init_database()
    return workdir


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class NoOpenClawInternalsTest(unittest.TestCase):
    """AC1: no OpenClaw-workspace reading logic left in backend/app.py."""

    def test_app_module_has_no_openclaw_or_identity_internals(self) -> None:
        for attr in ("OPENCLAW_WORKSPACE", "IDENTITY_FILE"):
            self.assertFalse(
                hasattr(app_mod, attr),
                f"app still defines {attr}: the backend must not know an agent's workspace layout",
            )
        self.assertFalse(
            hasattr(app_mod, "get_office_name_from_identity"),
            "get_office_name_from_identity must be deleted: the office name is env, not a file read",
        )

    def test_source_marker_is_not_openclaw(self) -> None:
        import inspect

        src = inspect.getsource(app_mod)
        self.assertNotIn("remote-openclaw", src)
        self.assertNotIn("openclaw", src.lower())

    def test_no_openclaw_routes_in_url_map(self) -> None:
        rules = [str(r) for r in app_mod.app.url_map.iter_rules()]
        self.assertFalse(
            [r for r in rules if "openclaw" in r.lower()],
            f"found /openclaw/* endpoints: {rules}",
        )


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class StatusOfficeNameTest(unittest.TestCase):
    """AC1: /status ignores any IDENTITY.md; OFFICE_NAME env is the only source."""

    def setUp(self) -> None:
        self.workdir = fresh_database()
        self.client = app_mod.app.test_client()

    def tearDown(self) -> None:
        shutil.rmtree(self.workdir, ignore_errors=True)
        os.environ.pop("OFFICE_NAME", None)
        os.environ.pop("OPENCLAW_WORKSPACE", None)

    def test_identity_md_in_an_openclaw_workspace_is_never_read(self) -> None:
        # Plant an IDENTITY.md and point *every* legacy handle at it: the
        # env var the old code read at import time, and the module constant
        # the old get_office_name_from_identity opened per request. Today
        # this leaks "GhostInMachine的办公室" into /status (the RED we watch
        # fail); decoupled, the backend reads no files and this is inert.
        workspace = os.path.join(self.workdir, "fake-openclaw-workspace")
        os.makedirs(workspace, exist_ok=True)
        identity = os.path.join(workspace, "IDENTITY.md")
        with open(identity, "w", encoding="utf-8") as f:
            f.write("# IDENTITY.md\n- **Name:** GhostInMachine\n")
        os.environ["OPENCLAW_WORKSPACE"] = workspace
        sentinel = object()
        orig = getattr(app_mod, "IDENTITY_FILE", sentinel)
        setattr(app_mod, "IDENTITY_FILE", identity)
        try:
            data = self.client.get("/status").get_json()
        finally:
            if orig is sentinel:
                try:
                    del app_mod.IDENTITY_FILE
                except AttributeError:
                    pass
            else:
                app_mod.IDENTITY_FILE = orig
        self.assertNotIn(
            "officeName",
            data,
            "/status still resolves officeName from an IDENTITY.md file",
        )
        self.assertNotIn("GhostInMachine", str(data))

    def test_office_name_env_is_served_when_set(self) -> None:
        os.environ["OFFICE_NAME"] = "Wojtek's Office"
        data = self.client.get("/status").get_json()
        self.assertEqual(data.get("officeName"), "Wojtek's Office")

    def test_no_office_name_key_when_env_unset(self) -> None:
        data = self.client.get("/status").get_json()
        self.assertNotIn("officeName", data)


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class DeadFilesGoneTest(unittest.TestCase):
    """AC1: the OpenClaw-era client files are deleted from the repo."""

    def test_openclaw_era_files_removed(self) -> None:
        for rel in DEAD_FILES:
            self.assertFalse(
                os.path.exists(os.path.join(REPO_ROOT, rel)),
                f"{rel} must be deleted (SOU-46: no OpenClaw-specific code or workflows)",
            )


@unittest.skipIf(sa is None, f"runtime extras not installed: {_IMPORT_ERROR}")
class FrontendMentionsNoOpenClawTest(unittest.TestCase):
    """AC3: no OpenClaw-specific UI element text survives in the live frontend."""

    LIVE_FRONTEND = (
        "game.js",
        "index.html",
        "invite.html",
        "join.html",
        "electron-standalone.html",
    )

    def test_frontend_free_of_openclaw(self) -> None:
        hits = []
        for name in self.LIVE_FRONTEND:
            path = os.path.join(REPO_ROOT, "frontend", name)
            if not os.path.exists(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                if "openclaw" in f.read().lower():
                    hits.append(name)
        self.assertEqual(hits, [], f"frontend files still mentioning OpenClaw: {hits}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
