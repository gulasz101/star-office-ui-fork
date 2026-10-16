#!/usr/bin/env python3
"""Generate the lodge v3 art set: the building shell, the outside, and the two fixes (SOU-198).

Set A ("lodge") is the second v3 set. It is the same pipeline as office-01 (SOU-158) - one
shared palette, top-down 3/4 camera, FLUX.2-klein-4B on MPS, key -> fit -> quantise -> manifest -
because "the same pixel scale and pixelize post-process as office-01" is what lets the two sets
sit in one game. Everything that is genuinely shared (the projection vocabulary, the keying
recipe, the fit and seam measurements) is IMPORTED from `gen_office01_art.py` rather than copied:
a second copy of the keying recipe is a second thing to fix.

Geometry is not invented here either:
  * tile slot order   -> the ticket's own table (16 slots, 32x32, 512x32 strip). There is no
                         lodge map in the tree yet, so the ticket IS the authority; when
                         `frontend/v3/maps/lodge.json` exists the checker cross-checks it.
  * prop texture px   -> the ticket's prop table, restated per asset below with the footprint
                         in tiles and the LIFT the renderer needs (texture_h - footprint_h).
  * the lift          -> `frontend/v3/src/placeholders.js` PROP_STYLE: the renderer draws a prop
                         as `footprint_w x (footprint_h + lift)` and anchors it bottom-centre.
                         `frontend/v3/assets/lodge/index.json` publishes the table so the wiring
                         step does not have to re-derive it (SOU-158's published-size drift).

Two passes, so a layout change never costs GPU time:
  generate   FLUX.2-klein-4B on MPS -> <id>.raw.png + prompt + seed + mode
  assemble   CPU-only, deterministic from the raws: draw -> key -> fit -> quantise -> manifests

Which tiles are GENERATED and which are DRAWN
---------------------------------------------
The material tiles are DRAWN from the declared palette and that is a measured ruling, not a
shortcut (ADR-064/065 "geometry is declared, never generated"; SOU-158 review evidence: the
generated floor returned a high-contrast vertical barcode, contrast 209/255, mean |dx| 38.2 vs
|dy| 2.4 - the stripes ran across the prompt's "horizontal planks"). Four of the lodge material
tiles (snow_a, corridor_floor, stone_floor, metal_grate) are additionally GENERATED and measured
(`explore` below) so the choice is evidenced rather than asserted: the raw stays on disk as the
record of what was rejected and the manifest carries both seam numbers side by side. The props
are all generated - that is where FLUX earns its keep, because a pine, a cat and a fireplace
have no lattice to declare.

Licence rules (ADR-061): FLUX.2-klein-4B (Apache-2.0) only; no LoRA (it collapses to black in
diffusers - ADR-060 field result); text-only prompts (never img2img of anything but OUR OWN
klein output as a reference edit); never trained on Star-Office or LimeZu pixels; no marks, no
readable text, no signage, no brand.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_lodge_art.py generate [--only pine_a,cat_box] [--force]
    python3 scripts/v3/gen_lodge_art.py assemble
    python3 scripts/v3/gen_lodge_art.py clean
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

import gen_office01_art as base  # noqa: E402  (same directory: shared pipeline machinery)

from gen_office01_art import (  # noqa: E402
    KEY,
    NO_TEXT,
    STYLE,
    VIEW,
    _label,
    _nearest,
    base_row_coverage,
    best_tile_crop,
    content_bbox,
    content_bottom_row,
    despeckle_outline,
    drop_stray_islands,
    fill_interior_holes,
    fit_to_canvas,
    key_magenta,
    quantise,
    seam_ratio,
    substantial_bbox,
)

REPO = HERE.parent.parent
SET = "lodge"
OUT = REPO / "assets" / "generated" / "v3" / SET
SERVED = REPO / "frontend" / "v3" / "assets" / SET

MODEL_ID = base.MODEL_ID
LORA_ID = base.LORA_ID
STEPS = base.STEPS
GUIDANCE = base.GUIDANCE

TILE = 32

# --------------------------------------------------------------------------------------
# Palette: ONE palette for the whole set, declared once, quantised into every asset.
#
# The office-01 palette is warm daylight (wood, beige, amber, CRT green). The lodge is a
# night set - honey logs, amber lamplight, snow, deep blue - so it gets its own 61 colours
# rather than being squeezed into the office ramps; the two sets share the pipeline and the
# pixel scale, not the palette. Each material has a ramp (dark -> light) because the light
# comes from the top-left: the dark end is the bottom-right face.
# --------------------------------------------------------------------------------------
PALETTE = [
    # ink / outline
    ("outline", "#14161f"),
    ("ink", "#1f232e"),
    # night sky (windows)
    ("night_900", "#0d1730"),
    ("night_700", "#1b2a4a"),
    ("night_500", "#2b3f68"),
    ("night_300", "#44618a"),
    ("night_100", "#6f8cb4"),
    # slate / steel (metal, factory)
    ("slate_900", "#2a2f3e"),
    ("slate_700", "#3a4152"),
    ("slate_500", "#505a70"),
    ("steel_200", "#9aa4bb"),
    ("steel_100", "#c3cad9"),
    # snow
    ("snow_light", "#ffffff"),
    ("snow", "#eef4ff"),
    ("snow_mid", "#d8e4f2"),
    ("snow_trodden", "#c2cede"),
    ("snow_shadow", "#a8bcd8"),
    ("snow_deep", "#8ba0c0"),
    # honey logs / warm timber
    ("wood_deep", "#3d2817"),
    ("wood_dark", "#5a3d22"),
    ("wood", "#7a5230"),
    ("wood_light", "#b98a52"),
    ("honey", "#cf9d5e"),
    ("honey_light", "#e0b878"),
    ("plank_dark", "#4a3320"),
    ("plank", "#63452a"),
    ("plank_light", "#7d5836"),
    # woven runner / mat
    ("rust_dark", "#7a3b2b"),
    ("rust", "#a3543a"),
    ("rust_light", "#c2704a"),
    ("woven", "#8b4a35"),
    # stone
    ("stone_dark", "#4f5560"),
    ("stone", "#6d747f"),
    ("stone_light", "#929aa6"),
    ("stone_warm", "#7d7a70"),
    # metal
    ("metal_dark", "#23262e"),
    ("metal", "#363b46"),
    ("metal_light", "#565d6b"),
    # warm light: lamps, fire, brass
    ("amber_deep", "#8a5a12"),
    ("amber", "#e8a33d"),
    ("gold", "#ffea99"),
    ("fire", "#ff8c2b"),
    ("brass", "#b98f3c"),
    ("brass_light", "#d8b45c"),
    # LEDs on gear
    ("teal", "#3d6b73"),
    ("teal_light", "#4f8790"),
    ("ice", "#88c0d0"),
    ("led_teal", "#4fd6b0"),
    ("led_orange", "#ff9a3c"),
    # orange tabby cat
    ("cat_dark", "#b06a2a"),
    ("cat", "#d98a3c"),
    ("cat_light", "#eda95c"),
    ("cat_cream", "#f3d9b0"),
    # cardboard
    ("cardboard_dark", "#8a6136"),
    ("cardboard", "#b5824a"),
    ("cardboard_light", "#d0a068"),
    # pine
    ("pine_deep", "#16301f"),
    ("pine_dark", "#1f4029"),
    ("pine", "#2f5c38"),
    ("pine_light", "#437a48"),
    # skin (kept so a future character can reuse the set)
    ("skin", "#e2d3b4"),
]
PALETTE_HEX = [hexv for _name, hexv in PALETTE]
PALETTE_RGB = [tuple(int(hexv[i : i + 2], 16) for i in (1, 3, 5)) for _n, hexv in PALETTE]

LOOK = (
    "Cosy log-cabin lodge at night: honey-brown timber, warm amber lamplight coming from the "
    "top-left, deep blue night shadows, teal-green and orange LED accents. "
)

# --------------------------------------------------------------------------------------
# Tiles. `seam` declares which axes the tile must REPEAT along, and the checker only asserts
# the declared axes: a floor or a wall face is a material and must tile both ways, a runner
# strip is laid along one axis, and a step / mat / drift edge is an object that is deliberately
# not periodic. Measuring an object for seamlessness would be measuring the wrong thing, and
# quietly skipping it would hide a real seam in a material - so the classification is declared
# per slot and printed by both the generator and the gate.
# --------------------------------------------------------------------------------------
TILES = [
    # slot, name, class, seam axes, drawn spec
    ("snow_a", "material", "xy", {"base": "snow_mid", "light": 22, "shadow": 12, "seed": 198}),
    ("snow_b", "material", "xy", {"base": "snow_mid", "light": 16, "shadow": 10, "seed": 199,
                                  "tufts": 3, "pebbles": 2}),
    ("snow_path", "material", "xy", {"base": "snow_mid", "light": 8, "shadow": 10, "seed": 200,
                                     "trodden": "snow_trodden", "feet": 4}),
    ("corridor_floor", "material", "xy", {"planks": ["plank_dark", "plank", "plank_light",
                                                     "plank_dark"], "seam": "wood_deep"}),
    ("corridor_runner", "material", "x", {"runner": True}),
    ("wall_cap", "material", "xy", {"flat": "wood_deep", "edge": "ink"}),
    ("wall_face", "material", "xy", {"logs": True}),
    ("wall_base", "material", "x", {"baseboard": True}),
    ("stone_step", "decal", "none", {"step": True}),
    ("room_plank_a", "material", "xy", {"planks": ["wood_light", "honey", "honey_light",
                                                   "wood_light"], "seam": "wood_dark"}),
    ("room_plank_b", "material", "xy", {"planks": ["wood", "wood_light", "honey", "wood"],
                                        "seam": "wood_deep"}),
    ("stone_floor", "material", "xy", {"flagstones": True}),
    ("metal_grate", "material", "xy", {"grate": True}),
    ("door_mat", "decal", "none", {"mat": True}),
    ("snow_edge", "decal", "none", {"drift": True}),
    ("snow_deep", "material", "xy", {"base": "snow_deep", "light": 10, "shadow": 18, "seed": 201,
                                     "note": "the 'spare' slot, filled with the forest-floor snow "
                                             "the exterior needs rather than left transparent"}),
]

# Which drawn tiles are ALSO generated, as a measured comparison. The generated result is
# recorded in the manifest (seam after crop, seam before crop, colours, off-palette count) and
# the drawn tile ships; if a generated tile ever wins on both seam and palette, that is visible
# in the numbers rather than hidden in a commit message.
EXPLORE = ("snow_a", "corridor_floor", "stone_floor", "metal_grate")

# --------------------------------------------------------------------------------------
# Props. texture_px is the CONTRACT the renderer draws; footprint_px is where it is anchored.
# lift = texture_h - footprint_h (what `placeholders.js` PROP_STYLE must carry for the key).
# --------------------------------------------------------------------------------------
def _prop(pid, subject, texture, footprint, lift, note="", flat=False, sheet=False):
    """One prop. `texture_px` is the EXACT canvas that ships.

    Fit ruling for this set (SOU-198 AC1 says "at exactly the size given"): the lodge props are
    fitted with `mode="inside"`, so the canvas is always exactly the declared box and the object
    is scaled - never stretched - to sit inside it, anchored bottom-centre. office-01 fitted by
    width instead and let the canvas grow: five of its props shipped 4-11px taller than the table
    that had been published for the wiring step, and the SOU-158 review had to reconcile that
    drift by hand. Doing it this way makes the table true by construction; the cost is that a
    silhouette slimmer than its footprint (the lantern post, the wall lamp) is centred inside it
    rather than filling the width, which is stated here, in every manifest and on the ticket.
    """
    return {
        "id": pid,
        "kind": "sheet" if sheet else "prop",
        "subject": subject,
        "texture_px": list(texture),
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        "fit_mode": "inside" if not sheet else "inside",
        "flat": flat,
        "note": note,
    }


PROPS = [
    _prop("pine_a",
          "a snow-laden pine tree: a tall narrow evergreen conifer with tiered needle branches, "
          "each tier piled with a thick layer of white snow, and a short dark brown bare trunk at "
          "the very bottom. The silhouette is about twice as tall as it is wide",
          (64, 96), (64, 32), 64,
          "exterior tree; trunk base centred on the bottom row"),
    _prop("pine_b",
          "a taller snow-laden pine tree with more needle tiers than the previous one, every tier "
          "carrying a thick cap of white snow, short dark brown trunk at the very bottom. The "
          "silhouette is about twice as tall as it is wide",
          (64, 128), (64, 32), 96,
          "tall variant of pine_a"),
    _prop("pine_small",
          "a small young snow-covered pine sapling, a few short needle tiers with snow on them, a "
          "slender dark trunk. Tall and narrow",
          (32, 64), (32, 32), 32),
    _prop("lantern_post",
          "a SHORT garden lantern post: a squat black iron post with a wide angled arm carrying a "
          "warm glowing amber lantern, lit, a small square base at the bottom. A small knee-high "
          "garden light, NOT a tall street lamp and NOT a lamp with a long pole",
          (32, 64), (32, 32), 32),
    _prop("woodpile",
          "a neat stacked pile of split firewood logs seen from above and slightly in front: pale "
          "cut log ends facing the camera in rows, a layer of fresh white snow along the top of "
          "the stack. Wider than it is tall",
          (64, 48), (64, 32), 16),
    _prop("fireplace",
          "a stone fireplace hearth standing against a wall, seen from above and slightly in "
          "front: grey fieldstone surround, a dark arched firebox with a bright orange fire "
          "burning inside it, a stone mantel shelf above with a small stack of firewood on it",
          (64, 96), (64, 32), 64),
    _prop("armchair",
          "a plump orange-red leather armchair seen from above and slightly in front, its seat "
          "cushion and the tops of both armrests visible, a folded plaid blanket draped over one "
          "armrest. Roughly as wide as it is deep",
          (48, 48), (48, 32), 16),
    _prop("wall_lamp",
          "a small brass wall lantern seen from the front: a WIDE flat rectangular lantern box "
          "with warm glowing amber panes and a short brass bracket running down its back, mounted "
          "flat against a wall. As wide as it is tall - NOT a tall post lamp",
          (32, 32), (32, 32), 0,
          "wall decal: mounted on the back wall, no floor footprint"),
    _prop("window_night",
          "a wide log-framed window seen from inside at night: a chunky honey-brown log frame "
          "around a glass pane that shows a snowy pine forest under a deep blue starry night sky. "
          "Wider than it is tall, and the frame fills the whole image",
          (64, 48), (64, 48), 0,
          "wall decal: mounted on the back wall, no floor footprint"),
    _prop("cat_box",
          "an open corrugated cardboard box with an orange tabby cat curled up asleep inside it. "
          "The cat is LARGE and clearly readable inside the box: its striped orange head with "
          "both ears rests on the front edge, one front paw hangs over the rim, a striped tail "
          "curls over the side, and the box flaps are folded outward. The cat fills most of the "
          "box opening. Square overall",
          (64, 64), (64, 32), 32,
          "v2 (SOU-198): the owner's complaint was 'the cat in a box is a bit small, the cat "
          "itself is barely visible' - the cat is now the subject of the silhouette, not a "
          "texture inside a box"),
    _prop("rack_v2",
          "a TALL DOUBLE-WIDE black homelab server rack standing on the floor, seen from above and "
          "slightly in front: two columns of network switches and small servers stacked from the "
          "floor to the top, every unit covered in rows of glowing teal-green and orange LED "
          "lights, thick bundles of cables running down the left side, a dark glass door. Wide, "
          "tall and dominant, about twice as tall as it is wide",
          (64, 128), (64, 32), 96,
          "v2 (SOU-198): the owner asked for the server rack to be 'a little bit more prominent' "
          "- double-wide, taller, and more LEDs than office-01's single-width rack"),
]

CAT_WALK = {
    "id": "cat_walk",
    "kind": "sheet",
    "subject": "a small orange tabby cat with darker orange stripes walking to the RIGHT, full "
               "body side view in profile, all four legs visible mid-stride, its tail up, an "
               "orange tabby cat, wide and low",
    "texture_px": [32, 32],
    "footprint_px": [32, 32],
    "footprint_tiles": [1, 1],
    "lift_px": 0,
    "note": "sprite sheet, 4 frames of 32x32 in a 128x32 strip, cat walking right (the wander "
            "uses the mirrored copy for left)",
    "frames": 4,
}
PROPS.append(CAT_WALK)

BY_ID = {a["id"]: a for a in PROPS}

# --------------------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------------------
RATIO_WORD = {
    (64, 96): "The object's bounding box is about two thirds as wide as it is tall",
    (64, 128): "The object's bounding box is about half as wide as it is tall",
    (32, 64): "The object's bounding box is about half as wide as it is tall",
    (64, 48): "The object's bounding box is wider than it is tall",
    (48, 48): "The object's bounding box is square",
    (32, 32): "The object's bounding box is square",
    (64, 64): "The object's bounding box is square",
    (32, 48): "The object's bounding box is taller than it is wide",
}


def _ratio_clause(asset):
    """The exact aspect, stated numerically AND in words.

    Measured reason (2026-10-16, first lodge pass): a vague ratio word is not a specification.
    `lantern_post` came back as a 32x152 street lamp (declared 32x64, 2.4x too tall) and
    `wall_lamp` as a 32x78 lantern post (declared 32x32) because the words "tall thin iron lamp
    post" describe a street lamp and nothing in the prompt bounded the silhouette. Those two were
    re-rolled with the numeric clause below and the wording changed; the ones that landed inside
    their declared box on the first pass were kept, and each asset's own prompt file records
    which wording produced the shipped pixels.
    """
    tw, th = asset["texture_px"]
    word = RATIO_WORD.get((tw, th), "")
    return (f"The object's bounding box must be EXACTLY {tw}:{th} in width:height"
            + (f" ({word.lower()})" if word else "")
            + ". Draw the object to fill that rectangle: no empty margin above, below or to the "
              "sides, and do not make it taller or narrower than that proportion. ")


def compose_prop_prompt(asset):
    head = (f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in "
            f"frame. {_ratio_clause(asset)}")
    tail = (_palette_clause() + LOOK + VIEW + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game asset.")
    return head + tail


def _palette_clause():
    return "Palette limited to exactly these colours: " + ", ".join(PALETTE_HEX) + ". "


def compose_tile_prompt(name, spec):
    head = (f"seamless tileable pixel art texture: {spec['subject']}. "
            "Orthographic top-down view, flat and evenly lit, the pattern repeats without seams "
            "in every direction, no perspective, no single object, no border. ")
    tail = (_palette_clause() + STYLE + NO_TEXT +
            "Fill the entire frame edge to edge with the texture - no background, no margin, no "
            "transparent area. 32-bit pixel art texture, game tile.")
    return head + tail


TILE_SUBJECTS = {
    "snow_a": "soft fresh snow, a smooth white surface with a few gentle blue-grey dips",
    "snow_b": "soft fresh snow with a couple of small tufts of snow and a few tiny grey pebbles",
    "snow_path": "packed trodden snow, a slightly grey path worn through fresh snow with faint "
                 "footprint marks, the running direction horizontal",
    "corridor_floor": "dark warm brown wooden floorboards running horizontally, narrow planks, "
                      "soft grain",
    "corridor_runner": "a woven red-brown runner rug strip running horizontally, with a braided "
                       "border along its top and bottom edges",
    "wall_cap": "the flat top edge of a dark timber log wall seen from above, plain dark wood",
    "wall_face": "horizontal honey-brown log wall: thick round timber logs stacked in horizontal "
                 "rows, warm honey coloured wood",
    "wall_base": "the bottom of a honey-brown log wall where it meets the floor: a row of logs "
                 "above and a dark shadowed wooden baseboard along the bottom edge",
    "stone_step": "a flat grey stone entrance step, one large worn slab",
    "room_plank_a": "warm honey coloured wooden plank floorboards running horizontally, wide "
                    "planks, cosy warm timber",
    "room_plank_b": "warm honey coloured wooden plank floorboards running horizontally, the same "
                    "layout one shade darker and more saturated",
    "stone_floor": "grey stone flagstone floor with square slabs and thin darker joints",
    "metal_grate": "a dark metal floor grate: a heavy steel grid of square openings with a faint "
                   "warm rust-orange rim light along the top edges of the bars",
    "door_mat": "a woven coir door mat: a rough red-brown woven rectangle with a simple braided "
                "border",
    "snow_edge": "a snow drift piled against a wall: fresh snow banked up, with a darker blue "
                 "shadow band along the very top edge where the wall meets it",
    "snow_deep": "deep cold snow in the forest shade, white snow with more blue-grey shadow and "
                 "a few dark specks",
}


def compose_prop_prompt(asset):
    tw, th = asset["texture_px"]
    head = (f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in "
            f"frame. {RATIO_WORD.get((tw, th), '')}. ")
    tail = (_palette_clause() + LOOK + VIEW + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game asset.")
    return head + tail


def compose_prompt(asset):
    """Dispatch: a synthetic asset dict for the tiles, the asset itself for props/sheets."""
    if asset["kind"] == "tile":
        return compose_tile_prompt(asset["id"], {"subject": TILE_SUBJECTS[asset["id"]]})
    return compose_prop_prompt(asset)


# --------------------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------------------
def catalogue():
    """Every generatable asset: the 16 tiles (with their slot order) plus the props."""
    out = []
    for slot, (name, cls, seam, spec) in enumerate(TILES):
        out.append({"id": name, "kind": "tile", "slot": slot, "tile_class": cls, "seam_axes": seam,
                    "drawn_spec": spec, "texture_px": [TILE, TILE],
                    "explore": name in EXPLORE,
                    "note": spec.get("note", "")})
    out.extend(PROPS)
    return out


def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640 (same as office-01)."""
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


def cmd_generate(args):
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    OUT.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    todo = [a for a in catalogue() if only is None or a["id"] in only]
    if not todo:
        sys.exit(f"--only {args.only}: no such asset in the catalogue")

    pipe = None
    torch_mod = None
    for index, asset in enumerate(catalogue()):
        if asset not in todo:
            continue
        aid = asset["id"]
        raw = OUT / f"{aid}.raw.png"
        if raw.exists() and not args.force:
            print(f"[skip] {aid:14s} raw already on disk")
            continue
        # A material tile is DRAWN by default; the explore set is generated anyway so the drawn
        # choice is evidenced (see the module docstring).
        if asset["kind"] == "tile" and not asset.get("explore") and not args.force_all_tiles:
            print(f"[skip] {aid:14s} DRAWN from the palette - no FLUX pass, see assemble")
            continue
        if pipe is None:
            pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060 field result)
        tw, th = asset["texture_px"]
        gw, gh = _gen_size(tw, th)
        seed = 19800 + index * 7 + args.seed_shift
        prompt = compose_prompt(asset)
        ref = None
        if asset.get("reference") and not args.no_reference:
            ref_path = OUT / f"{asset['reference']}.keyed.png"
            if not ref_path.exists():
                sys.exit(f"{aid} wants a reference edit of {asset['reference']} but "
                         f"{ref_path.name} does not exist - generate and assemble the reference "
                         f"first")
            ref = Image.open(ref_path).convert("RGB")
            print(f"[gen ] {aid:14s} {gw}x{gh} seed={seed} REFERENCE EDIT of {ref_path.name}",
                  flush=True)
        else:
            print(f"[gen ] {aid:14s} {gw}x{gh} seed={seed}"
                  + (" (explore: the drawn tile ships, this is the measured comparison)"
                     if asset.get("explore") else ""), flush=True)
        image = _generate(frg, pipe, torch_mod, prompt, gw, gh, seed=seed, reference=ref)
        image.save(raw)
        (OUT / f"{aid}.seed.txt").write_text(f"{seed}\n")
        (OUT / f"{aid}.mode.txt").write_text(
            ("reference edit of " + asset["reference"] + ".keyed.png") if ref is not None
            else "text-only generation")
        (OUT / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")


def _generate(frg, pipe, torch_mod, prompt, width, height, seed, reference=None):
    """Text-only generation, or a reference edit of OUR OWN previous klein output.

    klein is a unified generate+edit model: the `image` argument is concatenated into the
    transformer input as a conditioning image, which is what carries an identity across poses.
    The reference is always a `.keyed.png` this pipeline produced - never upstream Star-Office or
    LimeZu art, which is what the no-img2img rule protects.
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
# drawn tiles (CPU, deterministic, palette-only)
# --------------------------------------------------------------------------------------
PALETTE_BY_NAME = {n: h for n, h in PALETTE}


def _rgb(name):
    """Palette colour by NAME or by hex - the drawn tiles name their colours, so a typo fails
    loudly here instead of shipping a colour that is not in the declared palette."""
    hexv = PALETTE_BY_NAME.get(name, name)
    if hexv not in PALETTE_HEX:
        raise ValueError(f"{name!r} is not in the declared lodge palette")
    return PALETTE_RGB[PALETTE_HEX.index(hexv)]


def _blank(fill=None):
    return Image.new("RGBA", (TILE, TILE), (0, 0, 0, 0) if fill is None else _rgb(fill) + (255,))


def _speckle(img, hexv, count, rnd, lo=2, hi=29):
    """Scatter 1px marks INSIDE a calm border ring.

    The border ring is what makes the tile seamless by construction: the wrap step is measured
    between the last and first row/column, so a ring of the base colour makes that step zero
    while the interior still carries the texture. This is a design choice, stated here, not a
    coincidence.
    """
    px = img.load()
    colour = _rgb(hexv) + (255,)
    for _ in range(count):
        px[rnd.randrange(lo, hi + 1), rnd.randrange(lo, hi + 1)] = colour


def _snow(spec):
    img = _blank(spec["base"])
    rnd = random.Random(spec["seed"])
    _speckle(img, "snow_light", spec.get("light", 0), rnd)
    _speckle(img, "snow_shadow", spec.get("shadow", 0), rnd)
    px = img.load()
    for _ in range(spec.get("tufts", 0)):
        x, y = rnd.randrange(3, 27), rnd.randrange(3, 27)
        px[x, y] = _rgb("snow_light") + (255,)
        px[x + 1, y] = _rgb("snow_light") + (255,)
        px[x, y + 1] = _rgb("snow_shadow") + (255,)
    for _ in range(spec.get("pebbles", 0)):
        px[rnd.randrange(3, 29), rnd.randrange(3, 29)] = _rgb("stone") + (255,)
    if spec.get("trodden"):
        for y in range(11, 21):
            for x in range(TILE):
                px[x, y] = _rgb(spec["trodden"]) + (255,)
        for i in range(spec.get("feet", 0)):
            x = 4 + i * 8
            y = 12 if i % 2 == 0 else 16
            for dx in (0, 1):
                for dy in (0, 1, 2):
                    px[(x + dx) % TILE, y + dy] = _rgb("snow_shadow") + (255,)
        for x in range(TILE):
            px[x, 11] = _rgb("snow_light") + (255,)
            px[x, 20] = _rgb("snow_shadow") + (255,)
    return img


def _planks(spec):
    """Four 8px planks with a 1px joint and staggered board ends (the office-01 floor ruling)."""
    img = _blank()
    d = ImageDraw.Draw(img)
    seam = _rgb(spec["seam"]) + (255,)
    for i, name in enumerate(spec["planks"]):
        y0 = i * 8
        d.rectangle([0, y0, TILE - 1, y0 + 7], fill=_rgb(name) + (255,))
        d.rectangle([0, y0 + 7, TILE - 1, y0 + 7], fill=seam)
        x = 8 if i % 2 == 0 else 24
        d.rectangle([x, y0, x, y0 + 6], fill=seam)
    return img


def _runner():
    """A woven runner strip: braided edges and a 2px CHECKERBOARD weave body.

    A banded body (alternating horizontal stripes) reads as brick - measured on the first pass:
    an aux vision check called this tile "a red brick wall". A 2x2 checkerboard of two shades
    reads as cloth, which is what a woven runner is. The weave period is 4 and the dash motif's
    is 8, both divisors of the 32px tile, so the pattern meets itself across the wrap - which is
    why a runner reads as continuous instead of showing a join every tile. Declared `seam: x`
    because a runner is laid ALONG the corridor: its two long edges ARE the tile's top and
    bottom, so it is deliberately not periodic in y.
    """
    img = _blank()
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TILE - 1, 1], fill=_rgb("rust_dark") + (255,))
    d.rectangle([0, TILE - 2, TILE - 1, TILE - 1], fill=_rgb("rust_dark") + (255,))
    d.rectangle([0, 2, TILE - 1, 3], fill=_rgb("rust") + (255,))
    d.rectangle([0, TILE - 4, TILE - 1, TILE - 3], fill=_rgb("rust") + (255,))
    for y in range(4, TILE - 4):
        for x in range(TILE):
            # 2x2 checkerboard: (x//2 + y//2) even -> one shade, odd -> the other
            shade = "woven" if ((x // 2) + (y // 2)) % 2 == 0 else "rust"
            d.point((x, y), fill=_rgb(shade) + (255,))
    for x in range(3, TILE, 8):
        for y in range(5, TILE - 5, 4):
            d.rectangle([x, y, x + 1, y + 1], fill=_rgb("rust_light") + (255,))
    return img


def _wall_cap(spec):
    img = _blank(spec["flat"])
    d = ImageDraw.Draw(img)
    d.rectangle([0, TILE - 1, TILE - 1, TILE - 1], fill=_rgb(spec["edge"]) + (255,))
    return img


def _log_rows(img, top, rows):
    """Rounded log rows: a 5-tone vertical profile per 8px log, not a flat band.

    Measured reason for the extra tones - an aux vision check read the first version (a flat
    honey band with a lit top line) as "a wood floor, not a wall face", and it was right to: a
    log seen from the front is a CYLINDER, so its shading runs highlight at the top through the
    body to a shaded underside before the gap. It also had to be pulled darker and warmer than
    the plank floor, which is the same honey family: the wall now measures about (161,113,66)
    against the floor's (180,134,80), so a back wall and the floor under it are distinguishable
    at a glance instead of merging into one warm mass. Still periodic in y (period 8).
    """
    d = ImageDraw.Draw(img)
    profile = ["honey_light", "honey", "wood_light", "wood_light", "wood", "wood", "wood_deep",
               "wood_deep"]
    for i in range(rows):
        y0 = top + i * 8
        for dy, shade in enumerate(profile):
            d.rectangle([0, y0 + dy, TILE - 1, y0 + dy], fill=_rgb(shade) + (255,))
    return img


def _wall_face():
    """Four 8px log rows with a rounded profile (period 8 in y)."""
    return _log_rows(_blank(), 0, 4)


def _wall_base():
    """Two log rows above a shadowed baseboard that meets the floor at the bottom edge.

    Declared `seam: x`: the tile's job is to be the BOTTOM of a wall (logs on top, baseboard
    meeting the floor on the bottom row), so it is deliberately not periodic in y.
    """
    img = _log_rows(_blank(), 0, 2)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 16, TILE - 1, 18], fill=_rgb("wood_deep") + (255,))
    d.rectangle([0, 19, TILE - 1, 28], fill=_rgb("wood_dark") + (255,))
    d.rectangle([0, 29, TILE - 1, TILE - 1], fill=_rgb("ink") + (255,))
    return img


def _flagstones():
    """2x2 slabs of 16px with 1px joints: period 16 divides the 32px tile, so it tiles both ways."""
    img = _blank()
    d = ImageDraw.Draw(img)
    tones = ("stone", "stone_light", "stone_warm", "stone")
    for gy in range(2):
        for gx in range(2):
            x0, y0 = gx * 16, gy * 16
            d.rectangle([x0, y0, x0 + 15, y0 + 15], fill=_rgb(tones[gy * 2 + gx]) + (255,))
    for i in (0, 16):
        d.rectangle([i, 0, i, TILE - 1], fill=_rgb("stone_dark") + (255,))
        d.rectangle([0, i, TILE - 1, i], fill=_rgb("stone_dark") + (255,))
    px = img.load()
    rnd = random.Random(199)
    for _ in range(14):
        px[rnd.randrange(1, 31), rnd.randrange(1, 31)] = _rgb("stone_dark") + (255,)
    return img


def _metal_grate():
    """A steel grid on an 8px period with a warm rust-orange rim on each bar's top edge."""
    img = _blank("metal_dark")
    d = ImageDraw.Draw(img)
    for i in range(0, TILE, 8):
        d.rectangle([0, i, TILE - 1, i + 1], fill=_rgb("metal") + (255,))
        d.rectangle([0, i, TILE - 1, i], fill=_rgb("amber") + (255,))       # warm rim light
        d.rectangle([i, 0, i + 1, TILE - 1], fill=_rgb("metal_light") + (255,))
    return img


def _stone_step():
    """A flat worn slab with a lit top edge and a dark face - an OBJECT, not a material."""
    img = _blank("stone")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TILE - 1, 2], fill=_rgb("stone_light") + (255,))
    d.rectangle([0, 3, TILE - 1, 27], fill=_rgb("stone") + (255,))
    d.rectangle([0, 28, TILE - 1, TILE - 1], fill=_rgb("stone_dark") + (255,))
    d.rectangle([0, TILE - 1, TILE - 1, TILE - 1], fill=_rgb("outline") + (255,))
    px = img.load()
    rnd = random.Random(197)
    for _ in range(10):
        px[rnd.randrange(2, 30), rnd.randrange(4, 26)] = _rgb("stone_dark") + (255,)
    return img


def _door_mat():
    img = _blank("rust_dark")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TILE - 1, TILE - 1], fill=_rgb("rust_dark") + (255,))
    d.rectangle([2, 2, TILE - 3, TILE - 3], fill=_rgb("woven") + (255,))
    d.rectangle([4, 4, TILE - 5, TILE - 5], outline=_rgb("rust_light") + (255,))
    d.rectangle([6, 6, TILE - 7, TILE - 7], fill=_rgb("rust") + (255,))
    d.rectangle([8, 8, TILE - 9, TILE - 9], outline=_rgb("rust_dark") + (255,))
    return img


def _snow_edge():
    """A drift against the wall: the TOP EDGE is the wall line (as the ticket specifies), so this
    tile is deliberately not vertically periodic - it is an edge, and it is declared as one."""
    img = _blank("snow_mid")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TILE - 1, 0], fill=_rgb("outline") + (255,))
    d.rectangle([0, 1, TILE - 1, 4], fill=_rgb("snow_shadow") + (255,))
    d.rectangle([0, 5, TILE - 1, 7], fill=_rgb("snow") + (255,))
    px = img.load()
    rnd = random.Random(196)
    for y in range(8, 24):
        for x in range(TILE):
            if rnd.random() < 0.06:
                px[x, y] = _rgb("snow_light") + (255,)
    for y in range(24, TILE):
        for x in range(TILE):
            if rnd.random() < 0.16:
                px[x, y] = _rgb("snow_shadow") + (255,)
    return img


DRAWN_TILES = {
    "snow_a": _snow,
    "snow_b": _snow,
    "snow_path": _snow,
    "snow_deep": _snow,
    "corridor_floor": _planks,
    "room_plank_a": _planks,
    "room_plank_b": _planks,
    "corridor_runner": lambda spec: _runner(),
    "wall_cap": _wall_cap,
    "wall_face": lambda spec: _wall_face(),
    "wall_base": lambda spec: _wall_base(),
    "stone_step": lambda spec: _stone_step(),
    "stone_floor": lambda spec: _flagstones(),
    "metal_grate": lambda spec: _metal_grate(),
    "door_mat": lambda spec: _door_mat(),
    "snow_edge": lambda spec: _snow_edge(),
}


def draw_tile(name, spec):
    return DRAWN_TILES[name](spec)


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
        "asset_key": asset.get("id"),
        "kind": asset.get("kind"),
        "set": SET,
        "view": "top-down 3/4",
    }
    payload.update(extra)
    return payload


def _read_seed(asset, index):
    f = OUT / f"{asset['id']}.seed.txt"
    return int(f.read_text().strip()) if f.exists() else 19800 + index * 7


def _read_prompt(asset):
    f = OUT / f"{asset['id']}.prompt.txt"
    return f.read_text().strip() if f.exists() else compose_prompt(asset)


def _read_mode(asset):
    f = OUT / f"{asset['id']}.mode.txt"
    return f.read_text().strip() if f.exists() else "text-only generation"


def wrap_vs_max(img):
    """Wrap-around edge step against the LARGEST interior step on the same axis.

    The twin of `check_v3_assets.wrap_vs_max` (same measurement, same 1.05 limit): the generator
    prints it so the number recorded in the manifest is the number the gate asserts. A periodic
    feature landing on the boundary (a plank joint every 8px, a log joint every 8px, a cap edge
    every 32px) is NOT a seam - when the tile repeats, that transition is what happens inside the
    tile too. Comparing against the max interior step rather than the mean is what makes that
    distinction; the mean-based `seam_ratio` is still recorded as context.
    """
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()

    def col_step(a, b):
        return sum(abs(px[a, y][i] - px[b, y][i]) for y in range(h) for i in range(3)) / (h * 3)

    def row_step(a, b):
        return sum(abs(px[x, a][i] - px[x, b][i]) for x in range(w) for i in range(3)) / (w * 3)

    inner_c = [col_step(x, x + 1) for x in range(w - 1)]
    inner_r = [row_step(y, y + 1) for y in range(h - 1)]
    wrap_c, wrap_r = col_step(w - 1, 0), row_step(h - 1, 0)
    return {
        "wrap_vs_max_x": round(wrap_c / max(inner_c), 2) if max(inner_c) else 0.0,
        "wrap_vs_max_y": round(wrap_r / max(inner_r), 2) if max(inner_r) else 0.0,
    }


def _generated_tile(raw):
    """The generated alternative for an `explore` tile, measured exactly like the drawn one."""
    src = Image.open(raw)
    naive = quantise(src.convert("RGB").resize((TILE, TILE), _nearest()).convert("RGBA"),
                     palette=PALETTE_RGB)
    tile_src, crop_meta = best_tile_crop(src, size=TILE)
    quant = quantise(tile_src.convert("RGBA"), palette=PALETTE_RGB)
    colours = {c for _n, c in enumerate(_colours(quant))}
    off = _off_palette(quant)
    return quant, naive, crop_meta, len(colours), off


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
    return [c for c in _colours(img) if tuple(c) not in set(PALETTE_RGB)]


def cmd_assemble(args):
    for sub in ("tiles", "props"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    (OUT / "palette.json").write_text(json.dumps({
        "name": "lodge-v1",
        "set": SET,
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "why": "One palette for the whole lodge set (SOU-198, same invariant as SOU-158 AC3). The "
               "office-01 palette is warm daylight; the lodge is a night set - snow, night sky, "
               "honey logs, amber lamplight, brass, and the teal/orange LED pair on the gear - so "
               "it declares its own 61 colours rather than being squeezed into the office ramps. "
               "Every tile and prop in this set is quantised to exactly these colours by "
               "scripts/v3/gen_lodge_art.py assemble.",
        "light": "top-left (same as office-01): each material ramp runs dark (bottom-right faces) "
                 "to light (top-left faces)",
    }, indent=2) + "\n")

    results = {}
    keyed = {}

    # ---- tiles ---------------------------------------------------------------------
    for asset in catalogue():
        if asset["kind"] != "tile":
            continue
        aid = asset["id"]
        quant = quantise(draw_tile(aid, asset["drawn_spec"]), palette=PALETTE_RGB)
        out_path = OUT / "tiles" / f"{aid}.png"
        quant.save(out_path)
        seam = seam_ratio(quant)
        wrap = wrap_vs_max(quant)
        meta = {
            "slot": asset["slot"],
            "tile_class": asset["tile_class"],
            "seam_axes": asset["seam_axes"],
            "derivation": f"drawn from the declared palette ({aid}), no FLUX pass",
            "seam": seam,
            "wrap_vs_max": wrap,
            "seam_checked_axes": asset["seam_axes"],
            "opaque": not any(px[3] == 0 for px in quant.getdata()),
            "colours": len(_colours(quant)),
            "off_palette": _off_palette(quant),
            "note": asset.get("note", ""),
        }
        raw = OUT / f"{aid}.raw.png"
        if raw.exists():
            gen, naive, crop_meta, n_colours, off = _generated_tile(raw)
            meta["generated_comparison"] = {
                "raw": raw.name,
                "prompt": _read_prompt(asset),
                "seed": _read_seed(asset, asset["slot"]),
                "seam": seam_ratio(gen),
                "wrap_vs_max": wrap_vs_max(gen),
                "seam_before_crop": seam_ratio(naive),
                "crop_offset": crop_meta["crop_offset"],
                "colours": n_colours,
                "off_palette_colours": len(off),
                "why_not_shipped": "generated textures are not seamless and a diffusion model has "
                                   "no declared plank/log direction: the drawn tile is periodic by "
                                   "construction (see ADR-064/065 and the SOU-158 review). The "
                                   "raw is kept as the record of what was rejected.",
            }
        manifest = _manifest(asset, seed=None,
                             prompt="drawn from the declared palette - no FLUX pass (see "
                                    "`derivation`)",
                             source_file="none - drawn, see DRAWN_TILES",
                             output_file=f"tiles/{aid}.png",
                             output_dims=asset["texture_px"], extra=meta)
        (OUT / "tiles" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"kind": "tile", "path": f"tiles/{aid}.png", **meta}
        gc = meta.get("generated_comparison")
        print(f"[asm ] {aid:15s} slot {asset['slot']:>2d} 32x32 DRAWN seam x={seam['ratio_x']:>5} "
              f"y={seam['ratio_y']:>5} wrap/max x={wrap['wrap_vs_max_x']:>5} "
              f"y={wrap['wrap_vs_max_y']:>5} checked={asset['seam_axes']:>4s} "
              f"colours={meta['colours']:>2d}"
              + (f"  [generated alt: wrap/max x={gc['wrap_vs_max']['wrap_vs_max_x']} "
                 f"y={gc['wrap_vs_max']['wrap_vs_max_y']} colours={gc['colours']}]"
                 if gc else ""))

    # ---- props and the walk sheet ---------------------------------------------------
    for asset in PROPS:
        aid = asset["id"]
        raw = OUT / f"{aid}.raw.png"
        if not raw.exists():
            print(f"[miss] {aid}: no raw generation on disk")
            continue
        seed = _read_seed(asset, 0)
        prompt = _read_prompt(asset)
        mode = _read_mode(asset)
        src = Image.open(raw)
        keyed_img, key = key_magenta(src)
        keyed[aid] = keyed_img
        keyed_img.save(OUT / f"{aid}.keyed.png")

        if asset["kind"] == "sheet":
            # Fitted inside the frame MINUS a 3px inset, then centred on the real canvas. Two
            # measured reasons: (1) the first pass fitted the cat to the full 32px width, so its
            # alpha bbox ran 0..32 in all four frames - not clipped (the bbox IS the subject) but
            # it reads as clipped at the frame edge and adjacent frames touch in the strip;
            # (2) the derived stride shifts content by up to 2px, which CLIPS at x=32 if the base
            # already touches the edge (measured: frames 1 and 2 still hit 32/0 with a 2px inset).
            # The inset must therefore be >= the largest leg shift.
            bbox, speckle_px, parts = substantial_bbox(keyed_img)
            tw, th = asset["texture_px"]
            inset = 3  # >= the largest leg shift in CAT_WALK_SEQUENCE (2px), so no derived frame
            inner, fit = fit_to_canvas(keyed_img, (tw - 2 * inset, th - 2 * inset),
                                       anchor="bottom", mode="inside", bbox=bbox)
            canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
            canvas.alpha_composite(inner, ((tw - inner.size[0]) // 2, th - inner.size[1]))
            fit["inset_px"] = inset
            fit["canvas_px"] = [tw, th]
            frame = quantise(canvas, palette=PALETTE_RGB)
            frame, islands, kept = drop_stray_islands(frame)
            frame, speckles = despeckle_outline(frame)
            frame, holes = fill_interior_holes(frame, palette=PALETTE_RGB)
            sheet, derivation = build_walk_sheet(frame)
            out_path = OUT / "props" / f"{aid}.png"
            sheet.save(out_path)
            meta = {
                "derivation": derivation,
                "generation_mode": mode,
                "sprite_sheet": {"frames": 4, "frame_px": [32, 32], "canvas_px": list(sheet.size),
                                 "facing": "right (the wander uses the mirrored copy for left)"},
                "fit": fit,
                "key_colour": list(key),
                "despeckled_px": speckles,
                "interior_holes_filled_px": holes,
                "keying_speckle_px_ignored": speckle_px,
                "substantial_components_kept": parts,
                "stray_islands_dropped_px": islands,
                "disconnected_parts_kept_px": kept,
                "colours": len(_colours(sheet)),
                "content_bottom_row_per_frame": [content_bottom_row(sheet.crop(
                    (i * 32, 0, (i + 1) * 32, 32))) for i in range(4)],
            }
            manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                                 output_file=f"props/{aid}.png",
                                 output_dims=list(sheet.size), extra=meta)
            (OUT / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            results[aid] = {"kind": "sheet", "path": f"props/{aid}.png", **meta}
            print(f"[asm ] {aid:15s} sheet 128x32 (4 frames of 32x32) fit={fit['fitted_px']} by "
                  f"{fit['fitted_by']} despeckled={speckles} holes={holes}")
            continue

        canvas, fit = fit_to_canvas(keyed_img, tuple(asset["texture_px"]), anchor="bottom",
                                    mode=asset.get("fit_mode", "width"))
        quant = quantise(canvas, palette=PALETTE_RGB)
        out_path = OUT / "props" / f"{aid}.png"
        quant.save(out_path)
        meta = {
            "footprint_tiles": asset["footprint_tiles"],
            "footprint_px": asset["footprint_px"],
            "texture_px": fit["canvas_px"],
            "declared_px": asset["texture_px"],
            "lift_px": asset["lift_px"],
            "fit_mode": asset.get("fit_mode", "width"),
            "anchor": "bottom-centre of the footprint (renderer origin 0.5,1)",
            "key_colour": list(key),
            "fit": fit,
            "generation_mode": mode,
            "content_bottom_row": content_bottom_row(quant),
            "base_row_coverage": round(base_row_coverage(quant), 3),
            "colours": len(_colours(quant)),
            "off_palette": _off_palette(quant),
        }
        if fit.get("overhang_px"):
            meta["height_note"] = (
                f"canvas grew {fit['overhang_px']}px taller than the declared "
                f"{asset['texture_px'][1]}px because the silhouette is taller than the placeholder "
                f"box; width is the footprint width exactly, which is what the renderer draws")
        if asset.get("note"):
            meta["note"] = asset["note"]
        manifest = _manifest(asset, seed=seed, prompt=prompt, source_file=raw.name,
                             output_file=f"props/{aid}.png",
                             output_dims=list(quant.size), extra=meta)
        (OUT / "props" / f"{aid}.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"kind": "prop", "path": f"props/{aid}.png", **meta}
        print(f"[asm ] {aid:15s} prop {quant.size[0]}x{quant.size[1]} "
              f"declared {asset['texture_px'][0]}x{asset['texture_px'][1]} "
              f"fit={fit['fitted_px']} by {fit['fitted_by']} base-rows={meta['base_row_coverage']}")

    build_tileset_sheet()
    build_contact_sheets(results)
    print("ASSEMBLE_DONE")


CAT_WALK_SEQUENCE = {
    # A four-frame walk for the wander: the base is a FLUX generation, frames 2-4 are
    # deterministic leg shifts of that same base with an underlay left behind (so no frame can
    # lose alpha - the SOU-187 invariant). It is a stride, not a hand-drawn cycle, and the
    # manifest says so.
    1: [("legs_right", 2, 0), ("legs_left", -1, 0)],
    2: [("legs_left", -2, 0), ("torso", 0, -1)],
    3: [("legs_right", -2, 0), ("legs_left", 1, 0)],
}


def build_walk_sheet(base):
    """4 frames of 32x32 in a 128x32 strip: frame 0 is the FLUX base, 1-3 are derived strides."""
    from gen_office01_art import _derived_frame

    sheet = Image.new("RGBA", (32 * 4, 32), (0, 0, 0, 0))
    sheet.alpha_composite(base, (0, 0))
    derivation = {"0": "the FLUX base (contact), unchanged"}
    for col in (1, 2, 3):
        ops = CAT_WALK_SEQUENCE[col]
        frame = _derived_frame(base, ops)
        frame, _filled = fill_interior_holes(frame, palette=PALETTE_RGB)
        sheet.alpha_composite(frame, (col * 32, 0))
        derivation[str(col)] = ("fore/hind leg halves shifted "
                               + ", ".join(f"{n} {dx:+d},{dy:+d}" for n, dx, dy in ops)
                               + " from the frame's own bbox, source kept as underlay "
                                 "(no vacated pixels)")
    return sheet, derivation


def build_tileset_sheet():
    """16 tiles of 32px in one 512x32 strip, in the ticket's declared slot order."""
    order = [name for name, _cls, _seam, _spec in TILES]
    sheet = Image.new("RGBA", (TILE * len(order), TILE), (0, 0, 0, 0))
    for slot, name in enumerate(order):
        tile = Image.open(OUT / "tiles" / f"{name}.png").convert("RGBA")
        sheet.alpha_composite(tile, (slot * TILE, 0))
    sheet.save(OUT / "tiles" / "tileset.png")
    (OUT / "tiles" / "tileset.png.manifest.json").write_text(json.dumps({
        "model": MODEL_ID, "lora": LORA_ID, "lora_strength": 0.0, "steps": STEPS,
        "guidance": GUIDANCE,
        "prompt": "composed from the drawn tiles in the declared slot order (see `derivation`)",
        "seed": None,
        "source_file": "tiles/<slot>.png per slot",
        "output_file": "tiles/tileset.png",
        "output_dims": [TILE * len(order), TILE],
        "palette_colors": len(PALETTE_HEX), "palette": PALETTE_HEX,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asset_key": "lodge-tileset", "kind": "tileset", "set": SET, "view": "top-down 3/4",
        "tileset_contract": {
            "consumer": "frontend/v3/assets/lodge/tiles.png, wired by the lodge map's tilesets[0]",
            "tile_px": TILE, "columns": len(order), "firstgid": 1,
            "order": order,
            "order_authority": "the SOU-198 ticket table; there is no lodge map in the tree yet, "
                               "so check_v3_assets.py --set lodge cross-checks the map only if it "
                               "exists",
        },
        "derivation": {name: f"tiles/{name}.png verbatim" for name in order},
        "note": "Drop-in for the placeholder sheet: 512x32, 16 slots, firstgid 1.",
    }, indent=2) + "\n")
    print(f"[asm ] tileset         tiles/tileset.png {sheet.size[0]}x{sheet.size[1]} "
          f"({len(order)} slots: {', '.join(order)})")


# --------------------------------------------------------------------------------------
# contact sheets. The ticket asks for tiles tiled 3x3 to show seams, plus the props on snow_a
# and on room_plank_a. Both are produced at 1x and 4x from the same files: the 4x one is where
# fringe, dithering and off-palette pixels would show.
# --------------------------------------------------------------------------------------
def _backdrop(tile_name, size, scale):
    tile_path = OUT / "tiles" / f"{tile_name}.png"
    tile = (Image.open(tile_path).convert("RGBA") if tile_path.exists()
            else Image.new("RGBA", (TILE, TILE), (120, 90, 60, 255)))
    if scale != 1:
        tile = tile.resize((TILE * scale, TILE * scale), _nearest())
    img = Image.new("RGBA", size, (20, 22, 31, 255))
    step = TILE * scale
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            img.alpha_composite(tile, (x, y))
    return img


def _flow(items, width, margin, gap, scale):
    """Greedy wrap of (label, image) pairs; returns (placed, total_height) with rows bottom-aligned.

    `items` are already at final scale.
    """
    x, y, row_h, placed = margin, 0, 0, []
    for label, img in items:
        w, h = img.size
        if x + w > width - margin and x > margin:
            x = margin
            y += row_h + gap * 3
            row_h = 0
        placed.append((label, img, x, y, h))
        x += w + gap
        row_h = max(row_h, h)
    return placed, y + row_h


def build_contact_sheets(results):
    order = [name for name, _cls, _seam, _spec in TILES]
    props = [a for a in PROPS if (OUT / "props" / f"{a['id']}.png").exists()]
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap = 16 * scale, 14 * scale
        header = 26 * scale
        block = TILE * 3 * scale
        per_row = 4 if scale == 1 else 2
        tiles_w = per_row * block + (per_row - 1) * gap + 2 * margin
        width = max(1024, tiles_w, 1024)

        items = []
        for slot, tid in enumerate(order):
            tile = Image.open(OUT / "tiles" / f"{tid}.png").convert("RGBA")
            tiled = Image.new("RGBA", (TILE * 3, TILE * 3))
            for ty in range(3):
                for tx in range(3):
                    tiled.alpha_composite(tile, (tx * TILE, ty * TILE))
            tiled = tiled.resize((block, block), _nearest())
            items.append((f"{slot}: {tid}", tiled))
        tiles_placed, tiles_h = _flow(items, width, margin, gap, scale)

        prop_rows = {}
        for backdrop, label in (("snow_a", "snow_a"), ("room_plank_a", "room_plank_a")):
            items = []
            for asset in props:
                img = Image.open(OUT / "props" / f"{asset['id']}.png").convert("RGBA")
                img = img.resize((img.size[0] * scale, img.size[1] * scale), _nearest())
                items.append((asset["id"], img))
            placed, h = _flow(items, width - 2 * margin - 8 * scale, margin, gap, scale)
            prop_rows[backdrop] = (placed, h)

        # ---- lay the sheet out ----
        y = margin
        y_tiles_label = y
        y_tiles = y + header
        y_props1_label = y_tiles + tiles_h + gap * 4
        y_props1 = y_props1_label + header
        y_props2_label = y_props1 + prop_rows["snow_a"][1] + gap * 4
        y_props2 = y_props2_label + header
        sheet_h = y_props2 + prop_rows["room_plank_a"][1] + margin + header

        img = _backdrop("snow_a", (width, sheet_h), scale)
        base._grid_overlay(img, scale)
        _label(img, (margin, y_tiles_label),
               f"lodge v3 art set (SOU-198) - TILES, each tiled 3x3 to show the seams. "
               f"{len(PALETTE_HEX)}-colour declared palette, 32px tiles.", scale=scale)
        for slot, (label, tile, px, py, h) in enumerate(tiles_placed):
            img.alpha_composite(tile, (px, y_tiles + py))
            _label(img, (px, y_tiles + py + h + 2), label, scale=scale)

        for backdrop, key, label_row in (("snow_a", "snow_a", y_props1_label),
                                         ("room_plank_a", "room_plank_a", y_props2_label)):
            placed, h = prop_rows[backdrop]
            band = _backdrop(backdrop, (width - 2 * margin, h + header), scale)
            base._grid_overlay(band, scale)
            img.alpha_composite(band, (margin, label_row + header - header))
            _label(img, (margin, label_row),
                   f"PROPS at {scale}x on {key} - each drawn at its declared texture px, anchored "
                   f"bottom-centre of its footprint (the square behind them is a 32px tile grid). "
                   f"cat_walk is the 4-frame 128x32 sheet.", scale=scale)
            for pid, prop, px, py, h in placed:
                img.alpha_composite(prop, (margin + px, label_row + header + py))
                declared = "\n".join([f"{pid}"])
                asset = BY_ID[pid]
                if asset["kind"] == "sheet":
                    txt = f"{declared} sheet 4x32x32 = 128x32"
                else:
                    d = asset["texture_px"]
                    got = Image.open(OUT / "props" / f"{pid}.png").size
                    txt = f"{declared} {got[0]}x{got[1]}px fp {asset['footprint_px'][0]}x" \
                          f"{asset['footprint_px'][1]} lift {asset['lift_px']}" \
                          + ("" if list(got) == list(d) else f" (declared {d[0]}x{d[1]})")
                _label(img, (margin + px, label_row + header + py + h + 2), txt, scale=scale)

        img.save(OUT / name)
        print(f"[asm ] {name:20s} {img.size[0]}x{img.size[1]}")


def cmd_clean(args):
    """Remove the DERIVED output only - never a raw generation (the SOU-158 lesson: `rm -rf` on
    the set directory threw away 22 raw FLUX outputs and cost a second GPU pass)."""
    removed, kept_raw = [], []
    for sub in ("tiles", "props", "character"):
        directory = OUT / sub
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
    print(f"cleaned {len(removed)} derived file(s); kept {len(kept_raw)} raw generation(s) + their "
          f"prompts")
    for name in removed:
        print(f"  - {name}")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", help="FLUX pass (needs the sou-flux venv + MPS)")
    g.add_argument("--only", help="comma-separated asset ids")
    g.add_argument("--force", action="store_true", help="regenerate even if a raw exists")
    g.add_argument("--force-all-tiles", action="store_true",
                   help="generate the drawn material tiles too (they are skipped by default)")
    g.add_argument("--seed-shift", type=int, default=0,
                   help="add N to the per-asset seed (recorded in <id>.seed.txt)")
    g.add_argument("--no-reference", action="store_true",
                   help="ignore the asset's `reference` and generate text-only")
    g.set_defaults(func=cmd_generate)
    a = sub.add_parser("assemble", help="CPU pass: draw, key, fit, quantise, manifests, sheets")
    a.set_defaults(func=cmd_assemble)
    c = sub.add_parser("clean", help="remove derived output only - the raws are kept")
    c.set_defaults(func=cmd_clean)
    args = ap.parse_args()
    if getattr(args, "func", None) is cmd_clean:
        sys.exit(cmd_clean(args))
    args.func(args)


if __name__ == "__main__":
    main()
