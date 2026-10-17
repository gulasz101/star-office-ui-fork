#!/usr/bin/env python3
"""Corridor cat coats: 7 coats x 8 poses, plus the cartoon fight cloud (SOU-277).

The corridor cats currently share ONE sheet (`frontend/v3/assets/lodge/props/cat_walk.png`,
an orange tabby, 4 walk frames of 32x32). SOU-277 asks for seven coats and eight poses per
coat, so `CorridorCat` (SOU-278) can give every cat its own look and its own behaviour poses.

Layout of `frontend/v3/assets/cats/<coat>.png` - an 8-frame strip, 256x32, RGBA, the same
frame order in every file:

    frame 0-3  walk cycle, facing right (the same stride derivation the shipped lodge cat uses)
    frame 4    sitting
    frame 5    curled up asleep
    frame 6    crouched with the tail up (play / pounce)
    frame 7    arched back, fur up (hiss / fight)

plus `frontend/v3/assets/cats/fight_cloud.png` - 4 frames of 48x32 (192x32), RGBA.

Why it is built this way
------------------------
* **One scale for the whole set.** The reference coat's raw content is fitted to exactly
  `REFERENCE_CONTENT_PX` px of the 32 px frame - the size the shipped lodge cat came out at -
  and every pose of every coat is placed at that SAME scale. Fitting each pose "inside 32x32"
  instead would blow a curled-up sleeping cat up to twice the walking cat's size, and the cat
  would change size as it sat down.
* **Coat coherence by reference edit, not by seven independent generations.** The orange tabby
  poses are generated text-only; the other six coats are REFERENCE EDITS of that coat's own
  keyed pose ("the same cat, the same pose, the same silhouette - change only the coat and the
  pattern"). klein is a unified generate+edit model, so an edit carries the pose over instead of
  re-inventing it (four independent text-only generations returned four different cats, measured
  on the office-01 character sheet 2026-10-16). The reference is always OUR OWN `.keyed.png` -
  never Star-Office or LimeZu art, which is what ADR-061's no-img2img rule protects.
* **The orange tabby's walk base is the SHIPPED lodge raw**, so frames 0-3 of `tabby_orange.png`
  are the pixels already in the game, re-derived - the gate byte-compares them against
  `frontend/v3/assets/lodge/props/cat_walk.png`.
* **The palette is the lodge's 61 colours**, imported from `gen_lodge_art` and quantised into
  every frame, so the cats sit in the same palette family as the room they walk through
  (CLAUDE.md: "one shared palette, one pixel scale and one light direction").

Two passes, so a layout change never costs GPU time (same shape as every other v3 set):

    generate   FLUX.2-klein-4B on MPS -> <coat>_<pose>.raw.png + prompt + seed + mode
    assemble   CPU-only, deterministic from the raws: key -> fit at the set scale -> quantise
               -> strip -> manifests

The edit order matters: the six non-reference coats need the reference coat's keyed poses, so

    generate --only tabby_orange_sit,tabby_orange_sleep,tabby_orange_crouch,tabby_orange_hiss
    assemble --only tabby_orange        # writes tabby_orange_<pose>.keyed.png
    generate                            # the 30 reference edits
    assemble                            # the whole set
    publish

Licence rules (ADR-061): FLUX.2-klein-4B (Apache-2.0) only; no LoRA (collapses to black in
diffusers - ADR-060); text-only prompts, plus reference edits of OUR OWN klein output; nothing
trained on or derived from Star-Office/LimeZu pixels; no marks, no readable text.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_cats_art.py generate [--only a,b] [--force] [--seed-shift N]
    python3 scripts/v3/gen_cats_art.py assemble [--only coat]
    python3 scripts/v3/gen_cats_art.py contact
    python3 scripts/v3/gen_cats_art.py clean
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gen_lodge_art as lodge  # noqa: E402  (the set family's palette and lookup strings)
from gen_lodge_art import (  # noqa: E402
    KEY,
    LOOK,
    NO_TEXT,
    PALETTE,
    PALETTE_HEX,
    PALETTE_RGB,
    STYLE,
    VIEW,
    _colours,
    _label,
    _off_palette,
)
from gen_office01_art import (  # noqa: E402
    _nearest,
    _region_shift,
    content_bottom_row,
    despeckle_outline,
    drop_stray_islands,
    fill_interior_holes,
    key_magenta,
    quantise,
    substantial_bbox,
)

REPO = HERE.parent.parent
SET = "cats"
OUT = REPO / "assets" / "generated" / "v3" / SET
SERVED = REPO / "frontend" / "v3" / "assets" / SET
LODGE_OUT = REPO / "assets" / "generated" / "v3" / "lodge"
LODGE_SERVED = REPO / "frontend" / "v3" / "assets" / "lodge"

MODEL_ID = lodge.MODEL_ID
LORA_ID = lodge.LORA_ID
STEPS = lodge.STEPS
GUIDANCE = lodge.GUIDANCE

FRAME = 32
FRAMES = 8
WALK_FRAMES = 4
CLOUD_FRAME = 48
CLOUD_FRAMES = 4

# The shipping contract this set has to satisfy (SOU-277's own table, restated once here so the
# gate can compare the generator's declaration against it rather than against the shipped files
# alone - a generator and a gate that agree with each other prove nothing about the ticket).
TICKET_SIZE = {"coat_strip_px": (256, 32), "cloud_strip_px": (192, 32),
               "frame_px": (32, 32), "cloud_frame_px": (CLOUD_FRAME, 32),
               "coats": 7, "frames_per_coat": 8, "cloud_frames": 4}

# --------------------------------------------------------------------------------------
# The set scale: ONE scale for every pose of every coat.
#
# Measured, not chosen: the shipped lodge cat_walk raw is 494 px wide and the shipping fit put
# it at 26 px of content inside the 32 px frame (see
# frontend/v3/assets/lodge/props/cat_walk.png.manifest.json -> fit.content_px [494, 381]).
# Keeping that number is what makes "the same pixel scale as today's cat" true rather than
# nearly true, and it is why the orange walk frames come out byte-identical to the shipped
# sheet.
# --------------------------------------------------------------------------------------
REFERENCE_CONTENT_PX = 26


class AssemblyError(RuntimeError):
    """A branch that could not be satisfied. Never printed as a note - the assemble stops."""


# --------------------------------------------------------------------------------------
# poses
# --------------------------------------------------------------------------------------
POSES = [
    {
        "id": "walk",
        "frames": [0, 1, 2, 3],
        "subject": "cat walking to the RIGHT, full body side view in profile, all four legs "
                   "visible mid-stride, its tail up, wide and low",
        "label": "walk 0-3 (the shipped stride derivation)",
        "walk_cycle": True,
    },
    {
        "id": "sit",
        "frames": [4],
        "subject": "cat sitting upright on its haunches facing RIGHT, full body side view in "
                   "profile, its front legs straight down and its hind legs folded under it, its "
                   "tail curled around its front paws",
        "label": "sit",
    },
    {
        "id": "sleep",
        "frames": [5],
        "subject": "cat curled up fast asleep in a tight round ball, full body side view in "
                   "profile, its head tucked down against its body with its eyes closed and its "
                   "tail wrapped round over its nose, low and compact",
        "label": "curl up asleep",
    },
    {
        "id": "crouch",
        "frames": [6],
        "subject": "cat crouched down low on its front paws with its bottom up in the air and "
                   "its tail held straight up, full body side view in profile facing RIGHT, the "
                   "pounce pose of a cat about to pounce",
        "label": "crouch, tail up",
    },
    {
        "id": "hiss",
        "frames": [7],
        "subject": "cat standing with its back arched high, its fur standing on end and its tail "
                   "puffed up and straight, its mouth open hissing, full body side view in "
                   "profile facing RIGHT",
        "label": "arched back, fur up (hiss)",
    },
]
POSE_BY_ID = {p["id"]: p for p in POSES}
FRAME_LABELS = ["walk 0", "walk 1", "walk 2", "walk 3", "sit", "sleep", "crouch", "hiss"]


# --------------------------------------------------------------------------------------
# coats
#
# `noun` goes into a text-only prompt ("a small <noun> <pose>"), `pattern` goes into the edit
# clause of a reference edit ("change ONLY the fur to: <pattern>"). `signature` is what the
# gate measures on the shipped pixels - the ticket names the patterns (black, white, grey,
# calico, tuxedo, siamese colour-point), so these are the ticket's colours, not invented ones.
# --------------------------------------------------------------------------------------
COATS = [
    {
        "id": "tabby_orange",
        "label": "orange tabby",
        "reference": None,
        "walk_shipped": True,
        "noun": "orange tabby cat with darker orange stripes across its body, its tail and its "
                "forehead",
        "pattern": "a warm orange tabby coat with darker orange-brown stripes",
    },
    {
        "id": "black",
        "label": "black",
        "reference": "tabby_orange",
        "noun": "solid black cat",
        "pattern": "a solid black coat, short glossy black fur with a faint smoky grey sheen "
                   "along the top of its back and the tops of its ears",
    },
    {
        "id": "white",
        "label": "white",
        "reference": "tabby_orange",
        "noun": "pure white cat",
        "pattern": "a pure white coat, soft white fur with pale blue-grey shading in the "
                   "shadows and a small pink nose and pink inner ears",
    },
    {
        "id": "grey",
        "label": "grey",
        "reference": "tabby_orange",
        "noun": "grey cat with darker grey stripes",
        "pattern": "a cool mid-grey coat with darker grey tabby stripes across its body, its "
                   "tail and its forehead",
    },
    {
        "id": "calico",
        "label": "calico",
        "reference": "tabby_orange",
        "noun": "calico cat",
        "pattern": "a calico coat: white fur over most of the body with big bold patches of "
                   "orange tabby and black across its back, its head, one ear and its tail",
    },
    {
        "id": "tuxedo",
        "label": "tuxedo",
        "reference": "tabby_orange",
        "noun": "black and white tuxedo cat",
        "pattern": "a black and white tuxedo coat: glossy black body, head, ears, back and tail, "
                   "with a white chest, a white muzzle and chin, and white front paws",
    },
    {
        "id": "siamese",
        "label": "siamese (colour point)",
        "reference": "tabby_orange",
        "noun": "siamese colourpoint cat",
        "pattern": "a siamese colourpoint coat: a smooth pale cream body with NO stripes and NO "
                   "markings at all on the body, the back or the sides, and dark seal-brown "
                   "colour only on the points - a dark mask over the face, dark ears, all four "
                   "paws and a dark tail",
        # The reference pose is an ORANGE TABBY, so "keep the same pattern, change the colour"
        # keeps the stripes - measured 2026-10-17: the first siamese pass came back as a
        # lynx-point (tan tabby stripes down the back), which is not the colour-point the ticket
        # asks for. The removal has to be stated explicitly.
        "edit_extra": "The reference cat is a striped tabby: REMOVE every stripe from its body, "
                      "its back, its sides and its head, and replace them with ONE flat pale "
                      "cream colour - a siamese body has no stripes, no patches and no tabby "
                      "markings anywhere except on the points.",
    },
]
COAT_BY_ID = {c["id"]: c for c in COATS}

# The per-coat colour SIGNATURES live in the gate, not here. One copy only: a second set of
# thresholds in the generator would be a second thing to drift, and the gate is the thing that
# asserts them against the shipped pixels (check_cats_assets.COAT_SIGNATURE). What the generator
# owns is the coat LIST and the pattern wording it generates from.


# --------------------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------------------
def _palette_clause() -> str:
    return "Palette limited to exactly these colours: " + ", ".join(PALETTE_HEX) + ". "


def _tail() -> str:
    return (_palette_clause() + LOOK + VIEW + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game asset.")


def _ratio_clause(w: int, h: int) -> str:
    if w == h:
        word = "The object's bounding box is square. "
    elif w > h:
        word = f"The object's bounding box is wider than it is tall, about {w / h:.1f}:1. "
    else:
        word = f"The object's bounding box is taller than it is wide, about {h / w:.1f}:1. "
    return (f"The object's bounding box must be EXACTLY {w}:{h} in width:height. " + word +
            "Draw the object to fill that rectangle: no empty margin above, below or to the "
            "sides, and do not make it taller or narrower than that proportion. ")


def compose_pose_prompt(asset) -> str:
    """The reference coat's poses: text-only generation, exactly the shipped cat's vocabulary."""
    coat = COAT_BY_ID[asset["coat"]]
    head = (f"pixel art game asset: a small {coat['noun']}, {POSE_BY_ID[asset['pose']]['subject']}. "
            f"Single object, centred, nothing else in frame. {_ratio_clause(*asset['texture_px'])}")
    return head + _tail()


def compose_edit_prompt(asset) -> str:
    """A coat change on OUR OWN keyed pose: keep the cat, change the fur."""
    coat = COAT_BY_ID[asset["coat"]]
    pose = POSE_BY_ID[asset["pose"]]
    head = (
        "pixel art game asset: this is an EDIT of the reference image. Keep EXACTLY the same cat "
        f"sprite: the same pose ({pose['label']}), the same silhouette, the same proportions, the "
        "same size, the same position in the frame, the same lighting from the top-left, the same "
        "1-pixel dark outline and the same flat magenta #FF00FF background. "
        f"Change ONLY the fur colour and pattern to {coat['pattern']}. "
        "Do not change the pose, do not move the cat, do not scale it, do not add or remove a "
        "tail, a leg, an ear or an eye, do not add a ground, a floor, a shadow, a box, a rug or "
        "anything else in the frame. "
        + (coat.get("edit_extra", "") + " " if coat.get("edit_extra") else "")
    )
    return head + _tail()


def compose_prompt(asset) -> str:
    if asset["kind"] == "cloud":
        return compose_cloud_prompt(asset)
    if asset.get("reference"):
        return compose_edit_prompt(asset)
    return compose_pose_prompt(asset)


def compose_cloud_prompt(asset) -> str:
    head = (
        "pixel art game asset: a cartoon dust cloud for a fight between two cats - a round puffy "
        "cloud made of overlapping round puffs, pale grey-white on top and dusty grey underneath, "
        "with TWO SMALL CAT PAWS and TWO CAT PAW PADS clearly sticking OUT of the cloud's outline, "
        "one paw on the lower left and one on the upper right, and TWO four-pointed sparkle "
        "stars: each star is a tiny plus-shaped cross of pale gold #ffea99 pixels with a white "
        "centre pixel, poking out of the puffs so that half of the cross is outside the cloud, "
        "one star on the upper left and one on the upper right, "
        "plus a few short motion strokes at the edges. The paws and the stars must "
        "break the cloud's outline so they are visible outside it. Single object, centred, "
        "nothing else in frame. " + _ratio_clause(*asset["texture_px"])
    )
    return head + _tail()


# --------------------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------------------
def catalogue():
    """Every generatable asset: 7 coats x 5 poses, plus the fight cloud."""
    out = []
    for coat in COATS:
        for pose in POSES:
            asset = {
                "id": f"{coat['id']}_{pose['id']}",
                "kind": "pose",
                "coat": coat["id"],
                "pose": pose["id"],
                "texture_px": [FRAME, FRAME],
                "frames": pose["frames"],
                "walk_cycle": bool(pose.get("walk_cycle")),
            }
            if asset["walk_cycle"] and coat.get("walk_shipped"):
                # The pixels already shipped in the game. Not regenerated: re-deriving them is
                # what makes the byte-identity claim checkable.
                asset["shipped_raw"] = LODGE_OUT / "cat_walk.raw.png"
            elif coat["reference"]:
                asset["reference"] = f"{coat['reference']}_{pose['id']}"
            out.append(asset)
    out.append({"id": "fight_cloud", "kind": "cloud", "texture_px": [CLOUD_FRAME, FRAME],
                "frames": list(range(CLOUD_FRAMES))})
    return out


CATALOGUE = catalogue()
ASSET_BY_ID = {a["id"]: a for a in CATALOGUE}


def _gen_size(width, height, long_side=640, step=16):
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


# --------------------------------------------------------------------------------------
# generation
# --------------------------------------------------------------------------------------
SEED_BASE = 27700


def cmd_generate(args) -> int:
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    OUT.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    todo = [a for a in CATALOGUE if only is None or a["id"] in only or a.get("coat") in only]
    if not todo:
        sys.exit(f"--only {args.only}: no such asset in the catalogue")

    pipe = None
    torch_mod = None
    for index, asset in enumerate(CATALOGUE):
        if asset not in todo:
            continue
        aid = asset["id"]
        raw = OUT / f"{aid}.raw.png"
        if raw.exists() and not args.force:
            print(f"[skip] {aid:22s} raw already on disk")
            continue
        if asset.get("shipped_raw"):
            print(f"[skip] {aid:22s} SHIPPED - the lodge cat_walk.raw.png is the base, no FLUX "
                  f"pass")
            continue
        if pipe is None:
            pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060 field result)
        tw, th = asset["texture_px"]
        gw, gh = _gen_size(tw, th)
        seed = SEED_BASE + index * 7 + args.seed_shift
        prompt = compose_prompt(asset)
        ref = None
        if asset.get("reference") and not args.no_reference:
            ref_path = OUT / f"{asset['reference']}.keyed.png"
            if not ref_path.exists():
                sys.exit(f"{aid} wants a reference edit of {asset['reference']} but "
                         f"{ref_path.name} does not exist - generate and assemble the reference "
                         f"coat first (`assemble --only {COAT_BY_ID[asset['coat']]['reference']}`)")
            ref = Image.open(ref_path).convert("RGB")
            print(f"[gen ] {aid:22s} {gw}x{gh} seed={seed} REFERENCE EDIT of {ref_path.name}",
                  flush=True)
        else:
            print(f"[gen ] {aid:22s} {gw}x{gh} seed={seed}"
                  + (" (reference dropped by --no-reference)" if asset.get("reference") else ""),
                  flush=True)
        image = _generate(frg, pipe, torch_mod, prompt, gw, gh, seed=seed, reference=ref)
        image.save(raw)
        (OUT / f"{aid}.seed.txt").write_text(f"{seed}\n")
        (OUT / f"{aid}.mode.txt").write_text(
            ("reference edit of " + asset["reference"] + ".keyed.png") if ref is not None
            else "text-only generation")
        (OUT / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")
    return 0


def _generate(frg, pipe, torch_mod, prompt, width, height, seed, reference=None):
    """Text-only generation, or a reference edit of OUR OWN previously generated image."""
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
# post-process (CPU only, deterministic)
# --------------------------------------------------------------------------------------
def fit_scaled(content, bbox, scale, size=(FRAME, FRAME), anchor="bottom"):
    """Scale the keyed content by an EXPLICIT scale and anchor it - the set's one scale.

    `gen_office01_art.fit_to_canvas` derives the scale from the canvas, which is right for a
    prop (width is the contract) and for one shape per frame; here a curled-up cat and a
    standing cat share one frame size, so the scale has to be the shared thing and the
    geometry has to give way. Anchored bottom-centre, which is where the renderer expects the
    feet.
    """
    w, h = size
    content = content.crop(bbox)
    cw, ch = content.size
    nw, nh = max(1, int(round(cw * scale))), max(1, int(round(ch * scale)))
    if nw > w or nh > h:
        raise AssemblyError(
            f"content {cw}x{ch} at the set scale {scale:.5f} is {nw}x{nh}, which does not fit "
            f"the {w}x{h} frame - re-roll the pose with a more compact description instead of "
            f"clipping it")
    fitted = content.resize((nw, nh), _nearest())
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    x = (w - nw) // 2
    y = h - nh if anchor == "bottom" else (h - nh) // 2
    canvas.alpha_composite(fitted, (x, y))
    return canvas, {"content_px": [cw, ch], "fitted_px": [nw, nh], "canvas_px": [w, h],
                    "scale": round(scale, 6), "anchor": anchor, "offset": [x, y]}


def clean_frame(canvas):
    """quantise -> drop stray islands -> despeckle the 1px outline -> fill interior holes.

    The exact chain the shipped lodge cat went through, in the same order, so the walk frames
    can be byte-identical to it.
    """
    img = quantise(canvas, palette=PALETTE_RGB)
    img, islands, kept = drop_stray_islands(img)
    img, speckles = despeckle_outline(img)
    img, holes = fill_interior_holes(img, palette=PALETTE_RGB)
    return img, {"stray_islands_dropped_px": islands, "keying_speckle_px_ignored": 0,
                 "disconnected_parts_kept_px": kept, "despeckled_px": speckles,
                 "interior_holes_filled_px": holes}


def build_walk_sheet(base):
    """The shipped 4-frame stride, imported from the lodge generator.

    Reused rather than re-implemented: `tabby_orange.png` frames 0-3 must be the pixels that
    are already in the game, and a second copy of the stride sequence is a second thing to
    drift.
    """
    return lodge.build_walk_sheet(base)


CLOUD_QUADRANTS = ["tl", "tr", "br", "bl"]
CLOUD_DIRS = {"tl": (-1, -1), "tr": (1, -1), "bl": (-1, 1), "br": (1, 1)}

# --------------------------------------------------------------------------------------
# The fight cloud's sparkle stars are DRAWN, not generated.
#
# Measured, not assumed (2026-10-17): three FLUX rolls of the cloud - one asking for "three small
# four-pointed stars", one for "two four-pointed sparkle stars ... plus-shaped cross of pale gold
# #ffea99 with a white centre", one repeating that with a different seed - produced ZERO
# 4-pointed star shapes. A scan of all four frames of every roll for a plus-shaped run of
# #ffea99 with a white centre returned nothing, and at 8x the closest thing was a 2px gold
# speck. klein-4B at 4 steps has no reliable grip on a 5px glyph, and a star is geometry: the
# house rule is that geometry is DECLARED, never generated (ADR-064/065, the drawn material
# tiles). So the stars are stamped from the declared palette at positions derived from each
# frame's own silhouette, and the manifest says so.
#
# The glyph is a 4-point sparkle: gold arms with a white glint at the centre. Nine gold px plus
# one white - legible at 32px and exactly the flat cel look of the rest of the set.
# --------------------------------------------------------------------------------------
STAR_GOLD = "#ffea99"   # palette: gold
STAR_GLINT = "#ffffff"  # palette: snow_light
STAR_GLYPH = [
    (0, -2, STAR_GOLD), (0, -1, STAR_GOLD),
    (-2, 0, STAR_GOLD), (-1, 0, STAR_GOLD), (0, 0, STAR_GLINT), (1, 0, STAR_GOLD),
    (2, 0, STAR_GOLD),
    (0, 1, STAR_GOLD), (0, 2, STAR_GOLD),
]


def _hex_rgb(hexv):
    return tuple(int(hexv[i:i + 2], 16) for i in (1, 3, 5)) + (255,)


def stamp_stars(frame):
    """Put two sparkle stars on a cloud frame, at the silhouette's own top-left and top-right.

    Returns (frame, positions). The positions come from THIS frame's content bbox, so the stars
    follow the boil instead of sitting still while the cloud churns.
    """
    bbox = frame.getchannel("A").getbbox()
    if bbox is None:
        raise AssemblyError("stamp_stars: the cloud frame is empty")
    x0, y0, x1, _y1 = bbox
    spots = [(x0 + 2, y0 + 2), (x1 - 3, y0 + 4)]
    px = frame.load()
    w, h = frame.size
    for cx, cy in spots:
        for dx, dy, hexv in STAR_GLYPH:
            x, y = cx + dx, cy + dy
            if 0 <= x < w and 0 <= y < h:
                px[x, y] = _hex_rgb(hexv)
    return frame, spots


def build_cloud_strip(base):
    """4 frames of the same cloud, boiling: each frame nudges ONE quadrant a pixel outward.

    A fight cloud has no limbs to move, so the animation is the puff itself. Frame 0 is the
    FLUX base; frames 1-3 displace one quadrant of the cloud's own bbox by 1px away from the
    centre, with the pre-shift content kept as an underlay (`_region_shift`), so no frame can
    lose a pixel - the SOU-187 invariant. Declared as a derivation in the manifest; the gate
    measures the per-frame changed-pixel count rather than trusting the description. The two
    sparkle stars are then stamped on each frame (see STAR_GLYPH).
    """
    bbox = base.getchannel("A").getbbox()
    if bbox is None:
        raise AssemblyError("the fight cloud keyed to nothing")
    x0, y0, x1, y1 = bbox
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    quads = {"tl": (x0, y0, cx, cy), "tr": (cx, y0, x1, cy),
             "bl": (x0, cy, cx, y1), "br": (cx, cy, x1, y1)}
    sheet = Image.new("RGBA", (CLOUD_FRAME * CLOUD_FRAMES, FRAME), (0, 0, 0, 0))
    derivation = {"0": "the FLUX base, unchanged, plus the two drawn sparkle stars"}
    stars = {}
    f0, spots0 = stamp_stars(base.copy())
    sheet.alpha_composite(f0, (0, 0))
    stars["0"] = spots0
    for k, name in enumerate(CLOUD_QUADRANTS, start=1):
        dx, dy = CLOUD_DIRS[name]
        frame = _region_shift(base.copy(), quads[name], dx, dy)
        frame, spots = stamp_stars(frame)
        sheet.alpha_composite(frame, (k * CLOUD_FRAME, 0))
        stars[str(k)] = spots
        derivation[str(k)] = (f"the {name} quadrant of the cloud's own bbox shifted {dx:+d},{dy:+d} "
                              f"outward, source kept as underlay (no vacated pixels), plus the two "
                              f"drawn sparkle stars")
    return sheet, derivation, stars


def _manifest(asset_key, *, kind, seed, prompt, source_file, output_file, output_dims, extra):
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
        "asset_key": asset_key,
        "kind": kind,
        "set": SET,
        "view": "side view (profile), facing right",
        "issue": "SOU-277",
    }
    payload.update(extra)
    return payload


def _read_seed(asset):
    f = OUT / f"{asset['id']}.seed.txt"
    if f.exists():
        return int(f.read_text().strip())
    return None


def _read_prompt(asset):
    f = OUT / f"{asset['id']}.prompt.txt"
    return f.read_text().strip() if f.exists() else compose_prompt(asset)


def _read_mode(asset):
    f = OUT / f"{asset['id']}.mode.txt"
    return f.read_text().strip() if f.exists() else "text-only generation"


def _provenance(asset):
    """Where this asset's pixels actually came from - the ship provenance, not the intent.

    The orange tabby's walk base is the SHIPPED lodge raw, so its prompt/seed/mode are read from
    the lodge pack: claiming a SOU-277 seed for pixels that shipped in SOU-198 would be a lie in
    the manifest, and the manifest is the thing a licence review reads.
    """
    if asset.get("shipped_raw"):
        stem = asset["shipped_raw"].name[:-len(".raw.png")]
        seed_f, prompt_f, mode_f = (LODGE_OUT / f"{stem}.{ext}" for ext in
                                    ("seed.txt", "prompt.txt", "mode.txt"))
        return {
            "seed": int(seed_f.read_text().strip()) if seed_f.exists() else None,
            "prompt": prompt_f.read_text().strip() if prompt_f.exists() else "",
            "mode": (mode_f.read_text().strip() if mode_f.exists() else "text-only generation"),
            "raw": str(asset["shipped_raw"].relative_to(REPO)),
            "provenance": "shipped in SOU-198 (assets/generated/v3/lodge/cat_walk.raw.png) - "
                          "re-derived here, not regenerated",
        }
    return {"seed": _read_seed(asset), "prompt": _read_prompt(asset), "mode": _read_mode(asset),
            "raw": f"{asset['id']}.raw.png",
            "provenance": "generated for SOU-277"}


def _key_asset(asset, keyed_store):
    """Key one asset's raw (or take the shipped key) and return the keyed image."""
    raw = asset.get("shipped_raw") or (OUT / f"{asset['id']}.raw.png")
    if not raw.exists():
        raise AssemblyError(f"{asset['id']}: no raw generation on disk ({raw})")
    keyed, key = key_magenta(Image.open(raw))
    keyed.save(OUT / f"{asset['id']}.keyed.png")
    keyed_store[asset["id"]] = keyed
    return keyed, key


def _set_scale(store):
    """content_w of the reference walk pose -> REFERENCE_CONTENT_PX px of content."""
    ref = store["tabby_orange_walk"]
    bbox, _speckle, _parts = substantial_bbox(ref)
    if bbox is None:
        raise AssemblyError("the reference walk pose keyed to nothing")
    cw = bbox[2] - bbox[0]
    return REFERENCE_CONTENT_PX / cw, bbox


def _pose_frame(asset, keyed, scale):
    """One pose of one coat -> its 32x32 frame(s), cleaned, plus the meta the manifest carries."""
    bbox, speckle_px, parts = substantial_bbox(keyed)
    if bbox is None:
        raise AssemblyError(f"{asset['id']}: keyed to nothing")
    canvas, fit = fit_scaled(keyed, bbox, scale)
    frame, clean = clean_frame(canvas)
    meta = {"kind": asset["kind"], "pose": asset["pose"], "coat": asset["coat"],
            "frames": asset["frames"], "fit": fit, "content_px": fit["content_px"],
            "keying_speckle_px_ignored": speckle_px, "substantial_components_kept": parts,
            **clean, "colours": len(_colours(frame))}
    if asset.get("walk_cycle"):
        sheet, derivation = build_walk_sheet(frame)
        meta["derivation"] = derivation
        return sheet, meta
    meta["derivation"] = {"single": "the FLUX base, unchanged"}
    return frame, meta


def build_coat_strip(coat, pose_results):
    """The 8-frame 256x32 strip for one coat, in the ticket's declared frame order."""
    sheet = Image.new("RGBA", (FRAME * FRAMES, FRAME), (0, 0, 0, 0))
    for pose in POSES:
        res = pose_results.get(pose["id"])
        if res is None:
            raise AssemblyError(f"{coat['id']}: pose {pose['id']} was never assembled")
        frames = res["frames"]
        for i, index in enumerate(pose["frames"]):
            if pose.get("walk_cycle"):
                cell = frames.crop((i * FRAME, 0, (i + 1) * FRAME, FRAME))
            else:
                cell = frames
            sheet.alpha_composite(cell, (index * FRAME, 0))
    return sheet


def cmd_assemble(args) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None

    (OUT / "palette.json").write_text(json.dumps({
        "name": "cats-v1",
        "set": SET,
        "colors": len(PALETTE_HEX),
        "named": [{"name": n, "hex": h} for n, h in PALETTE],
        "hex": PALETTE_HEX,
        "why": "The cats are quantised into the LODGE's 61-colour night palette (imported from "
               "gen_lodge_art, not re-declared here), because the cats walk through the lodge: "
               "one palette family, one pixel scale and one light direction across the building "
               "is the coherence rule in CLAUDE.md. The palette carries a cat ramp already "
               "(cat_dark/cat/cat_light = #b06a2a/#d98a3c/#eda95c, cat_cream = #f3d9b0), which "
               "is why all seven coats - including the calico's three-way white/orange/black and "
               "the siamese's cream body with dark points - can be expressed without adding a "
               "colour.",
    }, indent=2) + "\n")

    # ---- key every raw we have ------------------------------------------------------
    store, keys, provenance = {}, {}, {}
    for asset in CATALOGUE:
        if asset["kind"] == "cloud":
            continue
        if only and asset["coat"] not in only and asset["id"] not in only:
            continue
        keyed, key = _key_asset(asset, store)
        keys[asset["id"]] = list(key)
        provenance[asset["id"]] = _provenance(asset)

    if "tabby_orange_walk" not in store:
        raise AssemblyError(
            "the reference coat's walk pose is missing - the set scale is measured from it "
            "(assemble the shipped cat_walk raw first)")

    scale, ref_bbox = _set_scale(store)
    print(f"[asm ] set scale {scale:.5f} ({REFERENCE_CONTENT_PX}px of content in a {FRAME}px "
          f"frame, measured from the shipped lodge cat: {ref_bbox[2] - ref_bbox[0]}px raw)")

    results, coat_pages = {}, {}
    for coat in COATS:
        if only and coat["id"] not in only and f"{coat['id']}_walk" not in only:
            continue
        pose_results = {}
        for pose in POSES:
            aid = f"{coat['id']}_{pose['id']}"
            keyed = store.get(aid)
            if keyed is None:
                print(f"[miss] {aid}: no generation on disk")
                pose_results = None
                break
            frames, meta = _pose_frame(ASSET_BY_ID[aid], keyed, scale)
            meta.update(provenance[aid])
            meta["key_colour"] = keys[aid]
            pose_results[pose["id"]] = {"frames": frames, "meta": meta}
        if not pose_results:
            continue
        sheet = build_coat_strip(coat, pose_results)
        out_path = OUT / "props" / f"{coat['id']}.png"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out_path)
        walk_meta = pose_results["walk"]["meta"]
        meta = {
            "sprite_sheet": {
                "frames": FRAMES, "frame_px": [FRAME, FRAME], "canvas_px": list(sheet.size),
                "order": FRAME_LABELS,
                "facing": "right (the wander mirrors the sheet for left)",
                "ticket": "SOU-277: frames 0-3 walk, 4 sit, 5 curl up asleep, 6 crouch tail up, "
                          "7 arched back / hiss",
            },
            "coat": coat["label"],
            "set_scale": round(scale, 6),
            "set_scale_why": f"one scale for every pose of every coat: the reference coat's raw "
                             f"content is fitted to {REFERENCE_CONTENT_PX}px of the {FRAME}px "
                             f"frame, the size the shipped lodge cat shipped at",
            "poses": {p["id"]: pose_results[p["id"]]["meta"] for p in POSES},
            "generation_mode": walk_meta["mode"],
            "content_bottom_row_per_frame": [
                content_bottom_row(sheet.crop((i * FRAME, 0, (i + 1) * FRAME, FRAME)))
                for i in range(FRAMES)],
            "colours": len(_colours(sheet)),
            "off_palette": _off_palette(sheet),
        }
        manifest = _manifest(coat["id"], kind="sheet", seed=walk_meta["seed"],
                             prompt=walk_meta["prompt"],
                             source_file=f"{walk_meta['raw']} (walk) + per-pose raws (see `poses`)",
                             output_file=f"props/{coat['id']}.png",
                             output_dims=list(sheet.size), extra=meta)
        (OUT / "props" / f"{coat['id']}.png.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        coat_pages[coat["id"]] = sheet
        results[coat["id"]] = meta
        print(f"[asm ] {coat['id']:14s} {sheet.size[0]}x{sheet.size[1]} 8 frames "
              f"bottom-rows={meta['content_bottom_row_per_frame']} "
              f"colours={meta['colours']} off-palette={len(meta['off_palette'])}")

    # ---- the fight cloud ------------------------------------------------------------
    cloud = ASSET_BY_ID["fight_cloud"]
    cloud_raw = OUT / "fight_cloud.raw.png"
    cloud_meta = None
    if cloud_raw.exists():
        keyed, key = key_magenta(Image.open(cloud_raw))
        keyed.save(OUT / "fight_cloud.keyed.png")
        bbox, speckle_px, parts = substantial_bbox(keyed)
        if bbox is None:
            raise AssemblyError("the fight cloud keyed to nothing")
        cw, ch = bbox[2] - bbox[0], bbox[3] - bbox[1]
        cscale = min((CLOUD_FRAME - 4) / cw, (FRAME - 4) / ch)
        canvas, fit = fit_scaled(keyed, bbox, cscale, size=(CLOUD_FRAME, FRAME), anchor="center")
        frame, clean = clean_frame(canvas)
        strip, derivation, star_spots = build_cloud_strip(frame)
        path = OUT / "props" / "fight_cloud.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        strip.save(path)
        frames = [strip.crop((i * CLOUD_FRAME, 0, (i + 1) * CLOUD_FRAME, FRAME))
                  for i in range(CLOUD_FRAMES)]
        changed = []
        for f in frames[1:]:
            n = 0
            a, b = frames[0].convert("RGBA").load(), f.convert("RGBA").load()
            for y in range(FRAME):
                for x in range(CLOUD_FRAME):
                    if a[x, y] != b[x, y]:
                        n += 1
            changed.append(n)
        meta = {
            "sprite_sheet": {"frames": CLOUD_FRAMES, "frame_px": [CLOUD_FRAME, FRAME],
                             "canvas_px": list(strip.size), "order": [f"puff {i}"
                                                                     for i in range(CLOUD_FRAMES)],
                             "play": "at the midpoint between two fighting cats, both hidden"},
            "derivation": derivation,
            "drawn_stars": {
                "glyph": f"4-point sparkle: gold {STAR_GOLD} arms with a {STAR_GLINT} glint at the "
                         f"centre, 9 gold px + 1 white px",
                "positions_per_frame": star_spots,
                "why": "Measured: three FLUX rolls of this cloud produced zero 4-pointed star "
                       "shapes (a plus-shaped scan of every frame for #ffea99 with a white centre "
                       "found nothing). A star is geometry, so it is drawn from the declared "
                       "palette at a position derived from each frame's own silhouette - the same "
                       "rule that draws the material tiles instead of generating them (ADR-064/065).",
            },
            "changed_px_vs_frame0": changed,
            "fit": fit, "key_colour": list(key),
            "content_canvas_opaque_px": sum(
                1 for px in frame.convert("RGBA").get_flattened_data() if px[3]),
            "generation_mode": _read_mode(cloud),
            "colours": len(_colours(strip)),
            "off_palette": _off_palette(strip),
            **clean,
        }
        manifest = _manifest("fight_cloud", kind="sheet", seed=_read_seed(cloud),
                             prompt=_read_prompt(cloud), source_file=cloud_raw.name,
                             output_file="props/fight_cloud.png",
                             output_dims=list(strip.size), extra=meta)
        (path.parent / "fight_cloud.png.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        cloud_meta = meta
        print(f"[asm ] {'fight_cloud':14s} {strip.size[0]}x{strip.size[1]} 4 frames "
              f"changed-px-vs-0={changed} colours={meta['colours']} "
              f"off-palette={len(meta['off_palette'])}")
    else:
        print("[miss] fight_cloud: no generation on disk")

    (OUT / "assemble_summary.json").write_text(json.dumps({
        "set_scale": round(scale, 6),
        "reference_content_px": REFERENCE_CONTENT_PX,
        "coats": sorted(coat_pages),
        "fight_cloud": cloud_meta is not None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2) + "\n")
    print("ASSEMBLE_DONE")
    return 0


# --------------------------------------------------------------------------------------
# contact sheet
# --------------------------------------------------------------------------------------
def _chequer(size, scale, cell=8):
    """A light chequer plate to draw frames on.

    Measured need (2026-10-17): the first contact sheet put the sprites on the house dark navy
    (#14161f), which is the SAME value as a black cat's fur - so the `black` and `tuxedo` rows
    were legible only as faint blue highlights and a reviewer could not read the poses at all.
    The sheet is the artifact that has to show "each coat is recognisable at 1x", so the plate is
    part of the evidence: a light chequer reads a white cat AND a black cat, and it also shows
    the sprite's transparency honestly instead of hiding the alpha against a matching colour.
    """
    img = Image.new("RGBA", size, (214, 216, 224, 255))
    d = ImageDraw.Draw(img)
    step = cell * scale
    for y in range(0, size[1], step):
        for x in range(0, size[0], step):
            if ((x // step) + (y // step)) % 2:
                d.rectangle([x, y, x + step - 1, y + step - 1], fill=(184, 187, 200, 255))
    return img


def _plate(fw, fh, scale):
    plate = _chequer((fw * scale, fh * scale), scale)
    return plate


def build_contact_sheet(scale=4, name="contact_sheet_4x.png"):
    """7 coats x 8 frames at `scale`x nearest-neighbour, plus the fight cloud row."""
    rows = [c["id"] for c in COATS]
    label_w, header, margin, gap = 150, 22, 16, 6
    cell = FRAME * scale
    cloud_w = CLOUD_FRAME * scale
    width = margin + label_w + FRAMES * (cell + gap) + margin
    height = margin + header + len(rows) * (cell + gap) + (cell // 2 + gap) + margin
    img = Image.new("RGBA", (width, height), (20, 22, 31, 255))
    plate = _plate(FRAME, FRAME, scale)
    for i, lab in enumerate(FRAME_LABELS):
        _label(img, (margin + label_w + i * (cell + gap) + cell // 2 - 18, margin - 12), lab,
               scale=1)
    for r, coat in enumerate(rows):
        y = margin + header + r * (cell + gap)
        _label(img, (margin, y + cell // 2 - 8), f"{coat}\n{COAT_BY_ID[coat]['label']}", scale=1)
        path = OUT / "props" / f"{coat}.png"
        if not path.exists():
            continue
        sheet = Image.open(path).convert("RGBA")
        for i in range(FRAMES):
            frame = sheet.crop((i * FRAME, 0, (i + 1) * FRAME, FRAME))
            frame = frame.resize((cell, cell), _nearest())
            x = margin + label_w + i * (cell + gap)
            img.alpha_composite(plate, (x, y))
            img.alpha_composite(frame, (x, y))
    # the cloud, at its own frame width
    y = margin + header + len(rows) * (cell + gap)
    _label(img, (margin, y + cell // 4 - 8), "fight_cloud", scale=1)
    cp = OUT / "props" / "fight_cloud.png"
    if cp.exists():
        strip = Image.open(cp).convert("RGBA")
        for i in range(CLOUD_FRAMES):
            frame = strip.crop((i * CLOUD_FRAME, 0, (i + 1) * CLOUD_FRAME, FRAME))
            frame = frame.resize((cloud_w, cell), _nearest())
            x = margin + label_w + i * (cloud_w + gap)
            img.alpha_composite(_chequer((cloud_w, cell), scale), (x, y))
            img.alpha_composite(frame, (x, y))
    out = OUT / name
    img.save(out)
    print(f"[cs  ] {name} {img.size[0]}x{img.size[1]} ({len(rows)} coats x {FRAMES} frames at "
          f"{scale}x, on a light chequer plate)")
    return out


def cmd_contact(args) -> int:
    build_contact_sheet(4, "contact_sheet_4x.png")
    build_contact_sheet(1, "contact_sheet.png")
    print("CONTACT_DONE")
    return 0


def cmd_clean(args) -> int:
    """Remove DERIVED output only. Raws, prompts, seeds and modes are the expensive artifact."""
    kept = removed = 0
    for path in sorted(OUT.rglob("*")):
        if not path.is_file():
            continue
        if path.name.endswith((".raw.png", ".seed.txt", ".prompt.txt", ".mode.txt")):
            kept += 1
            continue
        if args.keep_keyed and path.name.endswith(".keyed.png"):
            kept += 1
            continue
        path.unlink()
        removed += 1
    print(f"[clean] removed {removed} derived files, kept {kept} (raws/prompts/seeds"
          f"{'/keyed' if args.keep_keyed else ''})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("generate", help="FLUX.2-klein-4B on MPS -> raws (takes ~35 s each)")
    g.add_argument("--only", default="", help="comma-separated asset ids or coat ids")
    g.add_argument("--force", action="store_true")
    g.add_argument("--seed-shift", type=int, default=0)
    g.add_argument("--no-reference", action="store_true",
                   help="drop the reference edit and generate text-only (a measured comparison)")
    g.set_defaults(func=cmd_generate)

    a = sub.add_parser("assemble", help="CPU-only: key -> fit -> quantise -> strips -> manifests")
    a.add_argument("--only", default="", help="comma-separated coat ids")
    a.set_defaults(func=cmd_assemble)

    c = sub.add_parser("contact", help="the 7x8 contact sheets at 1x and 4x")
    c.set_defaults(func=cmd_contact)

    cl = sub.add_parser("clean", help="delete derived output, keep raws/prompts/seeds")
    cl.add_argument("--keep-keyed", action="store_true")
    cl.set_defaults(func=cmd_clean)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
