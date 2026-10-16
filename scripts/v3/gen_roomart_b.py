#!/usr/bin/env python3
"""Generate the v3 art set B: the Andrzej server room and the OpenChamber factory (SOU-199).

Two room sets in ONE module, because they are the same pipeline, the same camera and the same
palette - only the prop tables differ:

    serverroom   the homelab manager's room: gear shelves, a workbench, a UPS stack, a wall
                 network cabinet, a stool, a cable coil
    factory      the "dummy workers" (OpenChamber/OpenCode) room: conveyor belts, an assembler,
                 an inserter, an ore pile, a crate and the control desk (the work zone)

Everything shared with the previous sets is IMPORTED, never copied - the projection vocabulary
(VIEW/STYLE/KEY/NO_TEXT), the keying recipe, the island/despeckle/hole chain, `quantise`,
`fit_to_canvas` and the label/grid overlay come from `gen_office01_art.py`; the 61-colour night
palette comes from `gen_lodge_art.py`. A second copy of the keying recipe is a second thing to
fix, and a second palette is how two sets of the same game stop looking like one game.

Ruling (stated on the ticket, cost recorded): **set B shares the lodge palette** - "one shared
palette per set (warm lodge palette)" is read as "the warm lodge palette for the lodge family".
Cost if wrong: a re-quantise pass, CPU-only, no GPU time (the raws stay on disk).

Licence rules (ADR-061, non-negotiable): FLUX.2-klein-4B (Apache-2.0) only; **no LoRA** (it
collapses to black in diffusers - ADR-060 field result); text-only prompts; the reference-edit
path (`asset["reference"]`) may only ever point at OUR OWN klein output; never trained on
Star-Office or LimeZu pixels; no marks, no readable text, no signage. Nothing here is derived
from the owner's reference images: they are described in words in the ticket and those words are
what the prompts carry.

Two passes, so a layout change never costs GPU time:

    generate   FLUX.2-klein-4B on MPS -> <id>.raw.png + prompt + seed + mode
    assemble   CPU-only, deterministic from the raws: key -> derive frames -> fit -> quantise ->
               manifests -> contact sheets

Frames are DERIVED, and each derivation is declared in the manifest:

    belts      a cyclic tread scroll: frames 1-3 are the base translated by 8/16/24 px along the
               belt's travel axis and wrapped inside the tile, so the four frames loop seamlessly
               (the corner shifts the lower-left half in x and the upper-right half in y - the two
               arms of the bend). Byte-deterministic and reproducible.
    assembler  frame 2 is the SAME base with the energy colours stepped up their declared ramps -
               "working glow", not a second generation.
    inserter   frame 2 is the base with the arm region (the upper 45% of its own content bbox)
               lifted 4px through `_region_shift`, which keeps the pre-shift pixels as an underlay
               so the frame cannot punch itself full of holes (SOU-187 defect 1).

Belts are FULL-BLEED: a belt section must meet the next section without a floor-coloured gap, so
the edge pixels of the three belt tiles are extended outward (declared here and asserted by the
gate as `transparent == 0`), unlike every other prop in the set.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_roomart_b.py generate --set serverroom
    $VENV/bin/python scripts/v3/gen_roomart_b.py generate --set factory
    python3 scripts/v3/gen_roomart_b.py assemble --set factory
    python3 scripts/v3/gen_roomart_b.py clean --set serverroom
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gen_office01_art as base  # noqa: E402  (shared pipeline machinery)
import gen_lodge_art as lodge  # noqa: E402  (the shared lodge palette)

from gen_office01_art import (  # noqa: E402
    KEY,
    NO_TEXT,
    STYLE,
    VIEW,
    _label,
    _nearest,
    _region_shift,
    base_row_coverage,
    content_bbox,
    content_bottom_row,
    despeckle_outline,
    drop_stray_islands,
    fill_interior_holes,
    key_magenta,
    quantise,
)

REPO = HERE.parent.parent
TILE = 32

MODEL_ID = base.MODEL_ID
LORA_ID = base.LORA_ID
STEPS = base.STEPS
GUIDANCE = base.GUIDANCE

# One palette for the whole lodge family - the same object the lodge set declares.
PALETTE = lodge.PALETTE
PALETTE_HEX = lodge.PALETTE_HEX
PALETTE_RGB = lodge.PALETTE_RGB
PALETTE_BY_NAME = {n: h for n, h in PALETTE}

SETS = {
    "serverroom": {
        "pack": "serverroom-v1",
        "issue": "SOU-199",
        "title": "Andrzej's server room",
        "map": "serverroom.json",
        "backdrop": "stone_floor",
        "look": ("Inside a cosy log-cabin lodge at night: honey-brown timber surfaces, warm amber "
                 "lamplight falling from the top-left, deep blue shadow, and teal-green and orange "
                 "LED accents on the electronic gear. "),
    },
    "factory": {
        "pack": "factory-v1",
        "issue": "SOU-199",
        "title": "the OpenChamber / OpenCode factory",
        "map": "factory.json",
        "backdrop": "metal_grate",
        "look": ("Inside a cosy log-cabin workshop at night: chunky riveted steel machinery with "
                 "warm brass and honey-brown timber fittings, warm amber task light falling from "
                 "the top-left, deep blue shadow, and teal-green and orange machine indicator "
                 "lights. "),
    },
}

# --------------------------------------------------------------------------------------
# The prop tables - THIS is the ticket's table, restated once and asserted against a second
# copy of it in check_v3_assets.py (a gate that only compares an asset to its own declaration
# cannot catch a declaration that drifted from the ticket).
#
# texture_px  the exact canvas that ships (single frame; for sheets, one frame)
# footprint   the map rect the renderer anchors: `placeholders.js` PROP_STYLE draws
#             footprint_w x (footprint_h + lift) with origin (0.5, 1)
# lift_px     texture_h - footprint_h
# --------------------------------------------------------------------------------------

# ramp used by the assembler's "working glow" frame: every energy colour one or two steps up.
GLOW_RAMP = {
    "teal": "led_teal",
    "teal_light": "ice",
    "amber_deep": "amber",
    "amber": "gold",
    "brass": "brass_light",
    "metal": "metal_light",
}


def _asset(pid, subset, subject, texture, footprint, lift, *, frames=1, anim=None, flat=False,
           post="full", full_bleed=False, connects=None, note=""):
    kind = "sheet" if frames > 1 else "prop"
    fw, fh = texture
    out = {
        "id": pid,
        "kind": kind,
        "set": subset,
        "subject": subject,
        "texture_px": [fw, fh],
        "sheet_px": [fw * frames, fh] if kind == "sheet" else None,
        "frames": frames,
        "animation": anim,
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        # SOU-199 asks for "exactly the size given", so props are fitted INSIDE the declared box
        # and the canvas is always exactly it (same ruling as the lodge set, SOU-198).
        "fit_mode": "inside",
        "flat": flat,
        "post": post,
        "full_bleed": full_bleed,
        # Tile edges this asset has to CONNECT to the neighbouring tile on (a belt section): the
        # gate asserts content on each named edge in EVERY frame, which is the property that
        # actually matters. It is declared instead of assuming "no transparent pixel anywhere",
        # because a corner bend legitimately leaves the outside of the bend as floor.
        "connects": connects,
        "note": note,
    }
    return out


SERVERROOM_PROPS = [
    _asset(
        "gear_shelf_a", "serverroom",
        "a tall narrow wooden shelving unit packed with small homelab electronics from the floor "
        "to the top shelf: black and dark grey network appliances with rows of tiny glowing "
        "teal-green and orange status lights, a small fan grille, short cables looping between "
        "the boxes, and a potted green plant sitting on the very top shelf. Seen from above and "
        "slightly in front, narrow and tall",
        (64, 112), (64, 32), 80,
        note="tall shelf; footprint is the 2x1 floor rect it stands on, the extra height is lift"),
    _asset(
        "gear_shelf_b", "serverroom",
        "a tall narrow wooden shelving unit full of different homelab gear: a stack of flat "
        "silver and black servers, one older beige box, coiled cables and a small woven basket, "
        "with a lit brass lantern standing on the middle shelf and more orange LED lights than "
        "the neighbouring shelf. Seen from above and slightly in front, narrow and tall",
        (64, 112), (64, 32), 80,
        note="variant of gear_shelf_a: different boxes, a lantern on a shelf"),
    _asset(
        "workbench", "serverroom",
        "a chunky wooden workbench with a thick honey-brown timber top, seen from above and "
        "slightly in front: a small retro computer monitor with a dark green glowing screen "
        "standing on it, a brass desk lamp on a curved arm throwing warm amber light, a white "
        "coffee mug, and two screwdrivers and a wrench lying flat on the top",
        (64, 80), (64, 64), 16),
    _asset(
        "ups_stack", "serverroom",
        "a stack of three black uninterruptible power supply units piled on the floor, seen from "
        "above and slightly in front: thick steel front panels, a small row of glowing amber "
        "indicator lights on each unit, and short thick power cables looping between them. Tall "
        "and narrow",
        (32, 64), (32, 32), 32),
    _asset(
        "cable_coil", "serverroom",
        "one flat coiled cable lying on the floor seen from directly above: a neat tight coil of "
        "dark grey cable with two short cable ends sticking out to the right, completely flat, "
        "square",
        (32, 32), (32, 32), 0, flat=True, post="light",
        note="flat floor decal; base_row_coverage is checked as a flat object"),
    _asset(
        "stool", "serverroom",
        "a small round wooden stool seen from above and slightly in front: a round honey-brown "
        "seat with three short dark wooden legs, slightly taller than it is wide",
        (32, 40), (32, 32), 8, post="light"),
    _asset(
        "network_cabinet", "serverroom",
        "a small wall-mounted network cabinet seen from the front: a slim dark metal box with a "
        "glass door, a horizontal patch panel inside with rows of sockets and short patch cables "
        "plugged into them, and small blinking teal and orange status lights along the units. Very "
        "tall and narrow",
        (32, 80), (32, 32), 48,
        note="wall cabinet: 1x1 floor rect, 48px of lift carries it up the wall"),
]

FACTORY_ASSETS = [
    _asset(
        "belt_h", "factory",
        "a single straight section of a chunky steel conveyor belt running horizontally across "
        "the frame, seen from directly above: a dark rubber belt carrying evenly spaced raised "
        "cross slats, gripped between two thick grey steel side rails that run along the top edge "
        "and the bottom edge of the frame, with a warm amber rim light on the rails. The belt and "
        "its rails fill the whole frame edge to edge, perfectly square",
        (32, 32), (32, 32), 0, frames=4, anim="tread_x", full_bleed=True, connects=["left", "right"],
        post="light",
        note="4-frame loop: frames 1-3 are the base's tread scrolled 8/16/24px east and wrapped"),
    _asset(
        "belt_v", "factory",
        "a single straight section of a chunky steel conveyor belt running vertically through the "
        "frame from the bottom edge to the top edge, seen from directly above: a dark rubber belt "
        "carrying evenly spaced raised cross slats, gripped between two thick grey steel side "
        "rails along the left edge and the right edge of the frame, with a warm amber rim light "
        "on the rails. The belt and its rails fill the whole frame edge to edge, perfectly square",
        (32, 32), (32, 32), 0, frames=4, anim="tread_y", full_bleed=True, connects=["top", "bottom"],
        post="light",
        note="same section rotated 90 degrees; the frames scroll north"),
    _asset(
        "belt_corner", "factory",
        "a right-angle corner section of a chunky steel conveyor belt seen from directly above: the "
        "belt enters flat through the LEFT edge of the frame in the lower half, runs straight to "
        "the right, turns one sharp 90 degree bend in the bottom-right corner and then runs "
        "straight up, leaving through the TOP edge on the right side. Both arms are strictly "
        "HORIZONTAL and VERTICAL - this is an L-shaped corner and NOT a diagonal belt and NOT a "
        "curved S. The outside of the bend is rounded by a curved steel guard plate; dark rubber "
        "tread slats follow the path between thick grey steel side rails with a warm amber rim "
        "light, and the upper-left area inside the bend is empty background",
        (32, 32), (32, 32), 0, frames=4, anim="tread_corner", full_bleed=True, post="light",
        connects=["left", "right", "top", "bottom"],
        note="east->north bend; frames scroll the horizontal arm in x and the vertical arm in y. "
             "FULL-BLEED, and that is a ruling, not a guess: this belt is ONE tile wide, so it "
             "fills a tile top-to-bottom while running east and left-to-right while running north "
             "- which means the corner tile is belt all the way to its four edges and the inside "
             "of the bend is a curved guard plate, not floor. Two rolls were spent learning it: "
             "the first prompt produced a diagonal band that would not join belt_h/belt_v at all, "
             "and the assertion that failed in between (transparent px at the inside of the bend) "
             "was the wrong contract, not the wrong art."),
    _asset(
        "assembler", "factory",
        "a chunky boxy assembly machine seen from above and slightly in front: a riveted steel and "
        "dark timber box with a glowing warm amber window in the middle of its front face, two "
        "toothed gear cogs visible on its right side, small teal indicator lights in a row along "
        "the top edge and a flat wide base plate under it",
        (64, 80), (64, 64), 16, frames=2, anim="glow", post="light",
        note="frame 2 is the same base with the energy colours stepped up their ramps (working glow)"),
    _asset(
        "inserter", "factory",
        "a small robotic arm machine seen from above and slightly in front: a squat dark steel base "
        "with a jointed arm folded up and reaching over to the right, a small claw gripper at the "
        "end of the arm, a warm amber indicator light on the base and a short cable running into "
        "the base. Taller than it is wide",
        (32, 48), (32, 32), 16, frames=2, anim="arm", post="light",
        note="frame 2 lifts the arm region 4px (the working pose)"),
    _asset(
        "item_box", "factory",
        "a tiny square wooden crate lying flat, seen from directly above: honey-brown planks on top "
        "with darker banding around all four edges and one small metal corner bracket, square",
        (16, 16), (16, 16), 0, flat=True, post="light", full_bleed=True,
        note="rides the belt: no floor footprint of its own, so the crate is assessed flat and "
             "full-bleed at 16x16 (one belt tile is 32px, i.e. two items per tile)"),
    _asset(
        "ore_pile", "factory",
        "a small pile of rough grey stone ore resting on the floor seen from above: a low flat mound "
        "of angular rocks with a few tiny warm amber mineral specks in them, roughly square and low",
        (32, 32), (32, 32), 0, flat=True, post="light"),
    _asset(
        "control_desk", "factory",
        "a wide control desk seen from above and slightly in front: a chunky steel and timber "
        "console with a row of three round dials, one big brass lever, a small glowing teal screen "
        "and a blank notepad lying on the top, on two short steel legs at the front. Wider than it "
        "is deep",
        (64, 64), (64, 32), 32),
]

ASSETS = {"serverroom": SERVERROOM_PROPS, "factory": FACTORY_ASSETS}


def out_dir(setname: str) -> Path:
    return REPO / "assets" / "generated" / "v3" / setname


def served_dir(setname: str) -> Path:
    return REPO / "frontend" / "v3" / "assets" / setname


# --------------------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------------------
RATIO_WORD = {
    (64, 112): "The object's bounding box is about half as wide as it is tall",
    (64, 96): "The object's bounding box is about two thirds as wide as it is tall",
    (64, 80): "The object's bounding box is wider than it is tall",
    (64, 64): "The object's bounding box is square",
    (32, 80): "The object's bounding box is about a third as wide as it is tall",
    (32, 64): "The object's bounding box is about half as wide as it is tall",
    (32, 48): "The object's bounding box is taller than it is wide",
    (32, 40): "The object's bounding box is slightly taller than it is wide",
    (32, 32): "The object's bounding box is square",
    (16, 16): "The object's bounding box is square",
}


def _ratio_clause(asset):
    """The exact aspect of the SHIPPED canvas, stated numerically and in words.

    Both are needed and the lodge set proved it: a vague ratio word is not a specification (a
    "tall thin iron lamp post" came back 2.4x taller than its declared box), while the numbers
    alone still let the model leave a wide empty margin. So: the number, the word, and an
    explicit "fill the rectangle, no margin" clause.
    """
    tw, th = asset["texture_px"]
    word = RATIO_WORD.get((tw, th), "")
    return (f"The object's bounding box must be EXACTLY {tw}:{th} in width:height"
            + (f" ({word.lower()})" if word else "")
            + ". Draw the object to fill that rectangle: no empty margin above, below or to the "
              "sides, and do not make it taller or narrower than that proportion. ")


def _palette_clause():
    return "Palette limited to exactly these colours: " + ", ".join(PALETTE_HEX) + ". "


def compose_prompt(asset):
    look = SETS[asset["set"]]["look"]
    head = (f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in "
            f"frame. {_ratio_clause(asset)}")
    tail = (_palette_clause() + look + VIEW + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game asset.")
    return head + tail


def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640 (same as office-01)."""
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


# --------------------------------------------------------------------------------------
# frame derivations (all deterministic; every one is recorded per frame in the manifest)
# --------------------------------------------------------------------------------------
def _cycle(img, dx=0, dy=0):
    """Translate the whole image and wrap the content that leaves the canvas back in.

    This is what makes the four belt frames a LOOP: frame 4's tread is one 8px step away from
    frame 0's, so the animation has no jump when it wraps. Whole-pixel only, no resampling.
    """
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if dx:
        dx %= w
        out.paste(img.crop((w - dx, 0, w, h)), (0, 0))
        out.paste(img.crop((0, 0, w - dx, h)), (dx, 0))
    elif dy:
        dy %= h
        out.paste(img.crop((0, h - dy, w, h)), (0, 0))
        out.paste(img.crop((0, 0, w, h - dy)), (0, dy))
    return out


def _diagonal_mask(size):
    """Lower-left triangle (y >= x) - the incoming arm of an east->north belt corner."""
    w, h = size
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (0, h), (w - 1, h - 1)], fill=255)
    return mask


def _corner_frame(base_img, shift):
    """Scroll the corner's two arms independently: x in the lower-left half, -y in the upper-right.

    Honest statement of what this is: a corner cannot be a single-axis scroll, so the tile is
    split on the bend's bisector and each half is scrolled along its own arm. The split lands on
    the bend where the base art has its roller/guard plate, which is why it reads as a moving
    belt rather than as a torn image.
    """
    horizontal = _cycle(base_img, dx=shift)
    vertical = _cycle(base_img, dy=-shift)
    out = vertical.copy()
    out.paste(horizontal, (0, 0), _diagonal_mask(base_img.size))
    return out


def _glow_frame(base_img):
    """Step the energy colours up their declared ramps - the machine's working glow.

    Runs on a QUANTISED frame on purpose. The first version remapped the raw FLUX image by exact
    hex match against the palette, matched nothing (a diffusion output is not on the declared
    palette before quantise), and shipped two identical frames - which the gate caught as
    "frames [1] identical to frame 0". Remapping after quantise makes the LUT match by
    construction, and the two frames then differ by exactly the declared ramp steps.
    """
    out = base_img.copy()
    px = out.load()
    w, h = out.size
    lut = {PALETTE_BY_NAME[k]: PALETTE_BY_NAME[v] for k, v in GLOW_RAMP.items()}
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                continue
            hexv = "#%02x%02x%02x" % (r, g, b)
            if hexv in lut:
                d = lut[hexv]
                px[x, y] = (int(d[1:3], 16), int(d[3:5], 16), int(d[5:7], 16), a)
    return out


def _arm_frame(base_img):
    """Lift the arm region of the content bbox by 4px (through `_region_shift`: hole-free)."""
    bbox = content_bbox(base_img)
    if bbox is None:
        return base_img.copy()
    x0, y0, x1, y1 = bbox
    arm_bottom = y0 + max(2, int((y1 - y0) * 0.45))
    out = base_img.copy()
    return _region_shift(out, (x0, y0, x1, arm_bottom), 0, -4)


def _frames_for(asset, keyed):
    """Every frame of an asset, frame 0 being the untouched FLUX base."""
    anim = asset.get("animation")
    n = asset["frames"]
    frames = [keyed.copy()]
    deriv = {"0": "the FLUX base (contact), unchanged"}
    for k in range(1, n):
        if anim == "tread_x":
            frames.append(_cycle(keyed, dx=8 * k))
            deriv[str(k)] = f"base tread scrolled {8 * k}px east, wrapped (cyclic)"
        elif anim == "tread_y":
            frames.append(_cycle(keyed, dy=8 * k))
            deriv[str(k)] = f"base tread scrolled {8 * k}px north, wrapped (cyclic)"
        elif anim == "tread_corner":
            frames.append(_corner_frame(keyed, 8 * k))
            deriv[str(k)] = (f"lower-left arm scrolled {8 * k}px east and upper-right arm "
                             f"{8 * k}px north, each wrapped (cyclic), split on the bend bisector")
        elif anim == "glow":
            frames.append(keyed.copy())
            deriv[str(k)] = ("same base; the working glow is applied AFTER quantise - the energy "
                             "colours step up their declared ramps ("
                             + ", ".join(f"{a}->{b}" for a, b in GLOW_RAMP.items()) + ")")
        elif anim == "arm":
            frames.append(_arm_frame(keyed))
            deriv[str(k)] = "arm region (upper 45% of the content bbox) lifted 4px, underlay kept"
        else:
            raise SystemExit(f"{asset['id']}: {n} frames declared but no animation kind")
    return frames, deriv


# --------------------------------------------------------------------------------------
# fit / post-process
# --------------------------------------------------------------------------------------
def _bleed_edges(img, max_passes=4):
    """Extend the outermost opaque pixels of every row/column outward until nothing is transparent.

    A belt section is a tile: if it ships with a transparent margin, the section next to it shows
    the floor through the gap and the belt does not read as continuous. This is the ONLY place in
    the pipeline that grows a silhouette, and it is declared per asset (`full_bleed`) and asserted
    by the gate (`transparent == 0`) - not a general "make it look solid" pass.
    """
    img = img.convert("RGBA")
    w, h = img.size
    limit = max(w, h)
    changed_total = 0
    for _ in range(max_passes):
        px = img.load()

        def first_opaque(seq):
            for i, (x, y) in enumerate(seq):
                if px[x, y][3]:
                    return i
            return None

        changed = 0
        for y in range(h):
            i = first_opaque([(x, y) for x in range(limit)])
            if i is not None and i > 0:
                for x in range(i):
                    px[x, y] = px[i, y]
                    changed += 1
                j = first_opaque([(x, y) for x in range(w - 1, -1, -1)])
                if j is not None and j > 0:
                    for x in range(w - 1, w - 1 - j, -1):
                        px[x, y] = px[w - 1 - j, y]
                        changed += 1
        for x in range(w):
            i = first_opaque([(x, y) for y in range(limit)])
            if i is not None and i > 0:
                for y in range(i):
                    px[x, y] = px[x, i]
                    changed += 1
                j = first_opaque([(x, y) for y in range(h - 1, -1, -1)])
                if j is not None and j > 0:
                    for y in range(h - 1, h - 1 - j, -1):
                        px[x, y] = px[x, h - 1 - j]
                        changed += 1
        changed_total += changed
        if changed == 0:
            break
    # A row or column that is transparent ALL THE WAY across cannot be reached by the edge passes
    # (there is no leading opaque pixel to extend). Spreading into the remaining holes 1px per
    # round closes those, which is what "the tile is opaque" actually requires.
    for _ in range(max(w, h)):
        px = img.load()
        updates = {}
        for y in range(h):
            for x in range(w):
                if px[x, y][3]:
                    continue
                for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3]:
                        updates[(x, y)] = px[nx, ny]
                        break
        if not updates:
            break
        for (x, y), colour in updates.items():
            px[x, y] = colour
        changed_total += len(updates)
    return img, changed_total


def _fit_series(frames, size, mode="inside"):
    """Fit a whole animation with ONE scale and ONE crop, measured from frame 0.

    Fitting each frame independently would change scale/offset between frames (each frame's own
    content bbox differs by the very pixels the derivation moved), and the sprite would jitter
    instead of animating. The crop rectangle is therefore taken from frame 0 and reused verbatim.

    mode="inside" (props)  scale uniformly so the object sits inside the declared box - the canvas
                           is exactly the box and a slim silhouette is centred in it.
    mode="cover"  (belts)  TAKE A CENTRED SQUARE of the content and fill the frame with it. A belt
                           section is a tile, not an object: it has to meet the next section with
                           rails on both edges and tread between them, so the frame is FILLED (the
                           first pass fitted belt_h inside its box and produced a 32x14 letterbox
                           band with 18 transparent rows - neighbouring sections would have shown
                           floor above the belt). The square crop keeps the model's own pixel
                           scale: the band's own height becomes the tile, so the tread stays
                           readable instead of being shrunk to fit.
    """
    w, h = size
    bbox = content_bbox(frames[0])
    if bbox is None:
        return [Image.new("RGBA", size, (0, 0, 0, 0)) for _ in frames], {"empty": True}
    cw, ch = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if mode == "cover":
        side = max(1, min(cw, ch))
        cx, cy = (bbox[0] + bbox[2]) // 2, (bbox[1] + bbox[3]) // 2
        x0 = min(max(0, cx - side // 2), frames[0].size[0] - side)
        y0 = min(max(0, cy - side // 2), frames[0].size[1] - side)
        crop_bbox = (x0, y0, x0 + side, y0 + side)
        nw, nh = w, h
    else:
        scale = min(w / cw, h / ch)
        nw, nh = max(1, round(cw * scale)), max(1, round(ch * scale))
        crop_bbox = bbox
    out = []
    for f in frames:
        crop = f.crop(crop_bbox).resize((nw, nh), _nearest())
        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        canvas.alpha_composite(crop, ((w - nw) // 2, h - nh))
        out.append(canvas)
    return out, {
        "content_px": [cw, ch],
        "fitted_px": [nw, nh],
        "canvas_px": [w, h],
        "mode": mode,
        "scale": round(nw / cw, 4),
        "crop_bbox": list(crop_bbox),
        "shared_across_frames": True,
        "why": ("centred square of the content, filling the tile (a belt section must meet its "
                "neighbour edge to edge)" if mode == "cover" else
                "uniform scale to sit inside the declared box (props ship at exactly the size "
                "the ticket declares)"),
    }


def _post(img, mode):
    """Island/despeckle/hole chain, per asset, because one aggressiveness does not fit all.

    The SOU-198 lesson: a loose boundary pass ate 2 552 px off a curled-up cat. Tiny assets
    (16x16 crate, 32x32 flat decals) and the full-bleed belts therefore only get the hole fill.
    """
    meta = {}
    if mode == "none":
        return img, meta
    if mode == "full":
        img, islands, kept = drop_stray_islands(img)
        meta["stray_islands_dropped_px"] = islands
        meta["disconnected_parts_kept_px"] = kept
    img, speckles = despeckle_outline(img) if mode == "full" else (img, 0)
    meta["despeckled_px"] = speckles
    img, holes = fill_interior_holes(img, palette=PALETTE_RGB)
    meta["interior_holes_filled_px"] = holes
    return img, meta


def _colours(img):
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    seen = set()
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a:
                seen.add((r, g, b))
    return sorted(seen)


def _off_palette(img):
    allowed = set(PALETTE_RGB)
    return [c for c in _colours(img) if tuple(c) not in allowed]


def _manifest(asset, *, seed, prompt, source_file, output_file, output_dims, extra):
    payload = {
        "model": MODEL_ID,
        "lora": LORA_ID,
        "lora_strength": 0.0,
        "steps": STEPS,
        "guidance": GUIDANCE,
        "prompt": prompt,
        "seed": seed,
        "source_file": source_file,
        "output_file": output_file,
        "output_dims": list(output_dims),
        "palette_colors": len(PALETTE_HEX),
        "palette": PALETTE_HEX,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asset_key": asset["id"],
        "kind": asset["kind"],
        "set": asset["set"],
        "view": "top-down 3/4",
    }
    payload.update(extra)
    return payload


# --------------------------------------------------------------------------------------
# generate
# --------------------------------------------------------------------------------------
def _read(asset, suffix, default=None):
    f = out_dir(asset["set"]) / f"{asset['id']}.{suffix}"
    return f.read_text().strip() if f.exists() else default


def cmd_generate(args):
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    only = set(args.only.split(",")) if args.only else None
    pipe = None
    torch_mod = None
    for setname in args.set:
        out = out_dir(setname)
        out.mkdir(parents=True, exist_ok=True)
        for index, asset in enumerate(ASSETS[setname]):
            aid = asset["id"]
            if only is not None and aid not in only:
                continue
            raw = out / f"{aid}.raw.png"
            if raw.exists() and not args.force:
                print(f"[skip] {aid:16s} raw already on disk")
                continue
            if pipe is None:
                pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060)
            fw, fh = asset["texture_px"]
            gw, gh = _gen_size(fw, fh)
            seed = 19900 + index * 7 + args.seed_shift
            prompt = compose_prompt(asset)
            print(f"[gen ] {setname}/{aid:16s} {gw}x{gh} seed={seed}", flush=True)
            image = _generate(frg, pipe, torch_mod, prompt, gw, gh, seed=seed)
            image.save(raw)
            (out / f"{aid}.seed.txt").write_text(f"{seed}\n")
            (out / f"{aid}.mode.txt").write_text("text-only generation\n")
            (out / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")


def _generate(frg, pipe, torch_mod, prompt, width, height, seed):
    return frg.generate(pipe, torch_mod, prompt, width, height, seed=seed,
                        steps=STEPS, guidance=GUIDANCE)


# --------------------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------------------
def cmd_assemble(args):
    for setname in args.set:
        _assemble_set(setname)
    print("ASSEMBLE_DONE")


def _assemble_set(setname):
    out = out_dir(setname)
    (out / "props").mkdir(parents=True, exist_ok=True)

    (out / "palette.json").write_text(json.dumps({
        "name": SETS[setname]["pack"],
        "set": setname,
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "shared_with": "lodge (SOU-198)",
        "why": "One palette for the whole lodge family (SOU-199 ruling). Set B is a room set, not "
               "a new game: the server room and the factory sit in the same lodge as the corridor "
               "and the office, so they quantise to the SAME 61 declared colours as the lodge set "
               "rather than to a second palette that would drift the two apart. The colour tables "
               "live in scripts/v3/gen_lodge_art.py and are imported here - there is one copy.",
        "light": "top-left (same as every v3 set): each material ramp runs dark (bottom-right "
                 "faces) to light (top-left faces)",
    }, indent=2) + "\n")

    results = {}
    for asset in ASSETS[setname]:
        aid = asset["id"]
        raw = out / f"{aid}.raw.png"
        if not raw.exists():
            print(f"[miss] {setname}/{aid}: no raw generation on disk")
            continue
        seed = int(_read(asset, "seed.txt", "0") or 0)
        prompt = _read(asset, "prompt.txt", compose_prompt(asset))
        mode = _read(asset, "mode.txt", "text-only generation")
        keyed_img, key = key_magenta(Image.open(raw))
        keyed_img.save(out / f"{aid}.keyed.png")

        frames_raw, deriv = _frames_for(asset, keyed_img)
        # Tile-like assets (a belt section: full-bleed or declaring which edges it joins) FILL the
        # frame; props are fitted inside their declared box.
        fitted, fit = _fit_series(
            frames_raw, tuple(asset["texture_px"]),
            mode="cover" if (asset["full_bleed"] or asset["connects"]) else "inside")
        bleed_px = 0
        treated = []
        for frame in fitted:
            frame, post_meta = _post(frame, asset["post"])
            if asset["full_bleed"]:
                frame, bleed_px = _bleed_edges(frame)
            treated.append(frame)

        n = asset["frames"]
        fw, fh = asset["texture_px"]
        treated_q = [quantise(f, palette=PALETTE_RGB) for f in treated]
        if asset["animation"] == "glow":
            # the working glow is a palette-space step, so it runs on the quantised frames - see
            # `_glow_frame`: remapping the raw image matched nothing and produced two identical
            # frames (caught by the gate, fixed here).
            treated_q = [treated_q[0]] + [_glow_frame(f) for f in treated_q[1:]]
        sheet = Image.new("RGBA", (fw * n, fh), (0, 0, 0, 0))
        for k, frame in enumerate(treated_q):
            sheet.alpha_composite(frame, (k * fw, 0))
        sheet = quantise(sheet, palette=PALETTE_RGB)
        sheet.save(out / "props" / f"{aid}.png")

        frames = [sheet.crop((k * fw, 0, (k + 1) * fw, fh)).convert("RGBA") for k in range(n)]
        edge_contact = {}
        for edge in ("left", "right", "top", "bottom"):
            row = []
            for f in frames:
                px = f.load()
                if edge == "left":
                    row.append(any(px[0, y][3] for y in range(fh)))
                elif edge == "right":
                    row.append(any(px[fw - 1, y][3] for y in range(fh)))
                elif edge == "top":
                    row.append(any(px[x, 0][3] for x in range(fw)))
                else:
                    row.append(any(px[x, fh - 1][3] for x in range(fw)))
            edge_contact[edge] = row
        meta = {
            "footprint_px": asset["footprint_px"],
            "footprint_tiles": asset["footprint_tiles"],
            "texture_px": list(asset["texture_px"]),
            "declared_px": list(asset["texture_px"]),
            "lift_px": asset["lift_px"],
            "fit_mode": asset["fit_mode"],
            "fit": fit,
            "anchor": "bottom-centre of the footprint (renderer origin 0.5, 1)",
            "key_colour": list(key),
            "generation_mode": mode,
            "frames": n,
            "frame_px": list(asset["texture_px"]),
            "sheet_px": [fw * n, fh],
            "frame_derivation": deriv,
            "post_process": asset["post"],
            "full_bleed": asset["full_bleed"],
            "connects": asset["connects"],
            "edge_contact_per_frame": edge_contact,
            "edge_bleed_px": bleed_px,
            "content_bottom_row_per_frame": [content_bottom_row(f) for f in frames],
            "opaque_bottom_row_per_frame": [base_row_coverage(f, rows=2) for f in frames],
            "base_row_coverage": round(base_row_coverage(frames[0]), 3),
            "colours": len(_colours(sheet)),
            "off_palette": _off_palette(sheet),
            "transparent_px_per_frame": [sum(1 for p in f.getchannel("A").getdata() if p == 0)
                                         for f in frames],
        }
        if asset.get("note"):
            meta["note"] = asset["note"]
        prefix = "props/"
        manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                            output_file=f"{prefix}{aid}.png",
                            output_dims=list(sheet.size), extra=meta)
        (out / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"path": f"props/{aid}.png", **meta}
        print(f"[asm ] {setname}/{aid:16s} {sheet.size[0]}x{sheet.size[1]} "
              f"({n} frame(s) of {fw}x{fh}) fit={fit.get('fitted_px')} "
              f"colours={meta['colours']} off={len(meta['off_palette'])} bleed={bleed_px}")

    build_contact_sheets(setname, results)
    if setname == "factory":
        build_belt_strip(results)


# --------------------------------------------------------------------------------------
# contact sheets: everything on the set's own floor
# --------------------------------------------------------------------------------------
def _tile_path(name):
    """Set B ships no tiles: it reuses the lodge material tiles (declared, not copied)."""
    return REPO / "assets" / "generated" / "v3" / "lodge" / "tiles" / f"{name}.png"


def _backdrop(tile_name, size, scale):
    path = _tile_path(tile_name)
    tile = (Image.open(path).convert("RGBA") if path.exists()
            else Image.new("RGBA", (TILE, TILE), (40, 44, 56, 255)))
    if scale != 1:
        tile = tile.resize((TILE * scale, TILE * scale), _nearest())
    img = Image.new("RGBA", size, (20, 22, 31, 255))
    step = TILE * scale
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            img.alpha_composite(tile, (x, y))
    return img


def _text_width(text, scale):
    """Label width in pixels for `_label`'s own font - so a row can be sized to fit its text.

    Measured need: at 4x the metadata labels are ~1000px wide while the objects are 256px, so a
    fixed 1024px canvas produced overlapping labels (an independent visual pass read the two
    shelf labels as one garbled string). The canvas is now as wide as the widest label needs.
    """
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=11 * scale)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    return int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=font))


def _flow(items, width, margin, gap, scale=1):
    """Greedy wrap of (label, image) pairs; a row reserves room for the wider of image and label."""
    x, y, row_h, placed = margin, 0, 0, []
    for label, img in items:
        w, h = img.size
        eff = max(w, _text_width(label, scale))
        if x + eff > width - margin and x > margin:
            x = margin
            y += row_h + gap * 3
            row_h = 0
        placed.append((label, img, x, y, h))
        x += eff + gap
        row_h = max(row_h, h)
    return placed, y + row_h


def build_contact_sheets(setname, results):
    backdrop = SETS[setname]["backdrop"]
    out = out_dir(setname)
    assets = [a for a in ASSETS[setname] if (out / "props" / f"{a['id']}.png").exists()]
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap, header = 16 * scale, 14 * scale, 26 * scale
        items = []
        for asset in assets:
            img = Image.open(out / "props" / f"{asset['id']}.png").convert("RGBA")
            img = img.resize((img.size[0] * scale, img.size[1] * scale), _nearest())
            items.append((_asset_label(asset, scale), img))
        widest = max(_text_width(label, scale) for label, _im in items)
        width = max(1024, 2 * margin + widest + gap)
        placed, body_h = _flow(items, width, margin, gap, scale)
        sheet_h = margin + header + body_h + margin + header * 2
        img = _backdrop(backdrop, (width, sheet_h), scale)
        base._grid_overlay(img, scale)
        _label(img, (margin, margin),
               f"{setname} v3 art set B (SOU-199) - {len(assets)} assets on {backdrop} "
               f"(the lodge material tile this room uses). {len(PALETTE_HEX)}-colour declared "
               f"palette. Multi-frame assets are shown as their full strip.",
               scale=scale)
        for label, prop, px, py, h in placed:
            img.alpha_composite(prop, (margin + px, margin + header + py))
            _label(img, (margin + px, margin + header + py + h + 2), label, scale=scale)
        img.save(out / name)
        print(f"[asm ] {setname}/{name:20s} {img.size[0]}x{img.size[1]}")


def _asset_label(asset, scale):
    tw, th = asset["texture_px"]
    txt = (f"{asset['id']} {tw * asset['frames']}x{th}"
           + (f" ({asset['frames']}x{tw}x{th})" if asset["frames"] > 1 else ""))
    txt += (f" fp {asset['footprint_px'][0]}x{asset['footprint_px'][1]}"
            f" lift {asset['lift_px']}")
    return txt


def build_belt_strip(results):
    """The belt frames as a strip: 3 belt kinds x 4 frames, on the factory floor."""
    out = out_dir("factory")
    belts = [a for a in FACTORY_ASSETS if a["id"].startswith("belt_")
             and (out / "props" / f"{a['id']}.png").exists()]
    if not belts:
        return
    scale = 4
    margin, gap, header = 16 * scale, 10 * scale, 26 * scale
    fw = fh = TILE * scale
    labels = [f"{a['id']} - {a['frames']} frames of {TILE}x{TILE} ({a['animation']})"
              for a in belts]
    width = max(margin * 2 + fw * 4 + gap * 3,
                margin * 2 + max(_text_width(t, scale) for t in labels))
    height = margin + header + len(belts) * (fh + gap + header) + margin
    img = _backdrop("metal_grate", (width, height), scale)
    base._grid_overlay(img, scale)
    _label(img, (margin, margin),
           "SOU-199 belt loop - 4 frames of each belt section, left to right = the animation "
           "order. On metal_grate, the tile this room uses.", scale=scale)
    y = margin + header
    for asset, label in zip(belts, labels):
        sheet = Image.open(out / "props" / f"{asset['id']}.png").convert("RGBA")
        for k in range(asset["frames"]):
            frame = sheet.crop((k * TILE, 0, (k + 1) * TILE, TILE))
            frame = frame.resize((fw, fh), _nearest())
            img.alpha_composite(frame, (margin + k * (fw + gap), y))
        _label(img, (margin, y + fh + 2), label, scale=scale)
        y += fh + gap + header
    img.save(out / "belts_strip_4x.png")
    print(f"[asm ] factory/belts_strip_4x.png   {img.size[0]}x{img.size[1]}")


def cmd_clean(args):
    """Remove the DERIVED output only - never a raw generation (the SOU-158 lesson)."""
    removed, kept = [], 0
    for setname in args.set:
        out = out_dir(setname)
        for sub in ("props",):
            directory = out / sub
            if directory.is_dir():
                for f in sorted(directory.iterdir()):
                    if f.suffix == ".png" or f.name.endswith(".manifest.json"):
                        f.unlink()
                        removed.append(str(f.relative_to(out)))
        for f in sorted(out.glob("*")):
            if f.is_file() and (f.name.endswith(".raw.png") or f.name.endswith(".prompt.txt")):
                kept += 1
                continue
            if f.is_file():
                f.unlink()
                removed.append(f"{setname}/{f.name}")
    print(f"cleaned {len(removed)} derived file(s); kept {kept} raw generation(s) + their prompts")
    for name in removed:
        print(f"  - {name}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    names = tuple(SETS)
    for name, helptext in (("generate", "FLUX pass (needs the sou-flux venv + MPS)"),
                           ("assemble", "CPU pass: key, derive frames, fit, quantise, manifests"),
                           ("clean", "remove derived output only - the raws are kept")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--set", action="append", choices=names,
                       help="repeatable; defaults to both sets")
        if name == "generate":
            p.add_argument("--only", help="comma-separated asset ids")
            p.add_argument("--force", action="store_true", help="regenerate even if a raw exists")
            p.add_argument("--seed-shift", type=int, default=0)
        p.set_defaults(func=cmd_generate if name == "generate" else
                       (cmd_assemble if name == "assemble" else cmd_clean))
    args = ap.parse_args()
    if not args.set:
        args.set = list(names)
    if args.func is cmd_clean:
        sys.exit(cmd_clean(args))
    args.func(args)


if __name__ == "__main__":
    main()
