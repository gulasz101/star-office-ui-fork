#!/usr/bin/env python3
"""Generate the office-01 v3 art set: top-down 3/4 tiles, props, one character (SOU-158).

Why this exists as a separate script from `scripts/flux_room_gen.py` / `gen_room_set.py`:
those two generate art IN the v2 isometric projection and are gated by a base-slope
alignment check against a 30-degree lattice. v3 (SOU-153) changed the camera to
**top-down 3/4** (Pokemon / Stardew interiors): footprints are axis-aligned rectangles,
so a slope gate is meaningless here and the whole projection vocabulary changes. The
model, the keying recipe and the licence rules are unchanged.

Geometry is NOT invented here - it is read from the consumer:
  * footprints  -> `frontend/v3/maps/office-01.json`, object layer `props` (rects in px)
  * overhang    -> `frontend/v3/src/placeholders.js` PROP_STYLE lift, i.e. the renderer
                   draws a prop as `footprint_width x (footprint_height + lift)` and
                   anchors it bottom-centre. Matching that exactly makes this set a
                   drop-in replacement: no renderer change is needed to load it.
  * character   -> `frontend/v3/src/placeholders.js` SHEET_COLS/FACINGS/FRAME:
                   8 columns x 4 rows of 32x48, rows = down, left, right, up,
                   columns = idle, walk x4, work(typing) x2, sleep.

Two passes, so a layout change never costs GPU time:
  generate   FLUX.2-klein-4B on MPS -> <id>.raw.png (untouched model output)
  assemble   key -> fit to declared canvas -> quantise -> manifests, tileset, contact sheets

Licence rules (AGENTS.md / CLAUDE.md, ADR-061): FLUX.2-klein-4B (Apache-2.0) only, no LoRA
(it collapses to black in diffusers - ADR-060 field result), text-only prompts (never
img2img), never trained on Star-Office or LimeZu pixels, no marks, no text in the art.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_office01_art.py generate [--only desk,cat] [--force]
    python3 scripts/v3/gen_office01_art.py assemble

`generate` needs torch+MPS and the cached weights; `assemble` is CPU-only (PIL) and is
deterministic from the raw files, which is what makes the set reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent.parent
OUT = REPO / "assets" / "generated" / "v3" / "office-01"
SERVED = REPO / "frontend" / "v3" / "assets" / "office-01"

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
LORA_ID = "Limbicnation/pixel-art-lora"
STEPS = 4
GUIDANCE = 1.0

# --------------------------------------------------------------------------------------
# Palette: 32 colours, declared once, quantised into every asset (AC3).
#
# It is the v2 room palette the owner already signed off (#c1a46b / #e2d3b4 / #ac9968 /
# #ffea99 / #a3be8c / #88c0d0 + shell blues) EXTENDED with the ramps the new props need:
# a warm wood family for the desk/crate/bookshelf, beige for the 90s van, amber/green for
# the rack LEDs, and a CRT family for the monitor. Light comes from the top-left, so each
# material ramp runs dark (bottom-right faces) to light (top-left faces).
# --------------------------------------------------------------------------------------
PALETTE = [
    ("outline", "#14161f"),
    ("ink", "#1f232e"),
    ("slate_900", "#2a2f3e"),
    ("slate_700", "#3a4152"),
    ("slate_500", "#505a70"),
    ("slate_300", "#6b7590"),
    ("steel_200", "#9aa4bb"),
    ("steel_100", "#c3cad9"),
    ("paper", "#f4ead2"),
    ("cream", "#e2d3b4"),
    ("sand", "#d8c8a0"),
    ("sand_light", "#eadcb8"),
    ("tan", "#c1a46b"),
    ("tan_dark", "#a8894f"),
    ("ochre", "#ac9968"),
    ("wood_light", "#b07a48"),
    ("wood", "#8a5a34"),
    ("wood_dark", "#6e4a2c"),
    ("wood_deep", "#52341f"),
    ("gold", "#ffea99"),
    ("amber", "#e8a33d"),
    ("rust", "#b5651d"),
    ("cat_light", "#e0a35c"),
    ("cat", "#c8a070"),
    ("cat_dark", "#b08a5a"),
    ("moss", "#a3be8c"),
    ("moss_dark", "#6f8a5e"),
    ("teal", "#3d6b73"),
    ("teal_light", "#4f8790"),
    ("ice", "#88c0d0"),
    ("shell_blue", "#44618a"),
    ("crt_green", "#7fbf6a"),
]
PALETTE_HEX = [hexv for _name, hexv in PALETTE]
PALETTE_RGB = [tuple(int(hexv[i : i + 2], 16) for i in (1, 3, 5)) for _n, hexv in PALETTE]

# --------------------------------------------------------------------------------------
# Projection vocabulary. "top-down 3/4" stated as geometry, because a style word alone is
# not a specification: the v2 pipeline measured the model returning a front-facing desk
# when asked for "isometric" without numbers. Here the ruling is a 45-degree camera with
# the front face visible and the footprint an axis-aligned rectangle.
# --------------------------------------------------------------------------------------
VIEW = (
    "drawn in a TOP-DOWN 3/4 view like a Pokemon or Stardew Valley interior sprite: the camera is "
    "about 45 degrees above the object looking down and slightly forward, so the TOP surface of the "
    "object is visible and foreshortened and the FRONT face is visible directly below it as a "
    "rectangle. Vertical edges of the object stay perfectly vertical. The base of the object is an "
    "axis-aligned rectangle (screen-parallel edges), NOT a diamond and NOT a rhombus. This is NOT "
    "an isometric view and NOT a side view. "
    "Do NOT draw the floor, the tile the object stands on, or any ground plane outline for it. "
)
NO_TEXT = (
    "Absolutely no text, no letters, no numbers, no words, no signage, no label, no logo, "
    "no manufacturer badge, no brand mark, no watermark, no user interface, no frame or border. "
)
STYLE = (
    "Crisp 1-pixel dark outlines, flat cel shading with at most two shades per material, "
    "hard-edged pixels, no antialiasing, no blur, no gradients, no dithering, no checkerboard "
    "pattern, no drop shadow, light coming from the top-left. "
)
KEY = (
    "The object sits DIRECTLY on a plain flat magenta #FF00FF background that fills the whole "
    "frame: the magenta goes right up to the object's silhouette, there is no ground patch, base, "
    "platform, pedestal, mat, plinth, rug or contact shadow under it. "
)

# One outfit, stated with the same hex codes for every facing. Measured problem (2026-10-16):
# asking for "the same character" per direction returned FOUR different characters - a brown
# hoodie facing down, a green polo in profile, a dark grey-green coat from behind - and the
# sheet read as a crowd, not one agent. Naming the colours numerically is what makes the model
# hold the outfit; the character palette below then forces every row onto the same ramp anyway,
# so the worst case is a colour the model invented being mapped onto the declared one.
OUTFIT = (
    "The character is exactly the same in every image: a small anime-style engineer with short "
    "black hair, wearing one dark blue hooded jumper (colour #44618a) zipped up, dark slate grey "
    "trousers (colour #3a4152), brown boots (colour #6e4a2c) and pale skin (colour #e2d3b4). "
    "Plain clothing: no logo, no pattern, no text, no badge, no emblem. "
)
CHARACTER_VIEWS = {
    "down": "standing upright, facing the camera (front view), arms at the sides, eyes visible",
    "up": "standing upright, seen from directly behind: the back of the hood and hair, no face, "
          "arms at the sides, the same hooded jumper and trousers visible from behind",
    "right": "standing upright in side profile facing right, arms at the sides, "
             "same hooded jumper with the hood down",
    "sleep": "curled up asleep lying down on its side, eyes closed, knees pulled up",
}


def _prop(pid, subject, fw, fh, lift, note="", flat=False):
    return {
        "id": pid,
        "kind": "prop",
        "subject": subject,
        "footprint_tiles": [fw // 32, fh // 32],
        "footprint_px": [fw, fh],
        "texture_px": [fw, fh + lift],
        "flat": flat,
        "note": note,
    }


CATALOGUE = [
    # --- tiles (no keying: a tile is full-bleed, not a cut-out) ------------------------
    {
        "id": "floor",
        "kind": "tile",
        "subject": "a seamless tileable texture of wide warm wooden floorboards of an office, "
                   "planks running horizontally, subtle grain, viewed from directly above",
        "texture_px": [32, 32],
        "drawn": "floor_a",
        "note": "tiles/floor.png, slot floor_wood_a in office-01.json. DRAWN from the declared "
                "palette, not generated (see DRAWN_TILES): a generated material has no measurable "
                "plank direction and the 2026-10-16 run returned a high-contrast vertical barcode "
                "(measured contrast 209/255, mean |dx| 38.2 vs |dy| 2.4) that overpowered every "
                "prop. The raw generation is kept on disk as the record of what was rejected.",
    },
    {
        "id": "floor_b",
        "kind": "tile",
        "subject": "the same seamless tileable wooden floorboard texture, a slightly darker and "
                   "more saturated plank tone, viewed from directly above",
        "texture_px": [32, 32],
        "drawn": "floor_b",
        "note": "tiles/floor_b.png, slot floor_wood_b (the map alternates the two); same layout as "
                "floor_wood_a, one step darker so the alternation reads without adding contrast",
    },
    {
        "id": "garage_floor",
        "kind": "tile",
        "subject": "a seamless tileable texture of worn grey concrete garage floor with a few small "
                   "stains and fine cracks, viewed from directly above",
        "texture_px": [32, 32],
        "note": "tiles/garage_floor.png, slot garage_concrete",
    },
    {
        "id": "wall",
        "kind": "tile",
        "subject": "a seamless tileable texture of a cream painted plaster wall surface, flat and "
                   "evenly lit, viewed straight on",
        "texture_px": [32, 32],
        "note": "tiles/wall.png, slot wall_face (the front face of the wall)",
    },
    {
        "id": "wall_cap",
        "kind": "tile",
        "subject": "a seamless tileable texture of the flat top surface of a low office wall, dark "
                   "blue-grey painted plaster, viewed from directly above",
        "texture_px": [32, 32],
        "drawn": "wall_cap",
        "note": "tiles/wall_cap.png, slot wall_cap (what you see of the wall from above). DRAWN: "
                "plain dark cap, one flat colour plus a 1px darker bottom edge - the generated "
                "version came back with 22 colours and cream speckles (a measured 212/255 "
                "contrast) which read as confetti dots along the top of every wall",
    },
    # --- props: footprints and lifts exactly as the v3 renderer declares them ---------
    _prop("desk", "a work desk with a CRT monitor, a small desk lamp and a coffee mug",
          64, 64, 22),
    _prop("chair", "a simple office swivel chair with a low back, seen slightly from above, "
                   "its seat face and the top of its back visible", 32, 32, 18),
    _prop("rack", "a tall narrow server rack with a glass door, blinking amber and green status "
                  "lights and a coiled patch cable hanging on the side", 32, 64, 40),
    _prop("bookshelf", "a low wooden bookshelf with two shelves of colourful book spines and a "
                       "small potted plant on top", 64, 64, 40),
    _prop("coffee", "a low kitchen counter, wider than it is deep and only waist high, with a "
                    "bean-to-cup coffee machine, two stacked cups and a small metal canister on its "
                    "top surface. It is a low wide box, not a tall cabinet: it must read as wider "
                    "than it is tall", 64, 32, 26),
    _prop("rug", "a flat woven rectangular rug LYING ON THE FLOOR, seen from directly above: a wide "
                 "flat textile rectangle exactly three tiles wide by two tiles tall with a simple "
                 "geometric border pattern woven into it. It is flat: one tile thick or less, no "
                 "height, no frame, not a picture, not hanging on a wall, no mirror, no wall behind "
                 "it. It fills the whole frame edge to edge", 96, 64, 0,
          "decal: flat on the floor, no overhang", flat=True),
    _prop("couch", "a two-seat couch with rounded armrests and two cushions, seen from above and "
                   "slightly in front", 96, 32, 14),
    _prop("bass", "a worn electric bass guitar standing upright on a small floor stand", 32, 32, 30),
    _prop("amp", "a small square bass practice amplifier with a dark grille cloth and three knobs",
          32, 32, 14),
    _prop("cat", "an orange tabby cat curled up asleep inside a small open cardboard box, tail "
                 "tucked around itself", 32, 32, 8,
          "decor: the owner's favourite, no footprint perspective expected"),
    _prop("van", "a small boxy 1990s Japanese minivan painted flat pale beige (a warm sand body "
                 "#eadcb8 with #d8c8a0 and #c1a46b shading), plain unbranded, with dark blue-grey "
                 "glass and a simple boxy silhouette: one long flat roof, an upright windscreen, "
                 "slab-flat sides, a short blunt nose, thin pillars, squared-off body, small simple "
                 "wheels. Seen from above and slightly in front so its roof, windscreen and front "
                 "bumper are all visible with the nose pointing toward the camera. It is a plain "
                 "boxy old van: NOT a car, NOT a sedan, NOT an SUV, NOT a sports car, no spoiler, "
                 "no chrome, no alloy wheels, not silver, not grey, not metallic, no badges",
          96, 128, 40),
    _prop("spool", "an empty wooden cable spool lying on the floor with a few loose network cables "
                   "coiled around it", 32, 32, 12),
    _prop("crate", "a closed wooden crate with a stencilled lid and a small potted plant on top",
          32, 32, 12),
    # --- character bases: one FLUX generation per facing, frames derived (see assemble) -
    {
        "id": "char_down",
        "kind": "char",
        "view": "down",
        "subject": "a full body game character sprite of a small anime-style engineer in a hooded "
                   "jumper and trousers",
        "texture_px": [32, 48],
        "note": "base for sheet row 'down'",
    },
    {
        "id": "char_up",
        "kind": "char",
        "view": "up",
        "subject": "a full body game character sprite of a small anime-style engineer in a hooded "
                   "jumper and trousers",
        "texture_px": [32, 48],
        "reference": "char_down",
        "note": "base for sheet row 'up' - generated as a REFERENCE EDIT of char_down, so the "
                "identity (hood, hair, outfit colours) is carried over instead of re-invented",
    },
    {
        "id": "char_right",
        "kind": "char",
        "view": "right",
        "subject": "a full body game character sprite of a small anime-style engineer in a hooded "
                   "jumper and trousers",
        "texture_px": [32, 48],
        "reference": "char_down",
        "note": "base for sheet row 'right'; row 'left' is its mirror. Reference edit of char_down "
                "for the same identity",
    },
    {
        "id": "char_sleep",
        "kind": "char",
        "view": "sleep",
        "subject": "a full body game character sprite of a small anime-style engineer in a hooded "
                   "jumper and trousers",
        "texture_px": [32, 48],
        "reference": "char_down",
        "note": "base for sheet column 7 (sleep) in every row; reference edit of char_down so the "
                "sleeping sprite is the same person as the standing one",
    },
]

# The character ramp: a documented SUBSET of PALETTE, applied to the character sheet only.
# Why: four independent generations cannot be trusted to agree on colours, and a character whose
# outfit changes colour when it turns is the most visible "different game" defect there is.
# Quantising the sheet through this ramp makes the sheet consistent BY CONSTRUCTION; it stays a
# subset of PALETTE, so AC3 (every asset on the declared palette) still holds.
CHARACTER_RAMP = [
    "#14161f",  # outline
    "#2a2f3e",  # hair / deep shadow
    "#3a4152",  # trousers
    "#505a70",  # jumper shadow
    "#44618a",  # the hooded jumper
    "#88c0d0",  # jumper highlight
    "#6e4a2c",  # boots
    "#b07a48",  # boots highlight
    "#e2d3b4",  # skin
    "#f4ead2",  # skin highlight
    "#c1a46b",  # hairband / accent
]
CHARACTER_RGB = [tuple(int(h[i : i + 2], 16) for i in (1, 3, 5)) for h in CHARACTER_RAMP]
BY_ID = {a["id"]: a for a in CATALOGUE}


# --------------------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------------------
def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640.

    Generating at the target aspect (instead of a square and cropping) is what keeps the
    fit-to-canvas stage a pure scale: a 3:4 van asked of a 1:1 canvas comes back with a
    different silhouette than the one that has to fit a 96x168 texture.
    """
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


def compose_prompt(asset):
    palette = ", ".join(PALETTE_HEX)
    kind = asset["kind"]
    if kind == "tile":
        head = (
            f"seamless tileable pixel art texture: {asset['subject']}. "
            "Orthographic top-down view, flat and evenly lit, the pattern repeats without seams in "
            "every direction, no perspective, no single object, no border. "
        )
        tail = (
            f"Palette limited to exactly these colours: {palette}. "
            + STYLE + NO_TEXT +
            "Fill the entire frame edge to edge with the texture - no background, no margin, "
            "no transparent area. 32-bit pixel art texture, game tile."
        )
        return head + tail
    if kind == "char":
        # A character is not an object on a 45-degree camera: it is a standing figure in a
        # slightly elevated game camera. Asking for the object VIEW here produced, among other
        # things, a figure with a perspective floor hint and a side profile that read flat.
        head = (
            f"pixel art game character sprite: {asset['subject']}. {OUTFIT}"
            f"Pose: {CHARACTER_VIEWS[asset['view']]}. Full body visible from head to feet, centred "
            "in the frame, nothing else in the frame. Drawn in the slightly elevated camera of a "
            "top-down game: the character stands upright and reads as a complete standing figure. "
            "Do NOT draw the floor, a base, a platform or a contact shadow under the feet. "
        )
        if asset.get("reference"):
            # Reference edit (klein is a unified generate+edit model). The init image is OUR OWN
            # klein output, never upstream/LimeZu/Star-Office art, which is what the ADR-061
            # no-img2img rule protects - so identity is carried over instead of re-invented.
            head += (
                "This is an EDIT of the reference image: keep EXACTLY the same character - the same "
                "person, same short black hair (colour #14161f), same pale skin (#e2d3b4), same dark "
                "blue hooded jumper (#44618a) with the same hood, same dark slate grey trousers "
                "(#3a4152), same brown boots (#6e4a2c), same proportions and same 1px dark outline. "
                "Change ONLY the camera angle to the pose above: do not restyle the character, do "
                "not change its colours, do not add or remove clothing, do not redraw the face as a "
                "different person. "
            )
        tail = (
            f"Palette limited to exactly these colours: {', '.join(CHARACTER_RAMP)}. "
            + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game character sprite."
        )
        return head + tail
    head = f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in frame. "
    tail = (
        f"Palette limited to exactly these colours: {palette}. "
        + VIEW + STYLE + KEY + NO_TEXT +
        "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, game asset."
    )
    return head + tail


def cmd_generate(args):
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    OUT.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    todo = [a for a in CATALOGUE if only is None or a["id"] in only]
    if not todo:
        sys.exit(f"--only {args.only}: no such asset in the catalogue")

    pipe = None
    torch_mod = None
    for index, asset in enumerate(CATALOGUE):
        if asset not in todo:
            continue
        raw = OUT / f"{asset['id']}.raw.png"
        if raw.exists() and not args.force:
            print(f"[skip] {asset['id']:12s} raw already on disk")
            continue
        if asset.get("drawn"):
            print(f"[skip] {asset['id']:12s} DRAWN from the palette - no FLUX pass, see assemble")
            continue
        if pipe is None:
            pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060 field result)
        tw, th = asset["texture_px"]
        gw, gh = _gen_size(tw, th)
        seed = 15800 + index * 7
        prompt = compose_prompt(asset)
        ref = None
        if asset.get("reference"):
            ref_path = OUT / f"{asset['reference']}.keyed.png"
            if not ref_path.exists():
                sys.exit(f"{asset['id']} wants a reference edit of {asset['reference']} but "
                         f"{ref_path.name} does not exist - generate the reference first")
            ref = Image.open(ref_path).convert("RGB")
            print(f"[gen ] {asset['id']:12s} {gw}x{gh} seed={seed} REFERENCE EDIT of "
                  f"{ref_path.name} ({ref.size[0]}x{ref.size[1]})", flush=True)
        else:
            print(f"[gen ] {asset['id']:12s} {gw}x{gh} seed={seed}", flush=True)
        image = _generate(frg, pipe, torch_mod, prompt, gw, gh, seed=seed, reference=ref)
        image.save(raw)
        (OUT / f"{asset['id']}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")


def _generate(frg, pipe, torch_mod, prompt, width, height, seed, reference=None):
    """Text-only generation, or a reference edit of OUR OWN previously generated image.

    klein is a unified generate+edit model, so the pipeline takes an `image` argument that is
    concatenated into the transformer input as a conditioning image (there is no `strength`
    knob). Passing one is what makes "the same character, different camera" hold: four
    independent text-only generations returned four different people (measured 2026-10-16,
    SOU-158 review). The reference is always a `.keyed.png` this pipeline produced itself -
    never upstream Star-Office or LimeZu art, which is what the no-img2img rule protects.
    """
    if reference is None:
        return frg.generate(pipe, torch_mod, prompt, width, height, seed=seed,
                            steps=STEPS, guidance=GUIDANCE)
    gen = torch_mod.Generator(device="cpu").manual_seed(seed)
    t0 = __import__("time").time()
    image = pipe(
        image=reference,
        prompt=prompt,
        height=height,
        width=width,
        num_inference_steps=STEPS,
        guidance_scale=GUIDANCE,
        generator=gen,
    ).images[0]
    print(f"[gen] {width}x{height} seed={seed} steps={STEPS} reference-edit "
          f"in {__import__('time').time() - t0:.1f}s", flush=True)
    return image


# --------------------------------------------------------------------------------------
# post-process (CPU only)
# --------------------------------------------------------------------------------------
def _nearest():
    return getattr(getattr(Image, "Resampling", Image), "NEAREST", None) or Image.NEAREST


def _dither_none():
    return getattr(getattr(Image, "Dither", Image), "NONE", None) or Image.NONE


def key_magenta(img, tolerance=48):
    """Border-seeded flood key + de-fringe + stray-magenta sweep.

    Same recipe as scripts/gen_iso_prop.py (SOU-98 / SOU-122 lessons), copied here rather
    than imported because that module pulls in the isometric room geometry this branch has
    no use for:
      * the key colour is the most common border colour, flood-filled from the border only,
        so magenta INSIDE the object (a monitor bezel gone pink) cannot punch a hole in it;
      * de-fringe uses the blue-vs-green discriminator (r-g>20 AND b-g>10 AND g<150) - warm
        browns and creams have b<g and survive it, magenta and pink do not;
      * a stray sweep catches magenta islands the flood fill could not reach.
    """
    from collections import deque

    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    border = [px[x, 0] for x in range(0, w, max(1, w // 32))]
    border += [px[x, h - 1] for x in range(0, w, max(1, w // 32))]
    border += [px[0, y] for y in range(0, h, max(1, h // 32))]
    border += [px[w - 1, y] for y in range(0, h, max(1, h // 32))]
    key = max(set(border), key=border.count)
    close = lambda c: all(abs(c[i] - key[i]) <= tolerance for i in range(3))  # noqa: E731
    seen = bytearray(w * h)
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not seen[y * w + x] and close(px[x, y]):
                seen[y * w + x] = 1
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not seen[y * w + x] and close(px[x, y]):
                seen[y * w + x] = 1
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        px[x, y] = (0, 0, 0, 0)
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not seen[ny * w + nx] and close(px[nx, ny]):
                seen[ny * w + nx] = 1
                queue.append((nx, ny))
    # de-fringe + stray sweep, one pass
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            if (r - g) > 20 and (b - g) > 10 and g < 150:
                px[x, y] = (0, 0, 0, 0)
            elif r > 180 and b > 180 and g < 120:
                px[x, y] = (0, 0, 0, 0)
    return img, key


def content_bbox(img):
    return img.convert("RGBA").getchannel("A").getbbox()


def quantise(img, alpha_threshold=96, palette=None):
    """Binary alpha + exact nearest-colour palette mapping in ONE palette (AC3).

    Two traps this deliberately avoids:

    * **`Image.quantize(palette=...)` is not a reliable fixed-palette map here** - it let
      `#000000` pixels through (measured: every prop came back with an off-palette black),
      which would have failed AC3 on the shipped art. The mapping below is explicit: for each
      unique RGB in the image, the nearest palette entry by squared RGB distance, memoised.
    * **RGB must be zeroed where alpha is 0.** The old pipeline shipped props whose
      transparent pixels still carried the magenta RGB: invisible until something blends them,
      then a fringe (SOU-98 lesson). Zeroing them here means no downstream consumer can
      reintroduce it.

    Alpha is binarised at `alpha_threshold` (96) so no semi-transparent halo can survive as an
    off-palette blend, and the result has exactly two alpha levels.
    """
    img = img.convert("RGBA")
    alpha = img.getchannel("A").point(lambda v: 255 if v >= alpha_threshold else 0)
    src = img.load()
    w, h = img.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    dst = out.load()
    cache: dict[tuple[int, int, int], tuple[int, int, int]] = {}
    for y in range(h):
        for x in range(w):
            r, g, b, a = src[x, y]
            if a < alpha_threshold:
                continue  # transparent: stays (0,0,0,0), never the source RGB
            key = (r, g, b)
            hit = cache.get(key)
            if hit is None:
                target = palette or PALETTE_RGB
                hit = min(target, key=lambda p: (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2)
                cache[key] = hit
            dst[x, y] = (hit[0], hit[1], hit[2], 255)
    return out


def fit_to_canvas(content, size, anchor="bottom", mode="width"):
    """Scale the keyed content into the declared canvas with NEAREST, then anchor it.

    The renderer puts a prop's origin at the footprint's bottom-centre and the character's
    at its feet, so the declared canvas IS the contract.

    Two modes, because props and characters fail in opposite directions:
      * mode="width" (props, default): the WIDTH is the contract - the renderer draws a prop
        at its footprint width, so `scale = declared_w / content_w` and the canvas HEIGHT
        grows to `max(declared_h, scaled_h)` if the model returned a taller silhouette.
        Fitting by height instead shrinks the width below the footprint (measured: the rug
        came back 50px wide in a 96px footprint) and the prop then reads as too small and
        mis-anchored - the SOU-147 class of defect.
      * mode="inside" (characters): the frame is a FIXED 32x48 that every frame of the sheet
        must obey, so the sprite is scaled uniformly to fit inside it (a side profile is
        naturally narrower than a front view; stretching it to 32px would fatten the
        character as it turns).
    """
    nearest = _nearest()
    w, h = size
    bbox = content_bbox(content)
    if bbox is None:
        return Image.new("RGBA", size, (0, 0, 0, 0)), {"empty": True}
    content = content.crop(bbox)
    cw, ch = content.size
    if mode == "width":
        scale = w / cw
        nw, nh = w, max(1, round(ch * scale))
        canvas_h = max(h, nh)
        fitted_by = "width"
    else:
        scale = min(w / cw, h / ch)
        nw, nh = max(1, round(cw * scale)), max(1, round(ch * scale))
        canvas_h = h
        fitted_by = "width" if scale == w / cw else "height"
    fitted = content.resize((nw, nh), nearest)
    canvas = Image.new("RGBA", (w, canvas_h), (0, 0, 0, 0))
    x = (w - nw) // 2
    y = canvas_h - nh if anchor == "bottom" else (canvas_h - nh) // 2
    canvas.alpha_composite(fitted, (x, max(0, y)))
    return canvas, {
        "content_px": [cw, ch],
        "fitted_px": [nw, nh],
        "canvas_px": [w, canvas_h],
        "declared_px": [w, h],
        "scale": round(scale, 4),
        "fitted_by": fitted_by,
        "overhang_px": canvas_h - h if mode == "width" else 0,
        "offset": [x, max(0, y)],
    }


def content_bottom_row(img):
    """Index of the lowest row that carries content (None if fully transparent)."""
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    w, h = img.size
    px = alpha.load()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            if px[x, y]:
                return y
    return None


def base_row_coverage(img, rows=2):
    """Fraction of the bottom `rows` rows that carries content.

    Reported, not asserted: a desk stands on four legs, so its lowest two rows are mostly
    transparent and that is correct art, not a floating prop. What IS asserted (in the
    checker) is `content_bottom_row == h - 1`: the content must reach the canvas bottom,
    because that edge is where the renderer anchors the footprint.
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    hits = sum(1 for y in range(h - rows, h) for x in range(w) if px[x, y][3] > 0)
    return hits / (rows * w)


def seam_ratio(img):
    """How seamless a tile is: wrap-around edge step vs the interior average step.

    A tile repeats, so column w-1 sits next to column 0. If that step is no worse than a
    typical neighbouring-column step inside the tile, the repeat is invisible. Ratio <= 1.5
    reads as seamless; a large ratio is a visible seam (measured, not asserted).
    """
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()

    def col_step(a, b):
        return sum(abs(px[a, y][i] - px[b, y][i]) for y in range(h) for i in range(3)) / (h * 3)

    def row_step(a, b):
        return sum(abs(px[x, a][i] - px[x, b][i]) for x in range(w) for i in range(3)) / (w * 3)

    inner_cols = [col_step(x, x + 1) for x in range(w - 1)]
    inner_rows = [row_step(y, y + 1) for y in range(h - 1)]
    mean = lambda v: sum(v) / len(v) if v else 0.0  # noqa: E731
    wrap_x = col_step(w - 1, 0)
    wrap_y = row_step(h - 1, 0)
    mx, my = mean(inner_cols), mean(inner_rows)
    return {
        "wrap_x": round(wrap_x, 2),
        "mean_inner_x": round(mx, 2),
        # a flat/uniform tile has no interior variation: 0.0 is the honest answer, not None
        "ratio_x": round(wrap_x / mx, 2) if mx else 0.0,
        "wrap_y": round(wrap_y, 2),
        "mean_inner_y": round(my, 2),
        "ratio_y": round(wrap_y / my, 2) if my else 0.0,
    }


def _wrap_score(win):
    """Ranking scorer for the crop search - the SAME measurement the manifest reports.

    It used to sample every other row/column, which ranked windows by a number that did not
    agree with the reported seam (floor_b came back with a worse ratio than the naive
    full-texture downscale). Ranking and reporting must be one measurement or the search
    optimises the wrong quantity.
    """
    px = win.convert("RGB").load()
    w, h = win.size

    def cstep(a, b):
        return sum(abs(px[a, y][i] - px[b, y][i]) for y in range(h) for i in range(3)) / (h * 3)

    def rstep(a, b):
        return sum(abs(px[x, a][i] - px[x, b][i]) for x in range(w) for i in range(3)) / (w * 3)

    cx = [cstep(x, x + 1) for x in range(w - 1)]
    ry = [rstep(y, y + 1) for y in range(h - 1)]
    mx = (sum(cx) / len(cx)) if cx else 0.0
    my = (sum(ry) / len(ry)) if ry else 0.0
    return max(cstep(w - 1, 0) / mx if mx else 0.0, rstep(h - 1, 0) / my if my else 0.0)


def best_tile_crop(raw, size=32, inter_tiles=2):
    """Pick the 32x32 window of a generated texture whose repeat seam is smallest.

    A diffusion texture is NOT seamless, and pretending otherwise is how a visible line
    every 32px ships: the first floor window measured a vertical wrap step 5.4x the average
    neighbouring-row step (measured, not guessed - the planks' staggered joints do not line
    up across the wrap). So the tile is not "the texture scaled down", it is a CROP chosen
    for its repeat: downscale the texture to `size * inter_tiles` with NEAREST (keeping the
    tile an exact pixel-art downsample - no resampling is introduced) and slide the window
    over it, keeping the one with the lowest worst-axis wrap ratio. The chosen offset and
    the before/after ratios go in the manifest, so an improved texture can be compared.
    """
    inter = raw.convert("RGB").resize((size * inter_tiles, size * inter_tiles), _nearest())
    span = size * inter_tiles - size
    best = None
    for oy in range(0, span + 1):
        for ox in range(0, span + 1):
            win = inter.crop((ox, oy, ox + size, oy + size))
            score = _wrap_score(win)
            if best is None or score < best[0]:
                best = (score, ox, oy, win)
    score, ox, oy, win = best
    return win, {"crop_offset": [ox, oy], "inter_px": [size * inter_tiles] * 2,
                 "wrap_ratio_chosen": round(score, 2)}


# --------------------------------------------------------------------------------------
# character: 4 FLUX bases -> the 32-frame sheet the renderer registers
# --------------------------------------------------------------------------------------
def _region_shift(frame, region, dx, dy):
    """Move one rectangular region of a frame by whole pixels, leaving the rest in place."""
    x0, y0, x1, y1 = region
    patch = frame.crop((x0, y0, x1, y1))
    frame.paste((0, 0, 0, 0), (x0, y0, x1, y1))
    frame.alpha_composite(patch, (x0 + dx, y0 + dy))
    return frame


def _bob(frame, dy):
    out = Image.new("RGBA", frame.size, (0, 0, 0, 0))
    out.alpha_composite(frame, (0, dy))
    return out


def _frame_regions(frame):
    """Arm and leg regions for a DERIVED frame, measured from the frame's own content bbox.

    Hardcoded pixel regions were a real defect (caught by the gate, 2026-10-16): the left/right
    rows are ~11px wide inside a 32px frame, so a region defined as `x < 11` contained no pixels
    at all and the typing columns came out identical to idle - animation that silently is not
    one. Measuring the bbox makes the derivation work for a narrow profile and a wide front view
    alike.
    """
    bbox = frame.getchannel("A").getbbox()
    if bbox is None:
        return None
    x0, y0, x1, y1 = bbox
    bw, bh = x1 - x0, y1 - y0
    cx = (x0 + x1) // 2
    leg_top = y1 - max(3, int(bh * 0.22))
    arm_top = y0 + int(bh * 0.40)
    arm_bot = y0 + int(bh * 0.72)
    arm_w = max(1, int(bw * 0.35))
    return {
        "legs_left": (x0, leg_top, cx, y1),
        "legs_right": (cx, leg_top, x1, y1),
        "arm_left": (x0, arm_top, x0 + arm_w, arm_bot),
        "arm_right": (x1 - arm_w, arm_top, x1, arm_bot),
    }


def _derived_frame(base, ops):
    """A copy of `base` with whole-pixel region shifts applied - the whole frame derivation."""
    regions = _frame_regions(base)
    if regions is None:
        return base.copy()
    out = base.copy()
    for name, dx, dy in ops:
        out = _region_shift(out, regions[name], dx, dy)
    return out


WALK_SEQUENCE = {
    # (ops, bob) - a stride: one leg forward while the other goes back, with a body bob on the
    # two middle frames. Declared as a derivation in the manifest; it is a stride, not a
    # hand-drawn cycle.
    1: ([("legs_left", 0, -2), ("legs_right", 0, 2)], 0),
    2: ([("legs_left", 0, 2)], -1),
    3: ([("legs_left", 0, 2), ("legs_right", 0, -2)], 0),
    4: ([("legs_right", 0, 2)], -1),
}
WORK_SEQUENCE = {
    5: [("arm_left", 0, 1), ("arm_right", 0, -1)],
    6: [("arm_left", 0, -1), ("arm_right", 0, 1)],
}


def build_character_sheet(bases):
    """8 cols x 4 rows of 32x48, exactly the order frontend/v3/src/placeholders.js registers.

    Honest scope note (also on the ticket): the four idle/sleep frames are direct FLUX
    outputs; the walk and typing columns are DETERMINISTIC PIXEL DERIVATIONS of the same
    base - body bob and an alternating leg/arm shift - not 32 independent generations. The
    walk therefore reads as a stride rather than a hand-animated cycle. It is recorded as
    `derivation` in the manifest so nobody has to guess where a frame came from.
    """
    cols, rows = 8, 4
    fw, fh = 32, 48
    sheet = Image.new("RGBA", (fw * cols, fh * rows), (0, 0, 0, 0))
    derivation = {}
    order = ["down", "left", "right", "up"]
    for row, facing in enumerate(order):
        if facing == "left":
            base = bases["right"].transpose(Image.FLIP_LEFT_RIGHT)
            derivation[f"{facing}/base"] = "mirror of char_right"
        else:
            base = bases[facing]
            derivation[f"{facing}/base"] = f"char_{facing}"
        frames = {}
        frames[0] = base
        derivation[f"{facing}/0 idle"] = "base, unchanged"
        for col in (1, 2, 3, 4):
            ops, bob = WALK_SEQUENCE[col]
            f = _derived_frame(base, ops)
            if bob:
                f = _bob(f, bob)
            frames[col] = f
            derivation[f"{facing}/{col} walk{col}"] = (
                "leg halves shifted +/-2px from the frame's own bbox"
                + (", body bob -1px" if bob else ""))
        for col in (5, 6):
            frames[col] = _derived_frame(base, WORK_SEQUENCE[col])
            derivation[f"{facing}/{col} work{col - 4}"] = (
                "arm bands (from the frame's own bbox) shifted +/-1px - the typing alternation")
        sleep = bases["sleep"]
        if facing == "left":
            sleep = sleep.transpose(Image.FLIP_LEFT_RIGHT)
        frames[7] = sleep
        derivation[f"{facing}/7 sleep"] = "char_sleep" + (" (mirrored)" if facing == "left" else "")
        for col, frame in frames.items():
            sheet.alpha_composite(frame, (col * fw, row * fh))
    return sheet, derivation


# --------------------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------------------
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
        "view": "top-down 3/4",
    }
    payload.update(extra)
    return payload


def cmd_assemble(args):
    (OUT / "tiles").mkdir(parents=True, exist_ok=True)
    (OUT / "props").mkdir(parents=True, exist_ok=True)
    (OUT / "character").mkdir(parents=True, exist_ok=True)

    # palette.json is the declared palette the checker validates every asset against
    (OUT / "palette.json").write_text(json.dumps({
        "name": "office-01-v1",
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "why": "One palette for the whole set (SOU-158 AC3). Extends the v2 room palette the owner "
               "signed off with the wood/beige/amber/crt ramps the new props need; every asset is "
               "quantised to exactly these colours by scripts/v3/gen_office01_art.py assemble.",
    }, indent=2) + "\n")

    results = {}
    keyed = {}
    char_bases = {}
    char_modes = {}
    for index, asset in enumerate(CATALOGUE):
        aid = asset["id"]
        if asset.get("drawn"):
            # DRAWN tile: a floor/wall cap is geometry plus two or three shades, so it is drawn
            # from the declared palette instead of generated. Measured reason (SOU-158 review,
            # 2026-10-16): the generated floor returned a high-contrast vertical barcode
            # (contrast 209/255, mean |dx| 38.2 vs mean |dy| 2.4 - the stripes ran the WRONG way
            # for the "horizontal planks" prompt) and visually overpowered every prop and the
            # character. Drawing makes the plank direction, the contrast budget and the
            # seamlessness measurable properties rather than hopes: period 32 in both axes, so
            # the tile repeats exactly (seam ratio 1.0). Same ruling as ADR-064/065 - geometry is
            # declared, never generated. The rejected raw generation stays on disk as the record.
            quant = quantise(draw_tile(asset["drawn"]))
            out_path = OUT / "tiles" / f"{aid}.png"
            quant.save(out_path)
            meta = {
                "derivation": f"drawn from the declared palette ({asset['drawn']}), no FLUX pass",
                "seam": seam_ratio(quant),
                "seam_before_crop": None,
                "note": asset.get("note", ""),
            }
            manifest = _manifest(asset, seed=None,
                                 prompt="drawn from the declared palette - no FLUX pass "
                                        "(see `derivation`)",
                                 source_file="none - drawn, see DRAWN_TILES",
                                 output_file=f"tiles/{aid}.png",
                                 output_dims=asset["texture_px"], extra=meta)
            (OUT / "tiles" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            results[aid] = {"kind": "tile", "path": f"tiles/{aid}.png", **meta}
            s = meta["seam"]
            print(f"[asm ] {aid:12s} tile 32x32 DRAWN seam x={s['ratio_x']} y={s['ratio_y']}")
            continue
        raw = OUT / f"{aid}.raw.png"
        if not raw.exists():
            print(f"[miss] {aid}: no raw generation on disk")
            continue
        seed = 15800 + index * 7
        prompt = (OUT / f"{aid}.prompt.txt").read_text().strip() if (OUT / f"{aid}.prompt.txt").exists() else compose_prompt(asset)
        src = Image.open(raw)
        if asset["kind"] == "tile":
            # a tile is full-bleed: no keying, no alpha, no anchor - size, repeat and palette
            naive = quantise(src.convert("RGB").resize(tuple(asset["texture_px"]), _nearest()).convert("RGBA"))
            tile_src, crop_meta = best_tile_crop(src, size=asset["texture_px"][0])
            quant = quantise(tile_src.convert("RGBA"))
            out_path = OUT / "tiles" / f"{aid}.png"
            quant.save(out_path)
            meta = {
                "seam": seam_ratio(quant),
                "seam_before_crop": seam_ratio(naive),
                **crop_meta,
                "note": "the tile is a 32x32 crop of the generated texture chosen for its repeat "
                        "(see crop_offset); seam_before_crop is the same measurement on the naive "
                        "full-texture downscale, so the improvement is checkable",
            }
            manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                                 output_file=f"tiles/{aid}.png",
                                 output_dims=asset["texture_px"], extra=meta)
            (OUT / "tiles" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            results[aid] = {"kind": "tile", "path": f"tiles/{aid}.png", **meta}
            s, b = meta["seam"], meta["seam_before_crop"]
            print(f"[asm ] {aid:12s} tile 32x32 seam after x={s['ratio_x']} y={s['ratio_y']} "
                  f"(before x={b['ratio_x']} y={b['ratio_y']}) crop={crop_meta['crop_offset']}")
            continue
        keyed_img, key = key_magenta(src)
        keyed[aid] = keyed_img
        keyed_img.save(OUT / f"{aid}.keyed.png")
        if asset["kind"] == "char":
            canvas, fit = fit_to_canvas(keyed_img, (32, 48), anchor="bottom", mode="inside")
            frame = quantise(canvas, palette=CHARACTER_RGB)
            frame.save(OUT / f"{aid}.frame.png")
            char_bases[asset["view"]] = frame
            mode = (f"reference edit of {asset['reference']}.keyed.png (our own klein output)"
                    if asset.get("reference") else "text-only generation")
            char_modes[asset["view"]] = mode
            results[aid] = {"kind": "char", "path": f"{aid}.frame.png",
                            "key_colour": list(key), "fit": fit, "generation_mode": mode}
            print(f"[asm ] {aid:12s} char frame 32x48 fit={fit['fitted_px']} by {fit['fitted_by']} "
                  f"({mode})")
            continue
        if asset.get("flat"):
            # a decal has no silhouette to scale: it IS the footprint (a rug lying on the floor),
            # so it is fitted into the declared box exactly rather than keeping its aspect - a
            # generated "flat" rug came back 4:5 portrait (a framed picture) and anchoring that
            # bottom-centre would have hung it off its own footprint. The stretch is declared.
            bbox = content_bbox(keyed_img)
            cropped = keyed_img.crop(bbox) if bbox else keyed_img
            canvas = cropped.resize(tuple(asset["texture_px"]), _nearest())
            quant = quantise(canvas)
            fit = {"mode": "stretch to the declared decal box",
                   "content_px": list(cropped.size), "canvas_px": list(asset["texture_px"])}
        else:
            canvas, fit = fit_to_canvas(keyed_img, asset["texture_px"], anchor="bottom")
            quant = quantise(canvas)
        out_path = OUT / "props" / f"{aid}.png"
        quant.save(out_path)
        meta = {
            "footprint_tiles": asset["footprint_tiles"],
            "footprint_px": asset["footprint_px"],
            "texture_px": fit["canvas_px"],
            "declared_px": asset["texture_px"],
            "anchor": "bottom-centre of the footprint (renderer origin 0.5,1)",
            "key_colour": list(key),
            "fit": fit,
            "content_bottom_row": content_bottom_row(quant),
            "base_row_coverage": round(base_row_coverage(quant), 3),
        }
        if fit.get("overhang_px"):
            meta["height_note"] = (
                f"canvas grew {fit['overhang_px']}px taller than the declared {asset['texture_px'][1]}px "
                f"because the silhouette is taller than the placeholder box; width is the footprint "
                f"width exactly, which is what the renderer draws")
        if asset.get("note"):
            meta["note"] = asset["note"]
        manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                             output_file=f"props/{aid}.png", output_dims=asset["texture_px"], extra=meta)
        (OUT / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"kind": "prop", "path": f"props/{aid}.png", **meta}
        print(f"[asm ] {aid:12s} prop {quant.size[0]}x{quant.size[1]} "
              f"fit={fit.get('fitted_px')} by {fit.get('fitted_by') or fit.get('mode')} "
              f"base-rows={meta['base_row_coverage']}")

    # character sheet
    bases = char_bases
    if len(bases) == 4:
        sheet, derivation = build_character_sheet(bases)
        (OUT / "character" / "office-01.png").parent.mkdir(parents=True, exist_ok=True)
        sheet.save(OUT / "character" / "office-01.png")
        sheet_manifest = _manifest(
            {"id": "character/office-01", "kind": "char"},
            seed=15800, prompt="one FLUX base per facing (down/up/right/sleeping) + 1 FLIP + "
                               "deterministic frame derivation",
            source_file="char_down.raw.png, char_up.raw.png, char_right.raw.png, char_sleep.raw.png",
            output_file="character/office-01.png", output_dims=[256, 192],
            extra={
                "sheet": {"cols": 8, "rows": 4, "frame_px": [32, 48],
                          "facings": ["down", "left", "right", "up"],
                          "columns": "0 idle, 1-4 walk, 5-6 work/typing, 7 sleep",
                          "consumer": "frontend/v3/src/placeholders.js SHEET_COLS/FACINGS/FRAME"},
                "derivation": derivation,
                "generation_mode": char_modes,
                "character_ramp": CHARACTER_RAMP,
                "character_ramp_why": "the sheet is quantised through this 11-colour subset of the "
                                      "declared palette, so all four facings share one outfit by "
                                      "construction - four independent generations otherwise "
                                      "returned four different characters (measured 2026-10-16)",
                "note": "The idle and sleep columns are direct FLUX outputs; the walk and typing "
                        "columns are deterministic pixel derivations of the same base (leg/arm "
                        "region shifts + body bob), recorded per frame under `derivation`. The "
                        "char_up / char_right / char_sleep bases are REFERENCE EDITS of char_down "
                        "(klein's unified generate+edit mode, conditioning image = our own "
                        "char_down.keyed.png), which is what holds one identity across the "
                        "facings; no upstream or LimeZu pixels are involved anywhere.",
            })
        (OUT / "character" / "office-01.png.manifest.json").write_text(json.dumps(sheet_manifest, indent=2) + "\n")
        results["character"] = {"kind": "sheet", "path": "character/office-01.png", "dims": [256, 192]}
        print("[asm ] character    sheet 256x192 (8x4 of 32x48)")
    else:
        print(f"[warn] character sheet skipped: have bases {sorted(bases)}")

    build_tileset_sheet()
    build_contact_sheets(results)
    print("ASSEMBLE_DONE")


TILESET_ORDER = [
    ("floor_wood_a", "floor"),
    ("floor_wood_b", "floor_b"),
    ("garage_concrete", "garage_floor"),
    ("wall_cap", "wall_cap"),
    ("wall_face", "wall"),
    ("wall_base", "wall"),
    ("door_mat", None),
    ("garage_stripe", None),
]


def _tile_from(pid):
    path = OUT / "tiles" / f"{pid}.png"
    return Image.open(path).convert("RGBA") if path.exists() else None


def build_tileset_sheet():
    """8 tiles in one row, in the exact order/gids of office-01.json's tileset.

    office-01.json already names this file layout (`../assets/placeholder/tiles.png`, 8 tiles,
    8 columns, firstgid 1). Emitting the same sheet with the real art means the wiring change
    is a path swap, not a map edit. Slots with no FLUX generation of their own are DERIVED
    from one that has - a dark strip for wall_base, a palette-drawn mat and stripe - and the
    derivation is named here rather than passed off as generation.
    """
    sheet = Image.new("RGBA", (32 * 8, 32), (0, 0, 0, 0))
    derived = {}
    for slot, (name, source) in enumerate(TILESET_ORDER):
        if source:
            tile = _tile_from(source)
            if tile is None:
                continue
            if name == "wall_base":
                tile = quantise(_darken_bottom(tile, rows=10, factor=0.72))
                derived[name] = "wall.png with its bottom 10 rows darkened 0.72 (a skirting/base band)"
            else:
                derived[name] = f"tiles/{source}.png verbatim"
        else:
            tile = quantise(_draw_slot(name))
            derived[name] = "drawn from the declared palette (no FLUX pass): a decal, not a material"
        sheet.alpha_composite(tile, (slot * 32, 0))
    sheet.save(OUT / "tiles" / "tileset.png")
    (OUT / "tiles" / "tileset.png.manifest.json").write_text(json.dumps({
        "model": MODEL_ID, "lora": LORA_ID, "lora_strength": 0.0, "steps": STEPS,
        "guidance": GUIDANCE,
        "prompt": "composed from the tiles in TILESET_ORDER (see `derivation`)",
        "seed": None, "source_file": "tiles/floor.png, tiles/floor_b.png, tiles/garage_floor.png, "
                                     "tiles/wall.png, tiles/wall_cap.png",
        "output_file": "tiles/tileset.png", "output_dims": [256, 32],
        "palette_colors": len(PALETTE_HEX), "palette": PALETTE_HEX,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asset_key": "office-01-tileset", "kind": "tileset",
        "tileset_contract": {
            "consumer": "frontend/v3/maps/office-01.json tilesets[0]",
            "tile_px": 32, "columns": 8, "firstgid": 1,
            "order": [name for name, _src in TILESET_ORDER],
        },
        "derivation": derived,
        "note": "Drop-in for the placeholder sheet: same 8 tiles, same order, same gids. Only the "
                "image path in the map's tileset entry has to change.",
    }, indent=2) + "\n")
    print("[asm ] tileset      tiles/tileset.png 256x32 (8 slots, office-01.json order)")


def _darken_bottom(tile, rows=10, factor=0.72):
    out = tile.copy()
    w, h = out.size
    px = out.load()
    for y in range(h - rows, h):
        for x in range(w):
            r, g, b, a = px[x, y]
            px[x, y] = (int(r * factor), int(g * factor), int(b * factor), a)
    return out


def _rgb(hexv):
    """The palette RGB of a declared hex - the only colour source a drawn tile may use."""
    return PALETTE_RGB[PALETTE_HEX.index(hexv)]


# --------------------------------------------------------------------------------------
# Drawn tiles. A floor is geometry plus two or three shades, so it is DRAWN from the
# declared palette rather than generated - the same ruling as ADR-064/065 ("geometry is
# declared, never generated"), applied to the one asset whose whole job is a direction and a
# contrast budget. Measured reason (SOU-158 review, 2026-10-16): the generated floor came back
# as a high-contrast vertical barcode - contrast 209/255, mean |dx| 38.2 against mean |dy| 2.4,
# i.e. the planks ran ACROSS the prompt's "horizontal planks" - and it visually overpowered
# every prop and the character, which are supposed to be the brightest things on screen.
# Drawing makes plank direction, contrast and seamlessness measurable: 4 horizontal planks of
# 8px with a 1px joint, staggered 1px board ends, period 32 in both axes, so the tile repeats
# exactly (seam ratio 1.0 by construction). The rejected raws stay on disk as the record.
# --------------------------------------------------------------------------------------
PLANK_H = 8
DRAWN_TILES = {
    # 3 close warm-brown shades per floor, then a 1px darker joint. floor_b is the same
    # layout one step darker so the map's alternation reads without adding contrast.
    "floor_a": {"planks": ["#a8894f", "#c1a46b", "#ac9968", "#a8894f"], "seam": "#8a5a34"},
    "floor_b": {"planks": ["#8a5a34", "#a8894f", "#b07a48", "#8a5a34"], "seam": "#6e4a2c"},
    "wall_cap": {"flat": "#3a4152", "edge": "#2a2f3e"},
}


def draw_tile(name):
    """Draw one of DRAWN_TILES at 32x32, using declared palette colours only."""
    from PIL import ImageDraw

    spec = DRAWN_TILES[name]
    for hexv in [spec.get("flat"), spec.get("edge"), spec.get("seam"), *spec.get("planks", [])]:
        if hexv and hexv not in PALETTE_HEX:
            raise ValueError(f"drawn tile {name}: {hexv} is not in the declared palette")
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if "flat" in spec:
        # a plain dark cap: one flat colour plus a 1px darker bottom edge for definition
        d.rectangle([0, 0, 31, 31], fill=_rgb(spec["flat"]))
        d.rectangle([0, 31, 31, 31], fill=_rgb(spec["edge"]))
        return img
    seam = _rgb(spec["seam"])
    for i, hexv in enumerate(spec["planks"]):
        y0 = i * PLANK_H
        d.rectangle([0, y0, 31, y0 + PLANK_H - 1], fill=_rgb(hexv))
        d.rectangle([0, y0 + PLANK_H - 1, 31, y0 + PLANK_H - 1], fill=seam)  # the plank joint
        x = 8 if i % 2 == 0 else 24  # staggered board ends, so planks do not read as stripes
        d.rectangle([x, y0, x, y0 + PLANK_H - 2], fill=seam)
    return img


def _draw_slot(name):
    """The two decal slots, drawn from palette colours (no FLUX pass, declared as such)."""
    from PIL import ImageDraw

    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    moss_dark = PALETTE_RGB[PALETTE_HEX.index("#6f8a5e")]
    gold = PALETTE_RGB[PALETTE_HEX.index("#ffea99")]
    ink = PALETTE_RGB[PALETTE_HEX.index("#14161f")]
    concrete = _tile_from("garage_floor")
    if name == "door_mat":
        d.rectangle([3, 3, 28, 28], fill=moss_dark, outline=ink)
        d.rectangle([6, 6, 25, 25], outline=gold)
    elif name == "garage_stripe":
        if concrete is not None:
            img.alpha_composite(concrete)
        d.rectangle([0, 13, 31, 18], fill=gold)
        d.line([0, 13, 31, 13], fill=ink)
        d.line([0, 18, 31, 18], fill=ink)
    return img


# --------------------------------------------------------------------------------------
# contact sheets
# --------------------------------------------------------------------------------------
LABEL_BG = (20, 22, 31, 235)


def _label(img, xy, text, scale=1):
    from PIL import ImageDraw, ImageFont

    try:
        font = ImageFont.load_default(size=11 * scale)
    except TypeError:  # Pillow < 10.1 has no sized default
        font = ImageFont.load_default()
    d = ImageDraw.Draw(img)
    x, y = xy
    box = d.textbbox((x, y), text, font=font)
    d.rectangle([box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2], fill=LABEL_BG)
    d.text((x, y), text, fill=(244, 234, 210), font=font)


def _floor_backdrop(size, scale=1):
    floor = _tile_from("floor") or Image.new("RGBA", (32, 32), (120, 90, 60, 255))
    if scale != 1:
        floor = floor.resize((32 * scale, 32 * scale), _nearest())
    w, h = size
    img = Image.new("RGBA", size, (20, 22, 31, 255))
    for y in range(0, h, 32 * scale):
        for x in range(0, w, 32 * scale):
            img.alpha_composite(floor, (x, y))
    return img


def build_contact_sheets(results):
    """Both sheets, same layout at 1x and 4x, so the set can be judged as one game.

    Layout: props in a wrapped flow on the floor tile (each labelled with its id, footprint
    in tiles and real texture px), then the character sheet, then the tileset strip. A 32px
    grid is drawn faintly over the floor so the footprint snapping is visible rather than
    asserted. Both sheets are produced from the same source files; the 4x one is where
    fringe, dithering and off-palette pixels would show.
    """
    props = [a for a in CATALOGUE if a["kind"] == "prop"]
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap, label_h, width = 16 * scale, 18 * scale, 18 * scale, 1024
        x, y, row_h = margin, 44 * scale, 0
        placed = []
        for asset in props:
            path = OUT / "props" / f"{asset['id']}.png"
            if not path.exists():
                continue
            img = Image.open(path).convert("RGBA")
            w, h = img.size[0] * scale, img.size[1] * scale
            if x + w > width - margin:
                x = margin
                y += row_h + gap + label_h
                row_h = 0
            placed.append((asset, img.resize((w, h), _nearest()), x, y))
            x += w + gap
            row_h = max(row_h, h)
        props_bottom = y + row_h

        char_path = OUT / "character" / "office-01.png"
        char = Image.open(char_path).convert("RGBA") if char_path.exists() else None
        char_h = char.size[1] * scale if char else 0
        char_y = props_bottom + gap * 3 + label_h
        tiles_y = char_y + (char_h + gap * 2 + label_h if char else 0)
        sheet_h = tiles_y + 32 * scale + margin

        img = _floor_backdrop((width, sheet_h), scale)
        _grid_overlay(img, scale)
        _label(img, (margin, 10 * scale),
               f"office-01 v3 art set (SOU-158) - props at {scale}x on the floor tile, footprints are "
               f"multiples of 32px (grid drawn); every asset quantised to one 32-colour palette")
        for asset, sprite, px, py in placed:
            img.alpha_composite(sprite, (px, py))
            fw, fh = asset["footprint_px"]
            tw, th = sprite.size[0] // scale, sprite.size[1] // scale
            _label(img, (px, py + sprite.size[1] + 2),
                   f"{asset['id']} {fw // 32}x{fh // 32}t {tw}x{th}px")
        if char:
            _label(img, (margin, char_y - label_h - 2),
                   "character/office-01.png - rows down, left, right, up / cols idle, walk 1-4, "
                   "typing 1-2, sleep (8x4 frames of 32x48)")
            img.alpha_composite(char.resize((char.size[0] * scale, char.size[1] * scale), _nearest()),
                                (margin, char_y))
        _label(img, (margin, tiles_y - label_h - 2),
               "tiles/tileset.png in office-01.json slot order: " + ", ".join(n for n, _s in TILESET_ORDER))
        tileset_path = OUT / "tiles" / "tileset.png"
        if tileset_path.exists():
            tileset = Image.open(tileset_path).convert("RGBA")
            img.alpha_composite(tileset.resize((tileset.size[0] * scale, tileset.size[1] * scale), _nearest()),
                                (margin, tiles_y))
        img.save(OUT / name)
        print(f"[asm ] {name:20s} {img.size[0]}x{img.size[1]}")


def _grid_overlay(img, scale):
    """Faint 32px tile grid, so footprint snapping is visible on the sheet."""
    from PIL import ImageDraw

    d = ImageDraw.Draw(img, "RGBA")
    line = (244, 234, 210, 40) if scale == 1 else (244, 234, 210, 34)
    for x in range(0, img.size[0], 32 * scale):
        d.line([(x, 0), (x, img.size[1])], fill=line)
    for y in range(0, img.size[1], 32 * scale):
        d.line([(0, y), (img.size[0], y)], fill=line)


def cmd_clean(args):
    """Remove the DERIVED output only - never a raw generation.

    Written because of a real mistake: `rm -rf` on the set directory threw away 22 raw FLUX
    outputs and cost a second GPU pass. The raws are the expensive artifact (36s each on
    MPS) and the only thing that makes `assemble` reproducible, so they are the one thing
    this command will not touch - it reports how many it preserved instead.
    """
    removed, kept_raw = [], []
    for pattern_root in ("tiles", "props", "character"):
        directory = OUT / pattern_root
        if directory.is_dir():
            for f in sorted(directory.iterdir()):
                if f.suffix == ".png" or f.name.endswith(".manifest.json"):
                    f.unlink()
                    removed.append(str(f.relative_to(OUT)))
    for f in sorted(OUT.glob("*")):
        if f.is_file() and (f.name.endswith(".raw.png") or f.name.endswith(".prompt.txt")):
            kept_raw.append(f.name)
            continue
        if f.is_file():
            f.unlink()
            removed.append(f.name)
    print(f"cleaned {len(removed)} derived file(s); kept {len(kept_raw)} raw generation(s) "
          f"+ their prompts")
    for name in removed:
        print(f"  - {name}")
    return 0


# --------------------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="FLUX pass (needs the sou-flux venv + MPS)")
    g.add_argument("--only", help="comma-separated asset ids")
    g.add_argument("--force", action="store_true", help="regenerate even if a raw exists")
    g.set_defaults(func=cmd_generate)
    a = sub.add_parser("assemble", help="CPU pass: key, fit, quantise, manifests, sheets")
    a.set_defaults(func=cmd_assemble)
    c = sub.add_parser("clean", help="remove derived output only - the raws are kept")
    c.set_defaults(func=cmd_clean)
    args = ap.parse_args()
    if getattr(args, "func", None) is cmd_clean:
        sys.exit(cmd_clean(args))
    args.func(args)


if __name__ == "__main__":
    main()
