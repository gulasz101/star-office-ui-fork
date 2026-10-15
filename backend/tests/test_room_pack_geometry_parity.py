"""Geometry parity for the room-pack loader (SOU-115 T2).

The app must place generated props by the DECLARED lattice, reproducing
``scripts/room_geometry.py`` (ADR-065) - not by re-deriving anything from the
background image, and not with hardcoded tile sizes. File dimensions and the
python script are ground truth; the JS block extracted from
``frontend/index.html`` between the ROOM_PACK_LOADER markers must agree with
them for every anchor of every published room.

The loader's pure functions are executed under ``node`` (skips cleanly when
node is absent) on inputs read from the real served contract
(``frontend/rooms/``), so this suite also fails if the served tree drifts.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_room_pack_geometry_parity.py -q
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(BACKEND_DIR)
INDEX_HTML = os.path.join(REPO_DIR, "frontend", "index.html")
SERVED = os.path.join(REPO_DIR, "frontend", "rooms")
GEN_SCRIPTS = os.path.join(REPO_DIR, "scripts")

try:
    from PIL import Image  # read-only: source image dimensions for the plan check
except ImportError as exc:  # pragma: no cover
    Image = None
    _IMPORT_ERROR = str(exc)

NODE = shutil.which("node")
BLOCK_RE = re.compile(
    r"/\*\s*ROOM_PACK_LOADER_START\s*\*/(.*?)/\*\s*ROOM_PACK_LOADER_END\s*\*/", re.S)

JS_RUNNER = r"""
const fs = require('fs');
global.window = {};
(0, eval)(fs.readFileSync(process.argv[2], 'utf8'));
if (!global.window.RoomPack) { console.error('window.RoomPack not defined'); process.exit(3); }
const RP = global.window.RoomPack;
const input = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const out = { anchors: [], plans: [], picks: [], parsed: null, parseError: null };
for (const a of input.anchors) {
    out.anchors.push(RP.gridToScreen(a.floor, a.gx, a.gy));
}
for (const p of input.plans) {
    out.plans.push(RP.propPlan(p.floorTileW, p.srcW, p.srcH, p.targetTiles));
}
for (const k of input.picks) {
    out.picks.push(RP.pickOfficeRoom(k.index, k.officeAgentId));
}
try { out.parsed = RP.parseRoom(input.decorRoom); } catch (e) { out.parseError = String(e); }
console.log(JSON.stringify(out));
"""


def _extract_loader_block():
    with open(INDEX_HTML, encoding="utf-8") as fh:
        html = fh.read()
    m = BLOCK_RE.search(html)
    return m.group(1) if m else None


def _load_geometry():
    """Import scripts/room_geometry.py read-only (issue: never edit it)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "room_geometry", os.path.join(GEN_SCRIPTS, "room_geometry.py"))
    geo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geo)
    return geo


def _published_rooms():
    idx_path = os.path.join(SERVED, "index.json")
    if not os.path.exists(idx_path):
        return None, []
    with open(idx_path) as fh:
        index = json.load(fh)
    rooms = {}
    for agent, entry in index.get("rooms", {}).items():
        # entry["room"] is relative to the /static root (frontend/), not frontend/rooms
        with open(os.path.join(REPO_DIR, "frontend", entry["room"])) as fh:
            rooms[agent] = json.load(fh)
    return index, rooms


class LoaderParityTests(unittest.TestCase):
    """JS loader math must equal scripts/room_geometry.py on the served data."""

    @classmethod
    def setUpClass(cls):
        if Image is None:
            raise unittest.SkipTest(f"Pillow missing: {_IMPORT_ERROR}")
        cls.block = _extract_loader_block()
        cls.geo = _load_geometry()
        cls.index, cls.rooms = _published_rooms()

    def _run_node(self, payload):
        self.assertTrue(NODE, "node is required for JS parity")
        self.assertIsNotNone(self.block,
                            "ROOM_PACK_LOADER block not found in frontend/index.html "
                            "(SOU-115 AC3: the loader must exist between the markers)")
        tmp = tempfile.mkdtemp(prefix="sou115t2-")
        try:
            block_path = os.path.join(tmp, "loader.js")
            input_path = os.path.join(tmp, "input.json")
            runner_path = os.path.join(tmp, "runner.cjs")
            with open(block_path, "w") as fh:
                fh.write(self.block)
            with open(input_path, "w") as fh:
                json.dump(payload, fh)
            with open(runner_path, "w") as fh:
                fh.write(JS_RUNNER)
            env = dict(os.environ)
            proc = subprocess.run([NODE, runner_path, block_path, input_path],
                                  capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(proc.returncode, 0,
                             f"node runner failed: {proc.stderr}")
            return json.loads(proc.stdout)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_grid_to_screen_parity_for_every_published_anchor(self):
        """AC3: anchor = grid_to_screen(gx+0.5, gy+0.5) on the room's OWN floor."""
        self.assertTrue(self.rooms, "no published room to test (run publish_room.py)")
        anchor_calls, expected = [], []
        for agent, room in self.rooms.items():
            self.assertIn("floor", room, f"{agent}: served room.json must carry floor")
            self.assertEqual(room["floor"]["origin"],
                             self.rooms[agent]["floor"]["origin"])
            for a in room["prop_anchors"]:
                gx, gy = a["grid"][0] + 0.5, a["grid"][1] + 0.5
                anchor_calls.append({"floor": room["floor"], "gx": gx, "gy": gy})
                expected.append(self.geo.grid_to_screen(
                    gx, gy, {"origin": room["floor"]["origin"],
                             "tile_w": room["floor"]["tile_w"],
                             "tile_h": room["floor"]["tile_h"]}))
        out = self._run_node({"anchors": anchor_calls, "plans": [],
                              "picks": [], "decorRoom": {"prop_anchors": []}})
        self.assertEqual(len(out["anchors"]), len(expected))
        for got, want, call in zip(out["anchors"], expected, anchor_calls):
            self.assertAlmostEqual(got["x"], want[0], places=6)
            self.assertAlmostEqual(got["y"], want[1], places=6)

    def test_prop_plan_matches_pillow_semantics(self):
        """width = target_tiles * tile_w preserved-aspect, NEVER upscale, int
        truncation (exactly scripts/finish_room_props.py: s = min(want/w, 1.0))."""
        plans, expected = [], []
        for agent, room in self.rooms.items():
            tile_w = room["floor"]["tile_w"]
            for a in room["prop_anchors"]:
                path = os.path.join(SERVED, agent, a["file"])
                with Image.open(path) as im:
                    src_w, src_h = im.size
                plans.append({"floorTileW": tile_w, "srcW": src_w,
                              "srcH": src_h, "targetTiles": a["target_tiles"]})
                s = min(tile_w * a["target_tiles"] / src_w, 1.0)
                expected.append({"displayW": int(src_w * s), "displayH": int(src_h * s),
                                 "scale": s})
        out = self._run_node({"anchors": [], "plans": plans, "picks": [],
                              "decorRoom": {"prop_anchors": []}})
        for got, want in zip(out["plans"], expected):
            self.assertEqual(got["displayW"], want["displayW"])
            self.assertEqual(got["displayH"], want["displayH"])
            self.assertAlmostEqual(got["scale"], want["scale"], places=9)

    def test_selection_is_data_driven_and_never_special_cases_an_agent(self):
        """AC1 fallback cases + AC6 second agent: identical shipped code,
        different fixture data."""
        entry = {"room": "rooms/second/room.json", "props": "rooms/second/props/",
                 "prop_count": 1}
        picks = [
            {"index": None, "officeAgentId": "anyone"},
            {"index": {"rooms": {}}, "officeAgentId": "anyone"},
            {"index": {"rooms": {"andrzej": {"room": "x"}}}, "officeAgentId": "other"},
            {"index": {"rooms": {"someone": entry}}, "officeAgentId": "someone"},
        ]
        out = self._run_node({"anchors": [], "plans": [], "picks": picks,
                              "decorRoom": {"prop_anchors": []}})
        self.assertIsNone(out["picks"][0], "no index -> fallback")
        self.assertIsNone(out["picks"][1], "empty index -> fallback")
        self.assertIsNone(out["picks"][2], "index without the office's room -> fallback")
        self.assertEqual(out["picks"][3], entry,
                         "the office's room must be selected purely from index data")

    def test_parse_room_tolerates_decor_and_gate_fields(self):
        """AC7: decor:true and gate must not raise; unknown fields are ignored."""
        room = {
            "canvas": {"w": 1280, "h": 720},
            "floor": {"origin": {"x": 640, "y": 250}, "tile_w": 96, "tile_h": 55},
            "prop_anchors": [
                {"id": "a", "file": "props/a.png", "grid": [1, 1],
                 "target_tiles": 1.0, "decor": True, "gate": "exempt (decor)",
                 "something_future": {"deep": [1, 2]}},
            ],
        }
        out = self._run_node({"anchors": [], "plans": [], "picks": [], "decorRoom": room})
        self.assertIsNone(out["parseError"], f"AC7 violated: {out['parseError']}")
        self.assertEqual(len(out["parsed"]), 1)
        self.assertEqual(out["parsed"][0]["id"], "a")

    def test_loader_contains_no_agent_literals_and_no_hardcoded_tile_sizes(self):
        """AC6/AC3: the shipped renderer must not contain agent ids or 96/55."""
        self.assertIsNotNone(self.block, "loader block missing")
        code = re.sub(r"//.*", "", self.block)
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        self.assertNotIn("andrzej", code, "loader must not name an agent")
        self.assertNotIn("szarik", code, "loader must not name an agent")
        self.assertNotIn("openchamber", code, "loader must not name an agent")
        self.assertIsNone(re.search(r"\b(96|55)\b", code),
                          "tile sizes must come from the room's own floor, never hardcoded")

    def test_geometry_comment_references_adr065(self):
        """Issue evidence requirement: loader code carries the ADR-065 geometry comment."""
        self.assertIsNotNone(self.block, "loader block missing")
        self.assertIn("ADR-065", self.block)


if __name__ == "__main__":
    unittest.main()
