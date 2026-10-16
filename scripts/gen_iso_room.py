#!/usr/bin/env python3
"""gen_iso_room.py - the renderer-v2 room generator (SOU-123, parent SOU-122 / ADR-067).

Why this file exists
--------------------
The v1 pack (``room.json`` schema 1) describes the floor as a flat pixel box and
props as pixel points, so the app has to re-derive geometry from numbers that
were never derived from a lattice.  Renderer v2 makes the tile GRID the single
source of truth: the app keeps its own camera, reads the floor as a Tiled
isometric map and walks it in grid cells.

This generator is the deterministic producer of that pair.  It is a
TRANSLATION, not a re-derivation:

* the declared room (origin, tile box, grid size, wall rise) comes from
  ``scripts/room_geometry.py``;
* the screen maths comes from ``scripts/iso_lattice.py``, the one lattice
  module, which is bit-identical to Phaser's ``IsometricTileToWorldXY``;
* the wall planes are rendered by ``room_geometry.draw_wall_planes`` - the same
  code path that draws the v1 ``--demo`` composite (AC8: reuse, not a fork);
* props, palette and scale come from ``profiles/rooms/<id>_room.json``.

What it writes (into ``--out``)
-------------------------------
    room.map.json        Tiled 1.10 isometric map: floor + collision layers
    room.json            schema-2 descriptor: grid cells, tile counts, metres
    tiles/floor.png      (96*N, 55) RGBA floor variants, procedural only
    tiles/collision.png  (96, 55) RGBA, the one tile the collision layer uses
    walls.png            the two declared wall planes on the declared lattice

Guarantees (SOU-123 acceptance criteria)
----------------------------------------
AC3 valid isometric Tiled JSON: ``type=map``, ``orientation=isometric``,
    ``renderorder=right-down``, ``infinite=false``, floor then collision, every
    gid defined by one of the two tilesets, ``collides`` as a tile *property*
    (true on the collision tile, false on every floor tile).
AC4 floor sheet is ``(96*N, 55)`` RGBA, rhombus tiles, the plain variant painted
    from ``palette.floor[0]``.  Drawn from polygons: no img2img and no
    Star-Office / LimeZu pixels (fork hard rules 1-2).
AC5 walkability: blocked cells exist (one per prop footprint cell), every anchor
    and every zone rect is walkable, and no blocked cell lies in a zone rect.
    An intent that cannot fit is clamped/snapped/shrunk and REPORTED as a
    finding - never dropped.
AC6 the descriptor carries NO pixel coordinates: no ``x``/``y`` key at any depth
    (origins are arrays, sizes are ``w``/``h``).
AC7 deterministic: no clock, no RNG, no absolute paths in the payload, fixed
    insertion order - same profile, byte-identical JSON and PIXEL-identical
    PNGs (the raster bytes themselves track the Pillow/zlib build; SOU-137
    AC5 states this precisely, and the suite asserts it the same way).
AC8 the walls and the lattice are ``room_geometry``'s and ``iso_lattice``'s.

Usage
-----
    python scripts/gen_iso_room.py \
        --profile profiles/rooms/andrzej_room.json \
        --out assets/generated/rooms/andrzej

A prop declared at ``grid=[gx,gy]`` with ``tiles=T`` blocks the
``max(1, round(T)) x max(1, round(T))`` cell square that starts at its anchor
and spreads towards +gx/+gy (the camera side), which is exactly the rule
``backend/tests/test_iso_room.py`` re-derives from the descriptor.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MAP_VERSION = "1.10"
TILED_VERSION = "1.10.2"
SCHEMA = 2
TILE_METERS = 0.7  # declared scale in the parent contract: 1 tile = 0.7 m
WALL_PAD = 8       # px of transparent canvas around the declared wall planes
GENERATOR = "scripts/gen_iso_room.py (SOU-123, ADR-067 renderer v2)"

# The screen formula, as one string both artefacts and humans can diff.
SCREEN_FORMULA = "screen(gx,gy) = (ox + (gx-gy)*tw/2, oy + (gx+gy)*th/2)"

# Authoritative zone set - Szarik's comment 3036 on SOU-123, quoted verbatim:
# ids, kinds, labels and facings are BINDING.  Only anchor/rect positions may
# be adjusted, and every adjustment is reported as a finding (never dropped).
# ``build`` is kind ``ops`` because the declared enum is
# work | ops | read | rest | wait | entry | decor; SOU-125 routes long builds
# by the zone ID, not by the kind.
ZONES = [
    {"id": "work", "label": "Work desk", "kind": "work", "facing": "ne",
     "anchor": [3, 4], "rect": [2, 2, 4, 5]},
    {"id": "ops", "label": "Server rack", "kind": "ops", "facing": "ne",
     "anchor": [7, 3], "rect": [7, 1, 3, 3]},
    {"id": "build", "label": "Garage bay", "kind": "ops", "facing": "se",
     "anchor": [5, 14], "rect": [5, 13, 3, 3]},
    {"id": "read", "label": "Reading nook", "kind": "read", "facing": "sw",
     "anchor": [7, 6], "rect": [6, 5, 4, 3]},
    {"id": "wait", "label": "Coffee corner", "kind": "wait", "facing": "sw",
     "anchor": [8, 8], "rect": [7, 6, 4, 4]},
    {"id": "rest", "label": "Rest corner", "kind": "rest", "facing": "sw",
     "anchor": [10, 10], "rect": [9, 9, 3, 3]},
    {"id": "entry", "label": "Door", "kind": "entry", "facing": "se",
     "anchor": [1, 9], "rect": [0, 8, 2, 4]},
    {"id": "decor", "label": "Cat corner", "kind": "decor", "facing": "sw",
     "anchor": [1, 14], "rect": [0, 14, 3, 3]},
]
# Snap findings stay possible wherever a rect meets a blocked cell, and they
# stay recorded (never silent).  The old FAR_INTENTS clamp (SOU-123, for the
# 12x12 base T1 ran against) is gone: on the declared 12x18 floor (SOU-137)
# both ``build`` and ``decor`` anchors are ordinary on-floor bay cells.

# Floor tileset: three procedural variants, none of them collides.
FLOOR_TILES = [
    {"name": "plain", "role": "walkable"},
    {"name": "plain-alt", "role": "walkable"},
    {"name": "blocked", "role": "prop-footprint-visual"},
]
COLLISION_TILE_NAME = "blocked"

# Doorways on a floor edge, beside the entry zone.  Grid cells only (AC6).
DOORS = [
    {"id": "main", "label": "Door", "grid": [0, 9], "side": "north-west",
     "room": "outside", "uses": 2, "walkable": True},
    {"id": "cat-flap", "label": "Cat flap", "grid": [0, 10], "side": "north-west",
     "room": "outside", "uses": 1, "walkable": True},
]


def _sibling(name: str):
    """Import ``scripts/<name>.py`` by path under its real module name.

    This file is loaded by ``backend/tests/test_iso_room.py`` through
    ``importlib`` under a name that is not ``__main__``, so a plain
    ``import iso_lattice`` would depend on sys.path; and re-registering a
    sibling under an alias would let two copies of the lattice exist at once.
    Loading by path under the true name keeps "the lattice has one
    implementation" (AC2) literally true.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


iso_lattice = _sibling("iso_lattice")
room_geometry = _sibling("room_geometry")

TILE_W = iso_lattice.TILE_W
TILE_H = iso_lattice.TILE_H


# --------------------------------------------------------------------------- #
# the declared grid, read from room_geometry (never re-declared here)
# --------------------------------------------------------------------------- #
def _floor():
    f = room_geometry.ROOM["floor"]
    return (int(f["origin"]["x"]), int(f["origin"]["y"]),
            int(f["grid"]["x"]), int(f["grid"]["y"]))


def _cells_in_rect(rect):
    x0, y0, w, h = rect
    return {(gx, gy) for gx in range(x0, x0 + w) for gy in range(y0, y0 + h)}


def footprint(grid, tiles):
    """Cells a prop blocks: ``max(1, round(tiles))^2`` spreading +gx/+gy."""
    side = max(1, int(round(float(tiles))))
    gx, gy = int(grid[0]), int(grid[1])
    return [[gx + dx, gy + dy] for dx in range(side) for dy in range(side)]


def blocked_cells(props):
    _, _, gx_max, gy_max = _floor()
    floor = {(gx, gy) for gx in range(gx_max) for gy in range(gy_max)}
    blocked = set()
    for prop in props:
        for cell in footprint(prop["grid"], prop["tiles"]):
            pair = (cell[0], cell[1])
            if pair in floor:  # off-floor cells simply are not on the map
                blocked.add(pair)
    return blocked


def walkable_cells(blocked):
    _, _, gx_max, gy_max = _floor()
    return {(gx, gy) for gx in range(gx_max) for gy in range(gy_max)} - blocked


# --------------------------------------------------------------------------- #
# zone snapping (AC5), with every adjustment recorded as a finding
# --------------------------------------------------------------------------- #
def _bias(facing):
    return {"ne": (1, -1), "se": (1, 1), "nw": (-1, -1), "sw": (-1, 1)}[facing]


def _snap_anchor(anchor, walkable, bias):
    """Nearest walkable cell; ties broken towards the declared facing, then
    lexicographically, so the result never depends on set iteration order."""
    ax, ay = anchor
    if (ax, ay) in walkable:
        return (ax, ay)
    return min(
        walkable,
        key=lambda c: (abs(c[0] - ax) + abs(c[1] - ay),
                       -((c[0] - ax) * bias[0] + (c[1] - ay) * bias[1]),
                       abs(c[0] - ax), abs(c[1] - ay), c[0], c[1]),
    )


def _fit_rect(anchor, declared, blocked, notes):
    """Largest walkable rect inside the declared floor that contains the
    anchor, preferring the declared size and position (never bigger than
    declared, never dropped)."""
    _, _, gx_max, gy_max = _floor()
    ax, ay = anchor
    dx0, dy0, dw, dh = declared
    cands = []
    for w in range(1, dw + 1):
        for h in range(1, dh + 1):
            for x0 in range(max(0, ax - w + 1), min(ax, gx_max - w) + 1):
                for y0 in range(max(0, ay - h + 1), min(ay, gy_max - h) + 1):
                    cells = {(gx, gy) for gx in range(x0, x0 + w)
                             for gy in range(y0, y0 + h)}
                    if cells & blocked:
                        continue
                    cands.append((-(w * h), abs(x0 - dx0) + abs(y0 - dy0),
                                  abs(w - dw) + abs(h - dh), x0, y0, w, h))
    if not cands:
        raise AssertionError(f"zone cannot be placed at all: {declared} @{anchor}")
    cands.sort()
    _, _, _, x0, y0, w, h = cands[0]
    rect = [x0, y0, w, h]
    if [dx0, dy0, dw, dh] != rect:
        notes.append(f"rect {list(declared)} -> {rect} (cleared blocked cells, "
                     f"kept anchor {list(anchor)} inside)")
    return rect


def snap_zones(props):
    """Resolve the binding zone set onto the walkable grid.

    Returns (zones, anchors, findings, blocked).  AC5 may move an intent -
    clamp, snap, shift or shrink - and may never drop a zone.
    """
    blocked = blocked_cells(props)
    walkable = walkable_cells(blocked)
    zones, anchors, findings = [], [], []
    for spec in ZONES:
        notes: list[str] = []
        anchor = [int(spec["anchor"][0]), int(spec["anchor"][1])]
        anchor = _snap_anchor(tuple(anchor), walkable, _bias(spec["facing"]))
        rect = [int(v) for v in spec["rect"]]
        if anchor not in _cells_in_rect(rect) or _cells_in_rect(rect) & blocked:
            rect = _fit_rect(anchor, rect, blocked, notes)
        for note in notes:
            findings.append({"id": spec["id"], "note": note,
                             "declared_anchor": list(spec["anchor"]),
                             "declared_rect": list(spec["rect"]),
                             "anchor": list(anchor), "rect": list(rect)})
        zones.append({"id": spec["id"], "label": spec["label"],
                      "kind": spec["kind"], "facing": spec["facing"],
                      "anchor": list(anchor), "rect": list(rect)})
        anchors.append({"id": f"{spec['id']}.stand", "zone": spec["id"],
                        "kind": spec["kind"], "facing": spec["facing"],
                        "grid": list(anchor)})
    for zone in zones:  # AC5, asserted here so a regression fails loudly
        _, _, w, h = zone["rect"]
        assert (zone["anchor"][0], zone["anchor"][1]) in _cells_in_rect(zone["rect"]), zone
        assert not _cells_in_rect(zone["rect"]) & blocked, f"blocked in {zone['id']}"
        assert w >= 1 and h >= 1, zone
    return zones, anchors, findings, blocked


# --------------------------------------------------------------------------- #
# tiles (AC4) - procedural polygons only, no img2img, no LimeZu/Star-Office art
# --------------------------------------------------------------------------- #
def _rgb(hex_str):
    h = hex_str.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _mix(c1, c2, k):
    return tuple(round(a + (b - a) * k) for a, b in zip(c1, c2))


def _rhombus(ox=0):
    """One tile on the declared 96x55 box: top, right, bottom, left."""
    return [(ox + TILE_W // 2, 0), (ox + TILE_W, TILE_H // 2),
            (ox + TILE_W // 2, TILE_H), (ox, TILE_H // 2)]


def build_floor_sheet(palette, out_path):
    n = len(FLOOR_TILES)
    img = Image.new("RGBA", (TILE_W * n, TILE_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    base = _rgb(palette["floor"][0])
    alt = _rgb(palette["floor"][1])
    shell = _rgb(palette["shell"][1])
    warn = _rgb(palette["warm"][3])
    for idx, tile in enumerate(FLOOR_TILES):
        pts = _rhombus(idx * TILE_W)
        fill = {"plain": base, "plain-alt": alt, "blocked": shell}[tile["name"]]
        draw.polygon(pts, fill=fill + (255,))
        # edge lighting only - never across the centre pixel (48, 27), which
        # AC4 pins to the exact profile colour on the plain tile
        draw.line([pts[3], pts[0]], fill=_mix(fill, (255, 255, 255), 0.22) + (255,), width=1)
        draw.line([pts[1], pts[2]], fill=_mix(fill, (0, 0, 0), 0.30) + (255,), width=1)
        draw.line([pts[0], pts[3]], fill=_mix(fill, (0, 0, 0), 0.10) + (255,), width=1)
        draw.line([pts[2], pts[1]], fill=_mix(fill, (255, 255, 255), 0.10) + (255,), width=1)
        if tile["name"] == "blocked":
            cx = idx * TILE_W + TILE_W // 2
            for k in (-10, 0, 10):
                draw.line([(cx + k - 8, TILE_H // 2 + 6), (cx + k + 8, TILE_H // 2 - 6)],
                          fill=_mix(fill, warn, 0.7) + (255,), width=2)
    img.save(out_path)
    return img


def build_collision_sheet(palette, out_path):
    img = Image.new("RGBA", (TILE_W, TILE_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pts = _rhombus()
    dark = _mix(_rgb(palette["shell"][1]), (0, 0, 0), 0.35)
    draw.polygon(pts, fill=dark + (255,))
    draw.line([pts[0], pts[1], pts[2], pts[3], pts[0]],
              fill=_mix(dark, (0, 0, 0), 0.5) + (255,), width=1)
    mark = _rgb(palette["warm"][3])
    cx, cy = TILE_W // 2, TILE_H // 2
    draw.line([(cx - 9, cy - 9), (cx + 9, cy + 9)], fill=mark + (255,), width=2)
    draw.line([(cx + 9, cy - 9), (cx - 9, cy + 9)], fill=mark + (255,), width=2)
    img.save(out_path)
    return img


# --------------------------------------------------------------------------- #
# the Tiled map (AC3)
# --------------------------------------------------------------------------- #
def build_tilesets():
    """Two tilesets, embedded.  ``collides`` is a Tiled *property*, because
    that is what the scene calls (``setCollisionByProperty``)."""
    floor = {
        "firstgid": 1,
        "name": "floor",
        "tilewidth": TILE_W,
        "tileheight": TILE_H,
        "tilecount": len(FLOOR_TILES),
        "columns": len(FLOOR_TILES),
        "margin": 0,
        "spacing": 0,
        "version": MAP_VERSION,
        "image": "tiles/floor.png",
        "imagewidth": TILE_W * len(FLOOR_TILES),
        "imageheight": TILE_H,
        "tiles": [{"id": i, "name": t["name"], "type": t["name"],
                   "properties": [{"name": "collides", "type": "bool",
                                   "value": False}]}
                  for i, t in enumerate(FLOOR_TILES)],
    }
    collision = {
        "firstgid": 1 + len(FLOOR_TILES),
        "name": "collision",
        "tilewidth": TILE_W,
        "tileheight": TILE_H,
        "tilecount": 1,
        "columns": 1,
        "margin": 0,
        "spacing": 0,
        "version": MAP_VERSION,
        "image": "tiles/collision.png",
        "imagewidth": TILE_W,
        "imageheight": TILE_H,
        "tiles": [{"id": 0, "name": COLLISION_TILE_NAME,
                   "type": COLLISION_TILE_NAME,
                   "properties": [{"name": "collides", "type": "bool",
                                   "value": True}]}],
    }
    return floor, collision


def build_map(floor_data, collision_data, width, height):
    floor_ts, collision_ts = build_tilesets()
    layers = [
        {"id": 1, "name": "floor", "type": "tilelayer", "opacity": 1.0,
         "visible": True, "x": 0, "y": 0, "width": width, "height": height,
         "data": floor_data},
        {"id": 2, "name": "collision", "type": "tilelayer", "opacity": 1.0,
         "visible": False, "x": 0, "y": 0, "width": width, "height": height,
         "data": collision_data},
    ]
    return {
        "type": "map",
        "version": MAP_VERSION,
        "tiledversion": TILED_VERSION,
        "orientation": "isometric",
        "renderorder": "right-down",
        "width": width,
        "height": height,
        "tilewidth": TILE_W,
        "tileheight": TILE_H,
        "hexsidelength": 0,
        "staggeraxis": None,
        "staggerindex": None,
        "backgroundcolor": None,
        "infinite": False,
        "compressionlevel": -1,
        "nextlayerid": 3,
        "nextobjectid": 1,
        "layers": layers,
        "tilesets": [floor_ts, collision_ts],
    }


# --------------------------------------------------------------------------- #
# walls.png - the ONE wall renderer (AC8), on a translated copy of the lattice
# --------------------------------------------------------------------------- #
def build_walls(out_path):
    ox, oy, gx_max, gy_max = _floor()
    wall = room_geometry.ROOM["wall"]
    rise = wall["rise_px"]
    # extremes of the two declared wall planes, straight from the lattice
    min_x = ox - gy_max * TILE_W / 2
    max_x = ox + gx_max * TILE_W / 2
    min_y = oy - rise
    max_y = max(oy + gx_max * TILE_H / 2, oy + gy_max * TILE_H / 2)
    width = int(max_x - min_x + 2 * WALL_PAD)
    height = int(max_y - min_y + 2 * WALL_PAD)

    # room_geometry draws from a floor dict; translate the origin into image
    # space instead of re-deriving geometry here (AC8), then render the exact
    # wall planes the v1 --demo composite produces.
    f = copy.deepcopy(room_geometry.ROOM["floor"])
    f["origin"] = {"x": ox - (min_x - WALL_PAD), "y": oy - (min_y - WALL_PAD)}
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    room_geometry.draw_wall_planes(draw, f, None, None, show_grid=False)
    img.save(out_path)
    return {"image": "walls.png",
            "origin": [min_x - WALL_PAD, min_y - WALL_PAD],
            "size": [width, height],
            "rise_px": rise,
            "baseboard_px": wall["baseboard_px"],
            "layers": ["north-east plane", "north-west plane", "baseboard",
                       "wall-top cap", "panel joins", "seam AO",
                       "inside corner seam"]}


# --------------------------------------------------------------------------- #
# descriptor (schema 2, AC6)
# --------------------------------------------------------------------------- #
def build_descriptor(profile, zones, anchors, findings, blocked, walls, doors,
                     map_obj):
    ox, oy, gx_max, gy_max = _floor()
    canvas = room_geometry.ROOM["canvas"]
    props = []
    for prop in profile["props"]:
        tiles = float(prop["tiles"])
        entry = {
            "id": prop["id"],
            "grid": [int(prop["grid"][0]), int(prop["grid"][1])],
            "tiles": tiles,
            "real_m": round(tiles * TILE_METERS, 3),
            "footprint": footprint(prop["grid"], tiles),
            "subject": prop["subject"],
            "variant": "se",
            "flip_x": False,
            "asset": f"props/{prop['id']}.png",
        }
        for key in ("personal", "decor", "note"):
            if key in prop:
                entry[key] = prop[key]
        props.append(entry)
    return {
        "schema": SCHEMA,
        "room_id": profile.get("agent_id", "room"),
        "generated_by": GENERATOR,
        "source_profile": profile["_source"],
        "canvas": {"w": canvas["w"], "h": canvas["h"]},
        "lattice": {
            "module": "scripts/iso_lattice.py",
            "origin": [ox, oy],
            "tile_w": TILE_W,
            "tile_h": TILE_H,
            "slope": TILE_H / TILE_W,
            "screen": SCREEN_FORMULA,
            "authority": "Phaser 4.2.1 IsometricTileToWorldXY (frontend/"
                         "static/vendor/phaser-4.2.1.min.js, sha256 66348b1b"
                         "5141e49b7d5ebbe688cddcb502eab1cb00f21c538686a5b2c5"
                         "abe4de; identity proven, not assumed - the live 4.2.1 "
                         "headless probe in tools/lattice_probe/ returned engine "
                         "== declared formula for 8 cells including all four "
                         "floor corners, max |delta| = 0)",
            "rise_px": room_geometry.ROOM["wall"]["rise_px"],
        },
        "map": "room.map.json",
        "tileset": {
            "image": "tiles/floor.png",
            "firstgid": 1,
            "count": len(FLOOR_TILES),
            "tile_w": TILE_W,
            "tile_h": TILE_H,
            "collision_image": "tiles/collision.png",
            "collision_firstgid": map_obj["tilesets"][1]["firstgid"],
        },
        "grid": {"w": gx_max, "h": gy_max, "tile_m": TILE_METERS},
        "rooms": [],
        "props": props,
        "anchors": anchors,
        "zones": zones,
        "doors": doors,
        "walls": walls,
        "blocked": [[gx, gy] for gx, gy in sorted(blocked)],
        "snap_findings": findings,
        "art_manifest": {
            "floor": "tiles/floor.png",
            "collision": "tiles/collision.png",
            "walls": "walls.png",
            "provenance": "procedural raster from the declared lattice - no "
                          "img2img and no Star-Office/LimeZu pixels (fork hard "
                          "rules 1-2)",
        },
    }


# --------------------------------------------------------------------------- #
# top level
# --------------------------------------------------------------------------- #
def _dumps(payload) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def generate(profile_path: Path, out_dir: Path) -> dict:
    """Emit the whole pack.  Same input -> byte-identical output (AC7): no
    clock, no RNG, no absolute paths in any payload, fixed insertion order."""
    profile_path = Path(profile_path)
    out_dir = Path(out_dir)
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    # recorded as a repo-relative name so the payload never carries a machine
    # absolute path (that would break AC7 across checkouts)
    profile["_source"] = "profiles/rooms/" + profile_path.name
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "tiles").mkdir(parents=True, exist_ok=True)

    zones, anchors, findings, blocked = snap_zones(profile["props"])

    # --- tiles ---
    build_floor_sheet(profile["palette"], out_dir / "tiles" / "floor.png")
    build_collision_sheet(profile["palette"], out_dir / "tiles" / "collision.png")

    # --- layers: data[gy * width + gx] (row-major, right-down) ---
    _, _, gx_max, gy_max = _floor()
    width, height = gx_max, gy_max
    collision_gid = 1 + len(FLOOR_TILES)
    floor_data = []
    for gy in range(height):
        for gx in range(width):
            if (gx, gy) in blocked:
                floor_data.append(len(FLOOR_TILES))  # the "blocked" variant
            elif (gx + gy) % 2 == 0:
                floor_data.append(1)                 # plain
            else:
                floor_data.append(2)                 # plain-alt
    collision_data = [collision_gid if (gx, gy) in blocked else 0
                      for gy in range(height) for gx in range(width)]
    map_obj = build_map(floor_data, collision_data, width, height)

    doors = [{"id": d["id"], "label": d["label"], "grid": list(d["grid"]),
              "side": d["side"], "room": d["room"], "uses": d["uses"],
              "walkable": d["walkable"]} for d in DOORS]
    for door in doors:  # a doorway is an opening: nothing may block it
        gx, gy = door["grid"]
        assert (gx, gy) not in blocked, f"door {door['id']} is blocked"

    walls = build_walls(out_dir / "walls.png")
    descriptor = build_descriptor(profile, zones, anchors, findings, blocked,
                                  walls, doors, map_obj)

    (out_dir / "room.map.json").write_text(_dumps(map_obj), encoding="utf-8")
    (out_dir / "room.json").write_text(_dumps(descriptor), encoding="utf-8")
    return {"map": map_obj, "descriptor": descriptor, "zones": zones,
            "anchors": anchors, "findings": findings, "blocked": blocked}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate the renderer-v2 iso room pack (SOU-123)")
    ap.add_argument("--profile", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)
    result = generate(args.profile, args.out)
    ox, oy, gx_max, gy_max = _floor()
    print(f"[gen_iso_room] {args.out} : {gx_max}x{gy_max} floor at origin "
          f"({ox},{oy}), {len(result['blocked'])} blocked cells, "
          f"{len(result['zones'])} zones, {len(result['descriptor']['doors'])} doors, "
          f"{len(result['findings'])} snap findings")
    for finding in result["findings"]:
        print(f"[gen_iso_room]   {finding['id']}: {finding['note']}")
    print("GEN_ISO_ROOM_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
