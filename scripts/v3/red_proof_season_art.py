#!/usr/bin/env python3
"""RED proof for the seasonal-art gate: mutate a COPY of a season's set and show the gate fails.

A gate that has never failed is not evidence (SOU-198 precedent), and a gate written by the
same session that wrote the art is the one most likely to be wired to pass.  So every assertion
the season branch adds gets a mutation that targets it, plus a CONTROL run of the unmutated
copy which must PASS - without the control, a gate that fails everything would look like a
triumph.

Each case prints only the gate's FAIL lines and its RESULT, so the mapping from mutation to
assertion is readable.

    python3 scripts/v3/red_proof_season_art.py                  # autumn (SOU-274)
    python3 scripts/v3/red_proof_season_art.py --season summer  # summer (SOU-276)

Run it for EVERY season that has a gate branch: the assertions are keyed by season now, so a
battery that only ever exercises autumn leaves summer's clauses unproven.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_season_art as S  # noqa: E402

OUT = Path("/tmp")

# Per-season mutation inputs.  A mutation that reached for a colour name from the wrong season's
# extension would raise KeyError inside the battery, and a battery that crashes reads like a pass.
# `out_of_band` is a colour in THIS palette whose HUE falls outside the season's crown bands;
# `bright_green` is the palette's brightest green, which keeps the hue assertion satisfied and so
# isolates the "dark green" luminance clause.
SEASON_INPUTS: dict[str, dict] = {
    "autumn": {
        "base": "earth",
        "base_other": "earth_dust",
        "wrap_ramp": ("earth", "earth_light", "earth_pale", "earth_dust"),
        "out_of_band": "leaf_orange",
        "bright_crown": "leaf_gold",
        "red_root": "/tmp/sou274-red-autumn",
        "issue": "SOU-274",
    },
    "summer": {
        "base": "grass",
        "base_other": "grass_pale",
        "wrap_ramp": ("grass_deep", "grass_dark", "grass", "grass_light"),
        "out_of_band": "earth",
        "bright_crown": "clover",
        "red_root": "/tmp/sou276-red-summer",
        "issue": "SOU-276",
    },
}

SEASON = "autumn"
INPUTS = SEASON_INPUTS[SEASON]
RED_ROOT = Path(INPUTS["red_root"])


def fresh() -> Path:
    """A clean copy of the generated set, plus the published copy so the served check has a
    byte-identical counterpart to compare against."""
    if RED_ROOT.exists():
        shutil.rmtree(RED_ROOT)
    shutil.copytree(S.out_dir(SEASON), RED_ROOT)
    return RED_ROOT


def restamp(root: Path) -> None:
    """Rebuild tiles.png from the mutated tiles.

    Without this, a tile mutation also trips the strip-vs-slot check and the battery cannot
    show WHICH assertion caught it.  Restamping keeps each case aimed at one assertion.
    """
    order = [n for n, _c, _s, _spec in S.SEASONS[SEASON]["tiles"]]
    sheet = Image.new("RGBA", (32 * len(order), 32), (0, 0, 0, 0))
    for slot, name in enumerate(order):
        sheet.alpha_composite(Image.open(root / "tiles" / f"{name}.png").convert("RGBA"),
                              (slot * 32, 0))
    sheet.save(root / "tiles.png")


def run(root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(HERE / "check_v3_assets.py"), "--set", SEASON,
         "--root", str(root), "--quiet"],
        capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={proc.returncode}\n" + "\n".join(lines)


def _tile(root: Path, name: str) -> Image.Image:
    return Image.open(root / "tiles" / f"{name}.png").convert("RGBA")


def _save(img: Image.Image, root: Path, name: str) -> None:
    img.save(root / "tiles" / f"{name}.png")


def _prop(root: Path, name: str) -> Image.Image:
    return Image.open(root / "props" / f"{name}.png").convert("RGBA")


def _save_prop(img: Image.Image, root: Path, name: str) -> None:
    img.save(root / "props" / f"{name}.png")


# ---------------------------------------------------------------------------------------
# tile / strip mutations
# ---------------------------------------------------------------------------------------
def mutate_strip_order(root: Path) -> None:
    """Swap slots 0 and 1 in tiles.png: the strip no longer matches the declared order."""
    strip = Image.open(root / "tiles.png").convert("RGBA")
    a = strip.crop((0, 0, 32, 32))
    b = strip.crop((32, 0, 64, 32))
    strip.paste(b, (0, 0))
    strip.paste(a, (32, 0))
    strip.save(root / "tiles.png")


def mutate_tile_wrap(root: Path) -> None:
    """Ramp ground_b's columns earth -> earth_dust so the tile no longer WRAPS.

    Every interior column step is one palette step, but the step from column 31 back to column 0
    is the whole ramp - which is exactly the seam the wrap measurement exists to find.
    """
    ramp = list(INPUTS["wrap_ramp"])
    by_name = dict(S.palette_of(SEASON))
    img = _tile(root, "ground_b")
    px = img.load()
    for x in range(32):
        colour = S._hex_to_rgb(by_name[ramp[min(3, x // 8)]]) + (255,)
        for y in range(32):
            px[x, y] = colour
    _save(img, root, "ground_b")
    restamp(root)


def mutate_cross_seam(root: Path) -> None:
    """Recolour ground_b's whole base so it still WRAPS but no longer tiles against ground_a.

    The self-seam stays 0.0 (the base is uniform), so only the cross-tile assertion can fire -
    which is the point: self-periodicity is not mutual tileability.
    """
    by_name = dict(S.palette_of(SEASON))
    img = _tile(root, "ground_b")
    px = img.load()
    old = S._hex_to_rgb(by_name[INPUTS["base"]])
    new = S._hex_to_rgb(by_name[INPUTS["base_other"]])
    for y in range(32):
        for x in range(32):
            if px[x, y][:3] == old:
                px[x, y] = new + (255,)
    _save(img, root, "ground_b")
    restamp(root)


def mutate_cross_json(root: Path) -> None:
    """Tamper the recorded cross-seam number: the gate must read the record, not trust it."""
    path = root / "cross_seam.json"
    data = json.loads(path.read_text())
    data["ground_a|ground_b"]["ratio_x"] = 0.01  # claim a seam-free join that is not there
    data["ground_b|ground_a"]["kind"] = "self"   # and claim the wrong classification
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------------------------------------------------------------------------------------
# prop mutations
# ---------------------------------------------------------------------------------------
def mutate_prop_size(root: Path) -> None:
    img = _prop(root, "tree_b")
    img.resize((img.size[0], img.size[1] - 4), Image.Resampling.NEAREST).save(
        root / "props" / "tree_b.png")


def mutate_manifest(root: Path) -> None:
    (root / "props" / "tree_small.png.manifest.json").unlink()


def mutate_magenta(root: Path) -> None:
    """Put magenta back under real alpha - the fringe the keying exists to prevent."""
    img = _prop(root, "tree_a")
    px = img.load()
    for y in range(8, 14):
        for x in range(8, 14):
            px[x, y] = (255, 0, 255, 255)
    _save_prop(img, root, "tree_a")


def mutate_crown_families(root: Path) -> None:
    """Repaint every crown pixel one flat colour whose hue is OUTSIDE the season's bands.

    Autumn: one flat orange, so no red family and no ochre family survive.  Summer: one flat earth
    brown, so no green survives at all.  Either way the crown stops carrying the colours the
    season's ticket names.
    """
    by_name = dict(S.palette_of(SEASON))
    flat = S._hex_to_rgb(by_name[INPUTS["out_of_band"]]) + (255,)
    for name in ("tree_a", "tree_b", "tree_small"):
        img = _prop(root, name)
        w, h = img.size
        px = img.load()
        for y in range(0, int(h * 0.6)):
            for x in range(w):
                if px[x, y][3]:
                    px[x, y] = flat
        _save_prop(img, root, name)


def mutate_crown_bright(root: Path) -> None:
    """Repaint every crown pixel the palette's BRIGHTEST green.

    The hue assertion still passes - it is green - so this case isolates the "DARK green" clause,
    which is the only assertion that can catch a bright spring-green crown.  Without it that clause
    would have no mutation aimed at it and would be decoration.  (On autumn, whose ticket asks for
    no dark crown, the same repaint lands outside the required families and fires there instead.)
    """
    by_name = dict(S.palette_of(SEASON))
    bright = S._hex_to_rgb(by_name[INPUTS["bright_crown"]]) + (255,)
    for name in ("tree_a", "tree_b", "tree_small"):
        img = _prop(root, name)
        w, h = img.size
        px = img.load()
        for y in range(0, int(h * 0.6)):
            for x in range(w):
                if px[x, y][3]:
                    px[x, y] = bright
        _save_prop(img, root, name)


def mutate_trunk_light(root: Path) -> None:
    """Paint tree_b's trunk band a light palette colour: no longer a dark trunk."""
    by_name = dict(S.palette_of(SEASON))
    light = S._hex_to_rgb(by_name["snow"]) + (255,)
    img = _prop(root, "tree_b")
    w, h = img.size
    px = img.load()
    for y in range(int(h * 0.75), h):
        for x in range(w):
            if px[x, y][3]:
                px[x, y] = light
    _save_prop(img, root, "tree_b")


def mutate_trunk_offcentre(root: Path) -> None:
    """Move tree_small's trunk base to the left edge: the footprint anchor would be wrong."""
    img = _prop(root, "tree_small")
    w, h = img.size
    px = img.load()
    for y in range(h - 6, h):
        for x in range(w):
            if px[x, y][3]:
                px[x, y] = (0, 0, 0, 0)
    by_name = dict(S.palette_of(SEASON))
    wood = S._hex_to_rgb(by_name["wood_deep"]) + (255,)
    for y in range(h - 6, h):
        for x in range(0, 4):
            px[x, y] = wood
    _save_prop(img, root, "tree_small")


def mutate_crown_on_ground(root: Path) -> None:
    """Fill tree_b's bottom row edge to edge: a crown sitting on the ground, not a trunk."""
    by_name = dict(S.palette_of(SEASON))
    leaf = S._hex_to_rgb(by_name[INPUTS["bright_crown"]]) + (255,)
    img = _prop(root, "tree_b")
    w, h = img.size
    px = img.load()
    for x in range(w):
        px[x, h - 1] = leaf
    _save_prop(img, root, "tree_b")


def mutate_off_palette(root: Path) -> None:
    img = _prop(root, "tree_a")
    px = img.load()
    px[1, 1] = (1, 254, 3, 255)  # a colour that is in no declared palette
    _save_prop(img, root, "tree_a")


# ---------------------------------------------------------------------------------------
# palette / declaration / served-copy mutations
# ---------------------------------------------------------------------------------------
def mutate_palette_family(root: Path) -> None:
    """Drop one LODGE colour from palette.json: the family claim is then false."""
    path = root / "palette.json"
    data = json.loads(path.read_text())
    lodge_first = S.LODGE_PALETTE[0][1]
    data["hex"] = [h for h in data["hex"] if h != lodge_first]
    data["colors"] = len(data["hex"])
    path.write_text(json.dumps(data, indent=2) + "\n")


def mutate_served_copy(root: Path) -> None:
    """Make the published file differ from the generated one, then put it back.

    This is the one mutation that has to touch the real served tree; it is restored in a
    `finally` so a failure here cannot leave the repo dirty.
    """
    served = S.served_dir(SEASON) / "tiles.png"
    original = served.read_bytes()
    try:
        img = Image.open(served).convert("RGBA")
        px = img.load()
        px[0, 0] = (1, 254, 3, 255)
        img.save(served)
        print("MUTATION [served copy] tiles.png in the served tree differs from the generated one")
        print(run(root))
    finally:
        served.write_bytes(original)


def mutate_rejected_pass(root: Path) -> bool:
    """The real negative control: rebuild the props from the REJECTED first FLUX pass.

    Not a synthetic mutation - this is the artifact the gate's crown-hue assertion exists to
    reject (two of its three crowns carry under 5% ochre).  If the gate passes this, the
    assertion is decoration.

    Returns False when the artifact does not exist for this season.  That case must NOT print a
    green gate result: a control that was never run is unproven, not passed, and a battery that
    shrugs with a note is exactly the false-PASS shape this whole file exists to guard against.
    """
    from gen_office01_art import (despeckle_outline, drop_stray_islands, fill_interior_holes,
                                  fit_to_canvas, key_magenta, quantise, substantial_bbox)
    src = S.out_dir(SEASON)
    for asset in S.SEASONS[SEASON]["props"]:
        aid = asset["id"]
        raw = src / f"{aid}.rejected-r1.raw.png"
        if not raw.exists():
            print(f"  no {raw.name} on disk for the {SEASON} set")
            return False
        keyed, _key = key_magenta(Image.open(raw))
        bbox, _sp, _parts = substantial_bbox(keyed)
        canvas, _fit = fit_to_canvas(keyed, tuple(asset["texture_px"]), anchor="bottom",
                                     mode="inside", bbox=bbox)
        q = quantise(canvas, palette=S.palette_rgb(SEASON))
        q, _i, _k = drop_stray_islands(q)
        q, _s = despeckle_outline(q)
        q, _h = fill_interior_holes(q, palette=S.palette_rgb(SEASON))
        q.save(root / "props" / f"{aid}.png")
    return True


CASES = [
    ("strip slot 0/1 swapped", mutate_strip_order),
    ("ground_b ramped - the tile no longer wraps", mutate_tile_wrap),
    ("ground_b recoloured - wraps, but no longer joins ground_a", mutate_cross_seam),
    ("cross_seam.json tampered (ratio + classification)", mutate_cross_json),
    ("tree_b resized 4px shorter", mutate_prop_size),
    ("tree_small manifest deleted", mutate_manifest),
    ("tree_a magenta reintroduced under alpha", mutate_magenta),
    ("all crowns repainted one flat colour OUTSIDE the season's hue bands", mutate_crown_families),
    ("all crowns repainted the palette's BRIGHTEST colour (dark-crown clause)", mutate_crown_bright),
    ("props rebuilt from the REJECTED first FLUX pass (negative control)",
     mutate_rejected_pass),
    ("tree_b trunk band painted light", mutate_trunk_light),
    ("tree_small trunk base moved off centre", mutate_trunk_offcentre),
    ("tree_b bottom row filled edge to edge", mutate_crown_on_ground),
    ("tree_a one off-palette pixel", mutate_off_palette),
    ("palette.json loses one lodge colour", mutate_palette_family),
]


def generator_declaration_drift() -> None:
    """Drift the generator's own slot order away from the ticket, in-process.

    No file can express this mutation - the assertion compares the MODULE's declaration to the
    hand-typed ticket table - so it is done by patching the module and calling the gate's
    branch directly.  A gate that only ever compares files would never catch its own source.
    """
    code = (
        "import sys, argparse, json;"
        f"sys.path.insert(0, {str(HERE)!r});"
        "import gen_season_art as S;"
        "import check_v3_assets as C;"
        f"t = list(S.SEASONS[{SEASON!r}]['tiles']);"
        "t[0], t[1] = t[1], t[0];"
        f"S.SEASONS[{SEASON!r}]['tiles'] = t;"
        f"a = argparse.Namespace(root={str(RED_ROOT)!r}, map=None, quiet=True);"
        f"rc = C.main_season(a, {SEASON!r});"
        "print('gate exit', rc)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if "FAIL" in ln or "gate exit" in ln]
    print("MUTATION [generator] slot order drifted from the ticket table (in-process)")
    print("\n".join(lines) or proc.stderr[-400:])


def main(argv=None) -> int:
    global SEASON, INPUTS, RED_ROOT
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--season", default="autumn", choices=sorted(SEASON_INPUTS),
                    help="which season's set to run the battery against (default: autumn)")
    args = ap.parse_args(argv)
    SEASON = args.season
    INPUTS = SEASON_INPUTS[SEASON]
    RED_ROOT = Path(INPUTS["red_root"])

    print("=" * 90)
    print(f"RED PROOF battery for the {SEASON} set ({INPUTS['issue']}); "
          f"scratch root {RED_ROOT}")
    print("=" * 90)
    print(f"CONTROL: the unmutated copy must PASS (a gate that fails everything proves nothing)")
    root = fresh()
    print(run(root))
    unproven = []
    for label, fn in CASES:
        root = fresh()
        applied = fn(root)
        print("=" * 90)
        print(f"MUTATION {label!r}")
        if applied is False:
            unproven.append(label)
            print("  NOT PROVEN for this season: the artifact this control needs does not exist, "
                  "so the gate was NOT run and this case proves NOTHING.")
            continue
        print(run(root))
    print("=" * 90)
    fresh()
    generator_declaration_drift()
    print("=" * 90)
    exercised = len(CASES) - len(unproven)
    print(f"BATTERY SUMMARY ({SEASON}): {exercised}/{len(CASES)} mutations exercised.")
    if unproven:
        for label in unproven:
            print(f"  NOT PROVEN: {label}")
        print("  The unproven case(s) are EXTRA non-synthetic controls, not coverage of an "
              "assertion - every assertion still has a synthetic mutation aimed at it above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
