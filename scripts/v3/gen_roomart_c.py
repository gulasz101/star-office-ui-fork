#!/usr/bin/env python3
"""Generate the v3 art set C: four personality rooms of the lodge at night (SOU-200).

Four room sets in ONE module, because they are one pipeline, one camera and one palette - only the
prop tables differ:

    mystery    "the Bill Cipher cabin" (owner's nickname for hermes-default): jars and crystals,
               a one-eyed painting, a crystal ball, a lectern, a cipher-wheel rug, an eerie lantern
    tokenden   the grey-zone token trading den (hermes-szarik): coin stacks, a lucky cat, red
               lanterns, a tea table, a humming token shelf, an abacus desk with a CRT
    career     the career / personal-brand office (hermes-careerpa "Zdzislaw"): a coat stand, a
               whiteboard with an upward chart, a ring light, a tidy desk, blank framed certs, a ficus
    writer     the blog writer's loft (hermes-florian): a typewriter desk, manuscript stacks, a
               paper bin, a deep reading chair, a cork board, a leaning book tower

Everything shared with the previous sets is IMPORTED, never copied - the projection vocabulary
(VIEW/STYLE/KEY/NO_TEXT), the keying recipe, the island/despeckle/hole chain, `quantise`,
`fit_to_canvas`, `_label`/`_grid_overlay` and `_region_shift` come from `gen_office01_art.py`; the
61-colour night palette comes from `gen_lodge_art.py`. A second copy of the keying recipe is a
second thing to fix; a second palette is how two rooms of the same building stop looking like one
building.

Rulings (stated on the ticket, cost recorded):

1. **Set C shares the lodge palette.** SOU-200 says "one shared palette per set (warm lodge
   palette)"; read exactly as SOU-199 read it - the warm lodge palette for the whole lodge family,
   not four new palettes. Cost if wrong: a re-quantise pass, CPU-only, no GPU time (raws stay).
2. **The ticket gives size CLASSES, not pixel sizes** ("1x1 = 32x32-48, 2x1 = 64x64-112 tall,
   2x2 = 64x64-96", wall decals 64x48 and 64x32, one 3x2 flat rug). The exact box of every prop is
   therefore chosen here, inside its class, and `check_v3_assets.py` asserts both the class bounds
   AND the exact box against a hand-typed copy of the table (`TICKET_C`). A class range is not a
   specification - it is a licence to choose, and the choice has to be checkable.
3. **Props ship at EXACTLY the declared box** (`fit_mode="inside"`, the SOU-198/199 rule), so
   `texture_px` is the contract the renderer can rely on. Cost: a slim silhouette is centred in its
   box rather than width-filled.
4. **Wall decals carry a footprint plus a lift**, exactly like SOU-199's `network_cabinet`: the
   renderer draws `footprint_w x (footprint_h + lift_px)` anchored bottom-centre, so the texture
   still ships at its declared size and the anchor stays a rect the room map can place.
5. **Some props ARE a frame** (the eye painting, the blank certificate frames, the cork board, the
   whiteboard). The shared `NO_TEXT` clause forbids "any frame or border"; for those assets only,
   an explicit per-asset clause re-permits the object's own decorative wooden frame while keeping
   every other prohibition (no letters, numbers, words, logos, marks, labels) in force. Recorded in
   the prompt text and in the manifest `prompt_ruling`.
6. **No LoRA** (it collapses to black in diffusers - ADR-060 field result), text-only prompts, no
   marks, no readable text. Reference-edit of OUR OWN klein output is allowed and NOT used here:
   nothing in this set is derived from the owner's reference images - they are described in words
   in the ticket and those words are what the prompts carry.

Two passes, so a layout change never costs GPU time:

    generate   FLUX.2-klein-4B on MPS -> <id>.raw.png + prompt + seed + mode
    assemble   CPU-only, deterministic from the raws: key -> fit -> quantise -> manifests -> sheets

Set C is entirely single-frame: no belts, no machines, so every asset ships `frames=1` and there is
no frame derivation to declare beyond frame 0 (the untouched FLUX base). Nothing here is animated
because the ticket asks for furniture, not machinery - the factory's belts (SOU-199) stay the only
animated room set.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_roomart_c.py generate --set mystery --set tokenden
    python3 scripts/v3/gen_roomart_c.py assemble
    python3 scripts/v3/gen_roomart_c.py clean --set career
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

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

# Wall decals are the only place the shared NO_TEXT clause needs a per-asset exception (ruling 5).
FRAME_OK = ("This object's OWN carved wooden frame or border is wanted and is part of the object: "
            "the 'no frame or border' rule above does not apply to it. Every other prohibition "
            "stands unchanged - no letters, no numbers, no words, no logos, no marks, no labels, "
            "no signage anywhere on it. ")

PLAIN_GLOW = ("The glow is plain coloured light only - no letters, no numbers, no symbols, no "
              "characters, no writing of any kind. ")

# Per-asset reinforcement of the shared KEY clause, for the asset whose seed painted the background
# in a colour the object also uses (measured: a dark stand on a dark navy field, so the border-seeded
# flood fill leaked through the stand's outline and keyed the column away).
MAGENTA_FIELD = ("The background must be one single flat bright magenta #FF00FF field covering every "
                 "pixel that is not the object - never dark, never navy, never white, never a "
                 "gradient. The object itself is never magenta anywhere.")

# Per-asset reinforcement of the shared VIEW clause, for the desk whose seed came back as a pure
# top-down view of the table top with no front face and no legs (measured: 88% opaque, no legs, and
# a reviewer read the wooden top as a leftover floor patch under the desk).
FRONT_FACE = ("The FRONT FACE of this object and its four short legs must both be visible below the "
              "table top: the top surface occupies the upper part of the object and the front panel "
              "with its legs occupies the lower part, standing on the floor. This is NOT a flat "
              "top-down view of the table top alone, and there is no floor, rug or ground patch "
              "underneath the legs.")

SETS = {
    "mystery": {
        "pack": "mystery-v1",
        "issue": "SOU-200",
        "title": "the mystery cabin (hermes-default)",
        "map": "mystery.json",
        "backdrop": "room_plank_a",
        "seed_base": 20000,
        "look": ("Inside a cosy log-cabin lodge at night: honey-brown horizontal log walls, wide warm "
                 "plank floor, chunky honey-brown timber furniture, warm amber lamplight falling from "
                 "the top-left, deep blue shadow on the right, and a faint eerie green glow from the "
                 "strange objects. Slightly magical, lived-in. "),
    },
    "tokenden": {
        "pack": "tokenden-v1",
        "issue": "SOU-200",
        "title": "the token trading den (hermes-szarik)",
        "map": "tokenden.json",
        "backdrop": "room_plank_a",
        "seed_base": 20100,
        "look": ("Inside a cosy log-cabin back room at night: honey-brown horizontal log walls, wide "
                 "warm plank floor, chunky honey-brown and dark red timber furniture, warm amber "
                 "lamplight and red paper lantern glow from the top-left, deep blue shadow on the "
                 "right, and teal-green and gold electronic glow on the machinery. Playful and "
                 "lived-in, never a caricature. "),
    },
    "career": {
        "pack": "career-v1",
        "issue": "SOU-200",
        "title": "the career office (hermes-careerpa)",
        "map": "career.json",
        "backdrop": "room_plank_a",
        "seed_base": 20200,
        "look": ("Inside a cosy log-cabin office at night: honey-brown horizontal log walls, wide warm "
                 "plank floor, chunky honey-brown timber furniture, warm amber lamplight falling from "
                 "the top-left, deep blue shadow on the right, and a cool white ring-light glow plus "
                 "small teal-green and orange equipment lights. Tidy, modern, lived-in. "),
    },
    "writer": {
        "pack": "writer-v1",
        "issue": "SOU-200",
        "title": "the writer's loft (hermes-florian)",
        "map": "writer.json",
        "backdrop": "room_plank_a",
        "seed_base": 20300,
        "look": ("Inside a cosy log-cabin loft at night: honey-brown horizontal log walls, wide warm "
                 "plank floor, chunky honey-brown timber furniture, warm amber desk-lamp and candle "
                 "light falling from the top-left, deep blue shadow on the right, and cream paper "
                 "everywhere. Cluttered, warm, lived-in. "),
    },
}

# --------------------------------------------------------------------------------------
# The prop tables - THIS is the ticket's table, restated once and asserted against a second
# copy of it in check_v3_assets.py.
#
# texture_px  the exact canvas that ships (single frame; set C ships no sheets)
# footprint   the map rect the renderer anchors: `placeholders.js` PROP_STYLE draws
#             footprint_w x (footprint_h + lift) with origin (0.5, 1)
# lift_px     texture_h - footprint_h
# klass       the ticket's size class this box was chosen inside (asserted by the gate)
# --------------------------------------------------------------------------------------


def _asset(pid, subset, subject, texture, footprint, lift, *, klass, flat=False, post="full",
           frame_ok=False, extra="", note=""):
    return {
        "id": pid,
        "kind": "prop",
        "set": subset,
        "subject": subject,
        "texture_px": [texture[0], texture[1]],
        "sheet_px": None,
        "frames": 1,
        "animation": None,
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        "fit_mode": "inside",
        "flat": flat,
        "post": post,
        "full_bleed": False,
        "connects": None,
        "klass": klass,
        "frame_ok": frame_ok,
        "extra": extra,
        "note": note,
    }


MYSTERY_ASSETS = [
    _asset(
        "curiosity_shelf", "mystery",
        "a chunky honey-brown timber shelving unit seen from above and slightly in front, packed "
        "with curiosities: three glass jars with pale glowing contents, two odd grey and violet "
        "crystals, a thick old leather journal lying shut, a small stuffed owl with round amber "
        "button eyes, and a little potted green plant",
        (64, 96), (64, 32), 64, klass="2x1",
        note="2x1 shelf; the footprint is the 2x1 floor rect it stands on, the extra 64px is lift"),
    _asset(
        "eye_painting", "mystery",
        "a painted canvas in an ornate carved golden-brown wooden picture frame, hanging flat facing "
        "the viewer: on the canvas a single glowing yellow one-eyed triangle with a dark pupil, thin "
        "radiating rays around it and a few small floating geometric shapes, painted in flat pixel "
        "shades of gold, amber and deep blue",
        (64, 48), (64, 32), 16, klass="wall64x48", frame_ok=True,
        extra=PLAIN_GLOW,
        note="wall decal: 64x32 wall rect + 16px lift carries the 64x48 canvas up the wall"),
    _asset(
        "crystal_ball", "mystery",
        "a small round table with a glass crystal ball resting on a low brass ring stand: the ball "
        "is pale smoky blue-green with a bright highlight, and a soft pale glow sits inside it. Seen "
        "from above and slightly in front, the little table has three short dark wooden legs",
        (32, 40), (32, 32), 8, klass="1x1", post="light", extra=PLAIN_GLOW),
    _asset(
        "journal_stand", "mystery",
        "a small honey-brown wooden lectern stand holding one thick old book lying open on its "
        "sloped top: cream blank pages with faint ruled lines, a dark leather cover and a thin "
        "brass clasp. Seen from above and slightly in front on a short central post",
        (32, 48), (32, 32), 16, klass="1x1"),
    _asset(
        "strange_rug", "mystery",
        "one flat woven rug lying on the floor seen from directly above, rectangular with a simple "
        "dark border: a ring of small geometric spokes, triangles and short radiating dashes "
        "arranged around a plain dark centre, deep teal-green and warm amber wool with cream "
        "highlights, completely flat, square corners, no letters and no numbers anywhere",
        (96, 64), (96, 64), 0, klass="3x2", flat=True, post="light",
        note="3x2 flat floor decal; the ticket calls it a cipher-wheel pattern with no letters"),
    _asset(
        "lantern_green", "mystery",
        "a small floor lantern seen from above and slightly in front: a dark metal frame with four "
        "thin posts, panes of pale glass and a bright eerie teal-green flame glowing inside, with a "
        "small ring handle on top and a flat square base on the floor",
        (32, 48), (32, 32), 16, klass="1x1", extra=PLAIN_GLOW),
]

TOKENDEN_ASSETS = [
    _asset(
        "token_stacks", "tokenden",
        "short stacks of thick round coins lying on the floor seen from directly above: three small "
        "piles of coins in glowing gold and teal-green with pale highlights, and two loose coins "
        "beside them, completely flat, no markings or numbers on the coins",
        (32, 32), (32, 32), 0, klass="1x1", flat=True, post="light",
        note="flat floor decal: coins piled on the floor, assessed as a flat object"),
    _asset(
        "lucky_cat", "tokenden",
        "a small ceramic beckoning-cat figurine standing on a little dark wooden stand, seen from "
        "above and slightly in front: glossy cream-white body with warm amber patches, one raised "
        "round paw beside its head, dark closed eyes and a red collar with a small round gold bell",
        (32, 40), (32, 32), 8, klass="1x1", post="light"),
    _asset(
        "red_lanterns", "tokenden",
        "two round red paper lanterns hanging side by side, seen flat and facing the viewer: warm "
        "crimson paper panels with thin vertical ribs and a small gold cap and tassel on top and "
        "bottom of each, plain and completely unmarked - no characters, no symbols, no writing on "
        "the paper",
        (64, 32), (64, 32), 0, klass="wall64x32", frame_ok=True, post="light", extra=PLAIN_GLOW),
    _asset(
        "tea_table", "tokenden",
        "a low dark-red timber tea table seen from above and slightly in front: a short round "
        "teapot with a curved spout and a small lid, two shallow cream cups, a tiny brass tray and "
        "one folded pale cloth on a wide table top with four short legs",
        (64, 64), (64, 32), 32, klass="2x1"),
    _asset(
        "token_server", "tokenden",
        "a shelf of humming electronic boxes seen from above and slightly in front: three squat dark "
        "grey and black machines stacked on honey-brown timber shelves, each with a row of glowing "
        "teal-green status lights and a narrow vertical coin slot in its front face, short cables "
        "looping between them and coins resting in a small tray on the bottom shelf",
        (64, 96), (64, 32), 64, klass="2x1", extra=PLAIN_GLOW,
        note="2x1 shelf; the footprint is the floor rect, the extra 64px is lift"),
    _asset(
        "abacus_desk", "tokenden",
        "a chunky honey-brown timber desk seen from above and slightly in front with a small beige "
        "retro computer monitor standing on its right half showing a dark teal-green glowing screen, "
        "and a wooden counting frame with rows of small round beads standing on its left half, plus "
        "a short keyboard in front of the monitor and a dark blue mug",
        (64, 80), (64, 64), 16, klass="2x2", extra=PLAIN_GLOW),
]

CAREER_ASSETS = [
    _asset(
        "coat_stand", "career",
        "a slim honey-brown wooden coat stand seen from above and slightly in front: a dark navy "
        "blazer hanging on the shoulder of the stand with a warm amber scarf looped over it, and a "
        "small round wooden base with three splayed feet on the floor",
        (32, 48), (32, 32), 16, klass="1x1"),
    _asset(
        "whiteboard", "career",
        "a standing whiteboard on a dark metal frame seen from above and slightly in front: a white "
        "board with a dark grey border, a single ascending line of small bars and a rising arrow "
        "drawn on it in orange and teal, three small square sticky notes in pale yellow and pink "
        "stuck along one side, and a small tray with two short markers at the bottom edge - shapes "
        "and arrows only, no letters, no numbers and no words anywhere",
        (64, 80), (64, 32), 48, klass="2x1", frame_ok=True,
        note="2x1 standing board: 64x32 floor rect + 48px lift"),
    _asset(
        "ring_light", "career",
        "a ring light on a stand seen from above and slightly in front: a bright glowing white "
        "circular ring lamp at the top, held on a PALE GREY metal tube column, with three short "
        "THICK splayed legs of the same pale grey metal spreading out from a wide flat round base "
        "plate down to the floor. The tube, the legs and the base plate are drawn as solid unbroken "
        "shapes about a tenth of the object's width - no thin hairlines, no gaps, no dark silhouettes "
        "- and a small black camera sits in the middle of the ring pointing forward",
        (32, 48), (32, 32), 16, klass="1x1", post="light", extra=PLAIN_GLOW + MAGENTA_FIELD,
        note="post=light + two re-rolls (SEED 20214 -> 20227 -> 20237). Pass 1 drew the tripod legs "
             "as 3px hairlines inside a 260px-wide object, i.e. a QUARTER of a pixel once fitted "
             "into a 1x1 box, so NEAREST sampled them into a dotted 1px column and the "
             "island/despeckle chain removed what was left - the lamp floated (bottom row 21 of 48, "
             "measured). Pass 2 drew a DARK stand on a DARK non-magenta field, so the border-seeded "
             "flood fill leaked through the stand's outline and keyed the column away: the ring "
             "ended up in two pieces (checked with scripts/v3/_check_c_continuity.py before "
             "assembling). Pass 3 states the material (pale grey, never dark) and re-states the "
             "magenta field. Lesson: leg thickness has to be a FRACTION OF THE OBJECT, and an "
             "object whose colour matches a mis-painted background is a keying trap."),
    _asset(
        "advisor_desk", "career",
        "a tidy honey-brown timber desk seen from above and slightly in front, standing on four "
        "short legs with its front panel showing below the table top: an open slim grey laptop with "
        "a pale glowing screen in the middle, a small potted green plant in a terracotta pot on the "
        "left, a white coffee mug on the right, a neat stack of blank pale papers and a short pen",
        (64, 80), (64, 64), 16, klass="2x2", extra=PLAIN_GLOW + FRONT_FACE,
        note="re-rolled (SEED 20221 -> 20244): pass 1 came back as a pure TOP-DOWN view of the table "
             "top - no front face, no legs, 88% of the canvas opaque - so a reviewer read the wooden "
             "surface as a leftover floor patch under the desk, which is both a 3/4-view contract "
             "breach and the 'no ground patch' KEY breach. The FRONT_FACE clause states what the "
             "lower third of the object has to be."),
    _asset(
        "framed_certs", "career",
        "two rectangular certificates hanging side by side in simple dark wooden picture frames, "
        "seen flat facing the viewer: pale cream paper inside each frame with a plain gold seal "
        "circle and a short red ribbon at the bottom, and nothing else - completely blank, no "
        "letters, no numbers, no words, no signatures, no logos",
        (64, 32), (64, 32), 0, klass="wall64x32", frame_ok=True),
    _asset(
        "ficus", "career",
        "a tall potted indoor tree seen from above and slightly in front: a slim pale grey trunk "
        "rising out of a terracotta pot on the floor, with a rounded mass of small dark and mid green "
        "leaves at the top and a few loose leaves on the floor beside the pot",
        (32, 48), (32, 32), 16, klass="1x1", post="light",
        note="post=light: the pot's base and the loose leaves are 1-2px after the ~13x NEAREST "
             "downscale into a 1x1 box and the despeckle chain shaved the bottom two rows "
             "(measured bottom row 46 of 48 with post=full, 47 with post=light); the anchor AC is "
             "asserted by the gate either way"),
]

WRITER_ASSETS = [
    _asset(
        "typewriter_desk", "writer",
        "a chunky honey-brown timber writing desk seen from above and slightly in front: a small "
        "black and dark grey typewriter in the middle with a single sheet of cream paper standing "
        "up out of its roller, rows of small round keys on its front, a brass desk lamp on a curved "
        "arm on the left throwing warm amber light, and a short stack of blank paper on the right",
        (64, 80), (64, 64), 16, klass="2x2", extra=PLAIN_GLOW,
        note="keys are tiny blank dots - no letters or numbers on the key tops"),
    _asset(
        "paper_stacks", "writer",
        "two short stacks of cream manuscript pages lying on the floor seen from directly above: "
        "neat slightly uneven piles of loose blank paper with visible sheet edges, completely flat, "
        "no writing on the pages",
        (32, 32), (32, 32), 0, klass="1x1", flat=True, post="light"),
    _asset(
        "paper_bin", "writer",
        "a small round open waste bin seen from above and slightly in front: a dark grey metal drum "
        "with a rolled rim, overflowing with crumpled balls of cream and pale grey paper, a few of "
        "them resting on the floor beside the base",
        (32, 40), (32, 32), 8, klass="1x1", post="light"),
    _asset(
        "reading_chair", "writer",
        "a deep honey-brown timber and dark green fabric armchair seen from above and slightly in "
        "front: wide padded armrests and a high back, with a small side table beside it holding one "
        "short lit candle in a brass holder and a cream mug",
        (32, 48), (32, 32), 16, klass="1x1", extra=PLAIN_GLOW,
        note="the ticket calls it 1x1, so the whole chair, side table and candle live in a one-tile "
             "wide box and the canvas is taller instead of wider"),
    _asset(
        "cork_board", "writer",
        "a rectangular cork pin board in a thin dark wooden frame hanging flat facing the viewer: "
        "warm tan cork surface with six small blank pale paper notes pinned to it, tiny brass pin "
        "heads, and two short pieces of red string running diagonally between the notes - blank "
        "notes, no letters, no numbers, no words, no photographs",
        (64, 48), (64, 32), 16, klass="wall64x48", frame_ok=True,
        note="wall decal: 64x32 wall rect + 16px lift carries the 64x48 board up the wall"),
    _asset(
        "book_tower", "writer",
        "a leaning tower of five thick books stacked on top of each other on the floor, seen from "
        "above and slightly in front: each book slightly offset and rotated from the one below, "
        "cloth covers in deep red, dark green, teal and warm brown with pale blank page edges and "
        "spines carrying no text and no titles",
        (32, 48), (32, 32), 16, klass="1x1"),
]

ASSETS = {
    "mystery": MYSTERY_ASSETS,
    "tokenden": TOKENDEN_ASSETS,
    "career": CAREER_ASSETS,
    "writer": WRITER_ASSETS,
}


def out_dir(setname: str) -> Path:
    return REPO / "assets" / "generated" / "v3" / setname


def served_dir(setname: str) -> Path:
    return REPO / "frontend" / "v3" / "assets" / setname


# --------------------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------------------
RATIO_WORD = {
    (96, 64): "The object's bounding box is wider than it is tall",
    (64, 96): "The object's bounding box is about two thirds as wide as it is tall",
    (64, 80): "The object's bounding box is wider than it is tall",
    (64, 64): "The object's bounding box is square",
    (64, 48): "The object's bounding box is wider than it is tall",
    (64, 32): "The object's bounding box is about twice as wide as it is tall",
    (32, 48): "The object's bounding box is about two thirds as wide as it is tall",
    (32, 40): "The object's bounding box is slightly taller than it is wide",
    (32, 32): "The object's bounding box is square",
}


def _ratio_clause(asset):
    """The exact aspect of the SHIPPED canvas, stated numerically and in words.

    Both are needed (the SOU-198 lesson): a vague ratio word is not a specification (a "tall thin
    lamp post" came back 2.4x taller than its declared box), while numbers alone still let the model
    leave a wide empty margin. So: the number, the word, and an explicit fill-the-rectangle clause.
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
    tail = (_palette_clause() + look + VIEW + STYLE + KEY + NO_TEXT)
    if asset.get("frame_ok"):
        tail += FRAME_OK
    if asset.get("extra"):
        tail += asset["extra"]
    tail += ("Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
             "game asset.")
    return head + tail


def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640 (same as office-01)."""
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


# --------------------------------------------------------------------------------------
# fit / post-process (same shape as set B; set C ships single frames so there is no
# per-frame derivation to keep consistent - the fit is still measured once, not per frame)
# --------------------------------------------------------------------------------------
def _fit(img, size, mode="inside"):
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
        "mode": mode,
        "scale": round(nw / cw, 4),
        "crop_bbox": list(bbox),
        "why": ("uniform scale so the object sits inside the declared box - props ship at exactly "
                "the size the ticket declares (SOU-198/199 rule, restated for set C)"),
    }


def _post(img, mode):
    """Island/despeckle/hole chain, per asset (one aggressiveness does not fit all - SOU-198)."""
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
        "ticket": "SOU-200",
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
                print(f"[skip] {setname}/{aid:16s} raw already on disk")
                continue
            if pipe is None:
                pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060)
            gw, gh = _gen_size(*asset["texture_px"])
            seed = SETS[setname]["seed_base"] + index * 7 + args.seed_shift
            prompt = compose_prompt(asset)
            print(f"[gen ] {setname}/{aid:16s} {gw}x{gh} seed={seed}", flush=True)
            image = frg.generate(pipe, torch_mod, prompt, gw, gh, seed=seed,
                                 steps=STEPS, guidance=GUIDANCE)
            image.save(raw)
            (out / f"{aid}.seed.txt").write_text(f"{seed}\n")
            (out / f"{aid}.mode.txt").write_text("text-only generation\n")
            (out / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")


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
        "shared_with": "lodge (SOU-198), serverroom + factory (SOU-199)",
        "why": "One palette for the whole lodge family (SOU-199 ruling, restated for SOU-200). Set C "
               "is four rooms of the same building, not four new games: the mystery cabin, the token "
               "den, the career office and the writer's loft quantise to the SAME 61 declared colours "
               "as the lodge, the server room and the factory, rather than to four palettes that "
               "would drift the building apart. The colour tables live in scripts/v3/gen_lodge_art.py "
               "and are imported here - there is one copy.",
        "light": "top-left (same as every v3 set): each material ramp runs dark (bottom-right faces) "
                 "to light (top-left faces)",
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

        fitted, fit = _fit(keyed_img, tuple(asset["texture_px"]))
        treated, post_meta = _post(fitted, asset["post"])
        quantised = quantise(treated, palette=PALETTE_RGB)
        quantised.save(out / "props" / f"{aid}.png")

        frame = Image.open(out / "props" / f"{aid}.png").convert("RGBA")
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
            "frames": 1,
            "frame_px": list(asset["texture_px"]),
            "sheet_px": list(asset["texture_px"]),
            "frame_derivation": {"0": "the FLUX base (contact), unchanged - set C ships no sheets, "
                                      "so frame 0 is the whole asset"},
            "post_process": asset["post"],
            "post_process_meta": post_meta,
            "full_bleed": False,
            "connects": None,
            "ticket_size_class": asset["klass"],
            "prompt_ruling": ("the shared NO_TEXT clause forbids any frame or border; for this asset "
                              "the object's own decorative frame is explicitly re-permitted while "
                              "every other prohibition stands" if asset.get("frame_ok") else None),
            "content_bottom_row": content_bottom_row(frame),
            "base_row_coverage": round(base_row_coverage(frame), 3),
            "opaque_bottom_rows_coverage": round(base_row_coverage(frame, rows=2), 3),
            "transparent_px": sum(1 for p in frame.getchannel("A").getdata() if p == 0),
            "colours": len(_colours(frame)),
            "off_palette": _off_palette(frame),
        }
        if asset.get("note"):
            meta["note"] = asset["note"]
        manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                             output_file=f"props/{aid}.png",
                             output_dims=list(frame.size), extra=meta)
        (out / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"path": f"props/{aid}.png", **meta}
        print(f"[asm ] {setname}/{aid:16s} {frame.size[0]}x{frame.size[1]} "
              f"fit={fit.get('fitted_px')} colours={meta['colours']} "
              f"off={len(meta['off_palette'])} bottom={meta['content_bottom_row']}")

    build_contact_sheets(setname, results)


# --------------------------------------------------------------------------------------
# contact sheets: everything on the plank floor this room uses
# --------------------------------------------------------------------------------------
def _tile_path(name):
    """Set C ships no tiles: it reuses the lodge material tiles (declared, not copied)."""
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
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=11 * scale)
    except TypeError:  # Pillow < 10.1
        font = ImageFont.load_default()
    return int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(text, font=font))


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


def _asset_label(asset, scale):
    tw, th = asset["texture_px"]
    return (f"{asset['id']} {tw}x{th} fp {asset['footprint_px'][0]}x{asset['footprint_px'][1]} "
            f"lift {asset['lift_px']} ({asset['klass']})")


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
               f"{setname} v3 art set C (SOU-200) - {len(assets)} assets on {backdrop} (the lodge "
               f"floor tile this room uses). {len(PALETTE_HEX)}-colour declared palette, shared with "
               f"the lodge/server room/factory. All single-frame.",
               scale=scale)
        for label, prop, px, py, h in placed:
            img.alpha_composite(prop, (margin + px, margin + header + py))
            _label(img, (margin + px, margin + header + py + h + 2), label, scale=scale)
        img.save(out / name)
        print(f"[asm ] {setname}/{name:20s} {img.size[0]}x{img.size[1]}")


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
                           ("assemble", "CPU pass: key, fit, quantise, manifests, contact sheets"),
                           ("clean", "remove derived output only - the raws are kept")):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--set", action="append", choices=names,
                       help="repeatable; defaults to all four sets")
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
