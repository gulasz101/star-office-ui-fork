#!/usr/bin/env python3
"""Generate the v3 SEASONAL OUTDOOR art sets: autumn first (SOU-274).

This is the third art module on the same pipeline as `gen_office01_art.py` (SOU-158) and
`gen_lodge_art.py` (SOU-198), and it imports the shared machinery from them rather than
copying it: the projection vocabulary (`VIEW`), the style and keying strings (`STYLE`,
`KEY`, `NO_TEXT`), `key_magenta`, `quantise`, `fit_to_canvas`, the island/despeckle/hole
chain and the seam measurements.  A second copy of the keying recipe is a second thing to
fix.

What is genuinely NEW here is the set contract, and it comes from the consumer:

  * `frontend/v3/src/seasons.js` (SOU-273) declares one entry per season with
    `ground: [keyA, keyB]`, `path`, `edge`, `trees: [big, tall, small]`, and `main.js`
    preloads `assets/seasons/<season>/tiles.png` (a four-slot 32px strip) plus
    `assets/seasons/<season>/props/{tree_a,tree_b,tree_small}.png`.  So the strip slot
    order and the three prop names/sizes are a CONTRACT, not a preference:
    `ground_a, ground_b, path, edge` at 128x32, and 64x96 / 64x128 / 32x64.
  * the palette FAMILY is the lodge's (SOU-274: "same palette family and pixel scale as
    the Lodge set. Start from the Lodge palette and add autumn oranges and ochres"), so
    `PALETTE` is literally `gen_lodge_art.PALETTE` plus a declared autumn extension, and
    the gate asserts the superset relation instead of trusting this sentence.

Drawn tiles vs generated props - the same measured ruling as the lodge set
-------------------------------------------------------------------------
`ground_a`, `ground_b`, `path` and `edge` are DRAWN from the declared palette, because
they have to REPEAT across ~100x60 tiles and a diffusion texture is not seamless (SOU-158
measured the generated floor as a high-contrast vertical barcode; SOU-198 kept four raw
generations on disk as the record of what was rejected).  A drawn tile is periodic by
construction, which is what the renderer needs.  The four trees are GENERATED: a crown has
no lattice to declare, which is where FLUX earns its keep.

Two seam claims, both MEASURED, and they are different claims:
  * `wrap_vs_max` (imported, same 1.05 limit the gate uses) - a tile against ITSELF, on
    the axes the slot declares.  `edge` declares `x` only: it is drawn as a one-tile-high
    strip across the building's width, so it must repeat horizontally, and it is
    deliberately not periodic vertically because its top rows ARE the wall shadow.
  * `cross_vs_max` - the SOU-274 wording is that the three ground tiles must tile
    "with themselves and WITH EACH OTHER", and self-periodicity does NOT imply that: two
    tiles can each repeat perfectly and still show a join where A's last column meets B's
    first.  So the generator measures every ordered pair of {ground_a, ground_b, path} in
    both axes and records the numbers, and the gate asserts them.  It is the same
    measurement, taken across a boundary instead of across the wrap.

Licence rules (ADR-061, CLAUDE.md - not negotiable): FLUX.2-klein-4B (Apache-2.0) only, no
LoRA (it black-frames in diffusers - ADR-060), text-only prompts (never img2img of anything
but our own klein output), never trained on Star-Office or LimeZu pixels, no brand marks and
no readable text.  The outside is drawn in DAYLIGHT colours on purpose: the renderer
multiplies it by the season tint, which is what makes it night.

Usage::

    VENV=~/.venvs/sou-flux
    $VENV/bin/python scripts/v3/gen_season_art.py generate --season autumn
    python3 scripts/v3/gen_season_art.py assemble --season autumn
    python3 scripts/v3/gen_season_art.py publish  --season autumn     # into frontend/
    python3 scripts/v3/gen_season_art.py publish  --season autumn --check
    python3 scripts/v3/gen_season_art.py clean    --season autumn     # derived only, raws kept
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import gen_lodge_art as lodge  # noqa: E402  (the palette FAMILY this set extends)
from gen_office01_art import (  # noqa: E402  (shared pipeline machinery)
    KEY,
    NO_TEXT,
    STYLE,
    VIEW,
    _label,
    _nearest,
    base_row_coverage,
    content_bottom_row,
    despeckle_outline,
    drop_stray_islands,
    fill_interior_holes,
    fit_to_canvas,
    key_magenta,
    quantise,
    substantial_bbox,
)

REPO = HERE.parent.parent
TILE = 32

MODEL_ID = lodge.MODEL_ID
LORA_ID = lodge.LORA_ID
STEPS = lodge.STEPS
GUIDANCE = lodge.GUIDANCE

# --------------------------------------------------------------------------------------
# Palette. The FAMILY is the lodge's 61 night colours; the autumn extension is the
# daylight the outside is drawn in (the renderer's tint darkens it to night).
#
# Every addition is at least 12.0 RGB units away from every lodge colour, so nothing here
# is a near-duplicate that the nearest-colour quantiser could never reach.  Measured with
# the distance check in the module docstring's sibling note; the gate reports each
# colour's usage count so a dead entry would be visible rather than inferred.
# --------------------------------------------------------------------------------------
LODGE_PALETTE = list(lodge.PALETTE)

AUTUMN_EXTRA = [
    # earth: the forest floor, warm brown and clearly not the honey timber ramp
    ("earth", "#6b4f2c"),
    ("earth_light", "#8a6a3e"),
    ("earth_pale", "#a98a56"),
    ("earth_dust", "#c4a878"),
    # fallen leaves: orange
    ("leaf_orange_deep", "#8f4a12"),
    ("leaf_orange", "#d4791c"),
    ("leaf_orange_light", "#f7b452"),
    # fallen leaves: red
    ("leaf_scarlet", "#b8371c"),
    ("leaf_red_deep", "#6d1f16"),
    ("leaf_red", "#9e3120"),
    ("leaf_red_light", "#c8552c"),
    # fallen leaves: ochre / gold
    ("leaf_ochre_deep", "#7d6418"),
    ("leaf_ochre", "#a98c28"),
    ("leaf_ochre_light", "#cdb04a"),
    ("leaf_gold", "#e3cb72"),
    # a reddish-brown leaf, between the orange and the red ramps
    ("leaf_russet", "#8a4a1e"),
]


def _hex_to_rgb(hexv: str) -> tuple[int, int, int]:
    return tuple(int(hexv[i : i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def palette_of(season_name: str) -> list[tuple[str, str]]:
    """The whole declared palette: the lodge family first, then the season's extension."""
    return LODGE_PALETTE + list(SEASONS[season_name]["extra"])


def palette_hex(season_name: str) -> list[str]:
    return [hexv for _n, hexv in palette_of(season_name)]


def palette_rgb(season_name: str) -> list[tuple[int, int, int]]:
    return [_hex_to_rgb(h) for h in palette_hex(season_name)]


# --------------------------------------------------------------------------------------
# Tiles: (slot name, class, seam axes, drawn spec).
#
# `seam` declares which axes the tile must REPEAT along and the gate asserts only the
# declared axes.  ground_a / ground_b / path are materials and must repeat both ways (and
# across each other - see `cross_vs_max`).  `edge` is the row of ground that meets the
# building's front wall: it repeats HORIZONTALLY (it is drawn as a strip across the
# building) and is deliberately not vertically periodic, because its top rows are the wall
# shadow.  Declaring that instead of hiding it is the point.
# --------------------------------------------------------------------------------------
TILE_SUBJECTS = {
    "ground_a": "a forest floor in late autumn: bare brown earth underfoot with a scattering of "
                "fallen orange, red and ochre leaves",
    "ground_b": "a forest floor in late autumn under the trees: bare brown earth almost covered "
                "with fallen orange, red and ochre leaves",
    "path": "a trodden earth footpath through an autumn forest: packed bare brown earth worn "
            "through fallen leaves, the running direction horizontal",
    "edge": "the foot of a building wall in autumn: dark shadowed ground along the top edge "
            "where the wall meets it, brown earth with a few fallen leaves below",
}

AUTUMN_TILES = [
    ("ground_a", "material", "xy",
     {"base": "earth", "seed": 2741, "leaves": 15, "patch": "earth_light", "patches": 3,
      "speck": "earth_pale", "specks": 5,
      "leaf_colours": ["leaf_orange", "leaf_red", "leaf_ochre", "leaf_russet"]}),
    ("ground_b", "material", "xy",
     {"base": "earth", "seed": 2742, "leaves": 30, "patch": "earth_light", "patches": 4,
      "speck": "earth_pale", "specks": 6,
      "leaf_colours": ["leaf_orange", "leaf_red", "leaf_ochre", "leaf_scarlet",
                       "leaf_orange_light", "leaf_ochre_light"]}),
    ("path", "material", "xy",
     {"base": "earth", "seed": 2743, "band": "earth_dust", "band_top": "earth_pale",
      "band_bottom": "earth_light", "trodden": "earth_light", "foot": "wood_deep",
      "leaves": 4, "leaf_colours": ["leaf_orange", "leaf_ochre", "leaf_russet"]}),
    ("edge", "decal", "x",
     {"base": "earth", "seed": 2744, "shadow": ["wood_deep", "wood_dark"], "leaves": 6,
      "leaf_colours": ["leaf_orange", "leaf_red", "leaf_ochre"]}),
]

# --------------------------------------------------------------------------------------
# Props.  texture_px is the CONTRACT the renderer draws and, for this set, the EXACT canvas
# that ships (the SOU-198 inside-fit ruling): 64x96 / 64x128 / 32x64, one for one with the
# pines they replace, so no renderer change is needed beyond the season lookup.
# --------------------------------------------------------------------------------------
def _prop(pid, subject, texture, footprint, lift, note=""):
    return {
        "id": pid,
        "kind": "prop",
        "subject": subject,
        "texture_px": list(texture),
        "footprint_px": list(footprint),
        "footprint_tiles": [footprint[0] / TILE, footprint[1] / TILE],
        "lift_px": lift,
        "fit_mode": "inside",
        "note": note,
    }


AUTUMN_PROPS = [
    _prop("tree_a",
          "a deciduous tree in autumn: a broad rounded crown whose leaves are a MIX of three "
          "colours in roughly equal parts - deep scarlet red, bright orange, and golden "
          "ochre-yellow leaves - with a few small gaps between the leaf clusters showing short "
          "bare dark twigs, and a SHORT dark brown bare trunk at the very bottom. The trunk is "
          "about a tenth of the object's width and is centred under the crown. The silhouette is "
          "about two thirds as wide as it is tall",
          (64, 96), (64, 32), 64,
          "exterior tree; replaces pine_a one for one; trunk base centred on the bottom row"),
    _prop("tree_b",
          "a tall deciduous tree in autumn: a large dense rounded crown whose leaves are a MIX of "
          "three colours in roughly equal parts - deep scarlet red, bright orange, and golden "
          "ochre-yellow leaves - deeper and taller than a young tree's, with a few small gaps "
          "between the leaf clusters showing short bare dark twigs, and a SHORT dark brown bare "
          "trunk at the very bottom. The trunk is about a tenth of the object's width and is "
          "centred under the crown. The silhouette is about half as wide as it is tall",
          (64, 128), (64, 32), 96,
          "tall variant of tree_a; replaces pine_b one for one"),
    _prop("tree_small",
          "a small young deciduous sapling in autumn: a small rounded crown whose leaves are a MIX "
          "of three colours - deep scarlet red, bright orange, and golden ochre-yellow leaves - on "
          "a slender bare dark brown trunk that is clearly visible below the crown. The trunk is "
          "about a fifth of the object's width and is centred. Tall and narrow",
          (32, 64), (32, 32), 32,
          "replaces pine_small one for one"),
]

# --------------------------------------------------------------------------------------
# Seasons.  One entry per season; SOU-275 (spring) and SOU-276 (summer) add one each and
# need no change to the machinery below.
# --------------------------------------------------------------------------------------
SEASONS = {
    "autumn": {
        "name": "autumn",
        "label": "autumn",
        "seed_base": 27400,
        "extra": AUTUMN_EXTRA,
        "tiles": AUTUMN_TILES,
        "props": AUTUMN_PROPS,
        "look": (
            "Late-autumn deciduous forest in DAYLIGHT: warm brown bare earth, a thick litter of "
            "fallen orange, red and ochre leaves, dark bare branches, soft sunlight coming from "
            "the top-left. "
        ),
        "note": "SOU-274 ticket 2 of 4 of the v2.1.0 seasons initiative. The renderer multiplies "
                "this set by the season tint, so it is drawn in daylight colours on purpose.",
    },
}

# --------------------------------------------------------------------------------------
# prompts
# --------------------------------------------------------------------------------------
RATIO_WORD = {
    (64, 96): "about two thirds as wide as it is tall",
    (64, 128): "about half as wide as it is tall",
    (32, 64): "about half as wide as it is tall",
}


def _ratio_clause(asset) -> str:
    """The exact aspect, stated numerically AND in words (the SOU-198 measured ruling).

    A vague ratio word is not a specification: the first lodge pass returned a 32x152 street
    lamp against a declared 32x64 because nothing in the prompt bounded the silhouette.  The
    numeric form is what makes the model draw to the declared rectangle.
    """
    tw, th = asset["texture_px"]
    word = RATIO_WORD.get((tw, th), "")
    return (f"The object's bounding box must be EXACTLY {tw}:{th} in width:height"
            + (f" ({word})" if word else "")
            + ". Draw the object to fill that rectangle: no empty margin above, below or to the "
              "sides, and do not make it taller or narrower than that proportion. ")


def _palette_clause(season_name: str) -> str:
    return ("Palette limited to exactly these colours: "
            + ", ".join(palette_hex(season_name)) + ". ")


def compose_prop_prompt(season_name: str, asset) -> str:
    season = SEASONS[season_name]
    head = (f"pixel art game asset: {asset['subject']}. Single object, centred, nothing else in "
            f"frame. {_ratio_clause(asset)}")
    tail = (_palette_clause(season_name) + season["look"] + VIEW + STYLE + KEY + NO_TEXT +
            "Plain flat magenta #FF00FF background filling the whole frame. 32-bit pixel art, "
            "game asset.")
    return head + tail


def compose_tile_prompt(season_name: str, name: str) -> str:
    season = SEASONS[season_name]
    head = (f"seamless tileable pixel art texture: {TILE_SUBJECTS[name]}. "
            "Orthographic top-down view, flat and evenly lit, the pattern repeats without seams "
            "in every direction, no perspective, no single object, no border. ")
    tail = (_palette_clause(season_name) + season["look"] + STYLE + NO_TEXT +
            "Fill the entire frame edge to edge with the texture - no background, no margin, no "
            "transparent area. 32-bit pixel art texture, game tile.")
    return head + tail


def catalogue(season_name: str):
    """Every generatable asset: the four tiles (with their slot order) plus the props."""
    out = []
    for slot, (name, cls, seam, spec) in enumerate(SEASONS[season_name]["tiles"]):
        out.append({"id": name, "kind": "tile", "slot": slot, "tile_class": cls,
                    "seam_axes": seam, "drawn_spec": spec, "texture_px": [TILE, TILE]})
    out.extend(SEASONS[season_name]["props"])
    return out


def out_dir(season_name: str) -> Path:
    return REPO / "assets" / "generated" / "v3" / "seasons" / season_name


def served_dir(season_name: str) -> Path:
    return REPO / "frontend" / "v3" / "assets" / "seasons" / season_name


# --------------------------------------------------------------------------------------
# generation (FLUX on MPS)
# --------------------------------------------------------------------------------------
def _gen_size(width, height, long_side=640, step=16):
    """A generation canvas at the TARGET aspect ratio, long side ~640 (same as office-01).

    Not square and not 1024: the v2 pipeline measured that a 1024 prop run drove the M1 Max
    to ~100 MB free and stalled its VAE decode, and a square canvas teaches the model to
    centre a square silhouette - the opposite of what a 64x128 tree needs.
    """
    scale = long_side / max(width, height)
    w = max(step, int(round(width * scale / step)) * step)
    h = max(step, int(round(height * scale / step)) * step)
    return w, h


def cmd_generate(args) -> int:
    import importlib.util

    import torch  # noqa: F401  (imported to fail loudly if run outside the venv)

    season_name = args.season
    spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
    frg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frg)

    OUT = out_dir(season_name)
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(args.only.split(",")) if args.only else None
    todo = [a for a in catalogue(season_name) if only is None or a["id"] in only]
    if not todo:
        sys.exit(f"--only {args.only}: no such asset in the {season_name} catalogue")

    pipe = None
    torch_mod = None
    for index, asset in enumerate(catalogue(season_name)):
        if asset not in todo:
            continue
        aid = asset["id"]
        raw = OUT / f"{aid}.raw.png"
        if raw.exists() and not args.force:
            print(f"[skip] {aid:14s} raw already on disk")
            continue
        if asset["kind"] == "tile" and not args.force_all_tiles:
            print(f"[skip] {aid:14s} DRAWN from the palette - no FLUX pass, see assemble")
            continue
        if pipe is None:
            pipe, torch_mod = frg.load_pipe()  # LoRA off: it blacks out (ADR-060 field result)
        tw, th = asset["texture_px"]
        gw, gh = _gen_size(tw, th)
        seed = SEASONS[season_name]["seed_base"] + index * 7 + args.seed_shift
        prompt = (compose_tile_prompt(season_name, aid) if asset["kind"] == "tile"
                  else compose_prop_prompt(season_name, asset))
        print(f"[gen ] {aid:14s} {gw}x{gh} seed={seed}"
              + (" (explore: the drawn tile ships, this is the measured comparison)"
                 if asset["kind"] == "tile" else ""), flush=True)
        image = frg.generate(pipe, torch_mod, prompt, gw, gh, seed=seed,
                             steps=STEPS, guidance=GUIDANCE)
        image.save(raw)
        (OUT / f"{aid}.seed.txt").write_text(f"{seed}\n")
        (OUT / f"{aid}.mode.txt").write_text("text-only generation")
        (OUT / f"{aid}.prompt.txt").write_text(prompt + "\n")
    print("GENERATE_DONE")
    return 0


# --------------------------------------------------------------------------------------
# drawn tiles (CPU, deterministic, palette-only)
# --------------------------------------------------------------------------------------
def _rgb(season_name: str, name: str) -> tuple[int, int, int]:
    """Palette colour by NAME - a typo fails loudly here instead of shipping a stray colour."""
    by_name = dict(palette_of(season_name))
    if name not in by_name:
        raise ValueError(f"{name!r} is not in the declared {season_name} palette")
    return _hex_to_rgb(by_name[name])


def _blank(season_name: str, fill: str) -> Image.Image:
    return Image.new("RGBA", (TILE, TILE), _rgb(season_name, fill) + (255,))


def _put(px, x, y, colour) -> None:
    """Plot with the coordinates taken MODULO the tile.

    This is what makes the drawn tiles periodic by construction rather than by a calm
    border ring: a leaf whose second pixel falls off the right edge reappears on the left,
    so the feature that straddles the wrap is CONTINUOUS there.  The wrap step is then the
    same magnitude as an interior step, which is exactly what `wrap_vs_max` measures.
    """
    px[x % TILE, y % TILE] = colour


# The scatter keeps to the interior so columns 0/31 and rows 0/31 stay on the base colour.
# Measured reason: with leaves free to land anywhere, ground_b's wrap step came back at 0.8x
# and ground_b|ground_a at 0.96x of the largest interior step - under the 1.05 limit but
# needlessly close to it, because a leaf sitting on the boundary makes the wrap column/row
# unrepresentative.  The ring is 1px of base earth around a leaf litter that covers 2-3% of
# the tile: invisible, and it takes the wrap ratio to 0.0 without touching the periodicity
# (the wrap is still exact - it is the same modulo plot).
SCATTER_LO, SCATTER_HI = 1, 29


def _leaf(px, x, y, colour, rnd) -> None:
    """A 1- or 2-pixel fallen leaf.  A leaf at a leaf's own scale is 1-2 px at 32px/tile."""
    _put(px, x, y, colour)
    if rnd.random() < 0.45:
        _put(px, x + 1, y, colour)
    elif rnd.random() < 0.3:
        _put(px, x, y + 1, colour)


def _scatter_xy(rnd) -> tuple[int, int]:
    return rnd.randrange(SCATTER_LO, SCATTER_HI + 1), rnd.randrange(SCATTER_LO, SCATTER_HI + 1)


def _floor(season_name: str, spec: dict) -> Image.Image:
    """Bare earth with a scatter of fallen leaves - the forest floor."""
    img = _blank(season_name, spec["base"])
    rnd = random.Random(spec["seed"])
    px = img.load()
    for _ in range(spec.get("patches", 0)):
        x, y = rnd.randrange(SCATTER_LO, 24), rnd.randrange(SCATTER_LO, 29)
        w, h = rnd.choice((3, 4, 5)), rnd.choice((2, 3))
        for dy in range(h):
            for dx in range(w):
                if rnd.random() < 0.65:
                    _put(px, x + dx, y + dy, _rgb(season_name, spec["patch"]) + (255,))
    for _ in range(spec.get("specks", 0)):
        sx, sy = _scatter_xy(rnd)
        _put(px, sx, sy, _rgb(season_name, spec["speck"]) + (255,))
    for _ in range(spec.get("leaves", 0)):
        lx, ly = _scatter_xy(rnd)
        _leaf(px, lx, ly, _rgb(season_name, rnd.choice(spec["leaf_colours"])) + (255,), rnd)
    return img


def _path(season_name: str, spec: dict) -> Image.Image:
    """A trodden earth band across the middle, with footmarks and a few stray leaves.

    The band is uniform in x and sits at fixed rows, so the tile repeats in BOTH axes: the
    renderer draws the path as a 2-wide, N-tall strip, and a horizontal band repeated
    vertically is what reads as one continuous path.  The footmarks repeat on an 8px period
    and the leaves wrap, so nothing lands on a boundary as a discontinuity.
    """
    img = _blank(season_name, spec["base"])
    px = img.load()
    d = ImageDraw.Draw(img)
    band = (11, 20)
    d.rectangle([0, band[0], TILE - 1, band[1]], fill=_rgb(season_name, spec["band"]) + (255,))
    d.rectangle([0, band[0], TILE - 1, band[0]], fill=_rgb(season_name, spec["band_top"]) + (255,))
    d.rectangle([0, band[1], TILE - 1, band[1]],
                fill=_rgb(season_name, spec["band_bottom"]) + (255,))
    rnd = random.Random(spec["seed"])
    for i in range(4):
        x = 3 + i * 8
        y = 13 if i % 2 == 0 else 16
        for dx in (0, 1):
            for dy in (0, 1, 2):
                _put(px, x + dx, y + dy, _rgb(season_name, spec["foot"]) + (255,))
    for _ in range(spec.get("leaves", 0)):
        lx, ly = _scatter_xy(rnd)
        _leaf(px, lx, ly, _rgb(season_name, rnd.choice(spec["leaf_colours"])) + (255,), rnd)
    return img


def _edge(season_name: str, spec: dict) -> Image.Image:
    """The row of ground where it meets the building's front wall.

    The ticket says "a darker strip along the top", and that is what it is: the wall casts a
    shadow onto the first rows of ground.  Drawn as a one-tile-high strip across the whole
    building width, so it must repeat in x; the top rows are the shadow, so it is NOT
    periodic in y and does not pretend to be.
    """
    img = _blank(season_name, spec["base"])
    d = ImageDraw.Draw(img)
    px = img.load()
    d.rectangle([0, 0, TILE - 1, 0], fill=_rgb(season_name, spec["shadow"][0]) + (255,))
    d.rectangle([0, 1, TILE - 1, 2], fill=_rgb(season_name, spec["shadow"][1]) + (255,))
    d.rectangle([0, 3, TILE - 1, 4], fill=_rgb(season_name, "earth_light") + (255,))
    rnd = random.Random(spec["seed"])
    for _ in range(spec.get("leaves", 0)):
        _leaf(px, rnd.randrange(SCATTER_LO, SCATTER_HI + 1), 5 + rnd.randrange(4),
              _rgb(season_name, rnd.choice(spec["leaf_colours"])) + (255,), rnd)
    for _ in range(6):
        _put(px, rnd.randrange(SCATTER_LO, SCATTER_HI + 1), 10 + rnd.randrange(19),
             _rgb(season_name, "earth_pale") + (255,))
    return img


DRAWN_TILES = {
    "ground_a": _floor,
    "ground_b": _floor,
    "path": _path,
    "edge": _edge,
}


def draw_tile(season_name: str, name: str, spec: dict) -> Image.Image:
    return DRAWN_TILES[name](season_name, spec)


# --------------------------------------------------------------------------------------
# seam measurements
# --------------------------------------------------------------------------------------
def wrap_vs_max(img) -> dict:
    """Wrap-around edge step against the LARGEST interior step on the same axis.

    The twin of `check_v3_assets.wrap_vs_max` (same measurement, same 1.05 limit), imported
    behaviourally by being the same arithmetic: a periodic feature landing on the boundary
    (a leaf that straddles the wrap) is NOT a seam, because the same transition happens
    inside the tile.  Comparing against the max interior step rather than the mean is what
    makes that distinction; the mean-based ratio is the wrong question and is not asserted.
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


def cross_vs_max(a, b) -> dict:
    """The same measurement taken ACROSS a tile boundary instead of across the wrap.

    Self-periodicity does not imply mutual tileability: A can repeat perfectly and B can
    repeat perfectly while A's last column still meets B's first column with a step no
    interior step explains.  That is a visible join in the renderer, and the SOU-274 ticket
    asks for "with themselves and with each other" - so it is measured, both axes.
    """
    a, b = a.convert("RGB"), b.convert("RGB")
    w, h = a.size
    pa, pb = a.load(), b.load()

    def axis(horizontal: bool) -> tuple[float, float]:
        if horizontal:
            step = (sum(abs(pa[w - 1, y][i] - pb[0, y][i]) for y in range(h) for i in range(3))
                    / (h * 3))
            inner_a = [sum(abs(pa[x, y][i] - pa[x + 1, y][i]) for y in range(h)
                           for i in range(3)) / (h * 3) for x in range(w - 1)]
            inner_b = [sum(abs(pb[x, y][i] - pb[x + 1, y][i]) for y in range(h)
                           for i in range(3)) / (h * 3) for x in range(w - 1)]
        else:
            step = (sum(abs(pa[x, h - 1][i] - pb[x, 0][i]) for x in range(w) for i in range(3))
                    / (w * 3))
            inner_a = [sum(abs(pa[x, y][i] - pa[x, y + 1][i]) for x in range(w)
                           for i in range(3)) / (w * 3) for y in range(h - 1)]
            inner_b = [sum(abs(pb[x, y][i] - pb[x, y + 1][i]) for x in range(w)
                           for i in range(3)) / (w * 3) for y in range(h - 1)]
        worst = max(max(inner_a), max(inner_b))
        return step, worst

    sx, wx = axis(True)
    sy, wy = axis(False)
    return {
        "step_x": round(sx, 2), "worst_inner_x": round(wx, 2),
        "ratio_x": round(sx / wx, 2) if wx else 0.0,
        "step_y": round(sy, 2), "worst_inner_y": round(wy, 2),
        "ratio_y": round(sy / wy, 2) if wy else 0.0,
    }


# --------------------------------------------------------------------------------------
# assemble
# --------------------------------------------------------------------------------------
def cross_seam_kind(a: str, b: str) -> str:
    """What a measurement ACROSS the A|B boundary MEANS, so the gate asserts the right thing.

    Self-periodicity does not imply mutual tileability, so the boundary is measured.  But not
    every boundary is a seam, and pretending otherwise would make the gate assert a content
    feature:

    "interleave"  both are general ground.  The renderer mixes ground_a and ground_b freely
                  across ~100x60 cells, so this join must be invisible on BOTH axes.
    "self"        a material against itself - the wrap measured again across an explicit
                  boundary, and the 2-tile-wide path strip's own internal join.
    "path-edge"   the trodden band spans the tile's full width ON PURPOSE: a two-tile-wide
                  path strip is one continuous path only if every column of the path tile is
                  path.  So the horizontal join between the path and the ground IS the edge of
                  the path (measured ~2.2x, a brightness step of 100 vs 77 over the band rows)
                  - a content feature, not a grid - and it is reported rather than asserted.
                  The VERTICAL join is asserted, because that one would be a grid line across
                  the ground.
    """
    if a == b:
        return "self"
    if a.startswith("ground") and b.startswith("ground"):
        return "interleave"
    return "path-edge"


def cross_seam_axes(a: str, b: str) -> str:
    """The axes whose cross join the gate asserts for this ordered pair."""
    return "y" if cross_seam_kind(a, b) == "path-edge" else "xy"


def _manifest(season_name: str, asset, *, seed, prompt, source_file, output_file,
              output_dims, extra) -> dict:
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
        "palette_colors": len(palette_hex(season_name)),
        "palette": palette_hex(season_name),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "asset_key": asset.get("id"),
        "kind": asset.get("kind"),
        "set": season_name,
        "season": season_name,
        "view": "top-down 3/4",
    }
    payload.update(extra)
    return payload


def _read_seed(season_name: str, asset, index: int) -> int:
    f = out_dir(season_name) / f"{asset['id']}.seed.txt"
    return int(f.read_text().strip()) if f.exists() else \
        SEASONS[season_name]["seed_base"] + index * 7


def _read_prompt(season_name: str, asset) -> str:
    f = out_dir(season_name) / f"{asset['id']}.prompt.txt"
    if f.exists():
        return f.read_text().strip()
    return (compose_tile_prompt(season_name, asset["id"]) if asset["kind"] == "tile"
            else compose_prop_prompt(season_name, asset))


def _colours(img) -> list[tuple[int, int, int]]:
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


def _off_palette(season_name: str, img) -> list[tuple[int, int, int]]:
    allowed = set(palette_rgb(season_name))
    return [c for c in _colours(img) if tuple(c) not in allowed]


def build_strip(season_name: str) -> Image.Image:
    """The four tiles in one 128x32 strip, in the declared slot order.

    This IS the delivered file (`seasons/<season>/tiles.png`): the loader in `main.js`
    preloads it as a single image and slices four 32px slots out of it, so the slot order
    here is the contract SOU-273's `seasons.js` names.
    """
    order = [name for name, _c, _s, _spec in SEASONS[season_name]["tiles"]]
    sheet = Image.new("RGBA", (TILE * len(order), TILE), (0, 0, 0, 0))
    for slot, name in enumerate(order):
        tile = Image.open(out_dir(season_name) / "tiles" / f"{name}.png").convert("RGBA")
        sheet.alpha_composite(tile, (slot * TILE, 0))
    return sheet


def cmd_assemble(args) -> int:
    season_name = args.season
    OUT = out_dir(season_name)
    for sub in ("tiles", "props"):
        (OUT / sub).mkdir(parents=True, exist_ok=True)

    (OUT / "palette.json").write_text(json.dumps({
        "name": f"season-{season_name}-v1",
        "season": season_name,
        "colors": len(palette_hex(season_name)),
        "family": "lodge",
        "lodge_colors": len(LODGE_PALETTE),
        "season_extension_colors": len(SEASONS[season_name]["extra"]),
        "named": [{"name": n, "hex": h} for n, h in palette_of(season_name)],
        "hex": palette_hex(season_name),
        "why": (
            f"One palette for the whole {season_name} set, and it is the LODGE palette plus a "
            "declared extension: the SOU-274 ticket asks for the same palette family and pixel "
            "scale as the Lodge set, so the family is imported from gen_lodge_art rather than "
            "restated, and the gate asserts the superset relation. The extension is the autumn "
            "oranges, reds and ochres the ticket asks for (16 colours, each at least 12.0 RGB "
            "units from every lodge colour). Every tile and prop in this set is quantised to "
            "exactly these colours by scripts/v3/gen_season_art.py assemble."
        ),
        "light": "top-left (same as office-01 and the lodge): each material ramp runs dark "
                 "(bottom-right faces) to light (top-left faces)",
        "daylight": "drawn in daylight colours on purpose - the renderer multiplies the outside "
                    "by the season tint, which is what makes it night",
    }, indent=2) + "\n")

    results = {}

    # ---- tiles ---------------------------------------------------------------------
    print(f"{'slot':>4s} {'name':15s} {'class':8s} {'axes':>4s} {'colours':>7s} "
          f"{'wrap/max x':>10s} {'y':>6s}")
    for asset in catalogue(season_name):
        if asset["kind"] != "tile":
            continue
        aid = asset["id"]
        quant = quantise(draw_tile(season_name, aid, asset["drawn_spec"]),
                         palette=palette_rgb(season_name))
        out_path = OUT / "tiles" / f"{aid}.png"
        quant.save(out_path)
        wrap = wrap_vs_max(quant)
        meta = {
            "slot": asset["slot"],
            "tile_class": asset["tile_class"],
            "seam_axes": asset["seam_axes"],
            "seam_checked_axes": asset["seam_axes"],
            "derivation": f"drawn from the declared {season_name} palette ({aid}), no FLUX pass",
            "wrap_vs_max": wrap,
            "opaque": not any(p[3] == 0 for p in quant.getdata()),
            "colours": len(_colours(quant)),
            "off_palette": _off_palette(season_name, quant),
            "why_drawn": "a tile repeats across ~100x60 cells and a diffusion texture is not "
                         "seamless (SOU-158 measured the generated floor as a high-contrast "
                         "vertical barcode; SOU-198 kept the raws as the record). A drawn tile "
                         "is periodic by construction: every plotted coordinate is taken modulo "
                         "the tile, so a leaf that straddles the wrap is continuous there.",
        }
        raw = OUT / f"{aid}.raw.png"
        if raw.exists():
            from gen_office01_art import best_tile_crop, seam_ratio
            src = Image.open(raw)
            naive = quantise(src.convert("RGB").resize((TILE, TILE), _nearest()).convert("RGBA"),
                             palette=palette_rgb(season_name))
            tile_src, crop_meta = best_tile_crop(src, size=TILE)
            gen = quantise(tile_src.convert("RGBA"), palette=palette_rgb(season_name))
            meta["generated_comparison"] = {
                "raw": raw.name,
                "prompt": _read_prompt(season_name, asset),
                "seed": _read_seed(season_name, asset, asset["slot"]),
                "wrap_vs_max": wrap_vs_max(gen),
                "seam_ratio_before_crop": seam_ratio(naive),
                "crop_offset": crop_meta["crop_offset"],
                "colours": len(_colours(gen)),
                "off_palette_colours": len(_off_palette(season_name, gen)),
                "why_not_shipped": "generated textures are not seamless and a diffusion model has "
                                   "no declared leaf direction; the drawn tile is periodic by "
                                   "construction. The raw is kept as the record of what was "
                                   "rejected.",
            }
        manifest = _manifest(season_name, asset, seed=None,
                             prompt=f"drawn from the declared palette - no FLUX pass (see "
                                    f"`derivation`)",
                             source_file="none - drawn, see DRAWN_TILES",
                             output_file=f"tiles/{aid}.png",
                             output_dims=asset["texture_px"], extra=meta)
        (OUT / "tiles" / f"{aid}.png.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"kind": "tile", "path": f"tiles/{aid}.png", **meta}
        gc = meta.get("generated_comparison")
        print(f"{asset['slot']:>4d} {aid:15s} {asset['tile_class']:8s} {asset['seam_axes']:>4s} "
              f"{meta['colours']:>7d} {wrap['wrap_vs_max_x']:>10} {wrap['wrap_vs_max_y']:>6}"
              + (f"   [generated alt wrap/max x={gc['wrap_vs_max']['wrap_vs_max_x']} "
                 f"y={gc['wrap_vs_max']['wrap_vs_max_y']}]" if gc else ""))

    # ---- the strip (the delivered file) --------------------------------------------
    order = [name for name, _c, _s, _spec in SEASONS[season_name]["tiles"]]
    strip = build_strip(season_name)
    strip.save(OUT / "tiles.png")
    (OUT / "tiles.png.manifest.json").write_text(json.dumps(_manifest(
        season_name, {"id": f"{season_name}-tileset", "kind": "tileset"},
        seed=None,
        prompt="composed from the drawn tiles in the declared slot order (see `derivation`)",
        source_file="tiles/<slot>.png per slot",
        output_file="tiles.png",
        output_dims=list(strip.size),
        extra={
            "tileset_contract": {
                "consumer": f"frontend/v3/assets/seasons/{season_name}/tiles.png, preloaded by "
                            f"frontend/v3/src/main.js and named by frontend/v3/src/seasons.js "
                            f"(SOU-273)",
                "tile_px": TILE,
                "columns": len(order),
                "firstgid": 1,
                "order": order,
                "order_authority": "the SOU-274 ticket table; seasons.js consumes the same four "
                                   "keys in the same order",
                "slot_class": {n: c for n, c, _s, _spec in SEASONS[season_name]["tiles"]},
                "slot_seam_axes": {n: s for n, _c, s, _spec in SEASONS[season_name]["tiles"]},
            },
            "derivation": {n: f"tiles/{n}.png verbatim" for n in order},
            "note": f"128x32: four 32px slots, ground_a, ground_b, path, edge.",
        }), indent=2) + "\n")
    print(f"[asm ] tiles.png {strip.size[0]}x{strip.size[1]} slots: {', '.join(order)}")

    # ---- the cross-tile seam (the AC1 wording: "with themselves AND WITH EACH OTHER") ---
    ground = [n for n, c, s, _spec in SEASONS[season_name]["tiles"] if s == "xy"]
    cross = {}
    print("[asm ] cross-tile seam across an EXPLICIT boundary (every ordered pair of the three "
          "ground materials, both axes)")
    for a in ground:
        for b in ground:
            ia = Image.open(OUT / "tiles" / f"{a}.png")
            ib = Image.open(OUT / "tiles" / f"{b}.png")
            m = cross_vs_max(ia, ib)
            m["kind"] = cross_seam_kind(a, b)
            m["asserted_axes"] = cross_seam_axes(a, b)
            cross[f"{a}|{b}"] = m
            print(f"        {a:9s} | {b:9s} x={m['ratio_x']:>5} y={m['ratio_y']:>5}  "
                  f"{m['kind']:10s} asserted={m['asserted_axes']}")
    (OUT / "cross_seam.json").write_text(json.dumps(cross, indent=2) + "\n")

    # ---- props ---------------------------------------------------------------------
    for asset in SEASONS[season_name]["props"]:
        aid = asset["id"]
        raw = OUT / f"{aid}.raw.png"
        if not raw.exists():
            print(f"[miss] {aid}: no raw generation on disk")
            continue
        seed = _read_seed(season_name, asset, 0)
        prompt = _read_prompt(season_name, asset)
        src = Image.open(raw)
        keyed_img, key = key_magenta(src)
        keyed_img.save(OUT / f"{aid}.keyed.png")
        # The FIT bbox drops keying speckle: the SOU-187 defect was a few hundred speckle
        # fragments far above the figure stretching the content bbox, so the sprite was
        # scaled to 60% of the frame and its head came out tiny.  The tree is the same case.
        bbox, speckle_px, parts = substantial_bbox(keyed_img)
        canvas, fit = fit_to_canvas(keyed_img, tuple(asset["texture_px"]), anchor="bottom",
                                    mode=asset["fit_mode"], bbox=bbox)
        quant = quantise(canvas, palette=palette_rgb(season_name))
        quant, islands, kept = drop_stray_islands(quant)
        quant, speckles = despeckle_outline(quant)
        quant, holes = fill_interior_holes(quant, palette=palette_rgb(season_name))
        out_path = OUT / "props" / f"{aid}.png"
        quant.save(out_path)
        meta = {
            "footprint_tiles": asset["footprint_tiles"],
            "footprint_px": asset["footprint_px"],
            "texture_px": list(quant.size),
            "declared_px": asset["texture_px"],
            "lift_px": asset["lift_px"],
            "fit_mode": asset["fit_mode"],
            "anchor": "bottom-centre of the footprint (renderer origin 0.5,1)",
            "key_colour": list(key),
            "fit": fit,
            "generation_mode": "text-only generation",
            "content_bottom_row": content_bottom_row(quant),
            "base_row_coverage": round(base_row_coverage(quant), 3),
            "colours": len(_colours(quant)),
            "off_palette": _off_palette(season_name, quant),
            "keying_speckle_px_ignored": speckle_px,
            "substantial_components_kept": parts,
            "stray_islands_dropped_px": islands,
            "disconnected_parts_kept_px": kept,
            "despeckled_px": speckles,
            "interior_holes_filled_px": holes,
            "note": asset["note"],
        }
        manifest = _manifest(season_name, asset, seed=seed, prompt=prompt,
                             source_file=raw.name, output_file=f"props/{aid}.png",
                             output_dims=list(quant.size), extra=meta)
        (OUT / "props" / f"{aid}.png.manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        results[aid] = {"kind": "prop", "path": f"props/{aid}.png", **meta}
        print(f"[asm ] {aid:15s} {quant.size[0]}x{quant.size[1]} declared "
              f"{asset['texture_px'][0]}x{asset['texture_px'][1]} fit={fit['fitted_px']} by "
              f"{fit['fitted_by']} bottom-row={meta['content_bottom_row']} "
              f"base-cov={meta['base_row_coverage']}")

    build_seam_proof(season_name)
    build_contact_sheets(season_name, results)
    print("ASSEMBLE_DONE")
    return 0


# --------------------------------------------------------------------------------------
# contact sheets and the AC2 seam proof
# --------------------------------------------------------------------------------------
def build_seam_proof(season_name: str) -> None:
    """The AC2 artifact: a 6x4 tiling of ground_a/ground_b with a path column.

    Two sizes from the same files: 1x is what the renderer actually shows, 4x is where a
    fringe or a one-pixel join would be visible to a human eye.  No measurement is asserted
    here - the numbers live in `cross_seam.json` and in the gate; this is the picture the
    ticket asks to attach.
    """
    OUT = out_dir(season_name)
    cols, rows = 6, 4
    path_col = 4
    for scale, name in ((1, "seam_proof.png"), (4, "seam_proof_4x.png")):
        tile_px = TILE * scale
        img = Image.new("RGBA", (cols * tile_px, rows * tile_px), (0, 0, 0, 0))
        for cy in range(cols):
            for ry in range(rows):
                key = "path" if cy == path_col else ("ground_a" if (cy + ry) % 2 == 0
                                                     else "ground_b")
                tile = Image.open(OUT / "tiles" / f"{key}.png").convert("RGBA")
                if scale != 1:
                    tile = tile.resize((tile_px, tile_px), _nearest())
                img.alpha_composite(tile, (cy * tile_px, ry * tile_px))
        img.save(OUT / name)
        print(f"[asm ] {name:20s} {img.size[0]}x{img.size[1]} "
              f"(6x4 tiles, column {path_col} = path)")


def _backdrop(season_name: str, tile_name: str, size, scale) -> Image.Image:
    OUT = out_dir(season_name)
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


def _flow(items, width, margin, gap):
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


def build_contact_sheets(season_name: str, results) -> None:
    """Tiles tiled 3x3 (seams) plus the props on the ground tile, at 1x and 4x."""
    from gen_office01_art import _grid_overlay
    OUT = out_dir(season_name)
    order = [name for name, _c, _s, _spec in SEASONS[season_name]["tiles"]]
    props = [a for a in SEASONS[season_name]["props"] if (OUT / "props" / f"{a['id']}.png").exists()]
    for scale, name in ((1, "contact_sheet.png"), (4, "contact_sheet_4x.png")):
        margin, gap = 16 * scale, 14 * scale
        header = 26 * scale
        block = TILE * 3 * scale
        per_row = 4 if scale == 1 else 2
        width = max(1024, per_row * block + (per_row - 1) * gap + 2 * margin)

        items = []
        for slot, tid in enumerate(order):
            tile = Image.open(OUT / "tiles" / f"{tid}.png").convert("RGBA")
            tiled = Image.new("RGBA", (TILE * 3, TILE * 3))
            for ty in range(3):
                for tx in range(3):
                    tiled.alpha_composite(tile, (tx * TILE, ty * TILE))
            items.append((f"{slot}: {tid}", tiled.resize((block, block), _nearest())))
        tiles_placed, tiles_h = _flow(items, width, margin, gap)

        prop_rows = {}
        for backdrop in ("ground_a", "ground_b"):
            items = []
            for asset in props:
                img = Image.open(OUT / "props" / f"{asset['id']}.png").convert("RGBA")
                img = img.resize((img.size[0] * scale, img.size[1] * scale), _nearest())
                items.append((asset["id"], img))
            prop_rows[backdrop] = _flow(items, width - 2 * margin - 8 * scale, margin, gap)

        y = margin
        y_tiles_label = y
        y_tiles = y + header
        y_props1_label = y_tiles + tiles_h + gap * 4
        y_props1 = y_props1_label + header
        y_props2_label = y_props1 + prop_rows["ground_a"][1] + gap * 4
        y_props2 = y_props2_label + header
        sheet_h = y_props2 + prop_rows["ground_b"][1] + margin + header

        img = _backdrop(season_name, "ground_a", (width, sheet_h), scale)
        _grid_overlay(img, scale)
        _label(img, (margin, y_tiles_label),
               f"{season_name} v3 outdoor art set (SOU-274) - TILES, each tiled 3x3 to show the "
               f"seams. {len(palette_hex(season_name))}-colour declared palette "
               f"({len(LODGE_PALETTE)} lodge family + "
               f"{len(SEASONS[season_name]['extra'])} autumn), 32px tiles.", scale=scale)
        for slot, (label, tile, px, py, h) in enumerate(tiles_placed):
            img.alpha_composite(tile, (px, y_tiles + py))
            _label(img, (px, y_tiles + py + h + 2), label, scale=scale)

        for backdrop, label_row in (("ground_a", y_props1_label), ("ground_b", y_props2_label)):
            placed, h = prop_rows[backdrop]
            band = _backdrop(season_name, backdrop, (width - 2 * margin, h + header), scale)
            _grid_overlay(band, scale)
            img.alpha_composite(band, (margin, label_row))
            _label(img, (margin, label_row),
                   f"PROPS at {scale}x on {backdrop} - each drawn at its declared texture px, "
                   f"anchored bottom-centre of its footprint (the square behind them is a 32px "
                   f"tile grid).", scale=scale)
            for pid, prop, px, py, hh in placed:
                img.alpha_composite(prop, (margin + px, label_row + header + py))
                asset = next(a for a in SEASONS[season_name]["props"] if a["id"] == pid)
                got = Image.open(OUT / "props" / f"{pid}.png").size
                _label(img, (margin + px, label_row + header + py + hh + 2),
                       f"{pid} {got[0]}x{got[1]}px fp {asset['footprint_px'][0]}x"
                       f"{asset['footprint_px'][1]} lift {asset['lift_px']}", scale=scale)

        img.save(OUT / name)
        print(f"[asm ] {name:20s} {img.size[0]}x{img.size[1]}")


# --------------------------------------------------------------------------------------
# publish into the served tree
# --------------------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def publish_pairs(season_name: str):
    """source (relative to the generated set) -> served name (relative to the season dir).

    Only what the renderer loads is published: the strip and the three trees, each with its
    manifest.  The per-slot tiles stay in the generated tree as the record - the loader
    preloads `tiles.png` and slices it, so a second copy of the same pixels in the served
    tree would be a file nothing reads.
    """
    out = [("tiles.png", "tiles.png"), ("tiles.png.manifest.json", "tiles.png.manifest.json")]
    for asset in SEASONS[season_name]["props"]:
        aid = asset["id"]
        out.append((f"props/{aid}.png", f"props/{aid}.png"))
        out.append((f"props/{aid}.png.manifest.json", f"props/{aid}.png.manifest.json"))
    return out


def cmd_publish(args) -> int:
    season_name = args.season
    SRC, DST = out_dir(season_name), served_dir(season_name)
    if not SRC.is_dir():
        print(f"no generated set at {SRC}", file=sys.stderr)
        return 2

    entries, mismatches = [], []
    for src_rel, dst_rel in publish_pairs(season_name):
        src, dst = SRC / src_rel, DST / dst_rel
        if not src.exists():
            mismatches.append(f"missing source {src_rel}")
            continue
        if not args.check:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
        if not dst.exists():
            mismatches.append(f"not published: {dst_rel}")
            continue
        if _sha256(src) != _sha256(dst):
            mismatches.append(f"byte mismatch: {dst_rel}")
        entries.append({"served_path": f"assets/seasons/{season_name}/{dst_rel}",
                        "source": f"assets/generated/v3/seasons/{season_name}/{src_rel}",
                        "sha256": _sha256(src), "bytes": src.stat().st_size})

    props = []
    for asset in SEASONS[season_name]["props"]:
        aid = asset["id"]
        served = DST / "props" / f"{aid}.png"
        row = {"key": aid, "served_path": f"assets/seasons/{season_name}/props/{aid}.png",
               "texture_px_declared": asset["texture_px"],
               "footprint_px": asset["footprint_px"],
               "footprint_tiles": asset["footprint_tiles"],
               "lift_px": asset["lift_px"]}
        if served.exists():
            row["texture_px_actual"] = list(Image.open(served).size)
        props.append(row)

    order = [name for name, _c, _s, _spec in SEASONS[season_name]["tiles"]]
    index = {
        "pack": f"season-{season_name}-v1",
        "set": season_name,
        "season": season_name,
        "issue": "SOU-274",
        "generated_from": f"assets/generated/v3/seasons/{season_name}",
        "model": MODEL_ID,
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "camera": "top-down 3/4, light from the top-left (same projection vocabulary as "
                  "office-01 and the lodge)",
        "palette": f"assets/generated/v3/seasons/{season_name}/palette.json",
        "palette_family": "lodge",
        "tileset": {
            "path": f"assets/seasons/{season_name}/tiles.png",
            "tile_px": TILE, "columns": len(order), "firstgid": 1,
            "order": order,
            "order_authority": "the SOU-274 ticket table; seasons.js (SOU-273) consumes the same "
                               "four keys in the same order",
            "slot_class": {n: c for n, c, _s, _spec in SEASONS[season_name]["tiles"]},
            "slot_seam_axes": {n: s for n, _c, s, _spec in SEASONS[season_name]["tiles"]},
        },
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"]
                                          for a in SEASONS[season_name]["props"]},
        "props": props,
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "the season lookup itself lives in frontend/v3/src/seasons.js (SOU-273); this "
                "index.json is the published record of what the season's art set contains.",
    }
    if not args.check:
        (DST / "index.json").write_text(json.dumps(index, indent=2) + "\n")
        print(f"published {len(entries)} files to {DST}")
    else:
        print(f"checked {len(entries)} files against {SRC}")

    if mismatches:
        print("MISMATCH:")
        for m in mismatches:
            print(f"  - {m}")
        return 1
    print(f"OK: every published file is byte-identical to its source ({len(entries)} files, "
          f"{len(props)} prop entries)")
    return 0


def cmd_clean(args) -> int:
    """Remove DERIVED output only - never a raw generation (the SOU-158 lesson: `rm -rf` on
    the set directory threw away the raws and cost a second full GPU pass)."""
    OUT = out_dir(args.season)
    removed, kept = [], []
    for sub in ("tiles", "props"):
        directory = OUT / sub
        if directory.is_dir():
            for f in sorted(directory.iterdir()):
                if f.suffix == ".png" or f.name.endswith(".manifest.json"):
                    f.unlink()
                    removed.append(str(f.relative_to(OUT)))
    for f in sorted(OUT.glob("*")):
        if f.is_file() and (f.name.endswith(".raw.png") or f.name.endswith(".prompt.txt")):
            kept.append(f.name)
            continue
        if f.is_file():
            f.unlink()
            removed.append(f.name)
    print(f"cleaned {len(removed)} derived file(s); kept {len(kept)} raw generation(s) + prompts")
    for name in removed:
        print(f"  - {name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # `--season` lives on every SUBcommand (`gen_season_art.py assemble --season autumn`), which
    # is the form the ticket names and the form tickets 3 and 4 will use.
    season_arg = argparse.ArgumentParser(add_help=False)
    season_arg.add_argument("--season", default="autumn", choices=sorted(SEASONS),
                            help="which season's set (SOU-275/276 add spring/summer here)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate", parents=[season_arg],
                       help="FLUX pass (needs the sou-flux venv + MPS)")
    g.add_argument("--only", help="comma-separated asset ids")
    g.add_argument("--force", action="store_true", help="regenerate even if a raw exists")
    g.add_argument("--force-all-tiles", action="store_true",
                   help="generate the drawn tiles too (they are skipped by default)")
    g.add_argument("--seed-shift", type=int, default=0,
                   help="add N to the per-asset seed (recorded in <id>.seed.txt)")
    g.set_defaults(func=cmd_generate)
    a = sub.add_parser("assemble", parents=[season_arg],
                       help="CPU pass: draw, key, fit, quantise, manifests, sheets")
    a.set_defaults(func=cmd_assemble)
    p = sub.add_parser("publish", parents=[season_arg],
                       help="copy the set into frontend/v3/assets/seasons/<season>/")
    p.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    p.set_defaults(func=cmd_publish)
    c = sub.add_parser("clean", parents=[season_arg],
                       help="remove derived output only - the raws are kept")
    c.set_defaults(func=cmd_clean)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
