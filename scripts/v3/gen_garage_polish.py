#!/usr/bin/env python3
"""Generate the v3 garage bay art set + the SOU-216 polish pass (SOU-216).

Two deliveries in ONE module, because they are one pipeline, one camera and one palette - only the
asset tables differ:

    garage   the car's bay: a straight-parked beige van, a closed roller door, a pegboard, a red
             tool chest, a stack of old tyres, a dented oil drum and a seamless concrete floor
             (plus the same floor with one oil stain)
    polish   NEW files under NEW names for four SHIPPED assets the owner read as wrong at room
             scale: lodge `stone_floor_soft` (checkerboard too strong), lodge `wall_face_soft`
             (too stripy next to the planks), serverroom `gear_shelf_full_a/b` (the shelves read
             as nearly empty frames) and writer `paper_stacks_lined` (blank white blocks)

Everything shared with the previous sets is IMPORTED, never copied - the projection vocabulary
(VIEW/STYLE/KEY/NO_TEXT), the keying recipe, the island/despeckle/hole chain, `quantise`,
`key_magenta` and `_label`/`_grid_overlay` come from `gen_office01_art.py`; the 61-colour night
palette AND the drawn-tile vocabulary (`_blank`, `_rgb`, `wrap_vs_max`, `seam_ratio`) come from
`gen_lodge_art.py`. A second copy of the keying recipe is a second thing to fix; a second palette
is how two rooms of the same building stop looking like one building.

Rulings (stated on the ticket, cost recorded):

1. **The garage shares the lodge palette** (SOU-198/199/200, restated - the ticket says so).
   Cost if wrong: a re-quantise pass, CPU-only, the raws stay.
2. **Props ship at EXACTLY the declared box** (`fit_mode="inside"`, the SOU-198/199/200 rule), so
   `texture_px` is the contract the renderer can rely on and `lift_px = texture_h - footprint_h`.
3. **The van is a REFERENCE EDIT of OUR OWN office-01 van** - the ticket allows it ("the same
   beige boxy van ... reference edits of OUR OWN `office-01/props/van.png` are allowed") and it is
   the only kind of reference edit the licence rule permits: the conditioning image is a keyed
   prop this pipeline produced, never upstream Star-Office or LimeZu art. The committed
   `office-01/props/van.png` is already keyed and quantised, so it is composited on the magenta
   field before being passed to the model; the source file and its sha256 are recorded in the
   manifest and in `<id>.mode.txt`. `--no-reference` falls back to a text-only generation.
4. **The garage floor is DRAWN, not generated.** A material tile must be seamless *by
   construction* (the drawn tiles carry a calm border ring, so the wrap step is zero while the
   interior carries the texture) and a diffusion model has no declared trowel direction: this is
   the ADR-064/065 ruling restated. Cost if wrong: one GPU pass for a generated alternative; that
   was accepted rather than spending one here, because the ticket asks for a smooth material with
   faint marks and nothing about it is "an object".
5. **The polish assets are new files with new names and NOTHING shipped is overwritten.** The two
   soft lodge tiles therefore ship as their own 2-slot strip (`assets/lodge/tiles_soft.png`)
   instead of being appended to `assets/lodge/tiles.png`: appending would rewrite a file the
   already-shipped wiring consumes and shift slot ids under it.
6. **The polish GENERATED record lives under `assets/generated/v3/polish216/<home set>/`**, not
   inside the home set's tree, so no other module's `clean`/`assemble` can delete a delivered
   polish file; the SERVED files land in their home set's directory, which is where the wiring
   step reads them.
7. **"Softer" is a measurement, not an adjective.** Each soft tile's contrast is measured against
   the FILE IT REPLACES (the shipped PNG, read from the served tree) with two declared metrics -
   `block_contrast` (16px slab means) and `log_contrast` (8px log-period row spread) - and
   `check_v3_assets.py --set polish` asserts the polished number is LOWER. The "fuller shelf"
   claim is likewise a number (`interior_gear_share` + LED pixels + plant pixels) rather than a
   reviewer's adjective, and `paper_stacks_lined` is asserted to carry horizontal LINE runs (1px
   tall, >= 3px wide) and nothing else, which is the mechanical form of "lines, never letters".

Frames: every asset here is single-frame (`frames=1`), so each file is simply the FLUX base (props)
or the drawn tile, unchanged.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_garage_polish.py generate --set garage
    $VENV/bin/python scripts/v3/gen_garage_polish.py generate --set polish
    $VENV/bin/python scripts/v3/gen_garage_polish.py assemble --set garage
    python3 scripts/v3/gen_garage_polish.py assemble --set polish
    python3 scripts/v3/gen_garage_polish.py clean --set garage
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gen_lodge_art as lodge  # noqa: E402  (the shared lodge palette AND the drawn tiles)
import gen_office01_art as base  # noqa: E402  (shared pipeline machinery)

from gen_office01_art import (  # noqa: E402
    KEY,
    NO_TEXT,
    STYLE,
    VIEW,
    _label,
    _nearest,
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

PALETTE = lodge.PALETTE
PALETTE_HEX = lodge.PALETTE_HEX
PALETTE_RGB = lodge.PALETTE_RGB
PALETTE_BY_NAME = lodge.PALETTE_BY_NAME

ISSUE = "SOU-216"
POLISH_DIR = "polish216"

# --------------------------------------------------------------------------------------
# per-asset prompt reinforcements (each one answers a measured failure in an earlier set)
# --------------------------------------------------------------------------------------
# The 3/4 view contract, stated for the objects whose seed came back as a flat top-down
# silhouette (the SOU-200 `advisor_desk` defect: a pure table top with no front face and no
# legs passed every numeric AC). The van and the two small containers all need a visible front.
FRONT_FACE = (
    "The FRONT FACE of this object is fully visible below its top surface - the upper part of the "
    "object is the top surface seen from above and slightly in front, and the lower part is the "
    "front face standing on the floor. This is NOT a flat top-down view of the top surface alone. "
    "There is no floor, ground patch or contact shadow under it. "
)

# The metal/pale-material clause: a dark object on a dark non-magenta field is a keying trap
# (the SOU-200 `ring_light` defect: the border-seeded flood fill leaked through the outline and
# keyed the stand away, and the bottom-row AC still passed while the object was in two pieces).
MAGENTA_FIELD = (
    "The background must be one single flat bright magenta #FF00FF field covering every pixel "
    "that is not the object - never dark, never navy, never grey, never white, never a gradient. "
    "The object itself is never magenta anywhere. "
)

# Tiles are prompted as materials, never as objects (the lodge tile prompt).
GARAGE_TILE_SUBJECTS = {
    "garage_floor": "smooth poured concrete floor, even warm grey surface with very faint trowel "
                    "swirl marks and a few tiny dark specks",
    "garage_floor_stain": "smooth poured concrete floor, even warm grey surface with very faint "
                          "trowel swirl marks, one small dark oily stain patch in the middle",
}

SETS = {
    "garage": {
        "pack": "garage-v1",
        "title": "the van's garage bay",
        "map": "garage.json",
        "backdrop": "garage_floor",
        "seed_base": 21600,
        "look": ("Inside a cosy log-cabin garage bay at night: honey-brown timber frame walls, a "
                 "smooth grey concrete floor, one warm amber work lamp falling from the top-left, "
                 "deep blue shadow on the right, and small teal-green and orange indicator lights "
                 "on the tools and the drum. Practical, dusty, lived-in. "),
    },
}


# ======================================================================================
# GARAGE - props
# ======================================================================================
def _prop(pid, subject, texture, footprint, lift, *, post="full", flat=False, reference=None,
          extra="", frame_ok=False, note=""):
    """One garage prop. `texture_px` is the EXACT canvas that ships (fit_mode="inside")."""
    return {
        "id": pid,
        "kind": "prop",
        "set": "garage",
        "subject": subject,
        "texture_px": [texture[0], texture[1]],
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        "fit_mode": "inside",
        "flat": flat,
        "post": post,
        "reference": reference,
        "extra": extra,
        "frame_ok": frame_ok,
        "note": note,
    }


GARAGE_PROPS = [
    _prop(
        "van_parked",
        "the SAME small boxy 1990s Japanese minivan as the reference image - flat pale beige "
        "painted body (warm sand #eadcb8 with #c8a070 and #a8894f shading), plain and unbranded, "
        "dark blue-grey glass, one long flat roof, an upright windscreen, slab-flat sides, a "
        "short blunt nose, thin pillars, squared-off body, small simple wheels - now PARKED "
        "STRAIGHT and standing SQUARE to the frame with its nose pointing straight towards the "
        "viewer, its long sides parallel to the sides of the image (NOT diagonal, NOT angled, "
        "NOT turned to three quarters). The two headlights are switched OFF: plain pale glass "
        "blocks, not lit and not glowing. A thin even film of pale grey dust dulls the paint on "
        "the roof and the bonnet; no rust, no dirt clumps, no leaves. The roof rack is empty",
        (96, 128), (96, 96), 32,
        reference="assets/generated/v3/office-01/props/van.png",
        extra=FRONT_FACE + MAGENTA_FIELD +
              ("No licence plate, no plate with letters or numbers, no brand badge, no emblem, no "
               "lettering of any kind anywhere on the vehicle. "),
        note="the same beige boxy van as office-01 (SOU-216 allows a reference edit of OUR OWN "
             "van): straight-parked, nose to the viewer, headlights off, dusty. 3x3 footprint + "
             "32px of lift carries the 96x128 canvas.",
    ),
    _prop(
        "roller_door",
        "a CLOSED corrugated metal roller shutter door seen flat-on from the front, filling the "
        "whole frame: wide warm-grey steel slats stacked in horizontal bands from the floor to the "
        "top, a slightly darker bottom rail, two narrow vertical steel guide rails down the left "
        "and right edges, small rivets along each slat, faint warm amber rim light along the top "
        "edges of the slats and deep blue shadow in the grooves - completely plain, with no "
        "handle, no lock, no sign, no lettering, no number and no warning stripe",
        (96, 48), (96, 32), 16,
        frame_ok=True,
        extra=MAGENTA_FIELD,
        note="wall decal: 3x1 wall rect (96x32) + 16px of lift carries the 96x48 door up the wall",
    ),
    _prop(
        "pegboard",
        "a rectangular workshop pegboard hanging flat facing the viewer in a thin honey-brown "
        "timber frame: a pale warm-grey perforated board covered in rows of small round holes, "
        "with tool silhouettes hanging on it in dark grey and warm brown - two open-ended "
        "spanners of different sizes, one handsaw with a wooden handle, a pair of pliers, a "
        "hammer, and two short coils of cable hanging from small hooks. Tools and holes only: no "
        "letters, no numbers, no words, no labels, no outline drawings and no markings of any kind",
        (64, 48), (64, 32), 16,
        frame_ok=True,
        note="wall decal: 2x1 wall rect + 16px of lift carries the 64x48 board up the wall",
    ),
    _prop(
        "toolbox",
        "a red rolling tool chest seen from above and slightly in front: a bright red steel box "
        "with a slightly darker red lower drawer unit and two small black castors at the front, "
        "a wide black handle rail across the front of the top lid, thin dark steel edges, faint "
        "light along the top edge of the lid and one small teal-green indicator dot on the front. "
        "Completely plain: no lettering, no numbers, no badges",
        (32, 40), (32, 32), 8,
        post="light",
        extra=FRONT_FACE + MAGENTA_FIELD,
    ),
    _prop(
        "tire_stack",
        "a stack of three old car tyres lying flat one on top of another, seen from above and "
        "slightly in front: black rubber rings with a visible tread pattern around the outer "
        "edge, a dark hollow centre in the top tyre, a few pale grey scuff marks on the rubber "
        "and a thin film of dust on the top tyre. No hub caps, no rims, no wheel, no lettering "
        "and no markings on the sidewalls",
        (32, 40), (32, 32), 8,
        post="light",
        extra=FRONT_FACE + MAGENTA_FIELD,
        note="the tyre sidewalls are plain rubber - tyre markings would be lettering (NO_TEXT)",
    ),
    _prop(
        "oil_drum",
        "a dented metal oil drum standing upright seen from above and slightly in front: a "
        "cylindrical blue-grey steel barrel with two raised horizontal rolling hoops around its "
        "middle, one shallow dent in the lower left of the body, a dark flat steel lid with a "
        "small round cap, streaks of darker rust-brown running down one side and dusty pale "
        "scuffs at the base, standing on the floor on a narrow flat foot ring. Completely plain: "
        "no label, no lettering, no numbers, no hazard symbol and no marking of any kind",
        (32, 48), (32, 32), 16,
        extra=FRONT_FACE + MAGENTA_FIELD,
    ),
]

GARAGE_ASSETS = {"garage": GARAGE_PROPS}


# ======================================================================================
# GARAGE - drawn tiles
# ======================================================================================
GARAGE_TILES = [
    # slot, name, class, seam axes, drawn spec
    ("garage_floor", "material", "xy", {"concrete": True, "seed": 2160,
                                        "subject": GARAGE_TILE_SUBJECTS["garage_floor"]}),
    ("garage_floor_stain", "material", "xy", {"concrete": True, "seed": 2160, "stain": True,
                                              "subject": GARAGE_TILE_SUBJECTS["garage_floor_stain"]}),
]


def _concrete(spec):
    """Poured concrete: a flat grey field with faint trowel swirls and a few specks.

    Seamless BY CONSTRUCTION, like every drawn lodge tile: the two outermost rows/columns are left
    as the base colour (a calm ring), so the wrap step is zero while the interior carries the
    texture. The trowel marks are short shallow arcs in a single lighter grey and the specks are
    single pixels of the darker grey - faint marks, no pattern, which is what "smooth concrete"
    means at 32px.
    """
    img = lodge._blank("stone")
    px = img.load()
    rnd = random.Random(spec.get("seed", 2160))
    arc_offsets = (0, 1, 1, 2, 1, 1, 0)
    for _ in range(3):
        x0, y0 = rnd.randrange(3, 20), rnd.randrange(4, 27)
        for i, dy in enumerate(arc_offsets):
            x = x0 + i
            if 2 <= x <= 29 and 2 <= y0 + dy <= 29:
                px[x, y0 + dy] = lodge._rgb("stone_warm") + (255,)
    for _ in range(26):
        x, y = rnd.randrange(2, 30), rnd.randrange(2, 30)
        px[x, y] = lodge._rgb("stone_dark") + (255,)
    for _ in range(14):
        x, y = rnd.randrange(2, 30), rnd.randrange(2, 30)
        px[x, y] = lodge._rgb("stone_light") + (255,)
    if spec.get("stain"):
        # One oily stain, entirely inside the interior so the calm ring (and therefore the seam)
        # is untouched. Shape and size are fixed numbers, not a random walk: the gate asserts the
        # stain variant differs from the plain tile only inside this region.
        cx, cy = 18, 13
        for dy in (-3, -2, -1, 0, 1, 2, 3):
            half = 3 - abs(dy) // 2
            for dx in range(-half, half + 1):
                if (dx + dy) % 3 == 2:
                    continue  # an uneven, blotchy edge
                px[cx + dx, cy + dy] = lodge._rgb("ink") + (255,)
        px[cx - 1, cy - 1] = lodge._rgb("wood_deep") + (255,)
        px[cx + 1, cy + 1] = lodge._rgb("outline") + (255,)
    return img


GARAGE_DRAWN_TILES = {
    "garage_floor": _concrete,
    "garage_floor_stain": _concrete,
}


# ======================================================================================
# THE POLISH PASS - five new files, four of which replace a shipped asset by name
# ======================================================================================
POLISH_TILES = [
    ("stone_floor_soft", "lodge", "xy", {"flagstones_soft": True, "seed": 199}),
    ("wall_face_soft", "lodge", "xy", {"logs_soft": True}),
]

POLISH_STRIPS = [
    # name, home, members (in slot order), texture
    ("tiles_soft", "lodge", ["stone_floor_soft", "wall_face_soft"], [64, 32]),
]


def _flagstones_soft(spec):
    """The shipped `stone_floor` with the checkerboard taken out.

    Measured reason: the shipped tile's four 16px slabs alternate `stone` (#6d747f) and
    `stone_light` (#929aa6), which is a 37.8 luminance step between NEIGHBOURING flags - at room
    scale that reads as a checkerboard rather than as a stone floor. Two of the four slabs are
    therefore moved onto `stone_warm` (#7d7a70), 6.6 luminance from `stone`, and the speckle count
    drops from 14 to 6. The 1px `stone_dark` joints stay, so it still reads as slabs, and the
    16px period keeps the tile periodic on both axes.
    """
    img = lodge._blank()
    d = ImageDraw.Draw(img)
    tones = ("stone", "stone_warm", "stone_warm", "stone")
    for gy in range(2):
        for gx in range(2):
            x0, y0 = gx * 16, gy * 16
            d.rectangle([x0, y0, x0 + 15, y0 + 15], fill=lodge._rgb(tones[gy * 2 + gx]) + (255,))
    for i in (0, 16):
        d.rectangle([i, 0, i, TILE - 1], fill=lodge._rgb("stone_dark") + (255,))
        d.rectangle([0, i, TILE - 1, i], fill=lodge._rgb("stone_dark") + (255,))
    px = img.load()
    rnd = random.Random(spec.get("seed", 199))
    for _ in range(6):
        px[rnd.randrange(1, 31), rnd.randrange(1, 31)] = lodge._rgb("stone_dark") + (255,)
    return img


def _logs_soft(spec):
    """The shipped `wall_face` with a softer log profile - still LOGS, not flat planks.

    Measured reason: the shipped profile is
    `[honey_light, honey, wood_light, wood_light, wood, wood, wood_deep, wood_deep]`, i.e. 144.3
    luminance between the lit top of a log and its shaded underside - next to the flat plank floor
    that measures as hard stripes. The soft profile is
    `[honey, wood_light, wood_light, wood, wood, wood, wood_dark, wood_dark]`: 98.2 (32% lower), and
    it keeps a four-step cylinder ramp - honey top, wood_light shoulder, wood body, wood_dark
    underside - so the wall still reads as stacked ROUND logs rather than as flat siding.

    Measured cost of getting this wrong (first pass): the version that dropped the highlight AND
    lifted the underside to `wood` measured 74.7 and a reviewer read it as "planks, not logs - it
    flirts with a flat tan block". Calmer is not the same as formless, so the underside is back down
    to `wood_dark` and the ramp is four tones; only the harsh highlight/underside alternation went.
    """
    img = lodge._blank()
    d = ImageDraw.Draw(img)
    profile = ["honey", "wood_light", "wood_light", "wood", "wood", "wood", "wood_dark",
               "wood_dark"]
    for i in range(4):
        y0 = i * 8
        for dy, shade in enumerate(profile):
            d.rectangle([0, y0 + dy, TILE - 1, y0 + dy], fill=lodge._rgb(shade) + (255,))
    return img


POLISH_DRAWN_TILES = {
    "stone_floor_soft": _flagstones_soft,
    "wall_face_soft": _logs_soft,
}


def _polish_prop(pid, home, subject, texture, footprint, lift, *, replaces, post="full",
                 flat=False, extra="", note="", lines=False):
    return {
        "id": pid,
        "kind": "prop",
        "set": home,
        "home": home,
        "replaces": replaces,
        "subject": subject,
        "texture_px": [texture[0], texture[1]],
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        "fit_mode": "inside",
        "flat": flat,
        "post": post,
        "extra": extra,
        "note": note,
        "lines": lines,
    }


POLISH_PROPS = [
    _polish_prop(
        "gear_shelf_full_a", "serverroom",
        "a tall narrow wooden open shelving unit seen from above and slightly in front, PACKED "
        "from the floor to the top shelf with homelab gear - the shelves are FULL, with almost no "
        "empty space between the boxes: black and dark grey network appliances, flat silver and "
        "black servers stacked two and three deep, a small beige vintage computer box, a small "
        "fan grille, short cables looping between the units, coils of cable hanging off the side, "
        "rows and rows of tiny glowing teal-green and orange status LEDs on the front of almost "
        "every box, and a small potted green plant sitting on the very top shelf",
        (64, 112), (64, 32), 80, replaces="gear_shelf_a",
        post="light",
        extra=("Every shelf level must be visibly FULL of stacked electronic boxes - this is a "
               "packed equipment shelf, NOT an empty wooden frame with a few items on it. "),
        note="SOU-216 polish: the shipped gear_shelf_a reads as an almost empty wooden frame; this "
             "one is packed to the top. Same 64x112 canvas and 2x1 footprint as the file it "
             "replaces, so wiring is a name swap.",
    ),
    _polish_prop(
        "gear_shelf_full_b", "serverroom",
        "a tall narrow wooden open shelving unit seen from above and slightly in front, PACKED "
        "from the floor to the top shelf with a different collection of homelab gear - the shelves "
        "are FULL, with almost no empty space: a tall stack of flat silver and black rack servers, "
        "one older beige box, a small dark steel mini-tower, a woven basket crammed with coiled "
        "cables, a thick bundle of coloured cables running down the left upright, many small "
        "glowing orange and teal-green LED lights across the front panels, and a lit brass lantern "
        "standing between two boxes on the middle shelf",
        (64, 112), (64, 32), 80, replaces="gear_shelf_b",
        post="light",
        extra=("Every shelf level must be visibly FULL of stacked electronic boxes - this is a "
               "packed equipment shelf, NOT an empty wooden frame with a few items on it. "),
        note="variant of gear_shelf_full_a: silver server stack, beige box, cable basket, lantern",
    ),
    _polish_prop(
        "paper_stacks_lined", "writer",
        "two short stacks of loose manuscript pages lying on the floor seen from directly above: "
        "neat slightly uneven piles of WARM CREAM writing paper (warm cream #f3d9b0 with #e2d3b4 "
        "shading - definitely NOT white, NOT blue-white, NOT grey, NOT blue-grey), with several "
        "visible sheet edges and thin soft shadow lines between the single sheets, completely flat "
        "on the floor, the top page of each stack left completely BLANK and clean - no writing, no "
        "ruled lines, no letters, no words, no numbers, no symbols, no markings of any kind anywhere "
        "on the paper",
        (32, 32), (32, 32), 0, replaces="paper_stacks", flat=True, post="light", lines=True,
        extra=("The pages are warm cream paper only: no text, no letters, no numbers, no ruled "
               "lines, no glyphs, no squiggles, no symbols, no dots, no shading marks on the page "
               "surfaces. "),
        note="SOU-216 polish: the shipped paper_stacks reads as blank white blocks. Flat 1x1 decal. "
             "The 'hint of text lines' is DRAWN after quantise, not generated - see `ruled_lines` "
             "in the manifest: a diffusion model asked for tiny text at 32px paints glyph-shaped "
             "squiggles, which the ticket's 'lines, never letters' explicitly forbids, so the lines "
             "are 1px horizontal runs drawn in declared palette space instead.",
    ),
]

POLISH_ALL = POLISH_PROPS


# ======================================================================================
# measurements shared by the generator, the gate and the RED proof (ONE implementation)
# ======================================================================================
def _luminance(rgb):
    r, g, b = rgb[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _as_rgba(img):
    return img.convert("RGBA") if img.mode != "RGBA" else img


def block_contrast(img, block=16):
    """Max-minus-min luminance between the image's `block`-sized tiles.

    This is the metric behind "the checkerboard reads too strongly": neighbouring FLAGS are what
    the eye compares, and the flags here are the 16px blocks. Measured in the declared palette
    space, so the number is a property of the shipped pixels, not of the generator's intentions.
    """
    img = _as_rgba(img)
    w, h = img.size
    means = []
    for by in range(0, h, block):
        for bx in range(0, w, block):
            px = img.load()
            total = 0
            n = 0
            for y in range(by, min(by + block, h)):
                for x in range(bx, min(bx + block, w)):
                    total += _luminance(px[x, y])
                    n += 1
            if n:
                means.append(total / n)
    if not means:
        return 0.0
    return round(max(means) - min(means), 2)


def log_contrast(img, period=8):
    """Max-minus-min of the per-row mean luminance within one `period`-tall log row.

    The wall face is four 8px log rows stacked in y; the harshness the owner reported is the
    highlight-to-underside spread INSIDE each row, so the number is taken per period and the
    worst period is reported. `period` is 8 by construction (the log rows are 8px).
    """
    img = _as_rgba(img)
    w, h = img.size
    px = img.load()
    row_mean = []
    for y in range(h):
        total = sum(_luminance(px[x, y]) for x in range(w))
        row_mean.append(total / w)
    worst = 0.0
    for y0 in range(0, h, period):
        chunk = row_mean[y0:y0 + period]
        if len(chunk) > 1:
            worst = max(worst, max(chunk) - min(chunk))
    return round(worst, 2)


# The "status light" family: the declared cool-glow colours plus the orange LED. Measured reason
# for the wider set (2026-10-16, SOU-216): the quantiser maps most of the pale glow the model
# paints onto `teal_light`/`ice`, so counting only `led_teal`/`led_orange` read 3 px on a shelf
# whose front panels visibly carry a row of lights. The claim being asserted is "the shelf carries
# glowing status lights", so the set is the declared light family, not one hex.
LED_COLOURS = ("led_teal", "led_orange", "ice", "teal", "teal_light")
PLANT_COLOURS = ("pine", "pine_light", "pine_dark", "pine_deep")
GEAR_COLOURS = ("slate_900", "slate_700", "slate_500", "steel_200", "steel_100", "metal_dark",
                "metal", "metal_light", "ink", "outline")


def _rgb_set(names):
    return {lodge._rgb(n) for n in names}


def shelf_packing(img, inset=4):
    """How PACKED a shelf is, as numbers - the metric behind "the shelves read as nearly empty".

    The first version of this used the share of the content bounding box's interior that is opaque
    and it FAILED a correctly packed shelf (measured on the first pass: the shipped `gear_shelf_a`
    scored 0.9969 because its tight silhouette is solid, while the new packed shelf scored 0.9486
    because its frame is wider). It was measuring how solid the object is, not how full the shelves
    are. What actually changed between the two files is how much of the 64x112 CANVAS the thing
    occupies (a packed shelf fills its frame: 0.43 -> 0.58 and 0.23 -> 0.75) and how much of it is
    GEAR (168 -> 1923 and 25 -> 1658 gear-coloured px). Both are reported, with the LED and plant
    counts, and the gate asserts the canvas share AND the gear count went up.
    """
    img = _as_rgba(img)
    w, h = img.size
    px = img.load()
    gear = _rgb_set(GEAR_COLOURS)
    leds = _rgb_set(LED_COLOURS)
    plants = _rgb_set(PLANT_COLOURS)
    opaque = gear_px = led_px = plant_px = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            opaque += 1
            if (r, g, b) in gear:
                gear_px += 1
            if (r, g, b) in leds:
                led_px += 1
            if (r, g, b) in plants:
                plant_px += 1
    bbox = content_bbox(img)
    inner_share = 0.0
    if bbox is not None:
        inner = (bbox[0] + inset, bbox[1] + inset, bbox[2] - inset, bbox[3] - inset)
        area = max(0, inner[2] - inner[0]) * max(0, inner[3] - inner[1])
        if area:
            inside = sum(1 for y in range(inner[1], inner[3]) for x in range(inner[0], inner[2])
                         if px[x, y][3])
            inner_share = round(inside / area, 4)
    return {"canvas_opaque_share": round(opaque / (w * h), 4),
            "interior_opaque_share": inner_share,
            "gear_px": gear_px, "led_px": led_px, "plant_px": plant_px}


# The one threshold that separates a LINE from a glyph on a 32px page: a ruled line drawn by this
# pipeline is 8-12px long, while a letter stroke at this scale is 1-5px wide in any row. The ERASER
# (clean_page_marks) and the CHECK (check_v3_assets --set polish, via `line_marks`) both use it,
# deliberately: if the eraser removed everything below one number and the checker only demanded
# another, the artifact could satisfy the checker by luck (a 3px glyph row reads as a "line" under a
# >=3 rule - verified by the RED proof, where a glyph mutation did NOT fire until the numbers were
# unified).
MARK_MIN_RUN = 6


# Every declared tone that IS paper, i.e. everything that is not a mark: the cool snow/grey family
# AND the warm cream family. One list, used by the ink checker, the eraser and the line drawer - the
# first version kept a cool-only list in three places, and after the paper tone LUT moved the pages
# onto `cat_cream` the checker stopped recognising the paper at all: whole rows of cream then read
# as "line runs" and the file passed for the wrong reason. A colour set that is copied is a colour
# set that goes stale.
PAPER_TONES = ("snow", "snow_light", "snow_mid", "snow_trodden", "snow_shadow", "steel_100",
               "steel_200", "skin", "honey_light", "cat_cream", "cardboard_light", "cardboard")


def paper_set():
    return {lodge._rgb(n) for n in PAPER_TONES}


def line_marks(img):
    """Every dark mark on the paper, classified as a LINE run or something else.

    "Lines, never letters" is the ticket's wording, and at 32px it is mechanically checkable: a
    ruled line is a horizontal run of 1-pixel-tall marks at least MARK_MIN_RUN px long, and a
    letter is not (a glyph stroke is 1-5px wide in any row, and always has a vertical or diagonal
    stroke somewhere). So this walks every row, groups the contiguous dark runs, and returns the
    runs' (row, start, length) plus the count of dark pixels that are NOT part of such a run - the
    latter must be zero for the claim to hold.

    Two exclusions, both deliberate and both measured rather than assumed:
      * the paper's own tones (`PAPER_TONES`) are not marks;
      * an opaque pixel that touches TRANSPARENT space (8-connected) is the object's own
        silhouette outline or a sheet edge against the background, not a mark ON the paper -
        counting it would fail every correctly drawn stack for having an outline.
    """
    img = _as_rgba(img)
    w, h = img.size
    px = img.load()
    paper = paper_set()

    def is_boundary(x, y):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if px[nx, ny][3] == 0:
                    return True
        return False

    runs, loose = [], 0
    for y in range(h):
        x = 0
        while x < w:
            r, g, b, a = px[x, y]
            if a and (r, g, b) not in paper and not is_boundary(x, y):
                start = x
                while x < w:
                    r2, g2, b2, a2 = px[x, y]
                    if not a2 or (r2, g2, b2) in paper or is_boundary(x, y):
                        break
                    x += 1
                length = x - start
                if length >= MARK_MIN_RUN:
                    runs.append([y, start, length])
                else:
                    loose += length
            x += 1
    return {"rows_with_lines": sorted({r[0] for r in runs}), "runs": runs, "runs_count": len(runs),
            "loose_dark_px": loose}


def clean_page_marks(frame):
    """Erase every dark mark on a page surface that is not part of a horizontal line run.

    Measured reason (SOU-216, both passes): FLUX asked for "blank pages, no markings of any kind"
    still paints glyph-shaped marks on a manuscript stack - the subject is paper, and the prompt
    cannot win that argument. The ticket says LINES, NEVER LETTERS, so the marks that are not lines
    are erased deterministically (each one takes the most common paper tone among its 8-neighbours)
    and the ruled lines are drawn afterwards (see `draw_ruled_lines`). Both steps are declared in
    the manifest with their pixel counts, so a reader can see exactly what was removed and what was
    added rather than having to trust the word "clean".

    "Not part of a line" is decided by row-run length: a glyph blob at 32px is at most ~5px wide in
    any row, while the ruled lines this pipeline draws are 11-12px, so a 6px threshold separates
    them. Marks adjacent to transparent space (the object's own outline, a sheet edge against the
    background) are never touched.
    """
    img = frame.convert("RGBA")
    w, h = img.size
    px = img.load()
    paper = paper_set()

    def is_boundary(x, y):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h):
                    continue
                if px[nx, ny][3] == 0:
                    return True
        return False

    doomed = []
    for y in range(h):
        x = 0
        while x < w:
            r, g, b, a = px[x, y]
            if a and (r, g, b) not in paper and not is_boundary(x, y):
                start = x
                while x < w:
                    r2, g2, b2, a2 = px[x, y]
                    if not a2 or (r2, g2, b2) in paper or is_boundary(x, y):
                        break
                    x += 1
                if (x - start) < MARK_MIN_RUN:
                    doomed.extend((xx, y) for xx in range(start, x))
            x += 1

    if not doomed:
        return img, []

    counts = {}
    for x, y in doomed:
        counts[(x, y)] = counts.get((x, y), 0) + 1
    fixed = []
    for (x, y) in doomed:
        neighbours = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] and tuple(px[nx, ny][:3]) in paper:
                    neighbours.append(tuple(px[nx, ny][:3]))
        if not neighbours:
            # fall back to the nearest paper pixel by expanding rings, so the erase can never
            # leave a hole in the page
            nearest = None
            for radius in range(1, max(w, h)):
                for ny in range(max(0, y - radius), min(h, y + radius + 1)):
                    for nx in range(max(0, x - radius), min(w, x + radius + 1)):
                        if px[nx, ny][3] and tuple(px[nx, ny][:3]) in paper:
                            nearest = tuple(px[nx, ny][:3])
                            break
                    if nearest:
                        break
                if nearest:
                    break
            if nearest is None:
                continue
            neighbours = [nearest]
        pick = max(set(neighbours), key=neighbours.count)  # ties broken by colour, deterministically
        px[x, y] = (pick[0], pick[1], pick[2], 255)
        fixed.append([x, y])
    return img, fixed


def draw_ruled_lines(frame, seed=21660):
    """Draw the ticket's "hint of text lines" as 1px horizontal runs, in declared palette space.

    Ruling (stated on the ticket): a diffusion model asked for tiny writing at 32px paints
    glyph-shaped squiggles, which the ticket explicitly forbids ("lines, never letters"), so the
    lines are drawn AFTER quantise, in declared palette space, with a fixed seeded jitter. Recorded
    per rect in the manifest (`ruled_lines`), so the derivation is declared and a reader can tell
    the lines apart from anything the model painted.

    Each line is drawn inside ONE contiguous run of page pixels (`min_band` long), never across the
    gap between the two stacks: a line split by that gap produces short segments that are not line
    runs any more, which is exactly the "loose dark pixel" the gate counts. The row search steps by
    three so the lines read as a paragraph rather than a hatch.
    """
    import random as _random

    rnd = _random.Random(seed)
    frame = frame.convert("RGBA")
    w, h = frame.size
    bbox = content_bbox(frame)
    if bbox is None:
        return frame, []
    px = frame.load()
    paper = paper_set()
    ink = lodge._rgb("slate_500") + (255,)
    min_band = 12

    def boundary(x, y):
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and px[nx, ny][3] == 0:
                    return True
        return False

    def bands(y):
        """Longest contiguous run of page pixels in row `y`, as (start, end_exclusive)."""
        best = (0, 0)
        x = 0
        while x < w:
            if px[x, y][3] and tuple(px[x, y][:3]) in paper and not boundary(x, y):
                start = x
                while x < w and px[x, y][3] and tuple(px[x, y][:3]) in paper and not boundary(x, y):
                    x += 1
                if x - start > best[1] - best[0]:
                    best = (start, x)
            x += 1
        return best

    x0, y0, _x1, y1 = bbox
    rects = []
    row = y0 + 2
    while row < y1 and len(rects) < 3:
        start, end = bands(row)
        if end - start < min_band:
            row += 1
            continue
        length = min(end - start - 4, rnd.randrange(8, max(9, end - start - 3)))
        start += 2
        for i in range(length):
            px[start + i, row] = ink
        rects.append([start, row, length])
        row += 3
    return frame, rects


# --------------------------------------------------------------------------------------
# paper tone correction (declared, measured, palette-space) - used by `paper_stacks_lined` only
# --------------------------------------------------------------------------------------
# Measured problem (SOU-216, both rolls): asked for warm cream paper with the colour named, FLUX
# still returned a COOL white stack - the regenerated file measured 164 px of `snow` (#eef4ff) and
# a reviewer read the result as "blue-white, a different material from the shipped prop". The file
# this replaces is warm: 550 px `cat_cream` (#f3d9b0) + 78 px `skin`. A polish pass may not re-tint
# the room, so the cool paper tones are remapped onto the warm family IN PALETTE SPACE, declared
# here and counted in the manifest (`paper_tone_lut.remapped_px`). The white HIGHLIGHT is kept:
# the shipped prop uses `snow`/`snow_light` for the lit edge too.
PAPER_TONE_LUT = {
    "snow_mid": "cat_cream",
    "snow_trodden": "skin",
    "snow_shadow": "skin",
    "steel_200": "skin",
    "steel_100": "skin",
    "snow": "cat_cream",
}
WARM_PAPER_COLOURS = ("cat_cream", "skin", "honey_light", "cardboard_light", "cardboard")
COOL_PAPER_COLOURS = ("snow_mid", "snow_trodden", "snow_shadow", "steel_100", "steel_200", "snow")


def apply_palette_lut(frame, lut_names):
    """Remap exact palette colours to other palette colours; returns (image, changed_px)."""
    lut = {}
    for src, dst in lut_names.items():
        lut[lodge._rgb(src)] = lodge._rgb(dst) + (255,)
    out = frame.convert("RGBA")
    px = out.load()
    w, h = out.size
    changed = 0
    for y in range(h):
        for x in range(w):
            key = tuple(px[x, y][:3])
            if px[x, y][3] and key in lut:
                px[x, y] = lut[key]
                changed += 1
    return out, changed


def paper_families(img):
    """Warm vs cool paper pixels - the "did the polish re-tint the room" number.

    The ticket's polish pass is about SHAPE (a softer floor, a fuller shelf, a hint of lines); a new
    file that changes the material's hue is a regression, not a polish. This counts the two declared
    families so the gate can assert the polished paper sits on the same side as the file it replaces.
    """
    img = _as_rgba(img)
    w, h = img.size
    px = img.load()
    warm = _rgb_set(WARM_PAPER_COLOURS)
    cool = _rgb_set(COOL_PAPER_COLOURS)
    warm_px = cool_px = 0
    for y in range(h):
        for x in range(w):
            if not px[x, y][3]:
                continue
            rgb = tuple(px[x, y][:3])
            if rgb in warm:
                warm_px += 1
            elif rgb in cool:
                cool_px += 1
    return {"warm_paper_px": warm_px, "cool_paper_px": cool_px}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ======================================================================================
# paths
# ======================================================================================
def out_dir(setname):
    """The generated record for the garage, and for the polish pass (isolated - ruling 6)."""
    if setname == "polish":
        return REPO / "assets" / "generated" / "v3" / POLISH_DIR
    return REPO / "assets" / "generated" / "v3" / setname


def polished_out(home):
    return out_dir("polish") / home


def served_garage():
    return REPO / "frontend" / "v3" / "assets" / "garage"


def served_home(home):
    return REPO / "frontend" / "v3" / "assets" / home


def served_original(home, kind, name):
    """The SHIPPED file a polished asset replaces - the independent oracle for the softness AC.

    Read from the SERVED tree on purpose: that is the file the browser loads and the file the
    wiring step references, so it is the thing the owner's eye actually met.
    """
    if kind == "tile":
        return served_home(home) / "tiles" / f"{name}.png"
    return served_home(home) / "props" / f"{name}.png"


# ======================================================================================
# prompts
# ======================================================================================
RATIO_WORD = {
    (96, 128): "The object's bounding box is about three quarters as wide as it is tall",
    (96, 48): "The object's bounding box is about twice as wide as it is tall",
    (64, 48): "The object's bounding box is wider than it is tall",
    (64, 32): "The object's bounding box is about twice as wide as it is tall",
    (32, 48): "The object's bounding box is taller than it is wide",
    (32, 40): "The object's bounding box is slightly taller than it is wide",
    (32, 32): "The object's bounding box is square",
    (64, 112): "The object's bounding box is about half as wide as it is tall",
}


def _ratio_clause(asset):
    tw, th = asset["texture_px"]
    word = RATIO_WORD.get((tw, th), "")
    return (f"The object's bounding box must be EXACTLY {tw}:{th} in width:height"
            + (f" ({word.lower()})" if word else "")
            + ". Draw the object to fill that rectangle: no empty margin above, below or to the "
              "sides, and do not make it taller or narrower than that proportion. ")


def _palette_clause():
    return "Palette limited to exactly these colours: " + ", ".join(PALETTE_HEX) + ". "


FRAME_OK = ("This object's OWN thin wooden frame or border is wanted and is part of the object: "
            "the 'no frame or border' rule above does not apply to it. Every other prohibition "
            "stands unchanged - no letters, no numbers, no words, no logos, no marks, no labels, "
            "no signage anywhere on it. ")


def compose_prop_prompt(asset):
    look = SETS["garage"]["look"] if asset["set"] == "garage" else (
        base_look_for(asset["home"]))
    head = (f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in "
            f"frame. {_ratio_clause(asset)}")
    tail = _palette_clause() + look + VIEW + STYLE + KEY + NO_TEXT
    if asset.get("frame_ok"):
        tail += FRAME_OK
    if asset.get("extra"):
        tail += asset["extra"]
    tail += ("Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
             "game asset.")
    return head + tail


def base_look_for(home):
    """The LOOK of the set a polished prop lives in - the room it has to sit in unchanged."""
    if home == "serverroom":
        return ("Inside a cosy log-cabin lodge at night: honey-brown timber surfaces, warm amber "
                "lamplight falling from the top-left, deep blue shadow, and teal-green and orange "
                "LED accents on the electronic gear. ")
    if home == "writer":
        return ("Inside a cosy log-cabin loft at night: honey-brown horizontal log walls, wide warm "
                "plank floor, chunky honey-brown timber furniture, warm amber desk-lamp and candle "
                "light falling from the top-left, deep blue shadow on the right, and cream paper "
                "everywhere. Cluttered, warm, lived-in. ")
    return lodge.LOOK


def compose_tile_prompt(name, subject):
    return (f"seamless tileable pixel art texture: {subject}. Orthographic top-down view, flat and "
            "evenly lit, the pattern repeats without seams in every direction, no perspective, no "
            "single object, no border. " + _palette_clause() + STYLE + NO_TEXT +
            "Fill the entire frame edge to edge with the texture - no background, no margin, no "
            "transparent area. 32-bit pixel art texture, game tile.")


def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640 (same as every other set)."""
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


# ======================================================================================
# generate
# ======================================================================================
def _read(path, default=None):
    return path.read_text().strip() if path.exists() else default


def _reference_image(rel_path):
    """Our OWN keyed prop, composited on the magenta field, as a reference-edit conditioning image.

    klein is a unified generate+edit model: the image argument is conditioning, not img2img with a
    strength knob. The source is always a file this pipeline produced (ruling 3).
    """
    path = REPO / rel_path
    if not path.exists():
        sys.exit(f"reference {rel_path} does not exist - refusing to guess a conditioning image")
    subject = Image.open(path).convert("RGBA")
    field = Image.new("RGBA", subject.size, (255, 0, 255, 255))
    field.alpha_composite(subject)
    return field.convert("RGB"), path


def _generate(frg, pipe, torch_mod, prompt, width, height, seed, reference=None):
    if reference is None:
        return frg.generate(pipe, torch_mod, prompt, width, height, seed=seed,
                            steps=STEPS, guidance=GUIDANCE)
    import time
    gen = torch_mod.Generator(device="cpu").manual_seed(seed)
    t0 = time.time()
    image = pipe(image=reference, prompt=prompt, height=height, width=width,
                 num_inference_steps=STEPS, guidance_scale=GUIDANCE, generator=gen).images[0]
    print(f"[gen] {width}x{height} seed={seed} steps={STEPS} reference-edit "
          f"in {time.time() - t0:.1f}s", flush=True)
    return image


def _garage_catalogue():
    """Assets that need a FLUX pass: the props. The floor tiles are drawn (ruling 4)."""
    return list(GARAGE_PROPS)


def _polish_catalogue():
    """Assets that need a FLUX pass: the three props. The two soft tiles are drawn (ruling 7)."""
    return list(POLISH_PROPS)


def cmd_generate(args):
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    only = set(args.only.split(",")) if args.only else None
    pipe = None
    torch_mod = None

    jobs = []
    if "garage" in args.set:
        jobs += [(out_dir("garage"), SETS["garage"]["seed_base"], a) for a in _garage_catalogue()]
    if "polish" in args.set:
        jobs += [(polished_out(a["home"]), 21650, a) for a in _polish_catalogue()]

    for out, seed_base, asset in jobs:
        aid = asset["id"]
        if only is not None and aid not in only:
            continue
        out.mkdir(parents=True, exist_ok=True)
        raw = out / f"{aid}.raw.png"
        if raw.exists() and not args.force:
            print(f"[skip] {asset['set']}/{aid:20s} raw already on disk")
            continue
        if pipe is None:
            pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060 field result)
        tw, th = asset["texture_px"]
        gw, gh = _gen_size(tw, th)
        index = [a["id"] for a in (GARAGE_PROPS if asset["set"] == "garage" else POLISH_PROPS)]
        seed = seed_base + index.index(aid) * 7 + args.seed_shift
        prompt = compose_prop_prompt(asset)
        ref = None
        mode = "text-only generation"
        if asset.get("reference") and not args.no_reference:
            ref, ref_path = _reference_image(asset["reference"])
            mode = (f"reference edit of {asset['reference']} "
                    f"(sha256 {sha256(ref_path)[:16]}), composited on the magenta field")
            print(f"[gen ] {asset['set']}/{aid:20s} {gw}x{gh} seed={seed} REFERENCE EDIT "
                  f"of {asset['reference']}", flush=True)
        else:
            print(f"[gen ] {asset['set']}/{aid:20s} {gw}x{gh} seed={seed}"
                  + (" (reference dropped by --no-reference)" if asset.get("reference") else ""),
                  flush=True)
        image = _generate(frg, pipe, torch_mod, prompt, gw, gh, seed=seed, reference=ref)
        image.save(raw)
        (out / f"{aid}.seed.txt").write_text(f"{seed}\n")
        (out / f"{aid}.mode.txt").write_text(mode + "\n")
        (out / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")


# ======================================================================================
# assemble
# ======================================================================================
def _fit_inside(img, size):
    """Scale uniformly so the content sits inside the declared box, anchored bottom-centre."""
    w, h = size
    bbox = content_bbox(img)
    if bbox is None:
        return Image.new("RGBA", size, (0, 0, 0, 0)), {"empty": True}
    cw, ch = bbox[2] - bbox[0], bbox[3] - bbox[1]
    scale = min(w / cw, h / ch)
    nw, nh = max(1, round(cw * scale)), max(1, round(ch * scale))
    crop = img.crop(bbox).resize((nw, nh), _nearest())
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(crop, ((w - nw) // 2, h - nh))
    return canvas, {
        "content_px": [cw, ch],
        "fitted_px": [nw, nh],
        "canvas_px": [w, h],
        "mode": "inside",
        "scale": round(nw / cw, 4),
        "crop_bbox": list(bbox),
        "why": "uniform scale so the object sits inside the declared box - props ship at exactly "
               "the size the ticket declares (SOU-198/199/200 rule, restated for SOU-216)",
    }


def _post(img, mode):
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


def _manifest(asset, *, seed, prompt, source_file, output_file, output_dims, extra, home=None):
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
        "set": home or asset["set"],
        "view": "top-down 3/4",
        "ticket": ISSUE,
    }
    payload.update(extra)
    return payload


def _assemble_prop(asset, src_dir, seed_base, home=None):
    aid = asset["id"]
    raw = src_dir / f"{aid}.raw.png"
    if not raw.exists():
        print(f"[miss] {asset['set']}/{aid}: no raw generation on disk")
        return None
    seed = int(_read(src_dir / f"{aid}.seed.txt", str(seed_base)) or seed_base)
    prompt = _read(src_dir / f"{aid}.prompt.txt", compose_prop_prompt(asset))
    mode = _read(src_dir / f"{aid}.mode.txt", "text-only generation")
    keyed, key = key_magenta(Image.open(raw))
    keyed.save(src_dir / f"{aid}.keyed.png")
    fitted, fit = _fit_inside(keyed, tuple(asset["texture_px"]))
    treated, post_meta = _post(fitted, asset["post"])
    frame = quantise(treated, palette=PALETTE_RGB)
    ruled = None
    erased = None
    tone_lut = None
    derivation_0 = "the FLUX base (contact), unchanged - single-frame asset"
    if asset.get("lines"):
        frame, tone_lut = apply_palette_lut(frame, PAPER_TONE_LUT)
        frame, erased = clean_page_marks(frame)
        frame = quantise(frame, palette=PALETTE_RGB)
        frame, ruled = draw_ruled_lines(frame)
        frame = quantise(frame, palette=PALETTE_RGB)
        marks = line_marks(frame)
        if marks["loose_dark_px"] or marks["runs_count"] < 3 or len(marks["rows_with_lines"]) < 2:
            raise SystemExit(
                f"{aid}: the derivation did not produce the contracted artifact - "
                f"line_marks measured {marks}. The assembly refuses to ship a file whose own AC "
                f"('lines, never letters') it cannot demonstrate; re-roll the raw or widen the "
                f"derivation, do not weaken the check.")
        derivation_0 = ("the FLUX base (contact) with a THREE-STEP declared derivation: (1) the cool "
                        "paper tones are remapped onto the warm cream family the shipped prop uses "
                        "(`paper_tone_lut`, so the polish cannot re-tint the room), (2) the marks "
                        "the model painted on the pages that are not line runs are ERASED "
                        "(`page_cleanup`, each taking the surrounding paper tone) and (3) the "
                        "ticket's \"hint of text lines\" is DRAWN after quantise as 1px horizontal "
                        "runs (`ruled_lines`). The ticket says lines, never letters, and a diffusion "
                        "model asked for blank pages paints glyph-shaped marks instead")
    (src_dir / "props").mkdir(parents=True, exist_ok=True)
    frame.save(src_dir / "props" / f"{aid}.png")

    meta = {
        "kind": "prop",
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
        "reference_edit_source": asset.get("reference"),
        "frames": 1,
        "frame_px": list(asset["texture_px"]),
        "sheet_px": list(asset["texture_px"]),
        "frame_derivation": {"0": derivation_0},
        "ruled_lines": ruled,
        "page_cleanup": ({"erased_px": len(erased), "positions": erased,
                          "why": "dark marks the model painted on the page surfaces that are not "
                                 "line runs; each was replaced by the surrounding paper tone"} if
                         erased is not None else None),
        "paper_tone_lut": ({"lut": PAPER_TONE_LUT, "remapped_px": tone_lut,
                            "warm_vs_cool_after": paper_families(frame),
                            "why": "the base came back cool white; the prop this replaces is warm "
                                   "cream (550 px cat_cream + 78 px skin). A polish pass is about "
                                   "shape, not hue, so the cool paper tones are remapped onto the "
                                   "warm family in declared palette space."} if tone_lut is not None
                           else None),
        "post_process": asset["post"],
        "post_process_meta": post_meta,
        "prompt_ruling": ("the shared NO_TEXT clause forbids any frame or border; for this asset "
                         "the object's OWN frame is explicitly re-permitted while every other "
                         "prohibition stands" if asset.get("frame_ok") else None),
        "content_bottom_row": content_bottom_row(frame),
        "base_row_coverage": round(base_row_coverage(frame), 3),
        "opaque_bottom_rows_coverage": round(base_row_coverage(frame, rows=2), 3),
        "transparent_px": sum(1 for p in frame.getchannel("A").getdata() if p == 0),
        "colours": len(_colours(frame)),
        "off_palette": _off_palette(frame),
        # The sheet builder and the gate both need to know WHICH set a polished file belongs to and
        # WHAT it replaces without re-deriving it from the file name - so it is recorded on the
        # asset's own record, not inferred later (the SOU-158 lesson about re-deriving a
        # published contract).
        "home": home or asset["set"],
        "replaces": asset.get("replaces"),
    }
    if asset.get("note"):
        meta["note"] = asset["note"]
    manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                         output_file=f"props/{aid}.png", output_dims=list(frame.size),
                         extra=meta, home=home)
    (src_dir / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[asm ] {asset['set']}/{aid:20s} {frame.size[0]}x{frame.size[1]} "
          f"fit={fit.get('fitted_px')} colours={meta['colours']} off={len(meta['off_palette'])} "
          f"bottom={meta['content_bottom_row']}")
    return {"path": f"props/{aid}.png", **meta}


def _assemble_tile(name, home, cls, axes, spec, drawn, src_dir, replaces=None, extra_note=""):
    """One DRAWN tile: quantise the drawn image, measure it, write file + manifest."""
    (src_dir / "tiles").mkdir(parents=True, exist_ok=True)
    tile = quantise(drawn(spec), palette=PALETTE_RGB)
    tile.save(src_dir / "tiles" / f"{name}.png")
    seam = lodge.seam_ratio(tile)
    wrap = lodge.wrap_vs_max(tile)
    meta = {
        "kind": "tile",
        "tile_class": cls,
        "seam_axes": axes,
        "derivation": f"drawn from the declared palette ({name}), no FLUX pass",
        "seam": seam,
        "wrap_vs_max": wrap,
        "seam_checked_axes": axes,
        "opaque": not any(p[3] == 0 for p in tile.getdata()),
        "colours": len(_colours(tile)),
        "off_palette": _off_palette(tile),
        "block_contrast": block_contrast(tile),
        "log_contrast": log_contrast(tile),
        "home": home,
        "replaces": replaces,
    }
    reference = None
    if replaces:
        ref_path = served_original(home, "tile", replaces)
        if ref_path.exists():
            ref = Image.open(ref_path).convert("RGBA")
            reference = {
                "replaces": replaces,
                "served_path": f"frontend/v3/assets/{home}/tiles/{replaces}.png",
                "sha256": sha256(ref_path),
                "block_contrast": block_contrast(ref),
                "log_contrast": log_contrast(ref),
                "wrap_vs_max": lodge.wrap_vs_max(ref),
            }
        else:
            reference = {"replaces": replaces, "missing": True}
    if reference:
        meta["replaces_reference"] = reference
    if extra_note:
        meta["note"] = extra_note
    manifest = _manifest({"id": name, "kind": "tile"}, seed=None,
                         prompt=("drawn from the declared palette - no FLUX pass (see "
                                 "`derivation`)"),
                         source_file="none - drawn, see POLISH_DRAWN_TILES / GARAGE_DRAWN_TILES",
                         output_file=f"tiles/{name}.png", output_dims=[TILE, TILE], extra=meta,
                         home=home)
    (src_dir / "tiles" / f"{name}.png.manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    print(f"[asm ] {home}/{name:20s} 32x32 DRAWN wrap/max x={wrap['wrap_vs_max_x']} "
          f"y={wrap['wrap_vs_max_y']} block={meta['block_contrast']} log={meta['log_contrast']} "
          f"colours={meta['colours']}")
    return {"path": f"tiles/{name}.png", **meta}


def _assemble_strip(name, members, size, src_dir, tile_dir, home):
    """The 2-tile strip, byte-compared against the declared slot order by the gate."""
    strip = Image.new("RGBA", tuple(size), (0, 0, 0, 0))
    for slot, member in enumerate(members):
        tile = Image.open(tile_dir / f"{member}.png").convert("RGBA")
        strip.alpha_composite(tile, (slot * TILE, 0))
    strip.save(src_dir / f"{name}.png")
    meta = {
        "kind": "strip",
        "slot_px": TILE,
        "columns": len(members),
        "order": list(members),
        "order_authority": f"{ISSUE} ticket / generator table",
        "opaque": not any(p[3] == 0 for p in strip.getdata()),
        "colours": len(_colours(strip)),
        "off_palette": _off_palette(strip),
    }
    manifest = _manifest({"id": name, "kind": "strip"}, seed=None,
                         prompt="composited from the drawn tiles above (see `order`)",
                         source_file="none - composited",
                         output_file=f"{name}.png", output_dims=list(size), extra=meta, home=home)
    (src_dir / f"{name}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"[asm ] strip {name:18s} {size[0]}x{size[1]} slots={members}")
    return meta


def _backdrop_image(tile, size, scale):
    if scale != 1:
        tile = tile.resize((TILE * scale, TILE * scale), _nearest())
    img = Image.new("RGBA", size, (20, 22, 31, 255))
    step = TILE * scale
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            img.alpha_composite(tile, (x, y))
    return img


def _backdrop(tile_name, size, scale, tile_dir):
    path = Path(tile_dir) / f"{tile_name}.png"
    tile = (Image.open(path).convert("RGBA") if path.exists()
            else Image.new("RGBA", (TILE, TILE), (40, 44, 56, 255)))
    return _backdrop_image(tile, size, scale)


def _text_width(text, scale):
    from PIL import ImageDraw as _ID, ImageFont

    try:
        font = ImageFont.load_default(size=11 * scale)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    return int(_ID.Draw(Image.new("RGB", (1, 1))).textlength(text, font=font))


def _flow(items, width, margin, gap, scale=1):
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


def _polish_sheet(home, out, pairs, backdrop_tile, tile_dir):
    """The AC's contact sheet: each polished asset NEXT TO the file it replaces.

    The backdrop is a MATERIAL (the room's own floor tile); `tile_dir` is searched first and the
    shared lodge tile directory second, because a room set that ships no tiles of its own (the
    server room, the writer's loft) reuses the lodge material tiles through its room map.
    """
    tile = None
    for candidate in (Path(tile_dir) / f"{backdrop_tile}.png",
                      served_home("lodge") / "tiles" / f"{backdrop_tile}.png"):
        if candidate.exists():
            tile = Image.open(candidate).convert("RGBA")
            break
    if tile is None:
        print(f"[warn] no backdrop tile {backdrop_tile} for {home}/contact_sheet - using a plain "
              f"field (the sheet is evidence, so this is reported rather than hidden)")
        tile = Image.new("RGBA", (TILE, TILE), (20, 22, 31, 255))
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap, header = 16 * scale, 14 * scale, 26 * scale
        row_pair_gap = gap * 4
        items = []
        for label, old, new in pairs:
            for tag, img in ((f"{label['replaces']} (SHIPPED, replaced)", old),
                             (f"{label['key']} (NEW)", new)):
                scaled = img.resize((img.size[0] * scale, img.size[1] * scale), _nearest())
                items.append((f"{tag} {img.size[0]}x{img.size[1]}", scaled))
        widest = max(_text_width(label, scale) for label, _im in items)
        title = (f"SOU-216 {home}: shipped vs NEW ({scale}x) - NEW files only, nothing overwritten")
        width = max(1024, 2 * margin + widest + row_pair_gap,
                    2 * margin + _text_width(title, scale) + gap)
        placed, body_h = _flow(items, width, margin, gap, scale)
        height = margin + header + body_h + margin + header * 2
        img = _backdrop_image(tile, (width, height), scale)
        base._grid_overlay(img, scale)
        _label(img, (margin, margin), title, scale=scale)
        for label, prop, px, py, h in placed:
            img.alpha_composite(prop, (margin + px, margin + header + py))
            _label(img, (margin + px, margin + header + py + h + 2), label, scale=scale)
        img.save(out / name)
        print(f"[asm ] polish/{home}/{name:18s} {img.size[0]}x{img.size[1]}")


def cmd_assemble(args):
    if "garage" in args.set:
        _assemble_garage()
    if "polish" in args.set:
        _assemble_polish()
    print("ASSEMBLE_DONE")


def _assemble_garage():
    out = out_dir("garage")
    (out / "props").mkdir(parents=True, exist_ok=True)
    (out / "tiles").mkdir(parents=True, exist_ok=True)

    (out / "palette.json").write_text(json.dumps({
        "name": SETS["garage"]["pack"],
        "set": "garage",
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "shared_with": "lodge (SOU-198), serverroom + factory (SOU-199), sets C (SOU-200)",
        "why": "One palette for the whole lodge family (SOU-199 ruling, restated for SOU-216). The "
               "garage is a room of the same building as the lodge, the office and the four "
               "personality rooms, so it quantises to the SAME 61 declared colours rather than to "
               "a second palette that would drift the building apart. The colour tables live in "
               "scripts/v3/gen_lodge_art.py and are imported here - there is one copy.",
        "light": "top-left (same as every v3 set): each material ramp runs dark (bottom-right "
                 "faces) to light (top-left faces)",
    }, indent=2) + "\n")

    results = {}
    for asset in GARAGE_PROPS:
        meta = _assemble_prop(asset, out, SETS["garage"]["seed_base"], home="garage")
        if meta:
            results[asset["id"]] = meta

    extra_meta = {}
    tile_docs = {}
    for name, cls, axes, spec in GARAGE_TILES:
        note = ("same concrete as garage_floor with one oil stain in the interior" if spec.get("stain")
                else "")
        tile_docs[name] = _assemble_tile(name, "garage", cls, axes, spec, GARAGE_DRAWN_TILES[name],
                                         out, extra_note=note)
    _assemble_strip("tiles", [n for n, _c, _a, _s in GARAGE_TILES], [64, 32], out, out / "tiles",
                    "garage")

    # the stain variant must differ from the plain tile ONLY inside its interior region
    plain = Image.open(out / "tiles" / "garage_floor.png").convert("RGBA")
    stain = Image.open(out / "tiles" / "garage_floor_stain.png").convert("RGBA")
    diff = [(x, y) for y in range(TILE) for x in range(TILE)
            if plain.getpixel((x, y)) != stain.getpixel((x, y))]
    outside = [p for p in diff if not (2 <= p[0] <= 29 and 2 <= p[1] <= 29)]
    extra_meta["stain_vs_plain"] = {
        "changed_px": len(diff),
        "changed_outside_interior": len(outside),
        "why": "the stain variant is the same tile plus one interior stain, so the calm border ring "
               "(which is what makes the tile seamless by construction) is untouched",
    }

    index = {
        "pack": SETS["garage"]["pack"],
        "set": "garage",
        "issue": ISSUE,
        "title": SETS["garage"]["title"],
        "generated_from": "assets/generated/v3/garage",
        "model": MODEL_ID,
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "camera": "top-down 3/4, light from the top-left (same projection vocabulary as office-01)",
        "palette": "assets/generated/v3/garage/palette.json",
        "palette_shared_with": "lodge (SOU-198), serverroom + factory (SOU-199), set C (SOU-200)",
        "tileset": {
            "path": "assets/garage/tiles.png",
            "tile_px": TILE, "columns": 2, "firstgid": 1,
            "order": [n for n, _c, _a, _s in GARAGE_TILES],
            "slot_class": {n: c for n, c, _a, _s in GARAGE_TILES},
            "slot_seam_axes": {n: a for n, _c, a, _s in GARAGE_TILES},
            "why": "the garage stands on its own concrete, not on the lodge's plank or flagstone "
                   "floor: two slots, plain and stained, in a 64x32 strip",
        },
        "floor_tile_for_this_room": SETS["garage"]["backdrop"],
        "room_map": "frontend/v3/maps/rooms/garage.json",
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"] for a in GARAGE_PROPS},
        "assets": [{"key": a["id"], "served_path": f"assets/garage/props/{a['id']}.png",
                    "texture_px_declared": a["texture_px"], "footprint_px": a["footprint_px"],
                    "footprint_tiles": a["footprint_tiles"], "lift_px": a["lift_px"],
                    "frames": 1, "flat": a["flat"], "reference_edit": a.get("reference"),
                    "texture_px_actual": list(Image.open(out / "props" / f"{a['id']}.png").size)
                    if (out / "props" / f"{a['id']}.png").exists() else None}
                   for a in GARAGE_PROPS],
        "notes": extra_meta,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    _garage_contact_sheets(results, tile_docs)


def _garage_contact_sheets(results, tile_docs):
    out = out_dir("garage")
    assets = [a for a in GARAGE_PROPS if (out / "props" / f"{a['id']}.png").exists()]
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap, header = 16 * scale, 14 * scale, 26 * scale
        items = []
        for asset in assets:
            img = Image.open(out / "props" / f"{asset['id']}.png").convert("RGBA")
            img = img.resize((img.size[0] * scale, img.size[1] * scale), _nearest())
            items.append((f"{asset['id']} {asset['texture_px'][0]}x{asset['texture_px'][1]} "
                          f"fp {asset['footprint_px'][0]}x{asset['footprint_px'][1]} "
                          f"lift {asset['lift_px']}", img))
        widest = max(_text_width(label, scale) for label, _im in items)
        title = (f"SOU-216 garage - {len(assets)} props on the garage_floor concrete tile. "
                 f"{len(PALETTE_HEX)}-colour lodge palette, all single-frame.")
        width = max(1024, 2 * margin + widest + gap, 2 * margin + _text_width(title, scale) + gap)
        placed, body_h = _flow(items, width, margin, gap, scale)
        sheet_h = margin + header + body_h + margin + header * 2
        img = _backdrop(SETS["garage"]["backdrop"], (width, sheet_h), scale, out / "tiles")
        base._grid_overlay(img, scale)
        _label(img, (margin, margin), title, scale=scale)
        for label, prop, px, py, h in placed:
            img.alpha_composite(prop, (margin + px, margin + header + py))
            _label(img, (margin + px, margin + header + py + h + 2), label, scale=scale)
        img.save(out / name)
        print(f"[asm ] garage/{name:22s} {img.size[0]}x{img.size[1]}")


def _assemble_polish():
    root = out_dir("polish")
    root.mkdir(parents=True, exist_ok=True)
    (root / "palette.json").write_text(json.dumps({
        "name": "polish216",
        "set": "polish",
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "shared_with": "every set it polishes (lodge, serverroom, writer)",
        "why": "A polish pass may not re-tint the building: the new files are quantised to the SAME "
               "61 lodge colours as the files they replace, so the only thing that changes is the "
               "shape of the material, never the palette.",
        "light": "top-left (unchanged)",
    }, indent=2) + "\n")
    (root / "ISOLATION.md").write_text(
        "This directory carries the SOU-216 polish pass. It lives OUTSIDE the home sets' generated\n"
        "trees (`assets/generated/v3/lodge|serverroom|writer`) on purpose: those trees are cleaned\n"
        "and reassembled by their own modules (`gen_lodge_art.py clean`, `gen_roomart_b.py clean`),\n"
        "and a delivered polish file must not be deletable by another module. The SERVED files land\n"
        "in their home set's directory, which is where the wiring step reads them.\n")

    written = {}
    for name, home, axes, spec in POLISH_TILES:
        replaces = {"stone_floor_soft": "stone_floor", "wall_face_soft": "wall_face"}[name]
        cls = "material"
        doc = _assemble_tile(name, home, cls, axes, spec, POLISH_DRAWN_TILES[name],
                             polished_out(home), replaces=replaces)
        written[name] = doc
    for name, home, members, size in POLISH_STRIPS:
        _assemble_strip(name, members, size, polished_out(home), polished_out(home) / "tiles", home)
        written[name] = {"kind": "strip", "home": home, "order": members}

    pairs_by_home = {}
    for asset in POLISH_PROPS:
        doc = _assemble_prop(asset, polished_out(asset["home"]), 0, home=asset["home"])
        if doc is None:
            continue
        written[asset["id"]] = doc
        ref_path = served_original(asset["home"], "prop", asset["replaces"])

        doc["replaces_reference"] = {
            "replaces": asset["replaces"],
            "served_path": f"frontend/v3/assets/{asset['home']}/props/{asset['replaces']}.png",
            "sha256": sha256(ref_path) if ref_path.exists() else None,
            "missing": not ref_path.exists(),
        }
        manifest_path = polished_out(asset["home"]) / "props" / f"{asset['id']}.png.manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["replaces_reference"] = doc["replaces_reference"]
        if ref_path.exists():
            old = Image.open(ref_path).convert("RGBA")
            new = Image.open(polished_out(asset["home"]) / "props" / f"{asset['id']}.png").convert("RGBA")
            manifest["packing"] = {
                "old": shelf_packing(old),
                "new": shelf_packing(new),
                "why": "SOU-216 reads the shipped shelves as 'nearly empty wooden frames'. The "
                       "measures are how much of the 64x112 CANVAS the shelf fills (a packed shelf "
                       "fills its frame) and how many GEAR-coloured pixels it carries, plus the "
                       "status-light count (the declared cool-glow family + led_orange) and the "
                       "plant count. The gate asserts canvas share AND gear px both went up, "
                       "status lights >= 8 px, and a plant present.",
            }
            if asset["id"].startswith("paper_stacks"):
                manifest["line_marks"] = line_marks(new)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        pk = manifest.get("packing")
        if pk:
            print(f"[asm ] polish/{asset['home']}/{asset['id']}: canvas opaque "
                  f"{pk['old']['canvas_opaque_share']} -> {pk['new']['canvas_opaque_share']}, "
                  f"gear px {pk['old']['gear_px']} -> {pk['new']['gear_px']}, "
                  f"status lights {pk['new']['led_px']}, plant px {pk['new']['plant_px']}")

    (root / "polish.json").write_text(json.dumps({
        "issue": ISSUE,
        "set": "polish",
        "title": "polish pass on shipped sets",
        "generated_from": f"assets/generated/v3/{POLISH_DIR}",
        "model": MODEL_ID,
        "palette": f"assets/generated/v3/{POLISH_DIR}/palette.json",
        "why": "the owner read four shipped assets as wrong at room scale; these are NEW files "
               "under NEW names - nothing shipped is overwritten, so the wiring step can switch a "
               "key over and roll back by switching it back",
        "assets": written,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")

    # contact sheets: each polished asset next to the file it replaces
    for home, backdrop in (("lodge", "room_plank_a"), ("serverroom", "stone_floor"),
                           ("writer", "room_plank_a")):
        pairs = []
        for name, doc in written.items():
            if doc.get("kind") == "strip" or doc.get("home") != home:
                continue
            replaces = doc.get("replaces")
            if not replaces or not doc.get("path"):
                continue
            old_path = served_original(home, "tile" if doc.get("kind") == "tile" else "prop",
                                       replaces)
            new_path = polished_out(home) / doc["path"]
            if not new_path.exists() or not old_path.exists():
                continue
            pairs.append(({"key": name, "replaces": replaces},
                          Image.open(old_path).convert("RGBA"),
                          Image.open(new_path).convert("RGBA")))
        if pairs:
            _polish_sheet(home, polished_out(home), pairs, backdrop, out_dir("garage") / "tiles")


def cmd_clean(args):
    """Remove DERIVED output only - never a raw generation (the SOU-158 lesson)."""
    removed, kept = [], 0
    for setname in args.set:
        if setname == "polish":
            dirs = [polished_out(h) for h in ("lodge", "serverroom", "writer")]
        else:
            dirs = [out_dir(setname)]
        for out in dirs:
            if not out.is_dir():
                continue
            for sub in ("props", "tiles"):
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
                if f.is_file() and f.name not in ("ISOLATION.md",):
                    f.unlink()
                    removed.append(f"{out.name}/{f.name}")
    print(f"cleaned {len(removed)} derived file(s); kept {kept} raw generation(s) + their prompts")
    for name in removed:
        print(f"  - {name}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    names = ("garage", "polish")
    for name, helptext in (("generate", "FLUX pass (needs the sou-flux venv + MPS)"),
                           ("assemble", "CPU pass: key, fit, quantise, manifests, sheets"),
                           ("clean", "remove derived output only - the raws are kept")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--set", action="append", choices=names,
                       help="repeatable; defaults to both")
        if name == "generate":
            p.add_argument("--only", help="comma-separated asset ids")
            p.add_argument("--force", action="store_true", help="regenerate even if a raw exists")
            p.add_argument("--no-reference", action="store_true",
                           help="drop the reference edit and generate the van text-only")
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
