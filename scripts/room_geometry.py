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
    "canvas": {"w": 1280, "h": 720},
    "projection": "true 2:1 isometric",
    "floor": {
        "origin": {"x": 640, "y": 300},      # grid (0,0) on screen; the floor's far corner
        "tile_w": 96,
        "tile_h": 48,                         # MUST be tile_w/2 for true 2:1
        "grid": {"x": 14, "y": 14},           # how many cells each way are drawn
        "colours": {"a": (76, 84, 104), "b": (68, 76, 94), "grout": (44, 52, 68)},
    },
    "wall": {
        "seam_y": 300,                        # floor plane starts here; baseboard sits on it
        "colour": (58, 70, 94),
        "upper_colour": (48, 60, 82),
        "baseboard": (38, 46, 62),
        "height_px": 60,                      # thickness of darker band under the seam
    },
}


def grid_to_screen(gx, gy, f=None):
    f = f or ROOM["floor"]
    o, tw, th = f["origin"], f["tile_w"], f["tile_h"]
    return (o["x"] + (gx - gy) * tw / 2.0, o["y"] + (gx + gy) * th / 2.0)


def draw_room(show_grid=False):
    W, H = ROOM["canvas"]["w"], ROOM["canvas"]["h"]
    w, f = ROOM["wall"], ROOM["floor"]
    img = Image.new("RGB", (W, H), w["upper_colour"])
    d = ImageDraw.Draw(img)

    # wall: subtle vertical gradient, then the baseboard band
    for y in range(0, w["seam_y"]):
        t = y / max(1, w["seam_y"])
        c = tuple(int(w["upper_colour"][i] + (w["colour"][i] - w["upper_colour"][i]) * t) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    d.rectangle([0, w["seam_y"] - w["height_px"], W, w["seam_y"]], fill=w["baseboard"])

    # floor: alternating rhombi on the declared lattice
    gx_max, gy_max = f["grid"]["x"], f["grid"]["y"]
    for gx in range(gx_max):
        for gy in range(gy_max):
            pts = [grid_to_screen(gx, gy), grid_to_screen(gx + 1, gy),
                   grid_to_screen(gx + 1, gy + 1), grid_to_screen(gx, gy + 1)]
            col = f["colours"]["a"] if (gx + gy) % 2 == 0 else f["colours"]["b"]
            d.polygon(pts, fill=col, outline=f["colours"]["grout"] if show_grid else None)
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
