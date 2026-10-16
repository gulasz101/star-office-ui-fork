"""Renderer-v2 isometric room generator contract (SOU-123, parent SOU-122 / ADR-067;
SOU-137 corrects the base to the declared 12x18 floor with the garage bay).

The declared lattice in ``scripts/room_geometry.py`` must become an engine-native
Tiled isometric map plus a schema-2 descriptor whose every position is a GRID
coordinate. This suite pins the fixed contract that SOU-124 consumes:

* ``scripts/iso_lattice.py``  - the single importable implementation of
  ``screen(gx,gy) = (ox + (gx-gy)*TILE_W/2, oy + (gx+gy)*TILE_H/2)`` and its
  inverse ``grid_of(x,y)``.
* ``scripts/gen_iso_room.py --profile profiles/rooms/andrzej_room.json
  --out assets/generated/rooms/andrzej`` emitting ``room.map.json`` (Tiled 1.10
  isometric), ``tiles/floor.png``, ``tiles/collision.png``, ``walls.png`` and
  the schema-2 ``room.json`` descriptor, deterministically.

Run from the repo root::

    PATH=$HOME/.local/bin:$PATH PYTHONPATH=$PWD/.tooling/site python -m pytest backend/tests/test_iso_room.py -q

Same import/skip convention as the rest of the suite; the scripts are loaded
read-only by path, exactly like ``test_room_pack_geometry_parity.py`` does.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
SCRIPTS = REPO_DIR / "scripts"
PROFILE = REPO_DIR / "profiles" / "rooms" / "andrzej_room.json"
PACK = REPO_DIR / "assets" / "generated" / "rooms" / "andrzej"
VENDOR_PHASER = REPO_DIR / "frontend" / "vendor" / "phaser-3.80.1.min.js"

try:
    from PIL import Image  # noqa: F401
    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover
    Image = None
    _IMPORT_ERROR = str(exc)

# The authoritative zone ids are a cross-ticket contract (SOU-125 maps tool-call
# categories onto them). Quoted verbatim from the orchestrator comment on SOU-123.
ZONE_IDS = {"work", "ops", "build", "read", "wait", "rest", "entry", "decor"}
ZONE_KINDS = {"work", "ops", "read", "rest", "wait", "entry", "decor"}


def _load_script(name: str):
    """Import scripts/<name>.py by path (read-only), like the parity suite."""
    path = SCRIPTS / f"{name}.py"
    if not path.exists():
        raise AssertionError(f"scripts/{name}.py does not exist (SOU-123 not implemented yet)")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _pack_file(rel: str) -> Path:
    p = PACK / rel
    if not p.exists():
        raise AssertionError(f"{rel} missing from {PACK} - generator not run/committed")
    return p


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


class IsoLatticeTests(unittest.TestCase):
    """AC2 - the lattice module must reproduce Phaser exactly, nowhere else may
    the arithmetic live."""

    def test_lattice_identity(self):
        """`iso_lattice.screen()` equals the literal arithmetic of Phaser's
        IsometricTileToWorldXY on at least five cells including all four floor
        corners.

        Source of the comparison:
          src/tilemaps/components/IsometricTileToWorldXY.js
          (phaserjs/phaser@02d8931b, as quoted by the SOU-122 parent issue, and
          vendored in this repo at frontend/vendor/phaser-3.80.1.min.js,
          webpack module 14127: `var c=l+o/2*(t-e),d=u+(t+e)*(a/2)`.)

        Phaser computes, with l/u the layer world origin, o/a the base tile
        size, t/e the tile indices::

            x = l + o/2 * (t - e)
            y = u + (t + e) * (a/2)
        """
        lat = _load_script("iso_lattice")
        self.assertEqual((lat.TILE_W, lat.TILE_H), (96, 55),
                         "the declared tile box must be 96x55")

        if VENDOR_PHASER.exists():
            blob = VENDOR_PHASER.read_text(encoding="utf-8", errors="replace")
            self.assertIn("l+o/2*(t-e)", blob,
                          "vendored Phaser no longer contains the IsometricTileToWorldXY "
                          "arithmetic this test pins - stop and re-read the engine source")
            self.assertIn("(t+e)*(a/2)", blob)

        geo = _load_script("room_geometry")
        f = geo.ROOM["floor"]
        ox, oy = f["origin"]["x"], f["origin"]["y"]
        gx_max, gy_max = f["grid"]["x"], f["grid"]["y"]
        corners = [(0, 0), (gx_max - 1, 0), (0, gy_max - 1), (gx_max - 1, gy_max - 1)]
        cells = corners + [(3, 4), (5, 7), (gx_max, gy_max)]  # 4 corners + 3 more (>= 5)
        self.assertGreaterEqual(len(set(cells)), 5)

        for origin in ((0.0, 0.0), (float(ox), float(oy))):
            for (t, e) in cells:
                # the Phaser literal, written out inline on purpose:
                phaser_x = origin[0] + lat.TILE_W / 2 * (t - e)
                phaser_y = origin[1] + (t + e) * (lat.TILE_H / 2)
                got = lat.screen(t, e, origin)
                self.assertEqual(got[0], phaser_x, f"screen x mismatch at {(t, e)}")
                self.assertEqual(got[1], phaser_y, f"screen y mismatch at {(t, e)}")

    def test_grid_of_inverse(self):
        """AC2 companion: grid_of is the exact inverse of screen on the same
        declared tile box (float-exact within 1e-9)."""
        lat = _load_script("iso_lattice")
        origin = (640.0, 250.0)
        for gx in range(-2, 14):
            for gy in range(-2, 14):
                x, y = lat.screen(gx, gy, origin)
                rgx, rgy = lat.grid_of(x, y, origin)
                self.assertAlmostEqual(rgx, gx, places=9)
                self.assertAlmostEqual(rgy, gy, places=9)

    def test_no_hardcoded_origin(self):
        """The module must take the origin from its callers (profile/geometry
        module); it may not embed a literal room origin."""
        lat = _load_script("iso_lattice")
        src = (SCRIPTS / "iso_lattice.py").read_text()
        self.assertNotIn("640", src.replace("0.640", ""),
                         "iso_lattice.py embeds the room origin literal")
        self.assertNotIn("250", src.replace("0.250", ""))


class GeneratedPackTests(unittest.TestCase):
    """AC3-AC7 against the COMMITTED pack at assets/generated/rooms/andrzej -
    the artifact the board promises to SOU-124."""

    @classmethod
    def setUpClass(cls):
        if Image is None:
            raise unittest.SkipTest(f"Pillow missing: {_IMPORT_ERROR}")
        cls.map = json.loads(_pack_file("room.map.json").read_text())
        cls.desc = json.loads(_pack_file("room.json").read_text())
        cls.profile = json.loads(PROFILE.read_text())

    # ---- AC3 ---------------------------------------------------------------
    def test_map_is_a_valid_isometric_tiled_map(self):
        m = self.map
        self.assertEqual(m["type"], "map")
        self.assertEqual(m["orientation"], "isometric")
        self.assertEqual(m["renderorder"], "right-down")
        self.assertFalse(m["infinite"])
        self.assertEqual(m["tilewidth"], 96)
        self.assertEqual(m["tileheight"], 55)
        self.assertIn("version", m)
        self.assertIn("tiledversion", m)

        layers = {l["name"]: l for l in m["layers"]}
        self.assertEqual([l["name"] for l in m["layers"]][:2], ["floor", "collision"],
                         "layers must be floor then collision, in that order")
        floor, collision = layers["floor"], layers["collision"]
        for l in (floor, collision):
            self.assertEqual(l["type"], "tilelayer")
            self.assertEqual(l["width"], m["width"])
            self.assertEqual(l["height"], m["height"])
            self.assertEqual(len(l["data"]), m["width"] * m["height"])
        self.assertFalse(collision["visible"], "collision layer is hidden by contract")

        # every gid referenced is 0 or defined by a tileset
        defined = set()
        for ts in m["tilesets"]:
            first = ts["firstgid"]
            n = ts.get("tilecount", 0)
            defined.update(range(first, first + n))
        used = set(floor["data"]) | set(collision["data"])
        self.assertTrue(used <= defined | {0},
                        f"undefined gids in layer data: {used - defined - {0}}")
        print(f"map OK: {m['width']}x{m['height']}, gids used {sorted(used)}")

    def test_tilesets_match_the_contract(self):
        m = self.map
        self.assertEqual(len(m["tilesets"]), 2)
        floor_ts, collision_ts = m["tilesets"]
        self.assertEqual(floor_ts["firstgid"], 1)
        self.assertEqual(floor_ts["image"], "tiles/floor.png")
        n = floor_ts["tilecount"]
        self.assertEqual(floor_ts["columns"], n)
        self.assertEqual(floor_ts["imagewidth"], 96 * n)
        self.assertEqual(floor_ts["imageheight"], 55)
        self.assertEqual((floor_ts["margin"], floor_ts["spacing"]), (0, 0))
        self.assertEqual(collision_ts["firstgid"], 1 + n)
        self.assertEqual(collision_ts["tilecount"], 1)

        def props_of(tile):
            return {p["name"]: p for p in tile.get("properties", [])}

        # every floor tile declares collides:false - the collision layer is the
        # only walkability authority
        floor_ids = {t["id"] for t in floor_ts.get("tiles", [])}
        self.assertTrue(floor_ids <= set(range(n)))
        names = {t["id"]: t.get("name") for t in floor_ts.get("tiles", [])}
        self.assertIn("plain", names.values(), "floor tileset must contain the plain tile")
        self.assertIn("blocked", names.values(), "floor.png must show a blocked tile too")
        for t in floor_ts.get("tiles", []):
            p = props_of(t).get("collides")
            self.assertTrue(p and p["type"] == "bool" and p["value"] is False,
                            f"floor tile {t['id']} must carry collides:false")
        ct = collision_ts["tiles"][0]
        p = props_of(ct).get("collides")
        self.assertTrue(p and p["type"] == "bool" and p["value"] is True)

    # ---- AC4 ---------------------------------------------------------------
    def test_floor_tileset_is_the_declared_raster(self):
        n = self.map["tilesets"][0]["tilecount"]
        img = Image.open(_pack_file("tiles/floor.png"))
        self.assertEqual(img.size, (96 * n, 55), f"floor.png size {img.size}")
        self.assertEqual(img.mode, "RGBA")
        print(f"tiles/floor.png size={img.size} mode={img.mode}")
        # a rhombus, not a filled box: centre opaque, bbox corner transparent
        cx = img.crop((0, 0, 96, 55))
        self.assertGreater(cx.getpixel((48, 27))[3], 128)
        self.assertEqual(cx.getpixel((1, 1))[3], 0)
        # the plain tile is painted from the PROFILE palette, deterministically
        want = _hex_rgb(self.profile["palette"]["floor"][0])
        self.assertEqual(cx.getpixel((48, 27))[:3], want)
        # the collision tile is its own 96x55 RGBA image
        cimg = Image.open(_pack_file("tiles/collision.png"))
        self.assertEqual(cimg.size, (96, 55))
        self.assertEqual(cimg.mode, "RGBA")

    # ---- AC5 ---------------------------------------------------------------
    def test_walkability_is_real(self):
        m, d = self.map, self.desc
        floor_ts = m["tilesets"][0]
        collision_gid = floor_ts["firstgid"] + floor_ts["tilecount"]
        collision = next(l for l in m["layers"] if l["name"] == "collision")
        w, h = m["width"], m["height"]

        def gid(gx, gy):
            self.assertTrue(0 <= gx < w and 0 <= gy < h,
                            f"grid {(gx, gy)} outside the {w}x{h} floor")
            return collision["data"][gy * w + gx]

        blocked = {(i % w, i // w) for i, g in enumerate(collision["data"]) if g == collision_gid}
        self.assertTrue(blocked, "the collision layer must contain blocked cells")
        print(f"blocked cells: {sorted(blocked)}")

        for a in d["anchors"]:
            self.assertEqual(gid(*a["grid"]), 0,
                             f"anchor {a['id']} at {a['grid']} lands on a blocked cell")
        zone_cells = set()
        for z in d["zones"]:
            gx, gy, zw, zh = z["rect"]
            self.assertGreaterEqual(zw, 1)
            self.assertGreaterEqual(zh, 1)
            for cx in range(gx, gx + zw):
                for cy in range(gy, gy + zh):
                    self.assertEqual(gid(cx, cy), 0,
                                     f"zone {z['id']} rect covers blocked cell {(cx, cy)}")
                    zone_cells.add((cx, cy))
        self.assertFalse(blocked & zone_cells,
                         f"blocked cells inside zone rects: {blocked & zone_cells}")

        # every zone of the authoritative set exists, once, with a stand anchor
        ids = [z["id"] for z in d["zones"]]
        self.assertEqual(sorted(ids), sorted(ZONE_IDS),
                         "zones[] must be exactly the orchestrator's authoritative set")
        for z in d["zones"]:
            self.assertIn(z["kind"], ZONE_KINDS)
        stands = {a["id"]: a for a in d["anchors"] if a["id"].endswith(".stand")}
        self.assertEqual(sorted(stands), sorted(f"{z}.stand" for z in ZONE_IDS))
        for a in stands.values():
            self.assertIn(a["facing"], {"ne", "nw", "se", "sw"})
            self.assertIn(a["zone"], ZONE_IDS)

    # ---- AC6 ---------------------------------------------------------------
    def test_no_pixel_coordinates_in_the_descriptor(self):
        text = _pack_file("room.json").read_text()
        # the AC command is: grep -E '"(x|y)"' assets/generated/rooms/andrzej/room.json
        hits = re.findall(r'"[xy]"', text)
        self.assertEqual(hits, [],
                         f"screen-pixel keys leaked into the data path: {hits}")

    # ---- AC7 / SOU-137 AC5 --------------------------------------------------
    def test_two_consecutive_runs_are_deterministic(self):
        """SOU-137 AC5 restates determinism precisely: JSON files are
        BYTE-identical over two runs; PNGs are asserted PIXEL-identical, never
        byte-identical - the encoder's output depends on the Pillow/zlib build
        (the orchestrator measured 1520 -> 1341 B for floor.png with identical
        pixels)."""
        gen = _load_script("gen_iso_room")
        with tempfile.TemporaryDirectory(prefix="sou123-") as a, \
             tempfile.TemporaryDirectory(prefix="sou123-") as b:
            gen.generate(PROFILE, Path(a))
            gen.generate(PROFILE, Path(b))
            for rel in ("room.map.json", "room.json"):
                ha, hb = _sha(Path(a) / rel), _sha(Path(b) / rel)
                self.assertEqual(ha, hb, f"{rel} differs between runs: not deterministic")
                print(f"deterministic {rel}: {ha[:16]}")
            for rel in ("walls.png", "tiles/floor.png", "tiles/collision.png"):
                ia = Image.open(Path(a) / rel)
                ib = Image.open(Path(b) / rel)
                self.assertEqual(ia.size, ib.size)
                self.assertEqual(list(ia.convert("RGBA").getdata()),
                                 list(ib.convert("RGBA").getdata()),
                                 f"{rel} differs between runs: not deterministic")
                print(f"deterministic (pixels) {rel}")

    # ---- no-silent-drop clause + cross-checks -------------------------------
    def test_profile_props_are_carried_over(self):
        d = self.desc
        self.assertEqual(d["schema"], 2)
        props = {p["id"]: p for p in d["props"]}
        want = {p["id"] for p in self.profile["props"]}
        self.assertTrue({"cat", "jdm_van"} <= want)
        self.assertEqual(set(props), want,
                         "every profile prop must appear in the descriptor")
        for pp in self.profile["props"]:
            p = props[pp["id"]]
            self.assertEqual(p["grid"], list(pp["grid"]))
            self.assertEqual(p["tiles"], pp["tiles"])
            self.assertAlmostEqual(p["real_m"], round(pp["tiles"] * 0.7, 3), places=6)
            self.assertIn("variant", p)
            self.assertIsInstance(p["flip_x"], bool)

    def test_descriptor_matches_map_and_geometry(self):
        d, m = self.desc, self.map
        geo = _load_script("room_geometry")
        f = geo.ROOM["floor"]
        ox, oy = f["origin"]["x"], f["origin"]["y"]

        self.assertEqual(d["canvas"], {"w": geo.ROOM["canvas"]["w"],
                                       "h": geo.ROOM["canvas"]["h"]})
        self.assertEqual(d["lattice"]["origin"], [ox, oy])
        self.assertEqual((d["lattice"]["tile_w"], d["lattice"]["tile_h"]), (96, 55))
        self.assertEqual(d["lattice"]["slope"], 55 / 96)
        self.assertEqual(d["lattice"]["screen"],
                         "screen(gx,gy) = (ox + (gx-gy)*tw/2, oy + (gx+gy)*th/2)")
        self.assertEqual(d["map"], "room.map.json")
        self.assertEqual(d["tileset"]["image"], "tiles/floor.png")
        self.assertEqual(d["tileset"]["firstgid"], 1)
        self.assertEqual(d["tileset"]["count"], m["tilesets"][0]["tilecount"])
        self.assertEqual(d["rooms"], [])

        # doors: at least one, on a floor edge, walkable
        self.assertTrue(d["doors"])
        w = m["width"]
        collision = next(l for l in m["layers"] if l["name"] == "collision")
        for dr in d["doors"]:
            gx, gy = dr["grid"]
            self.assertIn(0, [gx, gy, w - 1 - gx, (f["grid"]["y"] - 1) - gy])
            self.assertEqual(collision["data"][gy * w + gx], 0)

        # collision layer equals the footprint rule derived from descriptor props
        n = m["tilesets"][0]["tilecount"]
        gid = 1 + n
        want_blocked = set()
        for p in d["props"]:
            fside = max(1, int(round(p["tiles"])))
            gx, gy = p["grid"]
            for cx in range(gx, gx + fside):
                for cy in range(gy, gy + fside):
                    want_blocked.add((cx, cy))
        got_blocked = {(i % w, i // w) for i, g in enumerate(collision["data"]) if g == gid}
        self.assertEqual(got_blocked, want_blocked & got_blocked | want_blocked,
                         "collision cells must be exactly the prop footprints")
        self.assertEqual(got_blocked, want_blocked)

    def test_walls_image_is_lattice_derived(self):
        d = self.desc
        geo = _load_script("room_geometry")
        gen = _load_script("gen_iso_room")
        f = geo.ROOM["floor"]
        ox, oy = f["origin"]["x"], f["origin"]["y"]
        tw, th = f["tile_w"], f["tile_h"]
        rise = geo.ROOM["wall"]["rise_px"]
        gx_max, gy_max = f["grid"]["x"], f["grid"]["y"]
        pad = gen.WALL_PAD
        # extremes of the two wall planes, straight from the declared lattice
        min_x = ox - gy_max * tw / 2
        max_x = ox + gx_max * tw / 2
        min_y = oy - rise
        # lowest seam point of either wall plane (here both far edges end at the same y)
        max_y = max(oy + gx_max * th / 2, oy + gy_max * th / 2)
        self.assertEqual(d["walls"]["image"], "walls.png")
        self.assertEqual(d["walls"]["origin"], [min_x - pad, min_y - pad])
        img = Image.open(_pack_file("walls.png"))
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.size, (int(max_x - min_x + 2 * pad), int(max_y - min_y + 2 * pad)))
        self.assertEqual(img.getpixel((0, 0))[3], 0, "the padding must be transparent")
        # the inside corner seam is opaque paint well inside the image
        seam = (int(ox - min_x + pad), 100)
        self.assertGreater(img.getpixel(seam)[3], 128)


def _hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


class BayCorrectionTests(unittest.TestCase):
    """SOU-137 - the pack must sit on the DECLARED 12x18 floor with the garage
    bay, not on the 12x12 base T1 accidentally ran against.

    AC1  map + descriptor cover the declared floor (12x18; 12x12 is a FAIL);
         the arithmetic that gy=18 must fit is pinned here.
    AC2  jdm_van carries the profile's grid (6,15) with NO clamp/adjust finding.
    AC3  build.stand has gy >= 12 and the build rect lies inside the bay's
         declared cell range; the other seven zones keep id/kind/label/facing.
    AC5  determinism is stated precisely: JSON byte-equal, PNG PIXEL-equal
         (never claim byte determinism for images - the encoder build leaks).
    AC6  lattice.authority names Phaser 4.2.1 (the engine renderer v2 runs on)
         with its source path, not the vendored 3.80.1.  The identity is
         PROVEN, not assumed: the live Phaser 4.2.1 headless probe
         (tools/lattice_probe/, pinned vendor sha256 66348b1b...e4de) returned
         engine == declared formula for 8 cells including all four floor
         corners, max |delta| = 0; iso_lattice.screen() equals that same
         literal arithmetic in IsoLatticeTests above.
    """

    @classmethod
    def setUpClass(cls):
        if Image is None:
            raise unittest.SkipTest(f"Pillow missing: {_IMPORT_ERROR}")
        cls.map = json.loads(_pack_file("room.map.json").read_text())
        cls.desc = json.loads(_pack_file("room.json").read_text())
        cls.profile = json.loads(PROFILE.read_text())
        cls.geo = _load_script("room_geometry")

    def test_ac1_floor_extent_is_the_declared_12x18(self):
        m, f = self.map, self.geo.ROOM["floor"]
        self.assertEqual((f["grid"]["x"], f["grid"]["y"]), (12, 18),
                         "the base declaration itself must be the bay floor")
        self.assertEqual((m["width"], m["height"]),
                         (f["grid"]["x"], f["grid"]["y"]),
                         "map width/height must cover the declared floor; 12x12 FAILS")
        g = self.desc["grid"]
        self.assertEqual((g["w"], g["h"]), (12, 18),
                         "descriptor grid must say 12x18")
        # the arithmetic for why gy=18 must fit: the floor's near corner is at
        # origin_y + (gx_max + gy_max) * tile_h/2 = 250 + 30*27.5 = 1075 <= canvas h
        oy = f["origin"]["y"]
        th = f["tile_h"]
        near_y = oy + (f["grid"]["x"] + f["grid"]["y"]) * th / 2
        self.assertEqual(near_y, 1075.0)
        self.assertLessEqual(near_y, self.geo.ROOM["canvas"]["h"])
        print(f"AC1: map {m['width']}x{m['height']}, descriptor grid "
              f"{g['w']}x{g['h']}, near corner y={near_y:g} <= canvas "
              f"{self.geo.ROOM['canvas']['h']}")

    def test_ac2_van_is_at_its_declared_bay_cell_unclamped(self):
        want = next(p for p in self.profile["props"] if p["id"] == "jdm_van")
        van = next(p for p in self.desc["props"] if p["id"] == "jdm_van")
        self.assertEqual(want["grid"], [6, 15])
        self.assertEqual(van["grid"], want["grid"],
                         "jdm_van must carry the profile's grid, not a clamp of it")
        self.assertEqual(van["tiles"], want["tiles"])
        for finding in self.desc["snap_findings"]:
            self.assertNotIn("jdm_van", json.dumps(finding),
                             f"van was clamped/adjusted: {finding}")
        bay = self.geo.ROOM["bay"]
        self.assertTrue(bay["gx"][0] <= van["grid"][0] <= bay["gx"][1],
                        "the van cell is inside the bay")
        self.assertTrue(bay["gy"][0] <= van["grid"][1] <= bay["gy"][1])
        # full prop table, printed so it can be diffed against the profile
        print("AC2 props (id, grid, tiles):")
        for p in self.desc["props"]:
            print(f"  {p['id']:10s} grid={p['grid']} tiles={p['tiles']}")
        prof = {(p["id"], tuple(p["grid"]), p["tiles"]) for p in self.profile["props"]}
        desc = {(p["id"], tuple(p["grid"]), p["tiles"]) for p in self.desc["props"]}
        self.assertEqual(prof, desc, "prop table must equal the profile table")

    def test_ac3_build_zone_sits_in_the_garage_bay(self):
        bay = self.geo.ROOM["bay"]
        stands = {a["id"]: a for a in self.desc["anchors"]}
        build = next(z for z in self.desc["zones"] if z["id"] == "build")
        stand = stands["build.stand"]
        self.assertGreaterEqual(stand["grid"][1], 12,
                                f"build.stand must be in the bay half: {stand}")
        x0, y0, w, h = build["rect"]
        self.assertTrue(bay["gx"][0] <= x0 and x0 + w - 1 <= bay["gx"][1],
                        f"build rect {build['rect']} outside bay gx range {bay['gx']}")
        self.assertTrue(bay["gy"][0] <= y0 and y0 + h - 1 <= bay["gy"][1],
                        f"build rect {build['rect']} outside bay gy range {bay['gy']}")
        # the other seven zones keep their binding id/kind/label/facing (SOU-123 set)
        others = [z for z in self.desc["zones"] if z["id"] != "build"]
        self.assertEqual(sorted(z["id"] for z in others),
                         sorted(ZONE_IDS - {"build"}))
        for z in others:
            self.assertIn(z["kind"], ZONE_KINDS)
            self.assertTrue(z["label"])
            self.assertIn(z["facing"], {"ne", "nw", "se", "sw"})
        print("AC3 zones (id, kind, facing, stand, rect):")
        for z in self.desc["zones"]:
            st = stands[f"{z['id']}.stand"]
            print(f"  {z['id']:6s} {z['kind']:6s} {z['facing']} stand={st['grid']} "
                  f"rect={z['rect']}")
        print(f"  bay: gx {bay['gx']}, gy {bay['gy']}")

    def test_ac5_determinism_json_bytes_png_pixels(self):
        """JSON byte-identical over two runs; PNGs PIXEL-identical (byte size
        may differ across Pillow/zlib builds - do not claim byte determinism
        for images; the orchestrator measured 1520 -> 1341 B for floor.png)."""
        gen = _load_script("gen_iso_room")
        with tempfile.TemporaryDirectory(prefix="sou137-") as a, \
             tempfile.TemporaryDirectory(prefix="sou137-") as b:
            gen.generate(PROFILE, Path(a))
            gen.generate(PROFILE, Path(b))
            for rel in ("room.map.json", "room.json"):
                self.assertEqual(_sha(Path(a) / rel), _sha(Path(b) / rel),
                                 f"{rel} not byte-identical over two runs")
                print(f"AC5 JSON byte-identical: {rel}")
            for rel in ("walls.png", "tiles/floor.png", "tiles/collision.png"):
                ia = Image.open(Path(a) / rel)
                ib = Image.open(Path(b) / rel)
                self.assertEqual(ia.size, ib.size)
                self.assertEqual(list(ia.convert("RGBA").getdata()),
                                 list(ib.convert("RGBA").getdata()),
                                 f"{rel} pixels differ between runs")
                print(f"AC5 PNG pixel-identical: {rel} "
                      f"(bytes {Path(a, rel).stat().st_size} vs {Path(b, rel).stat().st_size})")

    def test_ac6_lattice_authority_is_phaser_4_2_1(self):
        auth = self.desc["lattice"]["authority"]
        self.assertIn("4.2.1", auth)
        self.assertNotIn("3.80.1", auth,
                         "authority must name the engine v2 runs on, not the vendored 3.80.1")
        self.assertIn("frontend/static/vendor/phaser-4.2.1.min.js", auth,
                      "authority must carry the 4.2.1 source path")
        print(f"AC6 lattice.authority = {auth}")

    def test_no_12x12_workaround_clamp_messages_remain(self):
        """The bay exists now: nothing may be clamped with a 'no garage wing'
        excuse.  Findings are still legal, but only real snaps."""
        for finding in self.desc["snap_findings"]:
            self.assertNotIn("12x12", finding["note"])
            self.assertNotIn("no garage wing", finding["note"])
            self.assertNotIn("outside the declared", finding["note"])


class PackOnDiskEqualsRegenerated(unittest.TestCase):
    """The committed pack must be what the generator emits - no hand edits."""

    @classmethod
    def setUpClass(cls):
        if Image is None:
            raise unittest.SkipTest(f"Pillow missing: {_IMPORT_ERROR}")
        gen = _load_script("gen_iso_room")
        cls.tmp = tempfile.TemporaryDirectory(prefix="sou123-")
        gen.generate(PROFILE, Path(cls.tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_committed_pack_is_reproducible(self):
        """SOU-137 AC5: JSON byte-equal; PNGs PIXEL-equal (byte size is an
        encoder-build detail, not part of the contract)."""
        for rel in ("room.map.json", "room.json"):
            self.assertEqual(_sha(_pack_file(rel)),
                             _sha(Path(self.tmp.name) / rel),
                             f"{rel} in the repo differs from a fresh generator run")
        for rel in ("walls.png", "tiles/floor.png", "tiles/collision.png"):
            a = Image.open(_pack_file(rel))
            b = Image.open(Path(self.tmp.name) / rel)
            self.assertEqual(a.size, b.size)
            self.assertEqual(list(a.convert("RGBA").getdata()),
                             list(b.convert("RGBA").getdata()),
                             f"{rel} in the repo differs in pixels from a fresh generator run")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
