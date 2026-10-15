"""Publishing contract for generated room packs (SOU-115 T1).

Layering the fork committed to (see parent issue SOU-115):

  * ``assets/generated/rooms/<agent>/`` is the SOURCE OF TRUTH. Generators
    (``scripts/gen_room_set.py``, ``scripts/finish_room_props.py``) write it;
    it is never served.
  * ``frontend/rooms/`` is the SERVED COPY, produced ONLY by
    ``scripts/publish_room.py``. Flask serves ``frontend/`` at ``/static``,
    so a published room answers at ``/static/rooms/...``.

These tests pin the *contract the renderer loader is allowed to rely on*:
the discovery index, the normalized room.json (floor/target_tiles/wall plus
per-prop base_slopes and gate passthrough), byte-identical prop PNGs, and
HTTP 200 through Flask itself (not just on-disk files). Geometry stays the
declared lattice of ADR-065 - the publisher renames fields, it never
re-derives anything from the background image.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_room_pack_publish.py -q

Same import/skip convention as the rest of the suite.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(BACKEND_DIR)
PUBLISHER = os.path.join(REPO_DIR, "scripts", "publish_room.py")
GENERATED = os.path.join(REPO_DIR, "assets", "generated", "rooms")
SERVED = os.path.join(REPO_DIR, "frontend", "rooms")

try:
    import sqlalchemy as sa  # noqa: F401
    import flask  # noqa: F401
    import alembic  # noqa: F401
except ImportError as exc:  # pragma: no cover
    sa = None
    _IMPORT_ERROR = str(exc)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _source_agents():
    out = []
    if os.path.isdir(GENERATED):
        for agent in sorted(os.listdir(GENERATED)):
            if os.path.exists(os.path.join(GENERATED, agent, "room.json")):
                out.append(agent)
    return out


def _run_publisher(cwd=REPO_DIR):
    env = dict(os.environ)
    site = os.path.join(REPO_DIR, ".tooling", "site")
    if os.path.isdir(site):
        # Absolute path: a relative PYTHONPATH breaks when the subprocess cwd
        # is not backend/ (learned the hard way in SOU-104).
        env["PYTHONPATH"] = site + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, PUBLISHER],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )
    return proc


class RoomPackPublishTests(unittest.TestCase):
    """scripts/publish_room.py must produce the served contract from the
    real source-of-truth tree, deterministically and idempotently."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        cls.agents = _source_agents()
        if not cls.agents:
            raise unittest.SkipTest("no generated room packs on disk")
        cls.proc = _run_publisher()
        assert cls.proc.returncode == 0, (
            f"publish_room.py failed:\n{cls.proc.stdout}\n{cls.proc.stderr}")

    def test_index_json_contract(self):
        """AC1 discovery shape, exactly as the issue states it."""
        path = os.path.join(SERVED, "index.json")
        self.assertTrue(os.path.exists(path), "frontend/rooms/index.json missing")
        with open(path) as fh:
            index = json.load(fh)
        self.assertIn("rooms", index)
        for agent in self.agents:
            with self.subTest(agent=agent):
                entry = index["rooms"][agent]
                self.assertEqual(entry["room"], f"rooms/{agent}/room.json")
                self.assertEqual(entry["props"], f"rooms/{agent}/props/")
                with open(os.path.join(GENERATED, agent, "room.json")) as fh:
                    src = json.load(fh)
                self.assertEqual(entry["prop_count"], len(src["prop_anchors"]))

    def test_published_room_json_is_normalized_contract(self):
        """floor <- lattice, wall <- walls, target_tiles <- tiles, plus
        base_slopes/gate merged from the per-prop manifests."""
        for agent in self.agents:
            with self.subTest(agent=agent):
                with open(os.path.join(GENERATED, agent, "room.json")) as fh:
                    src = json.load(fh)
                with open(os.path.join(SERVED, agent, "room.json")) as fh:
                    pub = json.load(fh)
                self.assertEqual(pub["canvas"], src["canvas"])
                self.assertIn("projection", pub)
                # renamed blocks, values copied verbatim (no re-derivation)
                self.assertEqual(pub["floor"]["origin"], src["lattice"]["origin"])
                self.assertEqual(pub["floor"]["tile_w"], src["lattice"]["tile_w"])
                self.assertEqual(pub["floor"]["tile_h"], src["lattice"]["tile_h"])
                self.assertEqual(pub["wall"]["rise_px"], src["walls"]["rise_px"])
                self.assertEqual(len(pub["wall"]["planes"]), 2)
                # anchors
                self.assertEqual(len(pub["prop_anchors"]), len(src["prop_anchors"]))
                for s_a, p_a in zip(src["prop_anchors"], pub["prop_anchors"]):
                    self.assertEqual(p_a["id"], s_a["id"])
                    self.assertEqual(p_a["file"], s_a["file"])
                    self.assertEqual(p_a["grid"], s_a["grid"])
                    self.assertEqual(p_a["target_tiles"], s_a["tiles"])
                    manifest = os.path.join(
                        GENERATED, agent, "props",
                        os.path.basename(s_a["file"]).replace(".png", ".manifest.json"))
                    with open(manifest) as fh:
                        man = json.load(fh)
                    self.assertEqual(p_a["base_slopes"], man["base_slopes"])
                    if "gate" in man:
                        self.assertEqual(p_a["gate"], man["gate"])

    def test_served_props_are_byte_identical_and_minimal(self):
        for agent in self.agents:
            props_dir = os.path.join(SERVED, agent, "props")
            with self.subTest(agent=agent):
                listed = sorted(os.listdir(props_dir))
                self.assertEqual(listed, sorted([
                    os.path.basename(a["file"]) for a in json.load(open(
                        os.path.join(GENERATED, agent, "room.json")))["prop_anchors"]
                ]), "served props must be exactly the anchored props (no raw/rejected)")
                for name in listed:
                    self.assertEqual(
                        _sha(os.path.join(GENERATED, agent, "props", name)),
                        _sha(os.path.join(props_dir, name)),
                        f"{name} must be served byte-identical to the source of truth")
                self.assertFalse(os.path.exists(os.path.join(props_dir, "rejected")))

    def test_preview_copied(self):
        for agent in self.agents:
            src_preview = os.path.join(GENERATED, agent, "room_preview.png")
            if os.path.exists(src_preview):
                self.assertEqual(_sha(src_preview),
                                 _sha(os.path.join(SERVED, agent, "room_preview.png")))

    def test_idempotent_byte_stable(self):
        before = {}
        for root, _dirs, files in os.walk(SERVED):
            for f in files:
                p = os.path.join(root, f)
                before[p] = _sha(p)
        proc = _run_publisher()
        self.assertEqual(proc.returncode, 0, proc.stderr)
        after = {}
        for root, _dirs, files in os.walk(SERVED):
            for f in files:
                p = os.path.join(root, f)
                after[p] = _sha(p)
        self.assertEqual(before, after, "re-publishing must be byte-stable")


class RoomPackServedOverHttp(unittest.TestCase):
    """The contract is only real if the app itself serves it (AC2):
    /static/rooms/** must answer 200 from Flask, not just exist on disk."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        cls.agents = _source_agents()
        if not cls.agents:
            raise unittest.SkipTest("no generated room packs on disk")
        if not os.path.isdir(os.path.join(SERVED, cls.agents[0])):
            _run_publisher()
        cls._tmp = tempfile.mkdtemp(prefix="sou115-")
        cls._orig = {k: os.environ.get(k) for k in
                     ("DATABASE_URL", "ALEMBIC_SKIP_FILECONFIG")}
        os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(cls._tmp, "evidence.db")
        os.environ["ALEMBIC_SKIP_FILECONFIG"] = "1"
        sys.path.insert(0, BACKEND_DIR)
        import app as app_mod  # noqa: F401  (import runs the startup path)
        cls.client = app_mod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        for k, v in getattr(cls, "_orig", {}).items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        sys.path.remove(BACKEND_DIR)
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_static_room_paths_200(self):
        for agent in self.agents:
            with self.subTest(agent=agent):
                r = self.client.get("/static/rooms/index.json")
                self.assertEqual(r.status_code, 200)
                r = self.client.get(f"/static/rooms/{agent}/room.json")
                self.assertEqual(r.status_code, 200)
                pub = r.get_json()
                for a in pub["prop_anchors"]:
                    r = self.client.get(f"/static/rooms/{agent}/{a['file']}")
                    self.assertEqual(r.status_code, 200, a["file"])
                    self.assertEqual(r.headers.get("Content-Type"), "image/png")
                r = self.client.get(f"/static/rooms/{agent}/room_preview.png")
                self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
