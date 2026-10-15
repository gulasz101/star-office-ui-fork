#!/usr/bin/env python3
"""Finish room props: strip the keyed-in base pad, add procedural contact shadows.

Inspection of the first generated room found three real defects (and one artifact that was
NOT a defect: the floor's apparent 24-degree slope is a clipped-silhouette measurement, since
the 14x14 lattice extends past the 1280-wide canvas; the drawn tile edge is 55/96 = 0.573).

The two defects fixed here, both without re-generating anything:

  1. BASE PADS. The generator draws a light solid base/platform under the object. It is not
     magenta, so the chroma key cannot remove it, and it survives as a uniform rhombus that
     reads as "sticker backing" - the strongest pasted-on tell in the composite. Removed by
     flood-filling the flat colour that touches the BOTTOM edge of the sprite.
  2. NO CONTACT SHADOWS. Nothing anchors an object to the floor. A procedurally drawn ellipse
     with the lattice's own aspect ratio, placed at each prop's base centre, gives every prop
     the same light direction for free - and it is OUR drawing, so it can never arrive from the
     model with the wrong projection.

Still open after this (documented, not hidden): the generator renders each object in its own
implied camera (a desk in near front-elevation next to a crate showing two side faces), so prop
geometry still varies. That needs per-prop prompt work and a visual check per asset; it is not
something this pass can fix silently.

Usage: python scripts/finish_room_props.py [agent_id]
"""
import importlib.util
import json
import sys
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated" / "rooms"

# the declared room is the single source of geometry - load it at module level so the shadow
# functions can read ROOM["light"] without re-importing per call
_spec = importlib.util.spec_from_file_location("geo", REPO / "scripts" / "room_geometry.py")
geo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(geo)
ROOM = geo.ROOM


def strip_bottom_plate(img, tol=26, min_share=0.55):
    """Remove a flat base/platform that touches the sprite's bottom edge.

    Seeds a flood fill from every opaque pixel on the bottom row and grows through pixels
    within `tol` of the dominant bottom-row colour. Only runs when that colour actually owns
    the bottom edge (min_share), so a prop whose subject simply reaches the bottom (a chair's
    legs on the floor) is left alone.
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    ys = [y for y in range(h) if any(px[x, y][3] > 128 for x in range(w))]
    if not ys:
        return img, False
    bottom = max(ys)
    cols = [px[x, bottom][:3] for x in range(w) if px[x, bottom][3] > 128]
    if not cols:
        return img, False
    dominant = max(set(cols), key=cols.count)
    if cols.count(dominant) / len(cols) < min_share:
        return img, False
    close = lambda c: all(abs(c[i] - dominant[i]) <= tol for i in range(3))
    seen = bytearray(w * h)
    q = deque()
    for x in range(w):
        if px[x, bottom][3] > 128 and close(px[x, bottom][:3]):
            seen[bottom * w + x] = 1
            q.append((x, bottom))
    removed = 0
    while q:
        x, y = q.popleft()
        r, g, b, a = px[x, y]
        px[x, y] = (0, 0, 0, 0)
        removed += 1
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and px[nx, ny][3] > 128 \
                    and close(px[nx, ny][:3]):
                seen[ny * w + nx] = 1
                q.append((nx, ny))
    return img, removed > 0


def footprint(img, band=0.10):
    """Measure a prop's base footprint from its own alpha: (centre_x, width) of the bottom band.

    Shadows sized from the SPRITE go wrong, because a sprite's canvas is mostly padding - the
    first attempt used a fixed fraction of the sprite width and review called the result
    "detached blobs". The footprint is what the object actually stands on, so measure it.
    """
    a = np.asarray(img.convert("RGBA"))[:, :, 3] > 128
    ys, xs = np.nonzero(a)
    if len(ys) == 0:
        return None
    y1 = ys.max()
    y0 = int(y1 - max(2, (y1 - ys.min()) * band))
    slab = a[y0:y1 + 1, :]
    cols = np.nonzero(slab.any(axis=0))[0]
    if len(cols) == 0:
        return None
    return ((cols.min() + cols.max()) / 2.0, float(cols.max() - cols.min() + 1), int(y1))


def shadow_layer(size, items, tile_w, tile_h):
    """Two-part shadow per prop, from the DECLARED light vector: a tight contact shadow that
    hugs the measured footprint, plus a longer, softer cast shadow offset along the light.

    Review of the first room named the absence of exactly this as the reason props read as
    pasted on: "identical soft blob shadows with no contact hardening, no consistent light
    direction, no cast shadows".
    """
    L = ROOM["light"]
    v = L["vector"]
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for ax, ay, fp_w in items:
        # contact: slightly larger than the footprint, dark, centred on it
        rx = fp_w / 2.0 * L["contact"]["scale"]
        ry = max(4.0, rx * (tile_h / tile_w))
        d.ellipse([ax - rx, ay - ry, ax + rx, ay + ry],
                  fill=(14, 17, 24, L["contact"]["alpha"]))
        # cast: same footprint pushed along the light vector and stretched
        ox, oy = v["dx"] * fp_w * L["cast"]["length"], v["dy"] * fp_w * L["cast"]["length"] * 0.5
        crx = rx * L["cast"]["scale"]
        cry = ry * L["cast"]["scale"]
        d.ellipse([ax + ox - crx, ay + oy - cry, ax + ox + crx, ay + oy + cry],
                  fill=(12, 15, 22, L["cast"]["alpha"]))
    return layer


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else "andrzej"
    room_path = GEN / agent / "room.json"
    room = json.loads(room_path.read_text())
    out_dir = GEN / agent / "props"

    sys.path.insert(0, str(REPO / "scripts"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("geo", REPO / "scripts" / "room_geometry.py")
    geo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(geo)
    tw, th = geo.ROOM["floor"]["tile_w"], geo.ROOM["floor"]["tile_h"]

    # 1. strip pads
    for prop in room["prop_anchors"]:
        p = out_dir / Path(prop["file"]).name
        if not p.exists():
            continue
        im, changed = strip_bottom_plate(Image.open(p))
        if changed:
            im.save(p)
            print(f"   {prop['id']:8s} base pad removed")

    # 2. recomposite with shadows. Props are anchored by their measured FOOTPRINT, not by their
    # canvas: a sprite is mostly padding, so pasting at (ax - sprite_width/2, ay - sprite_height)
    # puts the object wherever its padding happens to fall. The footprint is what stands on the
    # grid cell, and the base row is what sits at the anchor's y.
    img, _ = geo.draw_room(show_grid=False)
    items = []
    for prop in room["prop_anchors"]:
        p = out_dir / Path(prop["file"]).name
        sp = Image.open(p).convert("RGBA")
        want_w = tw * prop["tiles"]
        s = min(want_w / sp.width, 1.0)
        if s != 1.0:
            sp = sp.resize((max(1, int(sp.width * s)), max(1, int(sp.height * s))), Image.NEAREST)
        fp = footprint(sp)
        fp_cx, fp_w, base_y = fp if fp else (sp.width / 2.0, sp.width * 0.6, sp.height)
        ax, ay = geo.grid_to_screen(prop["grid"][0] + 0.5, prop["grid"][1] + 0.5)
        items.append({"id": prop["id"], "sp": sp, "ax": ax, "ay": ay,
                      "fp_cx": fp_cx, "fp_w": fp_w, "base_y": base_y})

    img = Image.alpha_composite(img.convert("RGBA"),
                                shadow_layer(img.size,
                                             [(it["ax"], it["ay"], it["fp_w"]) for it in items],
                                             tw, th))
    for it in items:
        img.paste(it["sp"], (int(it["ax"] - it["fp_cx"]), int(it["ay"] - it["base_y"])), it["sp"])
        print(f"   {it['id']:9s} footprint {it['fp_w']:5.1f}px  base row {it['base_y']:4d}  "
              f"anchor ({it['ax']:.0f},{it['ay']:.0f})")

    img.convert("RGB").save(GEN / agent / "room_preview.png")
    img.convert("RGB").save(REPO / "assets" / "experiments" / "geometry" / f"{agent}_room_shadowed.png")
    print(f"== recomposited {agent}: {len(items)} props, footprint-anchored, "
          f"light from {ROOM['light']['from']} ==")
    print("FINISH_PROPS_DONE")


if __name__ == "__main__":
    main()
