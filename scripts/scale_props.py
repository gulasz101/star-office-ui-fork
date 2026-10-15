#!/usr/bin/env python3
"""Size room props from the room's real-world scale, measured rather than guessed.

THE BUG THIS FIXES: `target_tiles` scaled the prop's CANVAS (a 640x640 PNG that is mostly
padding) to `tiles * tile_w`, so the OBJECT inside always came out smaller than its slot. Review
of the assembled room called it directly - "the scale of items is not matching the scale of the
room". A prop's real size has to come from its measured object width, not its canvas width.

THE SCALE: 100 px per metre, derived from a quantity this project DECLARES rather than one I
assumed - ROOM['wall']['rise_px'] = 250 px for a room wall, and a room wall is ~2.5 m. It
cross-checks against art that was drawn to fit this room: sofa_idle measures 162 px wide ~ 1.6 m
(right for a two-seater), the coffee vignette 201 px ~ 2 m for table + machine + plant.

FIRST ATTEMPT, KEPT AS A WARNING: deriving px/m per-object from assumed real widths gave
90/167/247/376 px per metre across five sprites - the spread was the assumptions, not the art.
Do not fit scale from a table of guesses; anchor it on one declared reference and check the rest.

Real widths below are ordinary object dimensions, stated so they can be argued with.

Usage: python scripts/scale_props.py [--apply]
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
ROOMS = REPO / "assets" / "generated" / "rooms"
TILE_W = 96                     # declared in room_geometry.ROOM['floor']['tile_w']
PX_PER_M = 100                  # 250 px wall / 2.5 m - see the module docstring
WALL_RISE_PX, WALL_REAL_M = 250, 2.5

# id -> (real width in metres, why)
REAL_M = {
    "desk":     (1.40, "a desk; the sprite includes a monitor and a lamp"),
    "rack":     (0.60, "a 19-inch server rack is 600 mm wide"),
    "chair":    (0.60, "an office chair at its footprint"),
    "spool":    (0.80, "a cable spool including its pallet"),
    "crate":    (0.60, "a shipping crate"),
    "bass":     (0.70, "amp plus the leaning bass guitar"),
    "cat":      (0.50, "a cat curled in a box"),
    "jdm_van":  (4.50, "a Mazda MPV is ~4.5 m long - see the note it prints"),
}


def object_bbox(path):
    """Measured width/height of what is actually drawn in the sprite (alpha > 128)."""
    a = np.asarray(Image.open(path).convert("RGBA"))
    ys, xs = np.nonzero(a[:, :, 3] > 128)
    return int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)


def main():
    apply = "--apply" in sys.argv
    agent = "andrzej"
    room_path = ROOMS / agent / "room.json"
    room = json.loads(room_path.read_text())
    print(f"  scale: {PX_PER_M:.0f} px/m  (wall {WALL_RISE_PX}px = {WALL_REAL_M}m)   tile {TILE_W}px"
          f" = {TILE_W / PX_PER_M:.2f} m   room {12} x {12} tiles = {12 * TILE_W / PX_PER_M:.1f} m")
    print()
    print(f"  {'prop':9s} {'object px':>10s} {'real m':>7s} {'want px':>8s} {'tiles was':>10s} {'tiles now':>10s}")
    print(f"  {'-' * 9} {'-' * 10} {'-' * 7} {'-' * 8} {'-' * 10} {'-' * 10}")
    # the SOURCE pack (written by gen_room_set) calls it 'tiles'; the SERVED/normalised pack
    # (written by publish_room) calls it 'target_tiles'. Both must move together or the app and the
    # preview disagree - which is its own class of bug.
    served_path = REPO / "frontend" / "rooms" / agent / "room.json"
    served = json.loads(served_path.read_text()) if served_path.exists() else None
    served_by_id = {}
    if served:
        served_by_id = {p["id"]: p for p in served.get("prop_anchors", [])}

    changed = 0
    for p in room["prop_anchors"]:
        pid = p["id"]
        if pid not in REAL_M:
            continue
        real_m, why = REAL_M[pid]
        src = ROOMS / agent / p["file"]
        ow, oh = object_bbox(src)
        want_px = real_m * PX_PER_M                  # how wide the OBJECT should be on screen
        canvas_needed = want_px * (640.0 / ow)       # canvas width so the object lands at want_px
        tiles = canvas_needed / TILE_W
        was = p.get("tiles", p.get("target_tiles"))
        p["tiles"] = round(tiles, 3)
        p["real_m"] = real_m
        p["scale_note"] = (f"object measures {ow}px in a 640px canvas; {real_m} m at {PX_PER_M} px/m "
                           f"= {want_px:.0f}px -> canvas {canvas_needed:.0f}px = {tiles:.2f} tiles ({why})")
        if served and pid in served_by_id:
            s = served_by_id[pid]
            s["target_tiles"] = p["tiles"]
            s["real_m"] = real_m
            s["scale_note"] = p["scale_note"]
        if was != p["tiles"]:
            changed += 1
        print(f"  {pid:9s} {ow:10d} {real_m:7.2f} {want_px:8.0f} {str(was):>10s} {p['tiles']:10}")
    if apply:
        room_path.write_text(json.dumps(room, indent=2) + "\n")
        if served:
            served_path.write_text(json.dumps(served, indent=2) + "\n")
        # the PROFILE must move too: gen_room_set writes room.json from profile 'tiles', so leaving
        # the profile alone means the next regeneration silently reverts every size computed here.
        prof = REPO / "profiles" / "rooms" / f"{agent}_room.json"
        if prof.exists():
            pr = json.loads(prof.read_text())
            n = 0
            for prop in pr.get("props", []):
                if prop["id"] in REAL_M:
                    prop["tiles"] = REAL_M[prop["id"]][0] * PX_PER_M * (640.0 / 640.0) / TILE_W
                    # scaled by the same measured object width as above, not by the canvas
                    src = ROOMS / agent / "props" / f"{prop['id']}.png"
                    if src.exists():
                        ow, _ = object_bbox(src)
                        prop["tiles"] = round(REAL_M[prop["id"]][0] * PX_PER_M * (640.0 / ow) / TILE_W, 3)
                    prop["real_m"] = REAL_M[prop["id"]][0]
                    n += 1
            prof.write_text(json.dumps(pr, indent=2) + "\n")
            print(f"  profile synced: {prof.relative_to(REPO)} ({n} props)")
        print(f"\n  room.json updated ({changed} props resized); served copy synced: {bool(served)}")
    else:
        print("\n  (dry run - pass --apply)")
    print("SCALE_PROPS_DONE")


if __name__ == "__main__":
    main()
