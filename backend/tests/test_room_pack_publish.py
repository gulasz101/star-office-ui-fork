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
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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

try:
    from PIL import Image  # the temp packs need real PNG bytes, nothing else
    _IMAGE_ERROR = None
except ImportError as exc:  # pragma: no cover
    Image = None
    _IMAGE_ERROR = str(exc)


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _publisher_pointed_at(gen_dir, served_dir):
    """Load scripts/publish_room.py with its two tree constants aimed at temp
    dirs, so schema routing can be tested without touching the served tree."""
    spec = importlib.util.spec_from_file_location("publish_room_tmp", PUBLISHER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["publish_room_tmp"] = mod
    spec.loader.exec_module(mod)
    mod.GEN = Path(gen_dir)
    mod.SERVED = Path(served_dir)
    return mod


def _png(path, size=(96, 55)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (10, 20, 30, 255)).save(path)


def _write_v1_pack(dirpath):
    """A minimal schema-1 source pack (what the v1 generators emit)."""
    dirpath.mkdir(parents=True, exist_ok=True)
    _png(dirpath / "props" / "desk.png")
    (dirpath / "props" / "desk.manifest.json").write_text(json.dumps(
        {"base_slopes": {"left": 0.577, "right": -0.577},
         "gate": {"status": "pass", "tolerance": 0.05}}, indent=2) + "\n")
    _png(dirpath / "room_preview.png", (320, 180))
    (dirpath / "room.json").write_text(json.dumps({
        "schema": 1,
        "agent_id": "alpha",
        "display_name": "Alpha",
        "canvas": {"w": 1280, "h": 720},
        "projection": "true isometric",
        "wall_seam_y": 250,
        "lattice": {"origin": [640, 250], "tile_w": 96, "tile_h": 55},
        "walls": {"planes": ["north-east", "north-west"], "rise_px": 250,
                  "baseboard_px": 14},
        "preview": "room_preview.png",
        "prop_anchors": [{"id": "desk", "file": "props/desk.png",
                          "grid": [3, 3], "tiles": 2.0}],
    }, indent=2) + "\n")


def _write_v2_pack(dirpath):
    """A schema-2 descriptor (the SOU-123 shape): grid coordinates only, no
    prop_anchors and nothing the v1 publisher can normalize."""
    dirpath.mkdir(parents=True, exist_ok=True)
    (dirpath / "room.json").write_text(json.dumps({
        "schema": 2,
        "room_id": "beta",
        "canvas": {"w": 1280, "h": 720},
        "lattice": {"origin": [640, 250], "tile_w": 96, "tile_h": 55,
                    "slope": 55 / 96},
        "map": "room.map.json",
        "rooms": [],
        "props": [],
        "anchors": [],
        "zones": [],
        "doors": [],
        "walls": {"image": "walls.png", "origin": [56, -8]},
    }, indent=2) + "\n")


def _pack_schema(agent_dir):
    room = os.path.join(agent_dir, "room.json")
    if not os.path.exists(room):
        return 0
    with open(room) as fh:
        return int(json.load(fh).get("schema", 1))


def _agents_of_schema(want):
    """Generated packs speaking a given contract.

    ``1`` = the v1 pixel+lattice contract the SERVED tree and this suite
    normalize; ``2`` = the grid-native renderer-v2 descriptor of SOU-123, which
    the v1 publisher deliberately skips (Ruling 1) - covered by
    ``test_schema_two_pack_is_skipped_and_v1_serving_survives``.
    """
    out = []
    if os.path.isdir(GENERATED):
        for agent in sorted(os.listdir(GENERATED)):
            if _pack_schema(os.path.join(GENERATED, agent)) == want:
                out.append(agent)
    return out


def _source_agents():
    return _agents_of_schema(1)


def _env_with_site():
    env = dict(os.environ)
    site = os.path.join(REPO_DIR, ".tooling", "site")
    if os.path.isdir(site):
        # Absolute path: a relative PYTHONPATH breaks when the subprocess cwd
        # is not backend/ (learned the hard way in SOU-104).
        env["PYTHONPATH"] = site + os.pathsep + env.get("PYTHONPATH", "")
    return env


def _run_publisher(cwd=REPO_DIR, extra_args=()):
    return subprocess.run(
        [sys.executable, PUBLISHER, *extra_args],
        cwd=cwd, env=_env_with_site(), capture_output=True, text=True, timeout=120,
    )


class RoomPackPublishTests(unittest.TestCase):
    """scripts/publish_room.py must produce the served contract from the
    real source-of-truth tree, deterministically and idempotently."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        cls.agents = _source_agents()
        if not cls.agents:
            # A schema-2 pack (SOU-123) is not publishable by design, so the
            # v1 contract tests have nothing to read here; the skip case is
            # covered by SchemaTwoCoexistenceTests / PublisherSchemaRoutingTests.
            raise unittest.SkipTest("no schema-1 pack in the generated tree")
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


class SchemaTwoCoexistenceTests(unittest.TestCase):
    """SOU-123 Ruling 1 on the real tree: the v2 descriptor replaced
    ``assets/generated/rooms/<agent>/room.json``, and v1 must keep serving
    byte-identically until SOU-128 replaces the renderer."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        cls.v2 = [a for a in _agents_of_schema(2)
                  if os.path.exists(os.path.join(SERVED, a, "room.json"))]
        if not cls.v2:
            self_skip = "no schema-2 pack with a served v1 copy on disk"
            raise unittest.SkipTest(self_skip)

    def test_schema_two_pack_is_skipped_and_v1_serving_survives(self):
        """SOU-123 Ruling 1: a v2 descriptor may replace a source pack, but v1
        keeps serving until SOU-128.

        The publisher must (a) not try to normalize a schema-2 pack, (b) keep
        the served entry and files of that agent byte-identical, and (c) stay
        idempotent - a second run changes nothing in the served tree.
        """
        v2 = self.v2
        index_path = os.path.join(SERVED, "index.json")
        with open(index_path) as fh:
            index_before = json.load(fh)
        room_shas = {}
        for agent in v2:
            served_room = os.path.join(SERVED, agent, "room.json")
            if not os.path.exists(served_room):
                self.skipTest(f"{agent} is schema 2 but has no served v1 copy")
            room_shas[agent] = _sha(served_room)
            self.assertIn(agent, index_before["rooms"],
                          f"{agent}: a served entry must be carried over, not dropped")

        proc = _run_publisher()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("schema 2", proc.stdout,
                      "the publisher must say which packs it skipped and why")
        for agent, sha_before in room_shas.items():
            self.assertEqual(_sha(os.path.join(SERVED, agent, "room.json")), sha_before,
                             f"{agent}: v1 served room.json was rewritten")
        with open(index_path) as fh:
            index_after = json.load(fh)
        self.assertEqual(index_before, index_after,
                         "carried-over v1 index entries must survive byte-for-byte")

        check = _run_publisher(extra_args=["--check"])
        self.assertEqual(check.returncode, 0, check.stdout + check.stderr)



class PublisherSchemaRoutingTests(unittest.TestCase):
    """Routing of both schemas, on a temp tree, so the v1 publisher keeps a
    live gate even when the repo's only pack has moved to schema 2."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        if Image is None:
            raise unittest.SkipTest(f"Pillow missing: {_IMAGE_ERROR}")
        cls.tmp = tempfile.TemporaryDirectory(prefix="sou132-")
        root = Path(cls.tmp.name)
        cls.gen, cls.served = root / "generated", root / "served"
        cls.mod = _publisher_pointed_at(cls.gen, cls.served)
        _write_v1_pack(cls.gen / "alpha")
        _write_v2_pack(cls.gen / "beta")
        # beta was published while it was still schema 1: its served copy and
        # index entry are what SOU-128 must not lose on the way out.
        cls.mod.SERVED.mkdir(parents=True, exist_ok=True)
        (cls.mod.SERVED / "beta").mkdir(parents=True, exist_ok=True)
        (cls.mod.SERVED / "beta" / "room.json").write_text('{"v1": "served copy"}\n')
        cls.mod.build_index({"beta": {"room": "rooms/beta/room.json",
                                      "props": "rooms/beta/props/",
                                      "prop_count": 1}})
        cls.rc = cls.mod.main([])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_publisher_returns_zero_and_publishes_only_schema_one(self):
        self.assertEqual(self.rc, 0)
        self.assertTrue((self.served / "alpha" / "room.json").exists(),
                        "the schema-1 pack must still be published")
        self.assertEqual((self.served / "beta" / "room.json").read_text(),
                         '{"v1": "served copy"}\n',
                         "a schema-2 pack may not touch its served copy")

    def test_index_carries_the_schema_two_entry_over(self):
        index = json.loads((self.served / "index.json").read_text())
        self.assertEqual(index["rooms"]["alpha"],
                         {"room": "rooms/alpha/room.json",
                          "props": "rooms/alpha/props/", "prop_count": 1})
        self.assertEqual(index["rooms"]["beta"],
                         {"room": "rooms/beta/room.json",
                          "props": "rooms/beta/props/", "prop_count": 1},
                         "the carried-over v1 entry must be verbatim")

    def test_v1_normalization_still_works(self):
        pub = json.loads((self.served / "alpha" / "room.json").read_text())
        src = json.loads((self.gen / "alpha" / "room.json").read_text())
        self.assertEqual(pub["floor"]["origin"], src["lattice"]["origin"])
        self.assertEqual(pub["floor"]["tile_w"], src["lattice"]["tile_w"])
        self.assertEqual(len(pub["wall"]["planes"]), 2)
        self.assertEqual(pub["prop_anchors"][0]["target_tiles"],
                         src["prop_anchors"][0]["tiles"])
        self.assertEqual(pub["prop_anchors"][0]["base_slopes"],
                         {"left": 0.577, "right": -0.577})

    def test_check_mode_tolerates_a_schema_two_pack(self):
        self.assertEqual(self.mod.main(["--check"]), 0,
                         "--check must not report drift for a skipped pack")


class RoomPackServedOverHttp(unittest.TestCase):
    """The contract is only real if the app itself serves it (AC2):
    /static/rooms/** must answer 200 from Flask, not just exist on disk."""

    @classmethod
    def setUpClass(cls):
        if sa is None:
            raise unittest.SkipTest(f"runtime extras missing: {_IMPORT_ERROR}")
        cls.agents = _source_agents()
        if not cls.agents:
            # A schema-2 pack (SOU-123) is not publishable by design, so the
            # v1 contract tests have nothing to read here; the skip case is
            # covered by SchemaTwoCoexistenceTests / PublisherSchemaRoutingTests.
            raise unittest.SkipTest("no schema-1 pack in the generated tree")
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
