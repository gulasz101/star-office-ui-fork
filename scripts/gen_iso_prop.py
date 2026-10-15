#!/usr/bin/env python3
"""Generate an object IN the room's declared projection, then place it on the lattice.

The finding this answers (2026-10-15): with a mathematically exact 2:1 isometric floor,
the upstream props still read as pasted-on. Inspection of the composite showed why -- the
desk's front face is a true rectangle and its top recedes obliquely, i.e. it was drawn in a
front-facing/oblique projection. No placement can fix a projection mismatch, so reusing
upstream furniture in a declared-projection room is a dead end by construction.

The loop this proves: the room declares its projection -> the OBJECT is generated in that
same projection -> it is keyed, placed on a declared grid cell, and judged in the composite.
The prompt states the geometry numerically (2:1, 26.57 deg) because "isometric" alone is
not a specification: diffusion will happily return a front-facing desk.

Usage: python scripts/gen_iso_prop.py "a work desk with a CRT monitor" desk_iso
"""
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "assets" / "experiments" / "geometry"

spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
frg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frg)
spec2 = importlib.util.spec_from_file_location("msh", REPO / "scripts" / "make_scene_shell.py")
msh = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(msh)
spec3 = importlib.util.spec_from_file_location("geo", REPO / "scripts" / "room_geometry.py")
geo = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(geo)

PROJECTION = (
    "drawn in TRUE ISOMETRIC projection at 30 degrees like the classic isometric strategy games "
    "Pharaoh and Zeus: ground edges run at 30 degrees from horizontal, i.e. about 0.577 pixels "
    "down for every 1 pixel across. "
    "Vertical edges stay perfectly vertical. Viewed from the same fixed 30-degree isometric "
    "camera as an isometric floor grid. "
    # Do NOT describe the ground tile here. The previous wording said "the tile it stands on is a
    # rhombus twice as wide as it is tall would be WRONG here" and the model took it literally:
    # it painted an isometric diamond outline on the ground plane plus a magenta contour around
    # the object, both of which survived keying and shipped into the room. Measured on the van:
    # 4273 such pixels forming a thin silhouette contour AND a ground rhombus.
    "Do NOT draw the floor, the tile the object stands on, or any ground plane outline for it. "
)


def key_magenta(img, tolerance=48):
    """Border-seeded key + de-fringe, same contract as the sprite pipeline."""
    from collections import deque
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    border = [px[x, 0] for x in range(0, w, max(1, w // 32))]
    border += [px[x, h - 1] for x in range(0, w, max(1, w // 32))]
    border += [px[0, y] for y in range(0, h, max(1, h // 32))]
    border += [px[w - 1, y] for y in range(0, h, max(1, h // 32))]
    key = max(set(border), key=border.count)
    close = lambda c: all(abs(c[i] - key[i]) <= tolerance for i in range(3))
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not seen[y * w + x] and close(px[x, y]):
                seen[y * w + x] = 1; q.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not seen[y * w + x] and close(px[x, y]):
                seen[y * w + x] = 1; q.append((x, y))
    while q:
        x, y = q.popleft()
        px[x, y] = (px[x, y][0], px[x, y][1], px[x, y][2], 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and close(px[nx, ny]):
                seen[ny * w + nx] = 1; q.append((nx, ny))
    # de-fringe AND GROUND-MARKER REMOVAL in one pass. Whenever a ground tile is mentioned in the
    # prompt the model draws a magenta CONTOUR around the object plus an isometric DIAMOND on the
    # ground plane, and both survive a strict key. Confirmed by marking the detections and viewing
    # a sprite at 2x: a 1-2px contour plus a ground rhombus, NOT shading (the object's own outline
    # is near-black, a different colour).
    #
    # The discriminator is BLUE vs GREEN, not red vs green: magenta and pink have b >= g, while
    # brown, tan and cream have b < g. Using (r-g) alone stripped 8650px off a warm-wood desk
    # (#c08a60 has r-g=54). This lives HERE, in the one keying function, so a freshly generated
    # prop is clean without a separate manual step - it previously lived only in rekey_props.py and
    # fresh props kept shipping the contour.
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a and (r - g) > 20 and (b - g) > 10 and g < 150:
                px[x, y] = (0, 0, 0, 0)
    return img, key


def main():
    subject = sys.argv[1] if len(sys.argv) > 1 else "a work desk with a CRT monitor and a desk lamp"
    name = sys.argv[2] if len(sys.argv) > 2 else "prop_iso"
    OUT.mkdir(parents=True, exist_ok=True)
    prompt = (
        f"pixel art game asset: {subject}. {PROJECTION}"
        "Single object, centred, nothing else in frame, no furniture around it, no room, "
        "no floor grid drawn. Crisp thin dark outlines, flat cel shading, limited palette of "
        "dark blue-grey, warm wood browns and cream, no antialiasing, no blur, "
        "no checkerboard pattern. The object rests DIRECTLY on the flat magenta background: do "
        "NOT draw any ground patch, base, platform, pedestal, mat, plinth or contact shadow "
        "under it. Plain flat magenta background #FF00FF filling the whole frame. "
        "32-bit pixel art, game asset."
    )
    pipe, torch = frg.load_pipe()          # LoRA off (ADR-060 field result)
    raw = frg.generate(pipe, torch, prompt, 1024, 1024, seed=555)
    raw.save(OUT / f"{name}.raw.png")
    keyed, key = key_magenta(raw)
    keyed.save(OUT / f"{name}.keyed.png")
    print(f"   keyed with {key}")

    # place it on the SAME declared lattice as the upstream prop, same cell, for a fair A/B
    img, _ = geo.draw_room(show_grid=False)
    anchor, size, scale = geo.place(img, OUT / f"{name}.keyed.png", 4, 3, target_tiles=2.0)
    print(f"   placed at grid (4,3) anchor={anchor} size={size} scale={scale}")
    img.save(OUT / f"{name}_composite.png")

    (OUT / f"{name}.manifest.json").write_text(json.dumps({
        "model": frg.MODEL_ID, "lora": frg.LORA_ID, "lora_strength": 0.0,
        "steps": frg.STEPS, "guidance": frg.GUIDANCE, "seed": 555, "prompt": prompt,
        "projection": geo.ROOM["projection"],
        "grid_anchor": [4, 3], "anchor_px": anchor, "output_size": size,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "Generated IN the declared projection, unlike the upstream props (front-facing/oblique).",
    }, indent=2) + "\n")
    print("ISO_PROP_DONE")


if __name__ == "__main__":
    main()
