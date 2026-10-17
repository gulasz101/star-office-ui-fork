#!/usr/bin/env python3
"""RED proof for the seasonal gate: mutate a COPY of a season's set and show the gate fails.

A gate that has never failed is not evidence (SOU-198 precedent), and a gate written by the
same session that wrote the art is the one most likely to be wired to pass.  So every assertion
the season's branch adds gets a mutation that targets it, plus a CONTROL run of the unmutated
copy which must PASS - without the control, a gate that fails everything would look like a
triumph.

Each case prints only the gate's FAIL lines and its RESULT, so the mapping from mutation to
assertion is readable, and the run ends with a count: a case whose mutation does NOT fire is a
finding about the GATE, not about the art.

    python3 scripts/v3/red_proof_season_art.py                    # autumn (SOU-274)
    python3 scripts/v3/red_proof_season_art.py --season spring    # spring (SOU-275)
    python3 scripts/v3/red_proof_season_art.py --season summer    # summer (SOU-276)

Run it for EVERY season that has a gate branch: the assertions are keyed by season, so a battery
that only ever exercises autumn leaves the other seasons' clauses unproven.  (SOU-283: the spring
branch took the season POSITIONALLY and the summer branch as `--season`; both spellings are
accepted here so either branch's invocation keeps working.)

The spring battery is aimed at the assertions SOU-275 added, and two of its cases are about the
CHECKER rather than the art: the palette-aware magenta exemption (an undeclared pink must still
count as residue) and the palette-distance floor (a near-duplicate extension colour must fire).
The summer battery adds the case for the "dark green" crown luminance clause, which no hue
assertion can see.  A CONTROL whose input artifact a season does not ship reports NOT PROVEN
rather than inheriting the clean copy's PASS - that false-PASS shape is the one this file exists
to guard against.
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
# `out_of_band` is a colour in THIS palette whose hue falls outside the season's crown families, so
# the family clause is the one that fires; `bright_crown` keeps the family clause satisfied and
# isolates a different claim (summer: the palette's brightest GREEN, still inside the crown band, so
# only the "dark green" luminance ceiling can catch it; autumn: a gold leaf, which lands inside the
# ochre band but outside the required three-family mix, so the family clause fires there instead).
#
# The wrap ramp and ground base are deliberately NOT hand-typed here: they are read from the
# season's OWN tables (`SEASON[...]["tiles"]` / `["extra"]`), because a hand-typed copy is a second
# place to be wrong - spring's first battery run hunted autumn's `earth` pixels on a set whose
# ground is green, stayed clean and printed PASS.
SEASON_INPUTS: dict[str, dict] = {
    "autumn": {
        "out_of_band": "leaf_orange",
        "bright_crown": "leaf_gold",
        "red_root": "/tmp/sou274-red-autumn",
        "issue": "SOU-274",
    },
    "spring": {
        "out_of_band": "earth",
        "bright_crown": "grass_pale",
        "red_root": "/tmp/sou275-red-spring",
        "issue": "SOU-275",
    },
    "summer": {
        "out_of_band": "earth",
        "bright_crown": "clover",
        "red_root": "/tmp/sou276-red-summer",
        "issue": "SOU-276",
    },
}

# Defaults for a bare import; `main` re-binds all three from `--season`.
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
    # The ramp comes from the season's own extension (which is declared ramp-first), not from a
    # hand-typed per-season copy.
    ramp = [n for n, _h in S.SEASONS[SEASON]["extra"][:4]]
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

    The base colour is read from the season's OWN tile spec and the replacement is the declared
    colour furthest from it, because hard-coding autumn's `earth` made this case a NO-OP on a set
    whose ground is green: spring's first battery run had nothing to recolour, the copy stayed
    clean, and the case reported RESULT: PASS - the false-PASS direction, where the gate looks
    green and the battery looks like a hole.
    """
    by_name = dict(S.palette_of(SEASON))
    spec = next(s for n, _c, _s, s in S.SEASONS[SEASON]["tiles"] if n == "ground_b")
    old_name = spec["base"]
    old = S._hex_to_rgb(by_name[old_name])
    ext = [n for n, _h in S.SEASONS[SEASON]["extra"]]
    new_name = max(ext, key=lambda n: sum(abs(a - b)
                                          for a, b in zip(S._hex_to_rgb(by_name[n]), old)))
    new = S._hex_to_rgb(by_name[new_name]) + (255,)
    img = _tile(root, "ground_b")
    px = img.load()
    for y in range(32):
        for x in range(32):
            if px[x, y][:3] == old:
                px[x, y] = new
    _save(img, root, "ground_b")
    restamp(root)
    print(f"  [{SEASON}] ground_b base {old_name} -> {new_name} (the declared colour furthest "
          f"from it)")


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
    light = S._hex_to_rgb(by_name[_first_declared(by_name, "snow", "snow_light")]) + (255,)
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


def _first_declared(by_name: dict, *candidates: str) -> str:
    """The first palette name this season actually declares - the battery runs for every season."""
    for c in candidates:
        if c in by_name:
            return c
    raise KeyError(f"none of {candidates} is in the {SEASON} palette")


def mutate_crown_on_ground(root: Path) -> None:
    """Fill tree_b's bottom row edge to edge: a crown sitting on the ground, not a trunk."""
    by_name = dict(S.palette_of(SEASON))
    # A declared FOLIAGE colour this season actually ships: autumn's deep red leaf, spring's
    # blossom, summer's dark pine green.  `_first_declared` is what keeps this case portable -
    # a one-season literal would KeyError on another season's palette, and a battery that crashes
    # reads like a pass.
    leaf = S._hex_to_rgb(
        by_name[_first_declared(by_name, "leaf_red_deep", "blossom_deep", "pine_deep")]) + (255,)
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
# spring-only mutations (SOU-275).  Each targets ONE of the assertions the spring branch
# adds, and two of them target the CHECKER rather than the art.
# ---------------------------------------------------------------------------------------
def _repaint_crown(root: Path, name: str, colour: str) -> None:
    by_name = dict(S.palette_of(SEASON))
    rgb = S._hex_to_rgb(by_name[colour]) + (255,)
    img = _prop(root, name)
    w, h = img.size
    px = img.load()
    for y in range(0, int(h * 0.6)):
        for x in range(w):
            if px[x, y][3]:
                px[x, y] = rgb
    _save_prop(img, root, name)


def mutate_spring_crown_green(root: Path) -> None:
    """tree_a's crown repainted blossom pink: the light-green tree is gone (dominance fires)."""
    _repaint_crown(root, "tree_a", "blossom")


def mutate_spring_crown_blossom(root: Path) -> None:
    """tree_b's crown repainted green: the blossom VERSION the ticket names is gone."""
    _repaint_crown(root, "tree_b", "grass")


def mutate_spring_crown_dark(root: Path) -> None:
    """tree_a and tree_small repainted in the pine ramp's own green.

    The dominant family is still `green`, so the dominance clause stays quiet - only the
    "LIGHT green" floor can fire, which is exactly the assertion under test.  The colour is a
    declared palette colour (the lodge's pine_deep), so nothing else objects.
    """
    _repaint_crown(root, "tree_a", "pine_deep")
    _repaint_crown(root, "tree_small", "pine_deep")


def mutate_spring_base(root: Path) -> None:
    """Re-base BOTH ground tiles on earth: no longer fresh green grass.

    Both, not just ground_a, to keep the mutation aimed at one assertion: re-basing only one
    would also move the cross-tile join and fire that check too.
    """
    by_name = dict(S.palette_of(SEASON))
    old = S._hex_to_rgb(by_name["grass"])
    new = S._hex_to_rgb(by_name["earth"]) + (255,)
    for name in ("ground_a", "ground_b"):
        img = _tile(root, name)
        px = img.load()
        for y in range(32):
            for x in range(32):
                if px[x, y][:3] == old:
                    px[x, y] = new
        _save(img, root, name)
    restamp(root)


def mutate_spring_flowers(root: Path) -> None:
    """Erase ground_b's flowers, leaving ground_a's: 'ground_b has MORE flowers' is then false."""
    by_name = dict(S.palette_of(SEASON))
    family = S._hex_to_rgb(by_name["grass"]) + (255,)
    blossom = {S._hex_to_rgb(h) for h in
               (by_name[n] for n in ("blossom", "blossom_light", "blossom_pale", "blossom_white"))}
    img = _tile(root, "ground_b")
    px = img.load()
    for y in range(32):
        for x in range(32):
            if px[x, y][3] and tuple(px[x, y][:3]) in blossom:
                px[x, y] = family
    _save(img, root, "ground_b")
    restamp(root)


def mutate_undeclared_pink(root: Path) -> None:
    """A pink that is NOT in the declared palette, painted into tree_a's crown.

    (230,110,180) satisfies the de-fringe rule but is nobody's declared colour, so it is
    residue by definition.  This case exists because SOU-275 made the fringe count
    palette-aware, and a blanket exemption would have been the lazy version of that fix: the
    exemption must clear the DECLARED blossom and still catch everything else.
    """
    img = _prop(root, "tree_a")
    px = img.load()
    for y in range(6, 10):
        for x in range(6, 10):
            px[x, y] = (230, 110, 180, 255)
    _save_prop(img, root, "tree_a")


def palette_distance_drift(root: Path | None = None) -> None:
    """Drift a spring extension colour to a near-duplicate of a lodge colour, in-process.

    No file can express this cleanly: the gate checks palette.json against the generator FIRST,
    so the mutation has to move both at once (and the manifests, or the manifest-palette check
    fires instead).  Done in-process so the mutation is exactly one thing.
    """
    import argparse
    import contextlib
    import io

    import check_v3_assets as C

    root = fresh()
    ext = list(S.SEASONS[SEASON]["extra"])
    names = [n for n, _h in ext]
    ext[names.index("grass_light")] = ("grass_light", "#b98f3e")  # 2.0 units from the lodge brass
    old = S.SEASONS[SEASON]["extra"]
    S.SEASONS[SEASON]["extra"] = ext
    try:
        new_hex = S.palette_hex(SEASON)
        for sub in ("tiles", "props"):
            for man in sorted((root / sub).glob("*.manifest.json")):
                data = json.loads(man.read_text())
                data["palette"] = new_hex
                man.write_text(json.dumps(data, indent=2) + "\n")
        pal = root / "palette.json"
        data = json.loads(pal.read_text())
        data["hex"] = new_hex
        data["colors"] = len(new_hex)
        pal.write_text(json.dumps(data, indent=2) + "\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = C.main_season(argparse.Namespace(root=str(root), map=None, quiet=True), SEASON)
        print("MUTATION [palette distance] grass_light drifted to #b98f3e, a near-duplicate of "
              "the lodge brass (in-process, palette.json + manifests moved with it)")
        lines = [ln for ln in buf.getvalue().splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
        print("\n".join(lines) or f"NO FAILURE FIRED (gate exit {rc})")
    finally:
        S.SEASONS[SEASON]["extra"] = old


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


CASES_AUTUMN = [
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

# The SOU-275 battery.  The shared mutations come first (they must keep firing on a set with
# pink materials in it), then the six assertions the spring branch adds, then the two cases
# that are about the checker: the palette-aware fringe exemption and the palette-distance floor.
CASES_SPRING = [
    ("strip slot 0/1 swapped", mutate_strip_order),
    ("ground_b ramped - the tile no longer wraps", mutate_tile_wrap),
    ("ground_b recoloured - wraps, but no longer joins ground_a", mutate_cross_seam),
    ("cross_seam.json tampered (ratio + classification)", mutate_cross_json),
    ("tree_b resized 4px shorter", mutate_prop_size),
    ("tree_small manifest deleted", mutate_manifest),
    ("tree_a magenta reintroduced under alpha", mutate_magenta),
    ("tree_b trunk band painted light", mutate_trunk_light),
    ("tree_small trunk base moved off centre", mutate_trunk_offcentre),
    ("tree_b bottom row filled edge to edge", mutate_crown_on_ground),
    ("tree_a one off-palette pixel", mutate_off_palette),
    ("palette.json loses one lodge colour", mutate_palette_family),
    ("both ground tiles re-based on earth - no longer fresh green grass", mutate_spring_base),
    ("ground_b's flowers erased - fewer than ground_a's", mutate_spring_flowers),
    ("tree_a's crown repainted blossom pink - no longer the green tree",
     mutate_spring_crown_green),
    ("tree_b's crown repainted green - no longer the blossom variant",
     mutate_spring_crown_blossom),
    ("tree_a + tree_small repainted in the pine ramp's own dark green",
     mutate_spring_crown_dark),
    ("an UNDECLARED pink that satisfies the fringe rule (the exemption is not a muzzle)",
     mutate_undeclared_pink),
]

# The SOU-276 battery.  Its shared cases are the same checks the other seasons' batteries run (a
# summer set has to keep passing them) plus `mutate_crown_bright`: that is the only mutation that
# can reach the "dark green" luminance ceiling, because a bright green crown satisfies the hue
# assertion - without it, that clause would have no mutation aimed at it and would be decoration.
CASES_SUMMER = [
    ("strip slot 0/1 swapped", mutate_strip_order),
    ("ground_b ramped - the tile no longer wraps", mutate_tile_wrap),
    ("ground_b recoloured - wraps, but no longer joins ground_a", mutate_cross_seam),
    ("cross_seam.json tampered (ratio + classification)", mutate_cross_json),
    ("tree_b resized 4px shorter", mutate_prop_size),
    ("tree_small manifest deleted", mutate_manifest),
    ("tree_a magenta reintroduced under alpha", mutate_magenta),
    ("all crowns repainted one flat colour OUTSIDE the season's crown families",
     mutate_crown_families),
    ("all crowns repainted the palette's BRIGHTEST green (dark-crown clause)",
     mutate_crown_bright),
    ("props rebuilt from the REJECTED first FLUX pass (negative control)",
     mutate_rejected_pass),
    ("tree_b trunk band painted light", mutate_trunk_light),
    ("tree_small trunk base moved off centre", mutate_trunk_offcentre),
    ("tree_b bottom row filled edge to edge", mutate_crown_on_ground),
    ("tree_a one off-palette pixel", mutate_off_palette),
    ("palette.json loses one lodge colour", mutate_palette_family),
]

CASES_BY_SEASON = {"autumn": CASES_AUTUMN, "spring": CASES_SPRING,
                   "summer": CASES_SUMMER}


def generator_declaration_drift(root: Path | None = None) -> None:
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
    print(f"MUTATION [generator] slot order drifted from the ticket table (in-process, {SEASON})")
    print("\n".join(lines) or proc.stderr[-400:])


def main(argv=None) -> int:
    global SEASON, INPUTS, RED_ROOT
    # Both spellings are accepted on purpose (SOU-283): the spring branch took the season
    # POSITIONALLY, the summer branch as `--season`, and either branch's invocation must keep
    # working from this one merged file.
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("season_pos", nargs="?", default=None,
                    help="season name (positional, the SOU-275 spelling)")
    ap.add_argument("--season", default=None, choices=sorted(SEASON_INPUTS),
                    help="which season's set to run the battery against (default: autumn)")
    args = ap.parse_args(argv)
    chosen = args.season or args.season_pos or "autumn"
    if chosen not in SEASON_INPUTS:
        sys.exit(f"unknown season {chosen!r}; known: {sorted(SEASON_INPUTS)}")
    SEASON = chosen
    INPUTS = SEASON_INPUTS[SEASON]
    RED_ROOT = Path(INPUTS["red_root"])

    # Cases that have to run IN-PROCESS (no file can express the mutation on its own).  Defined
    # here so both functions exist first.
    extra_cases = [("generator slot order drifted from the ticket table (in-process)",
                    generator_declaration_drift)]
    if SEASON == "spring":
        extra_cases.insert(0, ("palette EXTENSION colour drifted to a near-duplicate of a lodge "
                               "colour (in-process)", palette_distance_drift))

    print("=" * 90)
    print(f"RED PROOF battery for the {SEASON} set ({INPUTS['issue']}); scratch root {RED_ROOT}")
    print("=" * 90)
    print(f"SEASON: {SEASON}")
    print(f"CONTROL: the unmutated copy must PASS (a gate that fails everything proves nothing)")
    root = fresh()
    print(run(root))
    cases = CASES_BY_SEASON[SEASON]
    unproven = []
    holes = []
    for label, fn in cases:
        root = fresh()
        applied = fn(root)
        print("=" * 90)
        print(f"MUTATION {label!r}")
        if applied is False:
            unproven.append(label)
            print("  NOT PROVEN for this season: the artifact this control needs does not exist, "
                  "so the gate was NOT run and this case proves NOTHING.")
            continue
        output = run(root)
        if "RESULT: FAIL" not in output:
            holes.append(label)
        print(output)
    # The in-process cases mutate the MODULE, so the on-disk copy they are given stays clean and
    # re-running the gate on it would print a PASS that means nothing.  They print their own
    # gate output instead - which is why the label says so.
    for label, fn in extra_cases:
        print("=" * 90)
        print(f"MUTATION {label!r} - self-reporting: the gate output above/below IS this case's "
              f"result (the mutation exists only in memory, so there is no mutated copy to re-run)")
        fn(fresh())
    print("=" * 90)
    fresh()
    exercised = len(cases) - len(unproven)
    total = len(cases) + len(extra_cases)
    print(f"BATTERY SUMMARY ({SEASON}): {exercised}/{len(cases)} on-disk mutations exercised "
          f"({len(holes)} of them did NOT fire), {len(extra_cases)} in-process case(s), "
          f"{total} case(s) run.")
    print(f"Every mutation above must show RESULT: FAIL and exit!=0; a case with 'NO FAILURE "
          f"FIRED' or a PASS is a hole in the gate.")
    for label in holes:
        print(f"  HOLE: {label} - the gate PASSED a mutated set, so that assertion is decoration")
    if unproven:
        for label in unproven:
            print(f"  NOT PROVEN: {label}")
        print("  The unproven case(s) are non-synthetic CONTROLS whose artifact this season does "
              "not ship, NOT coverage of an assertion - every assertion still has a synthetic "
              "mutation aimed at it above.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
