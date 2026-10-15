#!/usr/bin/env python3
"""Declared room geometry + grid-exact placement. The answer to "objects must follow the walls".

WHY THIS EXISTS
---------------
Measured on a generated backdrop (2026-10-15): grout spacing along scanlines came out
50, 42, 34, 62, 53 and 77.5 px, and two grout lines in the SAME image sloped at
0.241 and 0.450 px/row against a true 2:1 isometric value of 0.500. A diffusion-generated
floor is only locally plausible -- there is no lattice to measure, so no furniture placed
against it can be reliably aligned, and no amount of eyeballing converges.

Therefore: GEOMETRY IS DECLARED HERE AND DRAWN, NOT GENERATED. Generated art supplies
objects only. Placement is arithmetic on the declared grid, so "follows the floor" and
"follows the wall" become checkable facts:

    screen(gx, gy) = (origin_x + (gx - gy) * TILE_W / 2,
                      origin_y + (gx + gy) * TILE_H / 2)

with TILE_H = TILE_W / 2 for true 2:1 isometry. Every object is anchored to a grid cell,
so its base sits on a lattice point by construction rather than by eye.

Usage:
    python scripts/room_geometry.py --demo        # composite real props onto a declared room
    python scripts/room_geometry.py --show-grid   # same, with the lattice drawn on top
"""
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "assets" / "experiments" / "geometry"

# ---------------------------------------------------------------------------
# THE DECLARED ROOM. Everything else derives from this block. Change it here and
# every placement follows; nothing is tuned per-image.
# ---------------------------------------------------------------------------
ROOM = {
    # WIDER AND TALLER THAN THE VIEWPORT ON PURPOSE. The Supreme Leader asked for a garage bay for his
    # van rather than a van parked in an office ("give it a garage bay - extend the room"). The Office
    # part keeps its screen position; the bay is added to the LEFT, which in this projection is the
    # +gy direction (screen-down-left), so the grid grows from 12x12 to 12x18, the origin slides right
    # by the bay's width, and the canvas grows to hold the whole floor.
    #
    # MEASURED, not guessed: the floor's near corner is at y = origin_y + (gx_max + gy_max) * tile_h/2
    # = 250 + 30 * 27.5 = 1075, so a 720-tall canvas only ever showed the upper part of the room - the
    # near corner was already off-frame at y=910 in the 12x12 room. That is fine for a pannable world
    # (the game owns a 1280x720 viewport and MAP_W/MAP_H + camera.setBounds pan it), but the bay must
    # be REACHABLE, so the canvas is now sized to the whole declared floor rather than cropped.
    "canvas": {"w": 1568, "h": 1100},
    # DECIDED by the Supreme Leader, 2026-10-15: keep the 30-degree true isometric angle.
    # Reason: the look of classic isometric strategy games (Pharaoh, Zeus) that he loves, and
    # the single-desk composite rendered at this angle read as "so on point". Per ADR-061,
    # operator taste wins on look -- so this is 30 degrees (tan 30 = 0.5774), NOT the 2:1
    # pixel-art convention, and props are gated tightly to it rather than tolerated.
    "projection": "true isometric, 30 degrees (base slope 0.5774; Pharaoh/Zeus look)",
    "floor": {
        "origin": {"x": 928, "y": 250},        # grid (0,0) on screen; the floor's far corner
        "tile_w": 96,
        "tile_h": 55,                         # 55/96 = 0.573 ~= tan(30 deg); see DECIDED above
        # grid x = 12 (the office), grid y = 18 (12 office + a 6-wide garage bay toward the viewer's
        # left). The far corner stays at y=250 so a 250px wall rise still reaches y=0 exactly, and the
        # ORIGIN moved right by 6 tiles (6 * 96/2 = 288) so the office part did not shift on screen.
        "grid": {"x": 12, "y": 18},            # how many cells each way are drawn
        "colours": {"a": (76, 84, 104), "b": (68, 76, 94), "grout": (44, 52, 68)},
    },
    # THE GARAGE BAY. Declared, not drawn by hand: which cells it covers, its own floor material,
    # and the painted marking. A vehicle belongs on concrete in a marked bay with a door behind it -
    # "certain things must match" (the operator's words) applies to a van as much as to a coffee
    # machine needing a table.
    "bay": {
        "gx": (1, 10),                         # cell range along the office's depth axis
        "gy": (12, 18),                        # the 6 columns added to the left
        "colour": (64, 66, 72),                # concrete, deliberately greyer than the office floor
        "marking": (188, 156, 66),             # painted hazard line
        "door": {"colour": (96, 104, 120), "slat": (78, 86, 100)},   # roller door on the left wall
    },
    "wall": {
        # The room is enclosed by the TWO far wall planes, raised from the two far edges of the
        # floor diamond. A flat horizontal band (the first version) is what made the composite
        # read as "an asset blockout floating on a plane": with a declared lattice the walls are
        # geometry, not a gradient.
        "rise_px": 250,                        # wall height in screen pixels, purely vertical
        "colour": (58, 70, 94),                # left-back plane
        "colour2": (50, 61, 84),               # right-back plane (slightly darker = corner reads)
        "baseboard": (34, 42, 58),
        "baseboard_px": 14,
        "void": (22, 26, 36),                  # outside the room, incl. above the walls
    },
    # LIGHT IS GEOMETRY, so it is declared here with everything else. Review of the first room:
    # "identical soft blob shadows with no contact hardening, no consistent light direction, no
    # cast shadows... props look pasted onto the floor". One declared vector removes all three
    # problems at once, and every prop's shadow is then derived rather than guessed.
    "light": {
        "from": "front-upper-left",
        "vector": {"dx": -0.55, "dy": 0.62},   # screen-space direction shadows are cast TOWARD
        "contact": {"scale": 1.02, "alpha": 120},   # tight, dark, hugs the footprint
        "cast": {"scale": 1.25, "length": 0.85, "alpha": 58},  # longer, softer, offset
    },
}


def grid_to_screen(gx, gy, f=None):
    f = f or ROOM["floor"]
    o, tw, th = f["origin"], f["tile_w"], f["tile_h"]
    return (o["x"] + (gx - gy) * tw / 2.0, o["y"] + (gx + gy) * th / 2.0)


def wall_to_screen(gx, height, side="right", f=None):
    """Screen position on a WALL PLANE - the second anchor type from ADR-065.

    A wall-mounted object is not on the floor grid: it has a position ALONG the wall and a
    height ABOVE the floor seam. Keeping this separate from grid_to_screen is what lets
    wall decor and floor furniture stay consistent without pretending they share a plane.

    side="right" -> the plane rising from the floor edge (0,0)->(gx_max,0)
    side="left"  -> the plane rising from the floor edge (0,0)->(0,gy_max)
    """
    f = f or ROOM["floor"]
    a = grid_to_screen(0, 0, f)
    if side == "right":
        b = grid_to_screen(f["grid"]["x"], 0, f)
    else:
        b = grid_to_screen(0, f["grid"]["y"], f)
    t = gx / float(f["grid"]["x"] if side == "right" else f["grid"]["y"])
    x = a[0] + (b[0] - a[0]) * t
    y = a[1] + (b[1] - a[1]) * t - height
    return (x, y)


def draw_room(show_grid=False):
    W, H = ROOM["canvas"]["w"], ROOM["canvas"]["h"]
    w, f = ROOM["wall"], ROOM["floor"]
    img = Image.new("RGB", (W, H), w["void"])
    d = ImageDraw.Draw(img)

    # --- floor: alternating rhombi on the declared lattice ---
    gx_max, gy_max = f["grid"]["x"], f["grid"]["y"]
    bay = ROOM.get("bay")
    for gx in range(gx_max):
        for gy in range(gy_max):
            pts = [grid_to_screen(gx, gy), grid_to_screen(gx + 1, gy),
                   grid_to_screen(gx + 1, gy + 1), grid_to_screen(gx, gy + 1)]
            col = f["colours"]["a"] if (gx + gy) % 2 == 0 else f["colours"]["b"]
            if (bay and bay["gy"][0] <= gy < bay["gy"][1]
                    and bay["gx"][0] <= gx < bay["gx"][1]):
                col = bay["colour"]          # concrete: a bay is not a tiled office floor
            d.polygon(pts, fill=col, outline=f["colours"]["grout"] if show_grid else None)

    # --- the two rising wall planes, drawn from the same lattice ---
    rise = w["rise_px"]
    corners = {
        "far": grid_to_screen(0, 0),
        "right": grid_to_screen(gx_max, 0),
        "left": grid_to_screen(0, gy_max),
    }
    for side, colour in (("left", w["colour"]), ("right", w["colour2"])):
        a = corners["far"]
        b = corners["left"] if side == "left" else corners["right"]
        quad = [a, b, (b[0], b[1] - rise), (a[0], a[1] - rise)]
        d.polygon(quad, fill=colour)
        # baseboard: a band sitting on the floor seam
        bb = w["baseboard_px"]
        band = [a, b, (b[0], b[1] - bb), (a[0], a[1] - bb)]
        d.polygon(band, fill=w["baseboard"])
        if show_grid:
            d.line([a, (a[0], a[1] - rise)], fill=(120, 130, 150))
            d.line([b, (b[0], b[1] - rise)], fill=(120, 130, 150))
    if show_grid:
        d.line([corners["far"], (corners["far"][0], corners["far"][1] - rise)], fill=(120, 130, 150))

    # --- architectural cues. Without these the walls read as flat planes fading into the void
    # (review: "no wall top, no corner, no material"). All derived from the same lattice. ---
    def shade(col, f):
        return tuple(max(0, min(255, int(c * f))) for c in col)

    for side, colour in (("left", w["colour"]), ("right", w["colour2"])):
        a = corners["far"]
        b = corners["left"] if side == "left" else corners["right"]
        top_a = (a[0], a[1] - rise)
        top_b = (b[0], b[1] - rise)
        # wall-top cap: a lighter band so the wall has thickness instead of fading out
        d.line([top_a, top_b], fill=shade(colour, 1.45), width=5)
        d.line([(top_a[0] + 3, top_a[1] + 6), (top_b[0] + 3, top_b[1] + 6)], fill=shade(colour, 0.8), width=2)
        # panel joins every tile along the wall: gives the plane a material and a scale
        n = f["grid"]["x"] if side == "right" else f["grid"]["y"]
        for gi in range(1, n):
            p0 = wall_to_screen(gi, 0, side)
            d.line([(p0[0], p0[1] - 2), (p0[0], p0[1] - rise + 6)], fill=shade(colour, 0.86), width=2)
        # ambient occlusion where the wall meets the floor: the seam needs weight or both the
        # wall and the objects on the floor read as pasted on
        d.line([a, b], fill=shade(w["baseboard"], 0.6), width=3)

    # the inside corner: a vertical seam so the 90-degree join is legible
    cx, cy = corners["far"]
    d.line([(cx, cy - rise + 2), (cx, cy)], fill=shade(w["baseboard"], 0.5), width=4)
    d.line([(cx + 2, cy - rise + 2), (cx + 2, cy)], fill=shade(w["colour"], 0.55), width=2)

    # --- the garage bay: painted marking and the door the vehicle came through ---
    if bay:
        bx0, bx1 = bay["gx"]
        by0, by1 = bay["gy"]
        for (p, q) in (((bx0, by0), (bx1, by0)), ((bx1, by0), (bx1, by1)),
                       ((bx1, by1), (bx0, by1)), ((bx0, by1), (bx0, by0))):
            d.line([grid_to_screen(*p), grid_to_screen(*q)], fill=bay["marking"], width=3)
        # roller door, filling the wall behind the bay from the seam upward. Without it the bay is a
        # grey rectangle against a wall; with it the room says how the van got in.
        door = bay["door"]
        a = wall_to_screen(by0, 0, "left")
        b = wall_to_screen(by1, 0, "left")
        top = int(rise * 0.78)
        d.polygon([a, b, (b[0], b[1] - top), (a[0], a[1] - top)], fill=door["colour"])
        for i in range(1, 9):
            yy = top / 9.0 * i
            d.line([(a[0], a[1] - yy), (b[0], b[1] - yy)], fill=door["slat"], width=2)
        d.line([a, b], fill=shade(door["colour"], 0.7), width=3)
        d.line([(a[0], a[1] - top), (b[0], b[1] - top)], fill=shade(door["colour"], 0.7), width=3)
    return img, d


def place(img, sprite_path, gx, gy, target_tiles=2.0, scale_max=1.0, frame=None):
    """Anchor a prop's BASE-CENTRE to a declared grid cell.

    The anchor is the arithmetic truth: whatever the sprite is, its base lands on a
    lattice point. Returns the on-screen anchor so it can be asserted in tests.

    `frame=(w, h)` crops frame 0 out of a spritesheet first - scaling a whole sheet
    to a tile width produces a nonsense scale (measured: 0.035), because the sheet
    holds many frames side by side.
    """
    f = ROOM["floor"]
    sp = Image.open(sprite_path).convert("RGBA")
    if frame:
        fw, fh = frame
        sp = sp.crop((0, 0, fw, fh))
    want_w = f["tile_w"] * target_tiles
    scale = min(want_w / sp.width, scale_max)
    if scale != 1.0:
        sp = sp.resize((max(1, int(sp.width * scale)), max(1, int(sp.height * scale))), Image.NEAREST)
    ax, ay = grid_to_screen(gx + 0.5, gy + 0.5)          # centre of the cell
    img.paste(sp, (int(ax - sp.width / 2), int(ay - sp.height)), sp)
    return (round(ax), round(ay)), sp.size, round(scale, 3)


def main():
    show_grid = "--show-grid" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)
    img, d = draw_room(show_grid=show_grid)

    placed = []
    # real props from the fork, anchored to declared cells.
    # frame sizes come from the engine's own load calls (index.html), not from guessing:
    #   coffee_machine -> spritesheet frameWidth 230, frameHeight 230
    props = [
        ("frontend/desk-v3.webp", 4, 3, 2.0, None),
        ("frontend/coffee-machine-v3-grid.webp", 7, 2, 1.0, (230, 230)),
        ("frontend/sofa-idle-v3.png", 2, 6, 2.0, None),
    ]
    for rel, gx, gy, tiles, frame in props:
        p = REPO / rel
        if not p.exists():
            print(f"   skip {rel} (absent)")
            continue
        anchor, size, scale = place(img, p, gx, gy, target_tiles=tiles, frame=frame)
        placed.append({"asset": rel, "grid": [gx, gy], "anchor_px": anchor, "size": size, "scale": scale})
        print(f"   {rel.split('/')[-1]:32s} grid=({gx},{gy}) anchor={anchor} size={size} scale={scale}")

    name = "room_with_grid.png" if show_grid else "room_composite.png"
    img.save(OUT / name)
    (OUT / "room_geometry.manifest.json").write_text(json.dumps(
        {"room": ROOM, "placed": placed, "output": name,
         "note": "Geometry declared, not generated. Anchors are arithmetic on the lattice."}, indent=2) + "\n")
    print(f"   -> {OUT.relative_to(REPO)}/{name}")
    print("ROOM_GEOMETRY_DONE")


if __name__ == "__main__":
    main()
