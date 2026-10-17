#!/usr/bin/env python3
"""Check the office-01 v3 art set against its declared contract (SOU-158 AC2/AC3).

CPU-only, no network, no GPU, never writes to the tree it checks. Every number this
prints is measured from the bytes on disk - nothing is inferred from a manifest, because a
manifest is a cache, not evidence (SOU-142: a stale manifest claimed lattice slopes the PNG
no longer had).

What it proves, in the order the ticket asks for it:

  size      every asset is exactly the declared texture size (props: footprint width x
            (footprint height + lift) - the contract frontend/v3/src/placeholders.js draws
            and frontend/v3/src/office.js anchors bottom-centre);
  alpha     (AC2) props and the character sheet carry REAL alpha - both fully transparent
            and fully opaque pixels - and no magenta survives anywhere at any opacity, so
            there is no checkerboard and no fringe to find at 4x;
  palette   (AC3) every opaque pixel is one of the declared palette colours, checked as a
            set membership in RGB, and the per-asset colour list is printed for the ticket;
  anchors   prop content reaches the bottom rows of its canvas (the renderer anchors the
            footprint's bottom edge there, so an object that stops early would hover);
  sheet     the character sheet is 8x4 frames of 32x48 in the order placeholders.js
            registers (rows down/left/right/up, cols idle/walk1-4/work1-2/sleep), every
            frame is non-empty, and each animated frame differs from its row's idle frame;
  tileset   tiles/tileset.png is 8 tiles of 32px whose slot order equals the order of
            `tiles[]` in frontend/v3/maps/office-01.json - the map is the consumer, so its
            order is the authority, not ours;
  seam      for each floor/wall tile, the wrap-around edge step vs the mean interior step
            (a tile repeats, so column 31 sits next to column 0);
  derived    (SOU-187 AC1) every derived frame (4 facings x walk 1-4 + typing 1-2) carries no
            transparent pixel that its row's idle frame has content in, and no enclosed hole
            there either - the walk/typing frames used to be punched through by their own
            derivation, which the live floor showed as floor-coloured gaps in the legs;
  look       (SOU-187) the chibi head band is 30-50% of the 48px frame (target 40%) and the
            dominant garment colour of every facing is the signature orange hoodie.

Exit code 0 = all checks pass, 1 = at least one failure (each printed with the measurement),
2 = the checker itself could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter, deque

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gen_office01_art import (  # noqa: E402  (same directory)
    CATALOGUE,
    CHARACTER_EXTRA,
    CHARACTER_PALETTE_RGB,
    OUT,
    PALETTE_HEX,
    PALETTE_RGB,
    REPO,
    TILESET_ORDER,
    content_bottom_row,
)

EXIT_FAIL = 1
EXIT_ERROR = 2

# The v2.1.0 seasonal OUTDOOR sets (SOU-274 autumn, SOU-275 spring, SOU-276 summer).  One entry
# here is all a new season needs to get its own gate branch: `main_season` reads the set's own
# tables from `gen_season_art` and asserts them against the hand-typed ticket table for that
# season (TICKET_E_SEASONS), with the season's own crown rules (SEASON_CROWN_RULES).
SEASON_SETS = ("autumn", "spring")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def ok(self, text: str) -> None:
        self.lines.append(f"  ok    {text}")

    def fail(self, text: str) -> None:
        self.lines.append(f"  FAIL  {text}")
        self.failures.append(text)

    def note(self, text: str) -> None:
        self.lines.append(f"        {text}")


def unique_colours(img) -> list[tuple[int, int, int]]:
    """The RGB set of every pixel that is not fully transparent."""
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


def magenta_residue(img, palette=None) -> dict:
    """Count the pixels the keying contract should have removed.

    Two families, both fatal: the strong residue (a pink/magenta pixel anywhere in the
    image) and the wide fringe rule the de-fringe pass uses (r-g>20 AND b-g>10 AND g<150),
    which is what shows up as a 1px contour at 4x zoom.

    `palette` is the set's declared RGB palette, and passing it exempts those colours from the
    FRINGE count only.  Reason, measured on SOU-275: the fringe rule was written for palettes
    with no pink material in them, and spring's blossom pink satisfies it - #dd7aa6 is
    (221,122,166), so r-g=99, b-g=44, g=122<150 - which reported a legitimate pink flower as a
    keying remnant.  Every asset is QUANTISED to the declared palette, so a pixel that IS a
    declared colour cannot be keying residue; what the rule is for is a colour nobody declared.
    The strong rule stays strict for every set (no declared colour in any v3 palette matches it:
    checked across the lodge's 61, office-01's 32, autumn's 77 and spring's 76).
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    allowed = set(palette) if palette else set()
    strong = fringe = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            if r > 180 and b > 180 and g < 120:
                strong += 1
            if (r - g) > 20 and (b - g) > 10 and g < 150 and (r, g, b) not in allowed:
                fringe += 1
    return {"strong_magenta": strong, "fringe": fringe}


def alpha_profile(img) -> dict:
    img = img.convert("RGBA")
    alpha = img.getchannel("A")
    hist = alpha.histogram()
    return {
        "transparent": hist[0],
        "opaque": hist[255],
        "partial": sum(hist[1:255]),
        "has_real_alpha": hist[0] > 0 and hist[255] > 0,
    }


def palette_check(img, allowed=None) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    colours = unique_colours(img)
    allowed = set(allowed if allowed is not None else PALETTE_RGB)
    return colours, [c for c in colours if c not in allowed]


def base_row_coverage(img, rows=2) -> float:
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    hits = sum(1 for y in range(h - rows, h) for x in range(w) if px[x, y][3] > 0)
    return hits / (rows * w)


def seam_ratio(img) -> dict:
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()

    def col_step(a, b):
        return sum(abs(px[a, y][i] - px[b, y][i]) for y in range(h) for i in range(3)) / (h * 3)

    def row_step(a, b):
        return sum(abs(px[x, a][i] - px[x, b][i]) for x in range(w) for i in range(3)) / (w * 3)

    inner_c = [col_step(x, x + 1) for x in range(w - 1)]
    inner_r = [row_step(y, y + 1) for y in range(h - 1)]
    mean = lambda v: sum(v) / len(v) if v else 0.0  # noqa: E731
    return {
        # a flat/uniform tile has no interior variation: 0.0 is the honest answer, not None
        "ratio_x": round(col_step(w - 1, 0) / mean(inner_c), 2) if mean(inner_c) else 0.0,
        "ratio_y": round(row_step(h - 1, 0) / mean(inner_r), 2) if mean(inner_r) else 0.0,
    }


def hexs(rgb) -> str:
    return "#%02x%02x%02x" % rgb


def wrap_vs_max(img) -> dict:
    """Wrap-around edge step against the LARGEST interior step on the same axis.

    A periodic feature that lands on the tile boundary (a plank joint every 8px, a 1px cap edge
    every 32px) is not a seam: when the tile repeats, that transition is exactly what happens
    inside the tile too. The older ratio (wrap / MEAN interior step) flags such a tile even
    though it is perfectly periodic, so this compares the wrap step to the MAX interior step:
    <= 1.05 means the wrap is indistinguishable from an ordinary interior transition.
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


def luminance(img) -> list[float]:
    img = img.convert("RGB")
    return [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in img.getdata()]


def direction_and_contrast(img) -> dict:
    """Plank direction and the contrast budget of a tile, as numbers.

    mean |dx| / mean |dy|: with HORIZONTAL planks the variation is crossed by moving in y (the
    joints are lines of constant y), so mean |dy| must exceed mean |dx|. The 2026-10-16
    generated floor had mean |dx| 38.2 against mean |dy| 2.4 - planks running the wrong way -
    and a 209/255 luminance contrast that made the floor louder than every prop on it.
    """
    img = img.convert("RGB")
    w, h = img.size
    px = img.load()
    lum = luminance(img)
    at = lambda x, y: lum[(y % h) * w + (x % w)]  # noqa: E731
    dx = [abs(at(x, y) - at(x + 1, y)) for y in range(h) for x in range(w)]
    dy = [abs(at(x, y) - at(x, y + 1)) for y in range(h) for x in range(w)]
    return {
        "mean_dx": round(sum(dx) / len(dx), 2),
        "mean_dy": round(sum(dy) / len(dy), 2),
        "contrast": round(max(lum) - min(lum), 1),
        "horizontal_planks": (sum(dy) / len(dy)) > (sum(dx) / len(dx)),
    }


# The colours that make a vehicle read as the beige 90s van the owner signed off in the v2 run.
BEIGE_FAMILY = {"#e2d3b4", "#eadcb8", "#d8c8a0", "#c1a46b", "#a8894f", "#ac9968"}


def dominant_colour(img, exclude=("#14161f",)) -> tuple[str, int, float]:
    """The most common opaque colour, ignoring outline/hair, with its count and share.

    This is the identity measurement for the character sheet: the review found the four facings
    were four different people (down a dark jacket, left/right a blue hoodie, up a navy
    silhouette), which is exactly what "the dominant garment colour differs per row" detects.
    """
    img = img.convert("RGBA")
    counts: dict[tuple[int, int, int], int] = {}
    total = 0
    for r, g, b, a in img.getdata():
        if not a:
            continue
        total += 1
        counts[(r, g, b)] = counts.get((r, g, b), 0) + 1
    for rgb, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        h = hexs(rgb)
        if h not in exclude:
            return h, n, round(n / total, 3)
    return "none", 0, 0.0


# ---------------------------------------------------------------------------------------
# SOU-187 checks. AC1 is a NEW gate: the derived walk/typing frames must carry no interior
# alpha hole that the idle frame does not have. The old derivation cleared a rectangular
# region and composited the shifted patch over it, so the vacated band stayed transparent and
# the live floor showed through the legs and torso. The generator now keeps the pre-shift
# content as an underlay, and these two measurements are what make "no holes" checkable
# instead of asserted.
# ---------------------------------------------------------------------------------------
ORANGE_FAMILY = {h for _n, h in CHARACTER_EXTRA}
# Everything a row can be made of that is NOT the head: the orange jumper and the denim jeans.
# The light neutrals (#f4ead2 cream, #c3cad9 steel) are deliberately NOT here: the model paints
# the FACE with the same cream, so counting them as clothing made the face row read as
# garment-dominant and collapsed the measured head band (measured on the first pass of this
# check: the profile facings reported 4% where the pixels show a 13-row head).
GARMENT_FAMILY = ORANGE_FAMILY | {"#44618a", "#505a70"}
CHAR_FRAME = (32, 48)


def frame_alpha(frame) -> list[list[int]]:
    a = frame.convert("RGBA").getchannel("A")
    w, h = frame.size
    return [[1 if a.getpixel((x, y)) > 0 else 0 for x in range(w)] for y in range(h)]


def enclosed_holes(mask: list[list[int]]) -> int:
    """Transparent pixels NOT reachable from the frame border - i.e. holes inside the sprite."""
    h, w = len(mask), len(mask[0])
    seen = [[0] * w for _ in range(h)]
    queue = deque()
    for x in range(w):
        for y in (0, h - 1):
            if not mask[y][x] and not seen[y][x]:
                seen[y][x] = 1
                queue.append((x, y))
    for y in range(h):
        for x in (0, w - 1):
            if not mask[y][x] and not seen[y][x]:
                seen[y][x] = 1
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not mask[ny][nx] and not seen[ny][nx]:
                seen[ny][nx] = 1
                queue.append((nx, ny))
    return sum(1 for y in range(h) for x in range(w) if not mask[y][x] and not seen[y][x])


def derived_frame_integrity(sheet, report: Report, quiet: bool) -> dict:
    """AC1: no derived frame (cols 1-6) may carry an interior alpha hole.

    Two numbers per frame, because they answer different questions and the ticket names both:
      * `new_transparent` - pixels transparent in the derived frame but OPAQUE in that row's
        idle frame. This is the strong form of the derivation's own invariant (it is 0) and it
        is what the floor-coloured gaps measured as (24 frames, 5-36 px each, on the SOU-158
        sheet).
      * `enclosed` - transparent pixels not reachable from the frame border at all, i.e. a hole
        the floor is visible through. Reported as a TOTAL, not as "holes where idle had
        content": the first version of this check only counted a hole when the idle frame had
        content in that pixel, so it passed a real 1px hole in `up`/typing-1 that the shift
        created by *sealing* a 1px channel the base legitimately had (measured 2026-10-16). A
        hole is a hole wherever it comes from, so the gate counts them all.
    Column 7 (sleep) is excluded here because it is a different POSE, not a derivation of the
    idle base, so its silhouette is legitimately not comparable to the standing idle frame -
    `sheet_hole_free` covers it on the absolute measure instead.
    """
    fw, fh = CHAR_FRAME
    summary = {"frames": 0, "new_transparent": 0, "enclosed": 0}
    for row, facing in enumerate(("down", "left", "right", "up")):
        idle = frame_alpha(sheet.crop((0, row * fh, fw, (row + 1) * fh)))
        for col in range(1, 7):
            frame = sheet.crop((col * fw, row * fh, (col + 1) * fw, (row + 1) * fh))
            mask = frame_alpha(frame)
            new_transparent = sum(1 for y in range(fh) for x in range(fw)
                                  if idle[y][x] and not mask[y][x])
            enclosed = enclosed_holes(mask)
            summary["frames"] += 1
            summary["new_transparent"] += new_transparent
            summary["enclosed"] += enclosed
            if new_transparent or enclosed:
                report.fail(f"AC1 {facing}/col{col}: {new_transparent} pixel(s) transparent that "
                            f"the idle frame has content in, {enclosed} enclosed interior "
                            f"hole pixel(s) - the derivation vacated pixels instead of keeping "
                            f"the source as an underlay, or sealed a channel the base had")
            if not quiet:
                report.note(f"{facing}/col{col} ({('walk', 'walk', 'walk', 'walk', 'work',
                                                    'work')[col - 1]}): new_transparent="
                            f"{new_transparent}, enclosed_holes={enclosed}")
    if not summary["new_transparent"] and not summary["enclosed"]:
        report.ok(f"AC1 derived frame integrity: {summary['frames']} derived frames "
                  f"(4 facings x walk 1-4 + typing 1-2) carry ZERO transparent pixels that their "
                  f"row's idle frame has content in AND zero enclosed interior holes - no "
                  f"floor-coloured gaps inside legs or torso")
    return summary


def sheet_hole_free(sheet, report: Report, quiet: bool) -> dict:
    """AC1, absolute form: NO frame of the sheet encloses a single transparent pixel.

    Covers all 32 frames including idle and the four sleep frames, which `derived_frame_integrity`
    cannot judge because it compares each frame against its row's base. The renderer draws every
    frame over the floor, so an enclosed pixel is the floor showing through the body in all of
    them; the count has to be 0, not merely "no worse than idle".
    """
    fw, fh = CHAR_FRAME
    worst = {"frames": 0, "enclosed": 0, "worst_frame": None}
    names = ("idle", "walk1", "walk2", "walk3", "walk4", "work1", "work2", "sleep")
    for row, facing in enumerate(("down", "left", "right", "up")):
        for col in range(8):
            frame = sheet.crop((col * fw, row * fh, (col + 1) * fw, (row + 1) * fh))
            n = enclosed_holes(frame_alpha(frame))
            worst["frames"] += 1
            worst["enclosed"] += n
            if n and worst["worst_frame"] is None:
                worst["worst_frame"] = f"{facing}/{names[col]}"
            if not quiet and n:
                report.note(f"{facing}/{names[col]}: {n} enclosed transparent pixel(s)")
    if worst["enclosed"]:
        report.fail(f"AC1 sheet hole-free: {worst['enclosed']} enclosed transparent pixel(s) "
                    f"across {worst['frames']} frames (first: {worst['worst_frame']}) - the floor "
                    f"shows through the body there")
    else:
        report.ok(f"AC1 sheet hole-free: all {worst['frames']} frames of character/office-01.png "
                  f"carry zero enclosed transparent pixels")
    return worst


def _reachable(mask, sx, sy) -> bool:
    """Is (sx, sy) an empty pixel connected to the frame border?"""
    h, w = len(mask), len(mask[0])
    seen = [[0] * w for _ in range(h)]
    queue = deque([(sx, sy)])
    seen[sy][sx] = 1
    while queue:
        x, y = queue.popleft()
        if x in (0, w - 1) or y in (0, h - 1):
            return True
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= nx < w and 0 <= ny < h and not mask[ny][nx] and not seen[ny][nx]:
                seen[ny][nx] = 1
                queue.append((nx, ny))
    return False


def head_band_fraction(frame) -> tuple[float, int, int]:
    """Head height as a fraction of the 48px frame, measured from the declared ramp.

    A chibi's head is the top band that is NOT clothing. So: walk the rows from the top of the
    content and stop at the first row whose opaque pixels are mostly garment colours (the
    orange jumper reaching the shoulders). That row count / 48 is the head fraction the ticket
    asks to be ~40%. Deliberately measured on colours from the declared palette rather than on
    a shape model - it is a coarse measure, but it is reproducible and it fails loudly when the
    model returns a realistic 5-head figure (measured 2026-10-15: that is what it did).

    A row only counts as "garment-dominant" if it is SUBSTANTIAL (at least 40% of the widest
    row). Without that guard a one-pixel speck of denim at the top of the frame makes row 0 read
    as 100% garment and collapses the measured head band to 0% - which is exactly what the first
    run of this check reported on the profile facings, where a 6px stray island sat above the
    head (`drop_stray_islands` now removes it at the source; this guard is the second belt).
    """
    frame = frame.convert("RGBA")
    w, h = frame.size
    px = frame.load()
    top = None
    widths = []
    for y in range(h):
        n = sum(1 for x in range(w) if px[x, y][3])
        widths.append(n)
        if top is None and n:
            top = y
    if top is None:
        return 0.0, 0, 0
    substantial = max(3, int(0.4 * max(widths)))
    first_garment_row = h
    for y in range(top, h):
        if widths[y] < substantial:
            continue
        opaque = garment = 0
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            opaque += 1
            if hexs((r, g, b)) in GARMENT_FAMILY:
                garment += 1
        if opaque and garment / opaque >= 0.5:
            first_garment_row = y
            break
    return round((first_garment_row - top) / h, 3), top, first_garment_row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", default="office-01",
                    choices=("office-01", "lodge", "serverroom", "factory",
                             "mystery", "tokenden", "career", "writer", "garage", "polish")
                    + SEASON_SETS,
                    help="which v3 art set to check (default: office-01)")
    ap.add_argument("--root", default=None,
                    help="art set root (default: the set's own generated directory)")
    ap.add_argument("--map", default=None, help="v3 map whose tileset order is the authority")
    ap.add_argument("--shipped-ref", default="origin/master",
                    help="git ref the SHIPPED originals must still match (--set polish only): the "
                         "'never overwrite shipped files in place' AC is asserted against it")
    ap.add_argument("--quiet", action="store_true", help="only print failures and the summary")
    args = ap.parse_args()

    if args.set == "lodge":
        return main_lodge(args)
    if args.set in SEASON_SETS:
        return main_season(args, args.set)
    if args.set == "garage":
        return main_garage(args)
    if args.set == "polish":
        return main_polish(args)
    if args.set in ("serverroom", "factory"):
        return main_set_b(args, args.set)
    if args.set in TICKET_C:
        return main_set_c(args, args.set)
    if args.root is None:
        args.root = str(OUT)

    root = os.path.abspath(args.root)
    # The office map is a repo path, not an art-set path: resolve it from the repo root the
    # generator declares. Three `dirname`s off the art set landed on `assets/`, so the map was
    # never found and the tileset-order check silently no-opped (found and fixed 2026-10-16
    # during the SOU-158 review pass - a check that can pass by not running is not a check).
    repo = str(REPO)
    map_path = args.map or os.path.join(repo, "frontend", "v3", "maps", "office-01.json")
    report = Report()

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    palette_meta = os.path.join(root, "palette.json")
    if os.path.exists(palette_meta):
        declared = json.load(open(palette_meta))["hex"]
        if declared != PALETTE_HEX:
            report.fail(f"palette.json does not match the generator's palette "
                        f"({len(declared)} vs {len(PALETTE_HEX)} colours)")
        else:
            report.ok(f"palette.json declares {len(declared)} colours, identical to the generator")
        ext = json.load(open(palette_meta)).get("character_extension")
        want_ext = [h for _n, h in CHARACTER_EXTRA]
        if not ext:
            report.fail("palette.json does not declare character_extension - the character's "
                        "orange would be undeclared")
        elif [c["hex"] for c in ext["named"]] != want_ext:
            report.fail(f"palette.json character_extension {[c['hex'] for c in ext['named']]} != "
                        f"the generator's {want_ext}")
        else:
            report.ok(f"palette.json declares the character extension {want_ext} (character sheet "
                      f"only, shared palette frozen at {len(PALETTE_HEX)} colours)")
    else:
        report.fail("palette.json missing - the palette is not declared anywhere")

    print("=" * 96)
    print(f"office-01 v3 art set check   root={root}")
    print(f"palette: {len(PALETTE_HEX)} declared colours, {', '.join(PALETTE_HEX[:6])}, ...")
    print("=" * 96)

    # ---- props -----------------------------------------------------------------------
    print("\nPROPS (size = declared texture px; base-rows = content coverage of the bottom 2 rows)")
    print(f"{'asset':10s} {'size':>9s} {'footprint':>10s} {'colours':>7s} {'transp':>7s} {'opaque':>7s} "
          f"{'strong':>6s} {'fringe':>6s} {'base':>5s}  result")
    for asset in CATALOGUE:
        if asset["kind"] != "prop":
            continue
        path = os.path.join(root, "props", f"{asset['id']}.png")
        if not os.path.exists(path):
            report.fail(f"{asset['id']}: no props/{asset['id']}.png")
            continue
        img = Image.open(path)
        want_w, want_h = asset["texture_px"]
        got = img.size
        colours, off = palette_check(img)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        cov = base_row_coverage(img)
        bottom = content_bottom_row(img)
        problems = []
        if got[0] != want_w:
            problems.append(f"width {got[0]} != footprint width {want_w} (the renderer draws the "
                            f"prop at the footprint width, so the art would be scaled or mis-anchored)")
        if got[1] < want_h:
            problems.append(f"height {got[1]} < declared {want_h}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"]:
            problems.append("no real alpha")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        if bottom != got[1] - 1:
            problems.append(f"content stops at row {bottom} of {got[1]} - the footprint's bottom "
                            f"edge is where the renderer anchors, so the prop would float")
        if asset.get("flat") and cov < 0.5:
            problems.append(f"flat decal covers only {cov:.2f} of the bottom rows")
        height_note = "" if got[1] == want_h else f" (+{got[1] - want_h}px over the declared box)"
        print(f"{asset['id']:10s} {f'{got[0]}x{got[1]}':>9s} "
              f"{'x'.join(str(v) for v in asset['footprint_tiles'])+' tiles':>10s} "
              f"{len(colours):>7d} {alpha['transparent']:>7d} {alpha['opaque']:>7d} "
              f"{resid['strong_magenta']:>6d} {resid['fringe']:>6d} {cov:>5.2f}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{asset['id']}: " + "; ".join(problems))
        else:
            report.ok(f"{asset['id']}: {got[0]}x{got[1]}{height_note}, {len(colours)} palette colours, "
                      f"real alpha, no magenta, content reaches the canvas bottom (row {bottom})")
        if not args.quiet:
            report.note("colours: " + " ".join(hexs(c) for c in colours))

    # ---- tiles ----------------------------------------------------------------------
    print("\nTILES (32x32, full-bleed - opacity expected, no alpha)")
    for name in ("floor", "floor_b", "garage_floor", "wall", "wall_cap"):
        path = os.path.join(root, "tiles", f"{name}.png")
        if not os.path.exists(path):
            report.fail(f"tiles/{name}.png missing")
            continue
        img = Image.open(path)
        colours, off = palette_check(img)
        alpha = alpha_profile(img)
        seam = seam_ratio(img)
        wrap = wrap_vs_max(img)
        problems = []
        if img.size != (32, 32):
            problems.append(f"size {img.size} != (32, 32)")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if alpha["transparent"]:
            problems.append(f"{alpha['transparent']} transparent pixels (a tile is full-bleed)")
        if wrap["wrap_vs_max_x"] > 1.05 or wrap["wrap_vs_max_y"] > 1.05:
            problems.append(f"wrap step is larger than any interior step "
                            f"(x {wrap['wrap_vs_max_x']}, y {wrap['wrap_vs_max_y']} vs limit 1.05) "
                            f"- that is a real seam")
        if not args.quiet:
            report.note(f"{name}: seam {seam} (wrap/mean), wrap/MAX interior x="
                        f"{wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']} - a periodic feature "
                        f"landing on the boundary is not a seam")
        print(f"{name:14s} {f'{img.size[0]}x{img.size[1]}':>7s} colours={len(colours):>3d} "
              f"seam x={seam['ratio_x']:>5} y={seam['ratio_y']:>5} "
              f"wrap/max x={wrap['wrap_vs_max_x']:>5} y={wrap['wrap_vs_max_y']:>5}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"tiles/{name}: " + "; ".join(problems))
        else:
            report.ok(f"tiles/{name}: 32x32, {len(colours)} palette colours, opaque, "
                      f"seam x={seam['ratio_x']} y={seam['ratio_y']} "
                      f"(wrap/max x={wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']})")

    # ---- tileset sheet order vs the map (the consumer is the authority) --------------
    print("\nTILESET SHEET vs office-01.json")
    sheet_path = os.path.join(root, "tiles", "tileset.png")
    want_order = [name for name, _src in TILESET_ORDER]
    if os.path.exists(sheet_path):
        sheet = Image.open(sheet_path)
        if sheet.size != (32 * len(want_order), 32):
            report.fail(f"tileset.png is {sheet.size}, expected {32 * len(want_order)}x32")
        else:
            report.ok(f"tileset.png is {sheet.size[0]}x{sheet.size[1]} = "
                      f"{len(want_order)} tiles of 32px")
        colours, off = palette_check(sheet)
        if off:
            report.fail(f"tileset.png has {len(off)} off-palette colours e.g. {hexs(off[0])}")
        else:
            report.ok(f"tileset.png uses {len(colours)} palette colours only")
        if os.path.exists(map_path):
            tiles = json.load(open(map_path))["tilesets"][0]["tiles"]
            map_order = [t["type"] for t in sorted(tiles, key=lambda t: t["id"])]
            if map_order != want_order:
                report.fail(f"tileset slot order {want_order} != map order {map_order}")
            else:
                report.ok(f"slot order matches the map: {', '.join(map_order)}")
                report.note("drop-in = swap the tileset image path, no map edit")
        else:
            report.note(f"map not found at {map_path} - order not cross-checked")
    else:
        report.fail("tiles/tileset.png missing")

    # ---- character sheet ------------------------------------------------------------
    print("\nCHARACTER SHEET")
    char_path = os.path.join(root, "character", "office-01.png")
    if os.path.exists(char_path):
        sheet = Image.open(char_path)
        fw, fh, cols, rows = 32, 48, 8, 4
        if sheet.size != (fw * cols, fh * rows):
            report.fail(f"character sheet is {sheet.size}, expected {fw * cols}x{fh * rows}")
        else:
            report.ok(f"character sheet is {sheet.size[0]}x{sheet.size[1]} = {cols}x{rows} frames of {fw}x{fh}")
        colours, off = palette_check(sheet, allowed=CHARACTER_PALETTE_RGB)
        if off:
            report.fail(f"character sheet has {len(off)} colours outside the shared palette + its "
                        f"declared character extension, e.g. {hexs(off[0])}")
        else:
            report.ok(f"character sheet uses {len(colours)} colours, all from the {len(PALETTE_HEX)} "
                      f"shared colours + the {len(CHARACTER_EXTRA)} declared character-only orange "
                      f"entries ({', '.join(h for _n, h in CHARACTER_EXTRA)})")
        resid = magenta_residue(sheet)
        if resid["strong_magenta"] or resid["fringe"]:
            report.fail(f"character sheet magenta residue {resid}")
        else:
            report.ok("character sheet has no magenta residue")
        empty, identical = [], []
        for row in range(rows):
            idle = sheet.crop((0, row * fh, fw, (row + 1) * fh)).convert("RGBA")
            idle_bytes = idle.tobytes()
            for col in range(cols):
                frame = sheet.crop((col * fw, row * fh, (col + 1) * fw, (row + 1) * fh)).convert("RGBA")
                if frame.getchannel("A").getbbox() is None:
                    empty.append((row, col))
                elif col and frame.tobytes() == idle_bytes:
                    identical.append((row, col))
        if empty:
            report.fail(f"empty frames at (row, col) {empty}")
        else:
            report.ok("all 32 frames carry content")
        if identical:
            report.fail(f"frames identical to their row's idle frame at (row, col) {identical} "
                        f"- the derivation did not animate them")
        else:
            report.ok("every walk/work/sleep frame differs from its row's idle frame")
        report.note(f"colours: {' '.join(hexs(c) for c in colours)}")
    else:
        report.fail("character/office-01.png missing")

    # ---- SOU-187 AC1: derived frames must not carry holes the idle frame does not have ------
    print("\nDERIVED FRAME INTEGRITY (SOU-187 AC1 - derived walk/typing frames vs their row's idle frame)")
    char_path = os.path.join(root, "character", "office-01.png")
    if os.path.exists(char_path):
        derived_frame_integrity(Image.open(char_path), report, args.quiet)
        sheet_hole_free(Image.open(char_path), report, args.quiet)
    else:
        report.fail("character/office-01.png missing - cannot check derived frame integrity")

    # ---- SOU-187 look: the signature orange and the chibi head proportion --------------------
    print("\nCHARACTER LOOK (SOU-187 - signature colour + chibi head ~40% of the 48px frame)")
    if os.path.exists(char_path):
        sheet = Image.open(char_path)
        for row, facing in enumerate(("down", "left", "right", "up")):
            idle = sheet.crop((0, row * 48, 32, row * 48 + 48))
            frac, top, garment_row = head_band_fraction(idle)
            dom, n, share = dominant_colour(idle, exclude=("#14161f", "#2a2f3e"))
            problems = []
            if not 0.30 <= frac <= 0.50:
                problems.append(f"head band is {frac:.0%} of the frame, outside the 30-50% band "
                                f"the ticket asks for (~40%)")
            if dom not in ORANGE_FAMILY:
                problems.append(f"dominant garment colour is {dom} ({share:.0%}), not the "
                                f"signature orange family {sorted(ORANGE_FAMILY)}")
            print(f"  {facing:6s} head={frac:.0%} (rows {top}-{garment_row}) "
                  f"dominant={dom} ({share:.0%} of {n} px)  "
                  f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
            if problems:
                report.fail(f"look {facing}: " + "; ".join(problems))
            else:
                report.ok(f"look {facing}: head {frac:.0%} of 48px (chibi), dominant garment "
                          f"{dom} ({share:.0%}) = the orange hoodie")

    # ---- the 2026-10-16 review redo list, as machine checks --------------------------
    # Claude Code reviewed the 15:27 build and filed three defects. Each one is asserted here
    # against the bytes with the measurement printed, so "fixed" is checkable rather than
    # claimed. Where a threshold is arbitrary it is stated next to the check.
    print("\nREVIEW FIXES (Claude Code redo list, 2026-10-16)")
    char_path = os.path.join(root, "character", "office-01.png")
    if os.path.exists(char_path):
        sheet = Image.open(char_path)
        doms = {}
        for row, facing in enumerate(("down", "left", "right", "up")):
            frame = sheet.crop((0, row * 48, 32, row * 48 + 48))
            doms[facing] = dominant_colour(frame)
        table = ", ".join(f"{f}={h} ({s:.0%})" for f, (h, _n, s) in doms.items())
        print(f"  identity   {table}")
        if len({h for h, _n, _s in doms.values()}) == 1:
            report.ok("1. character identity: the dominant garment colour is identical in all four "
                      f"facings ({table})")
        else:
            report.fail("1. character identity: the facings disagree on the dominant garment "
                        f"colour ({table}) - that is the 'three different people' defect")

    van_path = os.path.join(root, "props", "van.png")
    if os.path.exists(van_path):
        van = Image.open(van_path)
        h, n, share = dominant_colour(van)
        print(f"  van        dominant={h} {share:.0%} of {n} px, canvas={van.size[0]}x{van.size[1]}")
        if h in BEIGE_FAMILY:
            report.ok(f"2. van reads as the beige 90s van: dominant colour {h} ({share:.0%}), "
                      f"no silver/steel body")
        else:
            report.fail(f"2. van: dominant colour is {h} ({share:.0%}), not in the declared beige "
                        f"family {sorted(BEIGE_FAMILY)}")

    for name, max_colours, max_contrast in (("floor", 6, 96), ("floor_b", 6, 96),
                                            ("wall_cap", 3, 48)):
        path = os.path.join(root, "tiles", f"{name}.png")
        if not os.path.exists(path):
            continue
        img = Image.open(path)
        dc = direction_and_contrast(img)
        colours, _off = palette_check(img)
        problems = []
        if len(colours) > max_colours:
            problems.append(f"{len(colours)} colours > {max_colours}")
        if dc["contrast"] > max_contrast:
            problems.append(f"luminance contrast {dc['contrast']} > {max_contrast}")
        if name.startswith("floor") and not dc["horizontal_planks"]:
            problems.append(f"planks not horizontal (mean|dx| {dc['mean_dx']} >= "
                            f"mean|dy| {dc['mean_dy']})")
        print(f"  {name:10s} colours={len(colours)} contrast={dc['contrast']} "
              f"mean|dx|={dc['mean_dx']} mean|dy|={dc['mean_dy']}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"3. {name}: " + "; ".join(problems))
        else:
            report.ok(f"3. {name}: calm - {len(colours)} colours, luminance contrast "
                      f"{dc['contrast']}/255 (limits {max_colours}/{max_contrast})"
                      + (f", planks horizontal (mean|dx| {dc['mean_dx']} < mean|dy| "
                         f"{dc['mean_dy']})" if name.startswith("floor") else ""))

    # 4. the walk stride, measured rather than described. The review ACCEPTED the slide for M1
    #    ("don't polish it, flag it"), so this prints a FLAG and never fails the gate.
    if os.path.exists(char_path):
        sheet = Image.open(char_path)
        for row, facing in enumerate(("down", "left", "right", "up")):
            xs = []
            for col in (1, 2, 3, 4):
                frame = sheet.crop((col * 32, row * 48, (col + 1) * 32, (row + 1) * 48))
                bbox = frame.getchannel("A").getbbox()
                xs.append((bbox[0], bbox[2]) if bbox else None)
            if len(set(xs)) == 1:
                print(f"  FLAG       walk stride: {facing} frames share the same lower-body x-range "
                      f"{xs[0]} - no in-frame step travel, the sprite slides (accepted for M1)")
                report.note(f"walk slide (flag, accepted for M1): {facing} x-ranges all {xs[0]}")

    # ---- summary --------------------------------------------------------------------
    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (this section is the AC3/AC2 record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print("RESULT: PASS - every asset is the declared size, on the declared palette, with real "
          "alpha and no magenta residue; the sheet and the tileset match the renderer's contract.")
    return 0


# ======================================================================================
# lodge set (SOU-198) - the second v3 set, checked by the SAME gate
#
# Same contract, same measurements, a per-set table. The office-01 path above is untouched:
# `--set lodge` is opt-in, so a lodge check can never change what an office check asserts.
#
# What is set-specific:
#   * the palette (44 lodge colours, night palette - `--set` picks the declared one);
#   * the tiles: 16 slots, each declared as material/decal and with the AXES it must repeat
#     along. A floor must tile both ways; a runner strip is laid along one axis; a step, a mat
#     and a drift edge are objects and are deliberately not periodic. Measuring an object for
#     seamlessness would be measuring the wrong thing and skipping it silently would hide a real
#     seam in a material, so the class is declared per slot and printed;
#   * the props: 12 entries including a 4-frame walk sheet (128x32), so the prop section has a
#     sheet branch the office set does not need;
#   * no character sheet (the lodge set ships no character; office-01's is SOU-187's).
# ======================================================================================
LODGE_SEAM_LIMIT = 1.05


# ---------------------------------------------------------------------------------------
# Set B (SOU-199): the server room and the factory.
#
# The size/frame table below is typed from the TICKET, not from the generator: a gate that only
# compares an asset to the generator's own declaration cannot catch a declaration that drifted
# from the ticket (the SOU-158 lesson - the gate passed while the published px table was stale).
# Every entry is asserted twice: shipped file vs ticket, and generator declaration vs ticket.
# ---------------------------------------------------------------------------------------
TICKET_B = {
    "serverroom": {
        # id: (frame_w, frame_h, frames, footprint_w, footprint_h)
        "gear_shelf_a": (64, 112, 1, 64, 32),
        "gear_shelf_b": (64, 112, 1, 64, 32),
        "workbench": (64, 80, 1, 64, 64),
        "ups_stack": (32, 64, 1, 32, 32),
        "cable_coil": (32, 32, 1, 32, 32),
        "stool": (32, 40, 1, 32, 32),
        "network_cabinet": (32, 80, 1, 32, 32),
    },
    "factory": {
        "belt_h": (32, 32, 4, 32, 32),
        "belt_v": (32, 32, 4, 32, 32),
        "belt_corner": (32, 32, 4, 32, 32),
        "assembler": (64, 80, 2, 64, 64),
        "inserter": (32, 48, 2, 32, 32),
        # the ticket says "footprint n/a" for the item that rides the belt; the ruling recorded
        # on the ticket is a 16x16 footprint so the crate is still measured like every other prop
        # (content on the bottom row, exact size, palette).
        "item_box": (16, 16, 1, 16, 16),
        "ore_pile": (32, 32, 1, 32, 32),
        "control_desk": (64, 64, 1, 64, 32),
    },
}

# A flat decal (a coil of cable, an ore pile) carries less of its lowest rows than a box does, and
# a RING-shaped decal legitimately carries little: the first draft of this gate used a 0.35 floor
# and FAILED cable_coil at 0.33 - a threshold invented by the gate, not by the ticket, whose AC is
# "feet/base on the bottom row of pixels" and is asserted separately (content_bottom_row == h-1).
# Re-rolling correct art to satisfy a made-up number is how a gate starts encoding taste, so the
# number is REPORTED for every flat asset and only a floor that means "the decal is not resting on
# its base at all" is asserted.
FLAT_BASE_COVERAGE_MIN = 0.10


def main_set_b(args, setname: str) -> int:
    import gen_roomart_b as B

    root = os.path.abspath(args.root or str(B.out_dir(setname)))
    repo = str(REPO)
    map_path = args.map or os.path.join(repo, "frontend", "v3", "maps", "rooms",
                                       B.SETS[setname]["map"])
    report = Report()

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    pal_path = os.path.join(root, "palette.json")
    if os.path.exists(pal_path):
        declared = json.load(open(pal_path))
        if declared.get("hex") != B.PALETTE_HEX:
            report.fail(f"palette.json does not match the shared lodge palette "
                        f"({len(declared.get('hex', []))} vs {len(B.PALETTE_HEX)} colours)")
        else:
            report.ok(f"palette.json declares {len(B.PALETTE_HEX)} colours, identical to the "
                      f"lodge palette ({declared.get('name')}, shared_with="
                      f"{declared.get('shared_with')!r})")
    else:
        report.fail("palette.json missing - the palette is not declared anywhere")

    print("=" * 96)
    print(f"{setname} v3 art set B check (SOU-199)   root={root}")
    print(f"palette: {len(B.PALETTE_HEX)} declared colours, {', '.join(B.PALETTE_HEX[:6])}, ...")
    print("=" * 96)

    # ---- declaration vs the ticket ------------------------------------------------------
    ticket = TICKET_B[setname]
    drift = []
    gen_ids = [a["id"] for a in B.ASSETS[setname]]
    if set(gen_ids) != set(ticket):
        report.fail(f"the generator's asset list {sorted(gen_ids)} != the ticket's "
                    f"{sorted(ticket)}")
    else:
        report.ok(f"{len(gen_ids)} assets, the same set the SOU-199 table names: "
                  f"{', '.join(gen_ids)}")
    for asset in B.ASSETS[setname]:
        want = ticket.get(asset["id"])
        if not want:
            continue
        tw, th, frames, fpw, fph = want
        if tuple(asset["texture_px"]) != (tw, th) or asset["frames"] != frames \
                or tuple(asset["footprint_px"]) != (fpw, fph):
            msg = (f"{asset['id']}: generator declares {tuple(asset['texture_px'])} x"
                   f"{asset['frames']} fp {tuple(asset['footprint_px'])} but the ticket says "
                   f"{(tw, th)} x{frames} fp {(fpw, fph)}")
            drift.append(msg)
            report.fail(msg)
        if asset["lift_px"] != th - fph:
            msg = (f"{asset['id']}: lift {asset['lift_px']} != texture_h - footprint_h "
                   f"({th} - {fph} = {th - fph})")
            drift.append(msg)
            report.fail(msg)
    if drift:
        report.fail("the generator's declaration has drifted from the SOU-199 table")
    else:
        report.ok("every size, frame count, footprint and lift agrees with the SOU-199 table")

    # ---- props and sheets ---------------------------------------------------------------
    print("\nASSETS (shipped px vs the TICKET table; frames checked as a strip)")
    print(f"{'asset':16s} {'shipped':>11s} {'ticket':>11s} {'frames':>6s} {'colours':>7s} "
          f"{'transp':>7s} {'fringe':>6s} {'base':>5s}  result")
    results = {}
    for asset in B.ASSETS[setname]:
        aid = asset["id"]
        tw, th, frames, fpw, fph = ticket[aid]
        path = os.path.join(root, "props", f"{aid}.png")
        if not os.path.exists(path):
            report.fail(f"props/{aid}.png missing")
            continue
        img = Image.open(path).convert("RGBA")
        want_size = (tw * frames, th)
        got = img.size
        colours, off = palette_check(img, allowed=B.PALETTE_RGB)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        parts = [img.crop((k * tw, 0, (k + 1) * tw, th)).convert("RGBA") for k in range(frames)]
        bottoms = [content_bottom_row(f) for f in parts]
        covs = [base_row_coverage(f) for f in parts]
        framing = [sum(1 for p in f.getchannel("A").getdata() if p == 0) for f in parts]
        edges = {}
        for edge in ("left", "right", "top", "bottom"):
            edges[edge] = []
            for f in parts:
                px = f.load()
                if edge == "left":
                    edges[edge].append(any(px[0, y][3] for y in range(th)))
                elif edge == "right":
                    edges[edge].append(any(px[tw - 1, y][3] for y in range(th)))
                elif edge == "top":
                    edges[edge].append(any(px[x, 0][3] for x in range(tw)))
                else:
                    edges[edge].append(any(px[x, th - 1][3] for x in range(tw)))
        problems = []
        if got != want_size:
            problems.append(f"size {got} != ticket {want_size}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"] and not asset["full_bleed"]:
            problems.append("no real alpha")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        empty = [k for k, f in enumerate(parts) if f.getchannel("A").getbbox() is None]
        same = [k for k in range(1, frames) if parts[k].tobytes() == parts[0].tobytes()]
        if empty:
            problems.append(f"empty frames {empty}")
        if frames > 1 and same:
            problems.append(f"frames {same} identical to frame 0 - the derivation did not "
                            f"animate them")
        bad_bottom = [k for k, b in enumerate(bottoms) if b != th - 1]
        if bad_bottom:
            problems.append(f"frames {bad_bottom} do not reach the bottom row (bottoms {bottoms}) "
                            f"- the renderer anchors there, so the prop would float")
        if asset["full_bleed"]:
            framed = [k for k, t in enumerate(framing) if t]
            if framed:
                problems.append(f"full-bleed tile has transparent pixels in frames {framed} "
                                f"({framing}) - neighbouring belt sections would show the floor")
        for edge in (asset.get("connects") or []):
            missing = [k for k, ok in enumerate(edges[edge]) if not ok]
            if missing:
                problems.append(f"declared connection edge {edge!r} carries no content in frames "
                                f"{missing} - the neighbouring belt section would not join")
        if asset["flat"] and min(covs) < FLAT_BASE_COVERAGE_MIN:
            problems.append(f"flat decal covers only {min(covs):.2f} of its lowest rows "
                            f"(floor {FLAT_BASE_COVERAGE_MIN})")
        print(f"{aid:16s} {f'{got[0]}x{got[1]}':>11s} {f'{want_size[0]}x{want_size[1]}':>11s} "
              f"{frames:>6d} {len(colours):>7d} {alpha['transparent']:>7d} "
              f"{resid['strong_magenta']:>6d} {min(covs):>5.2f}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))
        else:
            report.ok(f"{aid}: {got[0]}x{got[1]} = {frames} frame(s) of {tw}x{th}, {len(colours)} "
                      f"palette colours, real alpha, no magenta, base on the bottom row "
                      f"({bottoms})")
        if not args.quiet:
            report.note(f"{aid}: colours {' '.join(hexs(c) for c in colours)}")
        results[aid] = {"size": list(got), "frames": frames, "bottoms": bottoms,
                        "coverage": [round(c, 3) for c in covs], "transparent": framing}

    # ---- tile-repeat measurement for the belts (reported, not asserted) -------------------
    belts = [a for a in B.ASSETS[setname] if a["id"].startswith("belt_")]
    if belts:
        print("\nBELT TILE-REPEAT (wrap edge step vs the largest interior step on each axis; "
              "reported - a belt's tread period comes from the model, not from a declared "
              "lattice, so this is a quality number for the review, not an AC)")
        for asset in belts:
            path = os.path.join(root, "props", f"{asset['id']}.png")
            if not os.path.exists(path):
                continue
            img = Image.open(path).convert("RGBA")
            w = asset["texture_px"][0]
            frame = img.crop((0, 0, w, w))
            wrap = wrap_vs_max(frame)
            print(f"  {asset['id']:14s} frame 0: wrap/max x={wrap['wrap_vs_max_x']:>5} "
                  f"y={wrap['wrap_vs_max_y']:>5}")
            report.note(f"{asset['id']} frame 0 tile-repeat: wrap/max x="
                        f"{wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']}")

    # ---- manifests ----------------------------------------------------------------------
    print("\nMANIFESTS (one next to every file, carrying model/seed/prompt/dims/frames)")
    for asset in B.ASSETS[setname]:
        aid = asset["id"]
        png = os.path.join(root, "props", f"{aid}.png")
        man = png + ".manifest.json"
        if not os.path.exists(png):
            continue
        if not os.path.exists(man):
            report.fail(f"props/{aid}.png: no manifest next to it")
            print(f"props/{aid:19s} FAIL: no manifest")
            continue
        try:
            m = json.load(open(man))
        except Exception as exc:  # noqa: BLE001
            report.fail(f"props/{aid}.png.manifest.json is not valid JSON ({exc})")
            continue
        problems = []
        if m.get("model") != B.MODEL_ID:
            problems.append(f"model {m.get('model')!r} != {B.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            problems.append("lora_strength is not 0.0 (the LoRA blacks out - ADR-060)")
        if not m.get("prompt"):
            problems.append("no prompt recorded")
        if not m.get("palette"):
            problems.append("no palette recorded")
        if not m.get("seed"):
            problems.append("no seed recorded")
        if list(m.get("output_dims", [])) != list(Image.open(png).size):
            problems.append(f"output_dims {m.get('output_dims')} != the file's "
                            f"{list(Image.open(png).size)}")
        if m.get("frames") != asset["frames"]:
            problems.append(f"frames {m.get('frames')} != {asset['frames']}")
        deriv = m.get("frame_derivation") or {}
        missing = [str(k) for k in range(asset["frames"]) if str(k) not in deriv]
        if missing:
            problems.append(f"frames {missing} have no declared derivation")
        if asset["frames"] > 1 and len(set(deriv.values())) < asset["frames"]:
            problems.append("the frame derivations are not distinct - the animation is not "
                            "described per frame")
        print(f"props/{aid:19s} {'sheet' if asset['frames'] > 1 else 'prop':5s} "
              f"{str(m.get('seed')):>8s}  {'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"props/{aid}.png.manifest.json: " + "; ".join(problems))
        else:
            report.ok(f"props/{aid}.png.manifest.json: model+palette+prompt+seed recorded, "
                      f"{asset['frames']} frame derivation(s), dims match the file")

    # ---- map cross-check: the footprint the ROOM map gives a key must be the one we declare --
    print("\nMAP CROSS-CHECK (a key the room map already places must have our footprint)")
    if os.path.exists(map_path):
        layers = json.load(open(map_path)).get("layers", [])
        placed = {}
        for layer in layers:
            if layer.get("type") != "objectgroup":
                continue
            for obj in layer.get("objects", []):
                key = obj.get("name") or obj.get("type")
                placed.setdefault(key, (obj.get("width"), obj.get("height")))
        for asset in B.ASSETS[setname]:
            aid = asset["id"]
            if aid in placed:
                if list(placed[aid]) == list(asset["footprint_px"]):
                    report.ok(f"{aid}: the map's rect {placed[aid]} matches the declared footprint")
                else:
                    report.fail(f"{aid}: the map places it at {placed[aid]} but the set declares "
                                f"the footprint {asset['footprint_px']}")
        absent = [a["id"] for a in B.ASSETS[setname] if a["id"] not in placed]
        if absent:
            report.note(f"not yet placed by {os.path.basename(map_path)}: {', '.join(absent)} - "
                        f"the wiring step places them; declared footprints are the contract")
    else:
        report.note(f"no room map at {map_path}")

    # ---- contact sheets -----------------------------------------------------------------
    print("\nCONTACT SHEETS (the visual record attached to the ticket)")
    need = ["contact_sheet.png", "contact_sheet_4x.png"]
    if setname == "factory":
        need.append("belts_strip_4x.png")
    for name in need:
        p = os.path.join(root, name)
        if os.path.exists(p):
            report.ok(f"{name} {Image.open(p).size[0]}x{Image.open(p).size[1]}")
        else:
            report.fail(f"{name} missing - the ticket asks for a contact sheet of everything on "
                        f"the room's floor tile")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC1 record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print(f"RESULT: PASS - every {setname} file exists at EXACTLY the pixel size and frame count "
          f"the SOU-199 table names (asserted against a copy of the table typed from the ticket), "
          f"on the shared 61-colour lodge palette, with real alpha and no magenta, each frame's "
          f"base on the frame's bottom row, the full-bleed belts carrying no transparent pixel at "
          f"all, a manifest with a per-frame derivation next to every file, and the declared "
          f"footprints agreeing with the room map wherever the map already places a key.")
    return 0


# ---------------------------------------------------------------------------------------
# SET C (SOU-200) - the four personality rooms: mystery cabin, token den, career office,
# writer's loft. Same gate shape as set B, plus one thing set B did not need: SOU-200 gives
# size CLASSES rather than pixel sizes ("1x1 = 32x32-48, 2x1 = 64x64-112 tall, 2x2 =
# 64x64-96", wall decals 64x48 / 64x32, one 3x2 flat rug), so a table of exact boxes alone
# could be internally consistent and still describe the wrong world. Every asset is therefore
# asserted TWICE against the ticket: its exact box (TICKET_C, hand-typed from the ticket) and
# its CLASS bounds (TICKET_C_CLASSES, hand-typed from the ticket's class table) - the second
# is the check that ties the artifact back to the declared source of truth instead of to the
# generator's own opinion.
# ---------------------------------------------------------------------------------------
TICKET_C = {
    "mystery": {
        # id: (class, frame_w, frame_h, frames, footprint_w, footprint_h)
        "curiosity_shelf": ("2x1", 64, 96, 1, 64, 32),
        "eye_painting": ("wall64x48", 64, 48, 1, 64, 32),
        "crystal_ball": ("1x1", 32, 40, 1, 32, 32),
        "journal_stand": ("1x1", 32, 48, 1, 32, 32),
        "strange_rug": ("3x2", 96, 64, 1, 96, 64),
        "lantern_green": ("1x1", 32, 48, 1, 32, 32),
    },
    "tokenden": {
        "token_stacks": ("1x1", 32, 32, 1, 32, 32),
        "lucky_cat": ("1x1", 32, 40, 1, 32, 32),
        "red_lanterns": ("wall64x32", 64, 32, 1, 64, 32),
        "tea_table": ("2x1", 64, 64, 1, 64, 32),
        "token_server": ("2x1", 64, 96, 1, 64, 32),
        "abacus_desk": ("2x2", 64, 80, 1, 64, 64),
    },
    "career": {
        "coat_stand": ("1x1", 32, 48, 1, 32, 32),
        "whiteboard": ("2x1", 64, 80, 1, 64, 32),
        "ring_light": ("1x1", 32, 48, 1, 32, 32),
        "advisor_desk": ("2x2", 64, 80, 1, 64, 64),
        "framed_certs": ("wall64x32", 64, 32, 1, 64, 32),
        "ficus": ("1x1", 32, 48, 1, 32, 32),
    },
    "writer": {
        "typewriter_desk": ("2x2", 64, 80, 1, 64, 64),
        "paper_stacks": ("1x1", 32, 32, 1, 32, 32),
        "paper_bin": ("1x1", 32, 40, 1, 32, 32),
        "reading_chair": ("1x1", 32, 48, 1, 32, 32),
        "cork_board": ("wall64x48", 64, 48, 1, 64, 32),
        "book_tower": ("1x1", 32, 48, 1, 32, 32),
    },
}

# (width, min_height, max_height) straight out of the SOU-200 size table, with the ticket's own
# words recorded next to each so a later reader can see what was being read.
TICKET_C_CLASSES = {
    "1x1": (32, 32, 48, "1x1 = 32x32-48"),
    "2x1": (64, 64, 112, "2x1 = 64x64-112 tall"),
    "2x2": (64, 64, 96, "2x2 = 64x64-96"),
    "wall64x48": (64, 48, 48, "wall decal 64x48"),
    "wall64x32": (64, 32, 32, "wall decal 64x32"),
    "3x2": (96, 64, 64, "3x2 flat"),
}

# Same floor as set B (FLAT_BASE_COVERAGE_MIN): the ticket's AC is "feet/base on the bottom row
# of pixels", asserted separately; this floor only catches a decal that is not resting on its
# base at all. Inventing a higher number would encode taste, not the contract.
FLAT_BASE_COVERAGE_MIN_C = 0.10


def main_set_c(args, setname: str) -> int:
    import gen_roomart_c as C

    root = os.path.abspath(args.root or str(C.out_dir(setname)))
    repo = str(REPO)
    map_path = args.map or os.path.join(repo, "frontend", "v3", "maps", "rooms",
                                       C.SETS[setname]["map"])
    report = Report()

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    # ---- the generator's four sets must be the ticket's four rooms ------------------------
    if set(C.ASSETS) != set(TICKET_C):
        report.fail(f"the generator's sets {sorted(C.ASSETS)} != the SOU-200 rooms "
                    f"{sorted(TICKET_C)}")
    else:
        report.ok(f"the generator carries the ticket's four rooms: {', '.join(sorted(C.ASSETS))}")

    pal_path = os.path.join(root, "palette.json")
    if os.path.exists(pal_path):
        declared = json.load(open(pal_path))
        if declared.get("hex") != C.PALETTE_HEX:
            report.fail(f"palette.json does not match the shared lodge palette "
                        f"({len(declared.get('hex', []))} vs {len(C.PALETTE_HEX)} colours)")
        else:
            report.ok(f"palette.json declares {len(C.PALETTE_HEX)} colours, identical to the "
                      f"lodge palette (shared_with={declared.get('shared_with')!r})")
    else:
        report.fail("palette.json missing - the palette is not declared anywhere")

    print("=" * 96)
    print(f"{setname} v3 art set C check (SOU-200)   root={root}")
    print(f"palette: {len(C.PALETTE_HEX)} declared colours, {', '.join(C.PALETTE_HEX[:6])}, ...")
    print("=" * 96)

    # ---- declaration vs the ticket: exact box AND size class -------------------------------
    ticket = TICKET_C[setname]
    drift = []
    gen_ids = [a["id"] for a in C.ASSETS[setname]]
    if set(gen_ids) != set(ticket):
        report.fail(f"the generator's asset list {sorted(gen_ids)} != the ticket's "
                    f"{sorted(ticket)}")
    else:
        report.ok(f"{len(gen_ids)} assets, the same set the SOU-200 table names: "
                  f"{', '.join(gen_ids)}")
    for asset in C.ASSETS[setname]:
        want = ticket.get(asset["id"])
        if not want:
            continue
        klass, tw, th, frames, fpw, fph = want
        if tuple(asset["texture_px"]) != (tw, th) or asset["frames"] != frames \
                or tuple(asset["footprint_px"]) != (fpw, fph):
            msg = (f"{asset['id']}: generator declares {tuple(asset['texture_px'])} x"
                   f"{asset['frames']} fp {tuple(asset['footprint_px'])} but the ticket table says "
                   f"{(tw, th)} x{frames} fp {(fpw, fph)}")
            drift.append(msg)
            report.fail(msg)
        if asset["lift_px"] != th - fph:
            msg = (f"{asset['id']}: lift {asset['lift_px']} != texture_h - footprint_h "
                   f"({th} - {fph} = {th - fph}) - the renderer draws footprint_h + lift, so the "
                   f"drawn box would not be the declared texture height")
            drift.append(msg)
            report.fail(msg)
        cw, lo, hi, phrase = TICKET_C_CLASSES[klass]
        if tw != cw or not lo <= th <= hi:
            drift.append(f"{asset['id']}: declared {tw}x{th} is outside its class {klass}")
            report.fail(f"{asset['id']}: declared box {tw}x{th} is outside the SOU-200 class "
                        f"{klass} ({phrase}) - width must be {cw}, height between {lo} and {hi}")
    if drift:
        report.fail("the generator's declaration has drifted from the SOU-200 table")
    else:
        report.ok("every exact size, frame count, footprint and lift agrees with the hand-typed "
                  "SOU-200 table, and every box sits inside its ticket size class")

    # ---- props -----------------------------------------------------------------------------
    print("\nASSETS (shipped px vs the TICKET table; class bounds asserted, not just the exact box)")
    print(f"{'asset':16s} {'shipped':>9s} {'ticket':>9s} {'class':>10s} {'colours':>7s} "
          f"{'transp':>7s} {'fringe':>6s} {'base':>5s}  result")
    results = {}
    for asset in C.ASSETS[setname]:
        aid = asset["id"]
        klass, tw, th, frames, fpw, fph = ticket[aid]
        path = os.path.join(root, "props", f"{aid}.png")
        if not os.path.exists(path):
            report.fail(f"props/{aid}.png missing")
            continue
        img = Image.open(path).convert("RGBA")
        want_size = (tw * frames, th)
        got = img.size
        colours, off = palette_check(img, allowed=C.PALETTE_RGB)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        parts = [img.crop((k * tw, 0, (k + 1) * tw, th)).convert("RGBA") for k in range(frames)]
        bottoms = [content_bottom_row(f) for f in parts]
        covs = [base_row_coverage(f) for f in parts]
        framing = [sum(1 for p in f.getchannel("A").getdata() if p == 0) for f in parts]
        problems = []
        if got != want_size:
            problems.append(f"size {got} != ticket {want_size}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"]:
            problems.append("no real alpha")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        empty = [k for k, f in enumerate(parts) if f.getchannel("A").getbbox() is None]
        if empty:
            problems.append(f"empty frames {empty}")
        bad_bottom = [k for k, b in enumerate(bottoms) if b != th - 1]
        if bad_bottom:
            problems.append(f"frames {bad_bottom} do not reach the bottom row (bottoms {bottoms}) "
                            f"- the renderer anchors there, so the prop would float")
        if asset["flat"] and min(covs) < FLAT_BASE_COVERAGE_MIN_C:
            problems.append(f"flat decal covers only {min(covs):.2f} of its lowest rows "
                            f"(floor {FLAT_BASE_COVERAGE_MIN_C})")
        print(f"{aid:16s} {f'{got[0]}x{got[1]}':>9s} {f'{want_size[0]}x{want_size[1]}':>9s} "
              f"{klass:>10s} {len(colours):>7d} {alpha['transparent']:>7d} "
              f"{resid['strong_magenta']:>6d} {min(covs):>5.2f}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))
        else:
            report.ok(f"{aid}: {got[0]}x{got[1]} = {frames} frame(s) of {tw}x{th} (class {klass}), "
                      f"{len(colours)} palette colours, real alpha, no magenta, base on the bottom "
                      f"row ({bottoms})")
        if not args.quiet:
            report.note(f"{aid}: colours {' '.join(hexs(c) for c in colours)}")
        results[aid] = {"size": list(got), "frames": frames, "bottoms": bottoms,
                        "coverage": [round(c, 3) for c in covs], "transparent": framing}

    # ---- manifests --------------------------------------------------------------------------
    print("\nMANIFESTS (one next to every file, carrying model/seed/prompt/dims + the class)")
    for asset in C.ASSETS[setname]:
        aid = asset["id"]
        png = os.path.join(root, "props", f"{aid}.png")
        man = png + ".manifest.json"
        if not os.path.exists(png):
            continue
        if not os.path.exists(man):
            report.fail(f"props/{aid}.png: no manifest next to it")
            print(f"props/{aid:19s} FAIL: no manifest")
            continue
        try:
            m = json.load(open(man))
        except Exception as exc:  # noqa: BLE001
            report.fail(f"props/{aid}.png.manifest.json is not valid JSON ({exc})")
            continue
        problems = []
        if m.get("model") != C.MODEL_ID:
            problems.append(f"model {m.get('model')!r} != {C.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            problems.append("lora_strength is not 0.0 (the LoRA blacks out - ADR-060)")
        if not m.get("prompt"):
            problems.append("no prompt recorded")
        if not m.get("palette"):
            problems.append("no palette recorded")
        if not m.get("seed"):
            problems.append("no seed recorded")
        if list(m.get("output_dims", [])) != list(Image.open(png).size):
            problems.append(f"output_dims {m.get('output_dims')} != the file's "
                            f"{list(Image.open(png).size)}")
        if m.get("frames") != asset["frames"]:
            problems.append(f"frames {m.get('frames')} != {asset['frames']}")
        if m.get("ticket") != "SOU-200":
            problems.append(f"ticket {m.get('ticket')!r} != 'SOU-200'")
        if m.get("ticket_size_class") != ticket[aid][0]:
            problems.append(f"recorded class {m.get('ticket_size_class')!r} != the ticket's "
                            f"{ticket[aid][0]!r}")
        deriv = m.get("frame_derivation") or {}
        missing = [str(k) for k in range(asset["frames"]) if str(k) not in deriv]
        if missing:
            problems.append(f"frames {missing} have no declared derivation")
        print(f"props/{aid:19s} {str(m.get('seed')):>8s} class={str(m.get('ticket_size_class')):9s} "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"props/{aid}.png.manifest.json: " + "; ".join(problems))
        else:
            report.ok(f"props/{aid}.png.manifest.json: model+palette+prompt+seed recorded, ticket "
                      f"SOU-200, class {ticket[aid][0]}, dims match the file")

    # ---- map cross-check --------------------------------------------------------------------
    print("\nMAP CROSS-CHECK (a key the room map already places must have our footprint)")
    if os.path.exists(map_path):
        layers = json.load(open(map_path)).get("layers", [])
        placed = {}
        for layer in layers:
            if layer.get("type") != "objectgroup":
                continue
            for obj in layer.get("objects", []):
                key = obj.get("name") or obj.get("type")
                placed.setdefault(key, (obj.get("width"), obj.get("height")))
        found = 0
        for asset in C.ASSETS[setname]:
            aid = asset["id"]
            if aid in placed:
                found += 1
                if list(placed[aid]) == list(asset["footprint_px"]):
                    report.ok(f"{aid}: the map's rect {placed[aid]} matches the declared footprint")
                else:
                    report.fail(f"{aid}: the map places it at {placed[aid]} but the set declares "
                                f"the footprint {asset['footprint_px']}")
        absent = [a["id"] for a in C.ASSETS[setname] if a["id"] not in placed]
        if absent:
            report.note(f"not yet placed by {os.path.basename(map_path)}: {', '.join(absent)} - "
                        f"the wiring step places them; the declared footprints are the contract")
        if found == 0:
            report.note(f"{os.path.basename(map_path)} still places the generic placeholder keys "
                        f"for this room, so no key could be cross-checked - wiring set C is a "
                        f"separate step (the SOU-208/212/213 pattern)")
    else:
        report.note(f"no room map at {map_path}")

    # ---- contact sheets ----------------------------------------------------------------------
    print("\nCONTACT SHEETS (the visual record attached to the ticket, on room_plank_a)")
    for name in ("contact_sheet.png", "contact_sheet_4x.png"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            report.ok(f"{name} {Image.open(p).size[0]}x{Image.open(p).size[1]}")
        else:
            report.fail(f"{name} missing - the ticket asks for a contact sheet of everything on "
                        f"room_plank_a")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print(f"RESULT: PASS - every {setname} file exists at EXACTLY the pixel size and frame count "
          f"the hand-typed SOU-200 table names, every declared box sits inside the ticket's own "
          f"size class, the shared 61-colour lodge palette, real alpha and no magenta, each base "
          f"on the frame's bottom row, a manifest next to every file carrying model/seed/prompt/"
          f"class, and a contact sheet for the set.")
    return 0


def main_lodge(args) -> int:
    import gen_lodge_art as L

    root = os.path.abspath(args.root or str(L.OUT))
    repo = str(REPO)
    map_path = args.map or os.path.join(repo, "frontend", "v3", "maps", "lodge.json")
    report = Report()

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    pal_path = os.path.join(root, "palette.json")
    if os.path.exists(pal_path):
        declared = json.load(open(pal_path))
        if declared.get("hex") != L.PALETTE_HEX:
            report.fail(f"palette.json does not match the generator's palette "
                        f"({len(declared.get('hex', []))} vs {len(L.PALETTE_HEX)} colours)")
        else:
            report.ok(f"palette.json declares {len(L.PALETTE_HEX)} colours, identical to the "
                      f"lodge generator ({declared.get('name')})")
    else:
        report.fail("palette.json missing - the palette is not declared anywhere")

    print("=" * 96)
    print(f"lodge v3 art set check   root={root}")
    print(f"palette: {len(L.PALETTE_HEX)} declared colours, {', '.join(L.PALETTE_HEX[:6])}, ...")
    print("=" * 96)

    # ---- tiles -----------------------------------------------------------------------
    print("\nTILES (32x32, full-bleed; seam asserted only on the axes the slot DECLARES)")
    print(f"{'slot':>4s} {'name':15s} {'class':8s} {'axes':>4s} {'colours':>7s} {'transp':>7s} "
          f"{'wrap/max x':>10s} {'y':>6s}  result")
    order = [name for name, _cls, _seam, _spec in L.TILES]
    for slot, (name, cls, axes, _spec) in enumerate(L.TILES):
        path = os.path.join(root, "tiles", f"{name}.png")
        if not os.path.exists(path):
            report.fail(f"tiles/{name}.png missing (slot {slot})")
            continue
        img = Image.open(path)
        colours, off = palette_check(img, allowed=L.PALETTE_RGB)
        alpha = alpha_profile(img)
        wrap = L.wrap_vs_max(img)
        problems = []
        if img.size != (32, 32):
            problems.append(f"size {img.size} != (32, 32)")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if alpha["transparent"]:
            problems.append(f"{alpha['transparent']} transparent pixels (a tile is full-bleed)")
        if "x" in axes and wrap["wrap_vs_max_x"] > LODGE_SEAM_LIMIT:
            problems.append(f"x seam: wrap step is {wrap['wrap_vs_max_x']}x the largest interior "
                            f"step (limit {LODGE_SEAM_LIMIT})")
        if "y" in axes and wrap["wrap_vs_max_y"] > LODGE_SEAM_LIMIT:
            problems.append(f"y seam: wrap step is {wrap['wrap_vs_max_y']}x the largest interior "
                            f"step (limit {LODGE_SEAM_LIMIT})")
        print(f"{slot:>4d} {name:15s} {cls:8s} {axes:>4s} {len(colours):>7d} "
              f"{alpha['transparent']:>7d} {wrap['wrap_vs_max_x']:>10} {wrap['wrap_vs_max_y']:>6}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"tiles/{name}: " + "; ".join(problems))
        else:
            report.ok(f"tiles/{name} (slot {slot}, {cls}): 32x32, {len(colours)} palette colours, "
                      f"opaque, wrap/max x={wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']} "
                      f"checked={axes}")
        if not args.quiet:
            report.note(f"{name}: colours {' '.join(hexs(c) for c in colours)}")

    # ---- tileset strip: size, palette, AND slot order vs the declared order ------------
    print("\nTILESET STRIP (512x32, 16 slots of 32px) - order checked against the declared table")
    sheet_path = os.path.join(root, "tiles", "tileset.png")
    if not os.path.exists(sheet_path):
        report.fail("tiles/tileset.png missing")
    else:
        sheet = Image.open(sheet_path).convert("RGBA")
        if sheet.size != (32 * len(order), 32):
            report.fail(f"tileset.png is {sheet.size}, expected {32 * len(order)}x32")
        else:
            report.ok(f"tileset.png is {sheet.size[0]}x{sheet.size[1]} = {len(order)} tiles of 32px")
        colours, off = palette_check(sheet, allowed=L.PALETTE_RGB)
        if off:
            report.fail(f"tileset.png has {len(off)} off-palette colours e.g. {hexs(off[0])}")
        else:
            report.ok(f"tileset.png uses {len(colours)} palette colours only")
        mismatched = []
        for slot, name in enumerate(order):
            tile_path = os.path.join(root, "tiles", f"{name}.png")
            if not os.path.exists(tile_path):
                continue
            tile = Image.open(tile_path).convert("RGBA")
            if sheet.crop((slot * 32, 0, (slot + 1) * 32, 32)).tobytes() != tile.tobytes():
                mismatched.append(f"slot {slot} != {name}.png")
        if mismatched:
            report.fail("tileset.png slot order/content does not match the declared order: "
                        + "; ".join(mismatched))
        else:
            report.ok("every one of the 16 slots holds exactly the tile the declared slot order "
                      f"names ({', '.join(order[:4])}, ... {order[-1]})")
        if os.path.exists(map_path):
            tiles = json.load(open(map_path))["tilesets"][0]["tiles"]
            map_order = [t["type"] for t in sorted(tiles, key=lambda t: t["id"])]
            if map_order != order:
                report.fail(f"tileset slot order {order} != lodge map order {map_order}")
            else:
                report.ok(f"slot order matches the lodge map: {', '.join(map_order)}")
        else:
            report.note(f"no lodge map at {map_path} yet - the declared table is the authority "
                        f"and the map cross-check will run once the map lands")

    # ---- props -----------------------------------------------------------------------
    print("\nPROPS (width = the footprint width the renderer draws; height = declared, or grown "
          "and reported)")
    print(f"{'asset':15s} {'shipped':>9s} {'declared':>9s} {'fp px':>7s} {'lift':>5s} "
          f"{'colours':>7s} {'transp':>7s} {'fringe':>6s} {'base':>5s}  result")
    for asset in L.PROPS:
        aid = asset["id"]
        path = os.path.join(root, "props", f"{aid}.png")
        if not os.path.exists(path):
            report.fail(f"props/{aid}.png missing")
            continue
        img = Image.open(path)
        colours, off = palette_check(img, allowed=L.PALETTE_RGB)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        cov = base_row_coverage(img)
        problems = []
        if asset["kind"] == "sheet":
            want = (32 * 4, 32)
            if img.size != want:
                problems.append(f"sheet is {img.size}, expected {want}")
            frames = [img.crop((i * 32, 0, (i + 1) * 32, 32)).convert("RGBA") for i in range(4)]
            empty = [i for i, f in enumerate(frames) if f.getchannel("A").getbbox() is None]
            same = [i for i in (1, 2, 3) if frames[i].tobytes() == frames[0].tobytes()]
            if empty:
                problems.append(f"empty frames {empty}")
            if same:
                problems.append(f"frames {same} identical to frame 0 - the derivation did not "
                                f"animate them")
            bottom = min(content_bottom_row(f) or 0 for f in frames)
        else:
            want_w, want_h = asset["texture_px"]
            # SOU-198 AC1 is "exists at the EXACT pixel size", and the lodge props are fitted
            # inside the declared box so the canvas is exactly it - both axes are asserted, not
            # just the width. (office-01 fitted by width and let the canvas grow; its reviewer
            # had to reconcile five props that shipped taller than the published table.)
            if (img.size[0], img.size[1]) != (want_w, want_h):
                problems.append(f"size {img.size[0]}x{img.size[1]} != declared {want_w}x{want_h}")
            bottom = content_bottom_row(img)
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"]:
            problems.append("no real alpha")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        if asset["kind"] != "sheet" and bottom != img.size[1] - 1:
            problems.append(f"content stops at row {bottom} of {img.size[1]} - the footprint's "
                            f"bottom edge is where the renderer anchors, so the prop would float")
        declared = (f"{asset['texture_px'][0]}x{asset['texture_px'][1]}" if asset["kind"] != "sheet"
                    else "128x32")
        over = ""
        print(f"{aid:15s} {f'{img.size[0]}x{img.size[1]}':>9s} {declared:>9s} "
              f"{asset['footprint_px'][0]:>3d}x{asset['footprint_px'][1]:<3d} "
              f"{asset['lift_px']:>5d} {len(colours):>7d} {alpha['transparent']:>7d} "
              f"{resid['strong_magenta']:>6d} {cov:>5.2f}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))
        else:
            report.ok(f"{aid}: {img.size[0]}x{img.size[1]}{over}, {len(colours)} palette colours, "
                      f"real alpha, no magenta, content reaches the canvas bottom (row {bottom})")
        if not args.quiet:
            report.note(f"{aid}: colours {' '.join(hexs(c) for c in colours)}")

    # ---- manifests: AC3 is "a manifest next to every generated file", so it is checked -------
    print("\nMANIFESTS (AC3: one next to every file, carrying the model/seed/prompt/dims record)")
    pairs = [("tiles", f"{name}.png") for name in order] + [("tiles", "tileset.png")] \
        + [("props", f"{a['id']}.png") for a in L.PROPS]
    print(f"{'manifest':26s} {'kind':9s} {'seed':>8s}  result")
    for sub, fname in pairs:
        png = os.path.join(root, sub, fname)
        man = png + ".manifest.json"
        if not os.path.exists(png):
            continue  # already reported as missing above
        if not os.path.exists(man):
            report.fail(f"{sub}/{fname}: no manifest next to it (AC3)")
            print(f"{sub}/{fname:19s} {'-':9s} {'-':>8s}  FAIL: no manifest")
            continue
        try:
            m = json.load(open(man))
        except Exception as exc:  # noqa: BLE001
            report.fail(f"{sub}/{fname}.manifest.json is not valid JSON ({exc})")
            continue
        problems = []
        if m.get("model") != L.MODEL_ID:
            problems.append(f"model {m.get('model')!r} != {L.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            problems.append("lora_strength is not 0.0 (the LoRA blacks out - ADR-060)")
        if not (m.get("prompt") or m.get("derivation")):
            problems.append("no prompt and no derivation")
        if not m.get("palette"):
            problems.append("no palette recorded")
        if m.get("output_dims") and list(m["output_dims"]) != list(Image.open(png).size):
            problems.append(f"output_dims {m['output_dims']} != the file's "
                            f"{list(Image.open(png).size)}")
        if m.get("kind") == "prop" and not m.get("seed"):
            problems.append("generated prop with no seed")
        print(f"{sub}/{fname:19s} {str(m.get('kind')):9s} {str(m.get('seed')):>8s}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{sub}/{fname}.manifest.json: " + "; ".join(problems))
        else:
            report.ok(f"{sub}/{fname}.manifest.json: model+palette+prompt recorded, "
                      f"seed={m.get('seed') if m.get('seed') is not None else 'n/a (drawn)'}, "
                      f"dims match the file")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC1 record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print("RESULT: PASS - every lodge file exists at exactly its declared pixel size (props are "
          "fitted inside the declared box, so width AND height match the table), on the declared "
          "61-colour palette, with opaque tiles showing no seam on the axes each slot declares, "
          "real alpha and no magenta on every prop, a manifest next to every file, and the "
          "512x32 strip holding the 16 declared slots in order.")
    return 0


# ---------------------------------------------------------------------------------------
# SOU-216: the garage bay, and the polish pass on four shipped sets.
#
# Two `--set` values because they are two deliveries with two different oracles:
#
#   garage   six props and two drawn concrete tiles. The oracle is the TICKET's table, typed out
#            again below (TICKET_D) - a gate that only compares an asset to the generator's own
#            declaration cannot catch a declaration that drifted from the ticket (SOU-158).
#   polish   five NEW files under NEW names that replace five SHIPPED ones. Their oracle is the
#            FILE THEY REPLACE, read from the served tree: "softer than the checkerboard" and
#            "fuller than the empty shelf" are COMPARISONS, and a gate that only measured the new
#            file against itself would pass while the room looked exactly as wrong as before. The
#            same section asserts, against a git ref, that nothing shipped was overwritten.
# ---------------------------------------------------------------------------------------
TICKET_D = {
    # id: (texture_w, texture_h, frames, footprint_w, footprint_h, lift) - typed from the ticket
    "van_parked": (96, 128, 1, 96, 96, 32),
    "roller_door": (96, 48, 1, 96, 32, 16),
    "pegboard": (64, 48, 1, 64, 32, 16),
    "toolbox": (32, 40, 1, 32, 32, 8),
    "tire_stack": (32, 40, 1, 32, 32, 8),
    "oil_drum": (32, 48, 1, 32, 32, 16),
}
TICKET_D_TILES = (("garage_floor", "xy"), ("garage_floor_stain", "xy"))
TICKET_D_STRIP = (64, 32)

TICKET_D_POLISH = {
    # id: (home, kind, w, h, footprint_w, footprint_h, replaces, the metric that must IMPROVE)
    "stone_floor_soft": ("lodge", "tile", 32, 32, 32, 32, "stone_floor", "block_contrast"),
    "wall_face_soft": ("lodge", "tile", 32, 32, 32, 32, "wall_face", "log_contrast"),
    "gear_shelf_full_a": ("serverroom", "prop", 64, 112, 64, 32, "gear_shelf_a", "shelf_packing"),
    "gear_shelf_full_b": ("serverroom", "prop", 64, 112, 64, 32, "gear_shelf_b", "shelf_packing"),
    "paper_stacks_lined": ("writer", "prop", 32, 32, 32, 32, "paper_stacks", "line_marks"),
}
TICKET_D_POLISH_STRIP = {"tiles_soft": ("lodge", ["stone_floor_soft", "wall_face_soft"], (64, 32))}

# The shipped files this pass must NOT have touched, and the new files that must not have existed
# before it. Checked against a git ref (default origin/master) - "in place" is a claim about the
# repository, so it is checked against the repository.
SHIPPED_MUST_BE_UNTOUCHED = (
    "frontend/v3/assets/lodge/tiles/stone_floor.png",
    "frontend/v3/assets/lodge/tiles/wall_face.png",
    "frontend/v3/assets/lodge/tiles.png",
    "frontend/v3/assets/serverroom/props/gear_shelf_a.png",
    "frontend/v3/assets/serverroom/props/gear_shelf_b.png",
    "frontend/v3/assets/writer/props/paper_stacks.png",
)


def _git_blob(ref: str, rel_path: str):
    """(bytes, None) for the file in `ref`'s tree, (None, error) when git cannot answer.

    A failure here is a FAILURE, never a note: "we could not check" and "we checked and it is
    clean" are different answers, and only one of them is evidence.
    """
    proc = subprocess.run(["git", "show", f"{ref}:{rel_path}"], cwd=str(REPO),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        return None, proc.stderr.decode(errors="replace").strip().splitlines()[-1:]
    return proc.stdout, None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return _sha256_bytes(fh.read())


def main_garage(args) -> int:
    import gen_garage_polish as G

    root = os.path.abspath(args.root or str(G.out_dir("garage")))
    map_path = args.map or os.path.join(str(REPO), "frontend", "v3", "maps", "rooms",
                                        G.SETS["garage"]["map"])
    report = Report()

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    pal_path = os.path.join(root, "palette.json")
    if os.path.exists(pal_path):
        declared = json.load(open(pal_path))
        if declared.get("hex") != G.PALETTE_HEX:
            report.fail(f"palette.json does not match the shared lodge palette "
                        f"({len(declared.get('hex', []))} vs {len(G.PALETTE_HEX)} colours)")
        else:
            report.ok(f"palette.json declares {len(G.PALETTE_HEX)} colours, identical to the lodge "
                      f"palette ({declared.get('name')}, shared_with={declared.get('shared_with')!r})")
    else:
        report.fail("palette.json missing - the palette is not declared anywhere")

    print("=" * 96)
    print(f"garage v3 art set check (SOU-216)   root={root}")
    print(f"palette: {len(G.PALETTE_HEX)} declared colours, {', '.join(G.PALETTE_HEX[:6])}, ...")
    print("=" * 96)

    # ---- declaration vs the ticket ------------------------------------------------------
    ticket = TICKET_D
    gen_ids = [a["id"] for a in G.GARAGE_PROPS]
    if set(gen_ids) != set(ticket):
        report.fail(f"the generator's asset list {sorted(gen_ids)} != the ticket's "
                    f"{sorted(ticket)}")
    else:
        report.ok(f"{len(gen_ids)} props, the same set the SOU-216 table names: {', '.join(gen_ids)}")
    drift = []
    for asset in G.GARAGE_PROPS:
        tw, th, frames, fpw, fph, lift = ticket[asset["id"]]
        if tuple(asset["texture_px"]) != (tw, th) or asset["lift_px"] != th - fph \
                or tuple(asset["footprint_px"]) != (fpw, fph):
            drift.append(asset["id"])
            report.fail(f"{asset['id']}: generator declares {tuple(asset['texture_px'])} lift "
                        f"{asset['lift_px']} fp {tuple(asset['footprint_px'])} but the ticket says "
                        f"{(tw, th)} lift {lift} fp {(fpw, fph)}")
    if not drift:
        report.ok("every size, footprint and lift agrees with the SOU-216 table")

    # ---- props --------------------------------------------------------------------------
    print("\nPROPS (shipped px vs the TICKET table; base = content coverage of the bottom 2 rows)")
    print(f"{'asset':16s} {'shipped':>11s} {'ticket':>11s} {'colours':>7s} {'transp':>7s} "
          f"{'fringe':>6s} {'base':>5s}  result")
    for asset in G.GARAGE_PROPS:
        aid = asset["id"]
        tw, th, _frames, fpw, fph, _lift = ticket[aid]
        path = os.path.join(root, "props", f"{aid}.png")
        if not os.path.exists(path):
            report.fail(f"props/{aid}.png missing")
            continue
        img = Image.open(path).convert("RGBA")
        colours, off = palette_check(img, allowed=G.PALETTE_RGB)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        cov = base_row_coverage(img)
        bottom = content_bottom_row(img)
        problems = []
        if img.size != (tw, th):
            problems.append(f"size {img.size} != ticket {(tw, th)}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"]:
            problems.append("no real alpha")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        if bottom != th - 1:
            problems.append(f"content stops at row {bottom} of {th} - the renderer anchors the "
                            f"footprint's bottom edge there, so the prop would float")
        if asset["flat"] and cov < 0.10:
            problems.append(f"flat decal covers only {cov:.2f} of its lowest rows")
        print(f"{aid:16s} {f'{img.size[0]}x{img.size[1]}':>11s} {f'{tw}x{th}':>11s} "
              f"{len(colours):>7d} {alpha['transparent']:>7d} {resid['fringe']:>6d} {cov:>5.2f}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))
        else:
            report.ok(f"{aid}: {img.size[0]}x{img.size[1]}, {len(colours)} palette colours, real "
                      f"alpha, no magenta, base on the bottom row ({bottom}), footprint "
                      f"{fpw}x{fph} + {th - fph}px lift")
        if not args.quiet:
            report.note(f"{aid}: colours {' '.join(hexs(c) for c in colours)}")
        # a reference edit must SAY it is one, and name the file it was conditioned on
        man_path = path + ".manifest.json"
        if os.path.exists(man_path):
            m = json.load(open(man_path))
            if asset.get("reference"):
                if asset["reference"] not in str(m.get("generation_mode", "")):
                    report.fail(f"{aid}: the manifest's generation_mode "
                                f"{m.get('generation_mode')!r} does not name the reference "
                                f"{asset['reference']} the ticket allows")
                else:
                    report.ok(f"{aid}: manifest records a reference edit of {asset['reference']} "
                              f"(the same beige van - SOU-216 allows OUR OWN art as reference)")

    # ---- tiles ---------------------------------------------------------------------------
    print("\nTILES (32x32 drawn concrete; seam asserted on the declared axes; the ring assertion "
          "is what makes seamlessness true BY CONSTRUCTION)")
    order = [name for name, _axes in TICKET_D_TILES]
    tiles = {}
    for slot, (name, axes) in enumerate(TICKET_D_TILES):
        path = os.path.join(root, "tiles", f"{name}.png")
        if not os.path.exists(path):
            report.fail(f"tiles/{name}.png missing (slot {slot})")
            continue
        img = Image.open(path).convert("RGBA")
        tiles[name] = img
        colours, off = palette_check(img, allowed=G.PALETTE_RGB)
        alpha = alpha_profile(img)
        wrap = G.lodge.wrap_vs_max(img)
        ring = {img.getpixel((x, y)) for x in range(32) for y in (0, 1, 30, 31)}
        ring |= {img.getpixel((x, y)) for y in range(32) for x in (0, 1, 30, 31)}
        problems = []
        if img.size != (32, 32):
            problems.append(f"size {img.size} != (32, 32)")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if alpha["transparent"]:
            problems.append(f"{alpha['transparent']} transparent pixels (a tile is full-bleed)")
        if "x" in axes and wrap["wrap_vs_max_x"] > LODGE_SEAM_LIMIT:
            problems.append(f"x seam: wrap step is {wrap['wrap_vs_max_x']}x the largest interior "
                            f"step (limit {LODGE_SEAM_LIMIT})")
        if "y" in axes and wrap["wrap_vs_max_y"] > LODGE_SEAM_LIMIT:
            problems.append(f"y seam: wrap step is {wrap['wrap_vs_max_y']}x the largest interior "
                            f"step (limit {LODGE_SEAM_LIMIT})")
        if len(ring) != 1:
            problems.append(f"the border ring carries {len(ring)} colours - the drawn tile is "
                            f"periodic because the 2px ring is one colour, so this tile is not "
                            f"seamless by construction")
        print(f"{slot:>4d} {name:19s} {str(img.size):>9s} {axes:>4s} {len(colours):>7d} "
              f"{alpha['transparent']:>7d} {wrap['wrap_vs_max_x']:>10} {wrap['wrap_vs_max_y']:>6}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"tiles/{name}: " + "; ".join(problems))
        else:
            report.ok(f"tiles/{name} (slot {slot}): 32x32, {len(colours)} palette colours, opaque, "
                      f"wrap/max x={wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']} checked={axes}, "
                      f"border ring is a single colour (periodic by construction)")

    # the stain tile is the plain tile PLUS one interior stain: it may not touch the ring
    if "garage_floor" in tiles and "garage_floor_stain" in tiles:
        plain, stain = tiles["garage_floor"], tiles["garage_floor_stain"]
        changed = [(x, y) for y in range(32) for x in range(32)
                   if plain.getpixel((x, y)) != stain.getpixel((x, y))]
        outside = [p for p in changed if not (2 <= p[0] <= 29 and 2 <= p[1] <= 29)]
        if outside:
            report.fail(f"garage_floor_stain differs from garage_floor outside its interior in "
                        f"{len(outside)} px e.g. {outside[:4]} - it is meant to be the same tile "
                        f"plus ONE oil stain, and the ring must stay calm")
        elif not changed:
            report.fail("garage_floor_stain is identical to garage_floor - the oil stain is missing")
        elif len(changed) > 200:
            report.fail(f"garage_floor_stain changes {len(changed)} px (> 200) - that is not 'one "
                        f"oil stain' on the same tile")
        else:
            report.ok(f"garage_floor_stain = garage_floor + one interior stain "
                      f"({len(changed)} px changed, 0 outside the interior)")

    # ---- tileset strip ------------------------------------------------------------------
    print("\nTILESET STRIP (64x32, 2 slots of 32px) - order checked against the declared table")
    sheet_path = os.path.join(root, "tiles.png")
    declared_strip = TICKET_D_STRIP
    if not os.path.exists(sheet_path):
        report.fail("tiles.png missing - the wiring step needs the 2-tile strip")
    else:
        sheet = Image.open(sheet_path).convert("RGBA")
        if sheet.size != declared_strip:
            report.fail(f"tiles.png is {sheet.size}, expected {declared_strip}")
        else:
            report.ok(f"tiles.png is {sheet.size[0]}x{sheet.size[1]} = {len(order)} tiles of 32px")
        colours, off = palette_check(sheet, allowed=G.PALETTE_RGB)
        if off:
            report.fail(f"tiles.png has {len(off)} off-palette colours e.g. {hexs(off[0])}")
        mismatched = []
        for slot, name in enumerate(order):
            tile_path = os.path.join(root, "tiles", f"{name}.png")
            if not os.path.exists(tile_path):
                continue
            tile = Image.open(tile_path).convert("RGBA")
            if sheet.crop((slot * 32, 0, (slot + 1) * 32, 32)).tobytes() != tile.tobytes():
                mismatched.append(f"slot {slot} != {name}.png")
        if mismatched:
            report.fail("tiles.png slot order/content does not match the declared order: "
                        + "; ".join(mismatched))
        else:
            report.ok(f"both slots hold exactly the tile the declared order names "
                      f"({', '.join(order)})")

    # ---- manifests ----------------------------------------------------------------------
    print("\nMANIFESTS (one next to every file, carrying model/seed/prompt/dims)")
    pairs = [("tiles", f"{name}.png") for name in order] + [("", "tiles.png")] \
        + [("props", f"{a['id']}.png") for a in G.GARAGE_PROPS]
    for sub, fname in pairs:
        png = os.path.join(root, sub, fname)
        man = png + ".manifest.json"
        if not os.path.exists(png):
            continue
        if not os.path.exists(man):
            report.fail(f"{sub}/{fname}: no manifest next to it")
            print(f"{sub}/{fname:24s} FAIL: no manifest")
            continue
        try:
            m = json.load(open(man))
        except Exception as exc:  # noqa: BLE001
            report.fail(f"{sub}/{fname}.manifest.json is not valid JSON ({exc})")
            continue
        problems = []
        if m.get("model") != G.MODEL_ID:
            problems.append(f"model {m.get('model')!r} != {G.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            problems.append("lora_strength is not 0.0 (the LoRA blacks out - ADR-060)")
        if not (m.get("prompt") or m.get("derivation")):
            problems.append("no prompt and no derivation")
        if not m.get("palette"):
            problems.append("no palette recorded")
        if list(m.get("output_dims", [])) != list(Image.open(png).size):
            problems.append(f"output_dims {m.get('output_dims')} != the file's "
                            f"{list(Image.open(png).size)}")
        if m.get("kind") == "prop" and not m.get("seed"):
            problems.append("generated prop with no seed")
        print(f"{sub}/{fname:24s} {'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{sub}/{fname}.manifest.json: " + "; ".join(problems))

    # ---- index.json: the wiring contract ------------------------------------------------
    print("\nINDEX (the wiring contract: lift per key, declared vs MEASURED px, tile slot order)")
    index_path = os.path.join(root, "index.json")
    if not os.path.exists(index_path):
        report.fail("index.json missing - the wiring step re-derives nothing by design, so this "
                    "file is the contract")
    else:
        index = json.load(open(index_path))
        want_lift = {a["id"]: a["lift_px"] for a in G.GARAGE_PROPS}
        if index.get("prop_lift_for_placeholders_js") != want_lift:
            report.fail(f"index.json lift table {index.get('prop_lift_for_placeholders_js')} != "
                        f"the declared {want_lift}")
        else:
            report.ok(f"index.json carries the lift for every prop key: {want_lift}")
        bad = []
        for row in index.get("assets", []):
            actual = row.get("texture_px_actual")
            path = os.path.join(root, "props", f"{row['key']}.png")
            if actual is None or not os.path.exists(path):
                bad.append(f"{row['key']}: no measured size")
                continue
            if list(actual) != list(Image.open(path).size):
                bad.append(f"{row['key']}: index says {actual}, file is "
                           f"{list(Image.open(path).size)}")
            if list(row.get("texture_px_declared", [])) != list(TICKET_D[row["key"]][:2]):
                bad.append(f"{row['key']}: declared {row.get('texture_px_declared')} != the ticket's "
                           f"{list(TICKET_D[row['key']][:2])}")
        if bad:
            report.fail("index.json drifted from the files: " + "; ".join(bad))
        else:
            report.ok("every index entry's declared AND measured pixel size matches the file on "
                      "disk and the ticket table")
        if (index.get("tileset", {}).get("order") or []) != order:
            report.fail(f"index.json tileset order {index.get('tileset', {}).get('order')} != "
                        f"declared {order}")
        else:
            report.ok(f"index.json publishes the tile slot order {order}")

    # ---- map cross-check ----------------------------------------------------------------
    print("\nMAP CROSS-CHECK (a key the room map already places must have our footprint)")
    if os.path.exists(map_path):
        layers = json.load(open(map_path)).get("layers", [])
        placed = {}
        for layer in layers:
            if layer.get("type") != "objectgroup":
                continue
            for obj in layer.get("objects", []):
                key = obj.get("name") or obj.get("type")
                placed.setdefault(key, (obj.get("width"), obj.get("height")))
        for asset in G.GARAGE_PROPS:
            aid = asset["id"]
            if aid in placed and list(placed[aid]) != list(asset["footprint_px"]):
                report.fail(f"{aid}: the map places it at {placed[aid]} but the set declares the "
                            f"footprint {asset['footprint_px']}")
            elif aid in placed:
                report.ok(f"{aid}: the map's rect {placed[aid]} matches the declared footprint")
        absent = [a["id"] for a in G.GARAGE_PROPS if a["id"] not in placed]
        if absent:
            report.note(f"not placed yet by {os.path.basename(map_path)}: {', '.join(absent)} - "
                        f"SOU-216 hands the wiring to the next step; the declared footprints are "
                        f"the contract it wires against")
        legacy = {k: v for k, v in placed.items() if k in ("van", "spool")}
        if legacy:
            report.note(f"the map still places the pre-SOU-216 keys {legacy} - that layout is "
                        f"being re-laid out in the same epic; our keys ({', '.join(a['id'] for a in
                        G.GARAGE_PROPS)}) are what replaces them")
    else:
        report.fail(f"no room map at {map_path} - the garage room map is where these keys are "
                    f"wired, and the check cannot run without it")

    # ---- contact sheets -----------------------------------------------------------------
    print("\nCONTACT SHEETS (the visual record attached to the ticket)")
    for name in ("contact_sheet.png", "contact_sheet_4x.png"):
        p = os.path.join(root, name)
        if os.path.exists(p):
            report.ok(f"{name} {Image.open(p).size[0]}x{Image.open(p).size[1]}")
        else:
            report.fail(f"{name} missing - the ticket asks for the garage props on garage_floor")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print("RESULT: PASS - every garage file exists at EXACTLY the pixel size the SOU-216 table "
          "names (asserted against a copy of the table typed from the ticket), on the shared "
          "61-colour lodge palette, with real alpha and no magenta, each prop's base on the bottom "
          "row of its canvas, both concrete tiles opaque with a one-colour border ring and no seam "
          "on either axis, the stain variant differing from the plain tile only inside its "
          "interior, a manifest next to every file, an index.json whose lift and px tables match "
          "the files, and the 64x32 strip holding the two declared slots in order.")
    return 0


def main_polish(args) -> int:
    import gen_garage_polish as G

    root = os.path.abspath(args.root or str(G.out_dir("polish")))
    report = Report()

    if not os.path.isdir(root):
        print(f"no polish pack at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    print("=" * 96)
    print(f"SOU-216 polish pass check   root={root}   shipped ref={args.shipped_ref}")
    print(f"palette: {len(G.PALETTE_HEX)} declared colours (unchanged)")
    print("=" * 96)

    # ---- nothing shipped was overwritten, nothing new was already there ------------------
    print("\nSHIPPED FILES vs THE REPOSITORY (\"new files under new names, never overwrite shipped "
          "files in place\")")
    for rel in SHIPPED_MUST_BE_UNTOUCHED:
        blob, err = _git_blob(args.shipped_ref, rel)
        live = os.path.join(str(REPO), rel)
        if blob is None:
            report.fail(f"cannot read {rel} from {args.shipped_ref} ({err}) - the "
                        f"no-overwrite AC cannot be verified, and 'could not check' is not proof")
            print(f"  {rel:60s} FAIL: git could not answer")
            continue
        if not os.path.exists(live):
            report.fail(f"{rel} is missing from the working tree")
            print(f"  {rel:60s} FAIL: missing from the tree")
            continue
        same = _sha256_bytes(blob) == _sha256_file(live)
        print(f"  {rel:60s} {'unchanged' if same else 'FAIL: MODIFIED'}")
        if same:
            report.ok(f"{rel} is byte-identical to {args.shipped_ref} (untouched)")
        else:
            report.fail(f"{rel} DIFFERS from {args.shipped_ref} - the ticket says new files under "
                        f"new names, never an edit of a shipped file")
    for aid, (home, kind, *_rest) in TICKET_D_POLISH.items():
        rel = (f"frontend/v3/assets/{home}/tiles/{aid}.png" if kind == "tile"
               else f"frontend/v3/assets/{home}/props/{aid}.png")
        blob, _err = _git_blob(args.shipped_ref, rel)
        if blob is not None:
            report.fail(f"{rel} already exists at {args.shipped_ref} - a polish file must be NEW, "
                        f"otherwise it is an in-place edit")
            print(f"  {rel:60s} FAIL: already exists at the ref")
        else:
            report.ok(f"{rel} is new (not present at {args.shipped_ref})")

    # ---- per asset ----------------------------------------------------------------------
    print("\nPOLISHED ASSETS (shipped px vs the ticket table, then the comparison that matters)")
    print(f"{'asset':20s} {'home':10s} {'size':>9s} {'ticket':>9s} {'colours':>7s} {'transp':>7s} "
          f"{'metric':22s} {'old':>8s} {'new':>8s}  result")
    for aid, (home, kind, tw, th, fpw, fph, replaces, metric) in TICKET_D_POLISH.items():
        src = os.path.join(root, home, "tiles" if kind == "tile" else "props", f"{aid}.png")
        if not os.path.exists(src):
            report.fail(f"{aid}: {os.path.relpath(src, str(REPO))} missing")
            continue
        img = Image.open(src).convert("RGBA")
        colours, off = palette_check(img, allowed=G.PALETTE_RGB)
        alpha = alpha_profile(img)
        resid = magenta_residue(img)
        problems = []
        if img.size != (tw, th):
            problems.append(f"size {img.size} != the ticket's {(tw, th)}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if kind == "tile":
            if alpha["transparent"]:
                problems.append(f"{alpha['transparent']} transparent px (a tile is full-bleed)")
            wrap = G.lodge.wrap_vs_max(img)
            if wrap["wrap_vs_max_x"] > LODGE_SEAM_LIMIT or wrap["wrap_vs_max_y"] > LODGE_SEAM_LIMIT:
                problems.append(f"seam on a declared axis: {wrap}")
            old_metric = new_metric = None
        else:
            if not alpha["has_real_alpha"]:
                problems.append("no real alpha")
            if alpha["partial"]:
                problems.append(f"{alpha['partial']} partial-alpha px")
            bottom = content_bottom_row(img)
            if bottom != th - 1:
                problems.append(f"content stops at row {bottom} of {th} - the anchor would float")
            # The base-coverage floor applies to FLAT decals only (SOU-200/213 lesson: a threshold
            # the ticket never stated failed a correctly drawn ring-shaped decal). A standing prop's
            # base is legitimately narrow - a shelf stands on two thin side uprights - so its
            # coverage is REPORTED next to the file it replaces and not asserted.
            spec = next((a for a in G.POLISH_PROPS if a["id"] == aid), {})
            if spec.get("flat") and base_row_coverage(img) < 0.10:
                problems.append(f"flat decal covers only {base_row_coverage(img):.2f} of its "
                                f"lowest row - it would not rest on its base")
            old_metric = new_metric = None
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")

        ref_path = os.path.join(str(REPO), "frontend", "v3", "assets", home,
                                "tiles" if kind == "tile" else "props", f"{replaces}.png")
        if ref_path == src:
            report.fail(f"{aid}: the polished file IS the file it replaces ({replaces})")
        if not os.path.exists(ref_path):
            report.fail(f"{aid}: the file it replaces ({replaces}) is missing from the served tree "
                        f"- the comparison cannot be made")
            old = None
        else:
            old = Image.open(ref_path).convert("RGBA")

        # the comparison AC: the metric that this asset is supposed to improve
        if old is not None:
            if metric == "block_contrast":
                old_metric, new_metric = G.block_contrast(old), G.block_contrast(img)
                if not new_metric < old_metric:
                    problems.append(f"block_contrast {new_metric} is NOT lower than the shipped "
                                    f"{replaces}'s {old_metric} - the checkerboard is not softer")
                else:
                    report.ok(f"{aid}: block_contrast {old_metric} -> {new_metric} (the "
                              f"checkerboard the owner complained about is measurably softer)")
            elif metric == "log_contrast":
                old_metric, new_metric = G.log_contrast(old), G.log_contrast(img)
                if not new_metric < old_metric:
                    problems.append(f"log_contrast {new_metric} is NOT lower than the shipped "
                                    f"{replaces}'s {old_metric} - the log wall is not softer")
                else:
                    report.ok(f"{aid}: log_contrast {old_metric} -> {new_metric} (the stripes are "
                              f"measurably softer)")
            elif metric == "shelf_packing":
                o = G.shelf_packing(old)
                n = G.shelf_packing(img)
                old_metric, new_metric = o["canvas_opaque_share"], n["canvas_opaque_share"]
                if not n["canvas_opaque_share"] > o["canvas_opaque_share"]:
                    problems.append(f"the shelf fills {n['canvas_opaque_share']} of its canvas vs "
                                    f"the shipped {replaces}'s {o['canvas_opaque_share']} - it is "
                                    f"not FULLER")
                if not n["gear_px"] > o["gear_px"]:
                    problems.append(f"{n['gear_px']} gear-coloured px vs the shipped "
                                    f"{replaces}'s {o['gear_px']} - no more equipment on it")
                if n["led_px"] < 8:
                    problems.append(f"only {n['led_px']} status-light px (the ticket asks for rows "
                                    f"of glowing teal/orange LEDs)")
                if n["plant_px"] < 10:
                    problems.append(f"{n['plant_px']} plant px (the ticket asks for 'and a plant')")
                if not problems:
                    report.ok(f"{aid}: canvas opaque {o['canvas_opaque_share']} -> "
                              f"{n['canvas_opaque_share']}, gear px {o['gear_px']} -> "
                              f"{n['gear_px']}, status-light px {o['led_px']} -> {n['led_px']}, "
                              f"plant px {n['plant_px']} (fuller than the shelf it replaces)")
            elif metric == "line_marks":
                marks = G.line_marks(img)
                old_metric = G.line_marks(old)["runs_count"]
                new_metric = marks["runs_count"]
                if marks["loose_dark_px"]:
                    problems.append(f"{marks['loose_dark_px']} dark px on the paper are NOT part "
                                    f"of a horizontal line run of >= {G.MARK_MIN_RUN}px - the "
                                    f"ticket says lines, never letters")
                if marks["runs_count"] < 3:
                    problems.append(f"only {marks['runs_count']} line runs on the paper - the "
                                    f"ticket asks for a hint of text LINES")
                if len(marks["rows_with_lines"]) < 2:
                    problems.append(f"the lines occupy {len(marks['rows_with_lines'])} row(s) - at "
                                    f"least 2 are needed to read as a paragraph")
                if not problems:
                    report.ok(f"{aid}: {marks['runs_count']} horizontal line runs on "
                              f"{len(marks['rows_with_lines'])} rows (rows "
                              f"{marks['rows_with_lines']}), 0 dark px outside a line run - lines, "
                              f"not letters (the shipped {replaces} has {old_metric} runs)")

        print(f"{aid:20s} {home:10s} {f'{img.size[0]}x{img.size[1]}':>9s} {f'{tw}x{th}':>9s} "
              f"{len(colours):>7d} {alpha['transparent']:>7d} {metric:22s} "
              f"{str(old_metric):>8s} {str(new_metric):>8s}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))

        # manifest next to every file, carrying the replaced file's hash
        man = src + ".manifest.json"
        if not os.path.exists(man):
            report.fail(f"{aid}: no manifest next to it")
            continue
        m = json.load(open(man))
        mprob = []
        if m.get("model") != G.MODEL_ID:
            mprob.append(f"model {m.get('model')!r} != {G.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            mprob.append("lora_strength is not 0.0")
        if not (m.get("prompt") or m.get("derivation")):
            mprob.append("no prompt and no derivation")
        if not m.get("palette"):
            mprob.append("no palette recorded")
        if list(m.get("output_dims", [])) != list(img.size):
            mprob.append(f"output_dims {m.get('output_dims')} != the file's {list(img.size)}")
        rec = m.get("replaces_reference") or {}
        if rec.get("replaces") != replaces or rec.get("missing"):
            mprob.append(f"no usable record of the file it replaces ({rec})")
        else:
            blob, err = _git_blob(args.shipped_ref, rec["served_path"])
            live_sha = _sha256_file(ref_path)
            if rec.get("sha256") != live_sha:
                mprob.append(f"the manifest's sha256 of {replaces} ({str(rec.get('sha256'))[:12]}) "
                             f"!= the served file's ({live_sha[:12]}) - the recorded comparison "
                             f"baseline is not the file on disk")
            if blob is not None and _sha256_bytes(blob) != live_sha:
                mprob.append(f"the served {replaces} differs from {args.shipped_ref} - the "
                             f"comparison baseline is not the shipped art")
        if kind == "tile" and (m.get("block_contrast") is None or m.get("log_contrast") is None):
            mprob.append("no contrast metrics recorded")
        if aid == "paper_stacks_lined":
            if not m.get("ruled_lines"):
                mprob.append("no `ruled_lines` record - the drawn lines must be declared, "
                             "otherwise a reader cannot tell them from model output")
            if m.get("page_cleanup") is None:
                mprob.append("no `page_cleanup` record - the erased model marks must be declared "
                             "with their pixel count, not silently dropped")
            if "ruled_lines" not in json.dumps(m.get("frame_derivation", {})):
                mprob.append("the derivation does not declare the drawn lines")
            if m.get("paper_tone_lut") is None:
                mprob.append("no `paper_tone_lut` record - a hue correction of the paper must be "
                             "declared, not silently applied")
            # A polish pass is about shape, not hue: the new file must sit on the SAME side of the
            # warm/cool paper divide as the file it replaces (the first roll came back 164 px cool
            # white against a 550 px warm cream original and a reviewer read it as another material).
            if old is not None:
                o_fam = G.paper_families(old)
                n_fam = G.paper_families(img)
                old_metric = o_fam["warm_paper_px"] - o_fam["cool_paper_px"]
                new_metric = n_fam["warm_paper_px"] - n_fam["cool_paper_px"]
                if o_fam["warm_paper_px"] > o_fam["cool_paper_px"] and \
                        n_fam["warm_paper_px"] <= n_fam["cool_paper_px"]:
                    mprob.append(f"paper hue regression: the shipped {replaces} is warm "
                                 f"({o_fam}) but the new file is cool ({n_fam}) - the polish "
                                 f"changed the material, not the shape")
                else:
                    report.ok(f"{aid}: warm-vs-cool paper px {o_fam} -> {n_fam} (the new file stays "
                              f"on the shipped file's side of the warm/cool divide)")
        if mprob:
            report.fail(f"{aid}.manifest.json: " + "; ".join(mprob))
        else:
            report.ok(f"{aid}.manifest.json: model+palette+prompt+seed+dims recorded, the file it "
                      f"replaces recorded WITH its sha256, and the comparison metrics stored")

    # ---- strip + sheets ------------------------------------------------------------------
    print("\nSTRIP, CONTACT SHEETS")
    for name, (home, members, size) in TICKET_D_POLISH_STRIP.items():
        p = os.path.join(root, home, f"{name}.png")
        if not os.path.exists(p):
            report.fail(f"{name}.png missing at {os.path.relpath(p, str(REPO))}")
            continue
        strip = Image.open(p).convert("RGBA")
        problems = []
        if strip.size != tuple(size):
            problems.append(f"size {strip.size} != {tuple(size)}")
        colours, off = palette_check(strip, allowed=G.PALETTE_RGB)
        if off:
            problems.append(f"{len(off)} off-palette colours")
        for slot, member in enumerate(members):
            tile_path = os.path.join(root, home, "tiles", f"{member}.png")
            if not os.path.exists(tile_path):
                problems.append(f"slot {slot}: tiles/{member}.png missing")
                continue
            if strip.crop((slot * 32, 0, (slot + 1) * 32, 32)).tobytes() != \
                    Image.open(tile_path).convert("RGBA").tobytes():
                problems.append(f"slot {slot} != {member}.png")
        if problems:
            report.fail(f"{name}: " + "; ".join(problems))
            print(f"{name:22s} FAIL: " + "; ".join(problems))
        else:
            report.ok(f"{name}: {strip.size[0]}x{strip.size[1]}, slots hold exactly "
                      f"{', '.join(members)} in the declared order")
            print(f"{name:22s} ok   {strip.size[0]}x{strip.size[1]} slots {members}")
    for home in ("lodge", "serverroom", "writer"):
        for name in ("contact_sheet.png", "contact_sheet_4x.png"):
            p = os.path.join(root, home, name)
            if os.path.exists(p):
                report.ok(f"{home}/{name} {Image.open(p).size[0]}x{Image.open(p).size[1]}")
            elif any(v[0] == home for v in TICKET_D_POLISH.values()):
                report.fail(f"{home}/{name} missing - the ticket asks for each polished asset NEXT "
                            f"TO the one it replaces")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print("RESULT: PASS - five NEW files under NEW names at exactly the sizes the SOU-216 table "
          "names, on the unchanged 61-colour palette; every shipped original still byte-identical "
          "to the shipped ref and every polish file absent from it; the soft tiles measurably LESS "
          "contrast than the files they replace on the metric that the owner's complaint is about; "
          "the two shelves measurably FULLER (a larger share of the 64x112 canvas opaque, "
          "measurably more gear-coloured pixels, status lights present and a plant present) than "
          "the shelves they replace; the paper carries >= 3 horizontal 1px line runs on >= 2 rows, "
          "each inside one contiguous page band, with no dark pixel outside a line run; a manifest "
          "next to every file recording the replaced file's sha256 and the comparison metrics; the "
          "strip in declared slot order; and a before/after contact sheet per home set.")
    return 0


# ---------------------------------------------------------------------------------------
# SOU-274/275: the v2.1.0 SEASONAL OUTDOOR sets (autumn, spring; SOU-276 adds summer).
#
# The oracle is the TICKET's table, typed out again below (TICKET_E_SEASONS), never the
# generator's own declarations - a gate that only compares an asset to the module that produced
# it agrees with itself by construction (the SOU-199 lesson).  The seasonal tickets share one
# table because they share one contract (four 32px slots in a 128x32 strip, three trees at
# 64x96 / 64x128 / 32x64 replacing the pines one for one) - but it is typed once PER SEASON on
# purpose, so a later ticket that changes one season's sizes cannot silently ride on the other's
# entry.  Four claims in the seasonal tickets are not covered by any earlier branch:
#
#   * the palette FAMILY.  "same palette family and pixel scale as the Lodge set. Start from the
#     Lodge palette and add <the season's colours>", so the declared palette must be a SUPERSET
#     of the lodge's - asserted as a set relation against `gen_lodge_art.PALETTE`, imported
#     rather than restated.  SOU-275 added the second half of that claim as a MEASUREMENT: every
#     extension colour must be at least MIN_PALETTE_DISTANCE from every lodge colour AND from
#     every other extension colour, which the autumn module had only claimed in prose.
#   * the CROSS-tile seam.  "ground_a, ground_b and path must tile seamlessly with themselves
#     and WITH EACH OTHER" - and self-periodicity does NOT imply mutual tileability, so every
#     ordered pair is measured across an explicit boundary.  The one pair that is a content
#     feature rather than a seam (the trodden path's own edge against the ground) is reported
#     with its number and excluded from the assertion, and the classification comes from
#     `gen_season_art.cross_seam_axes` so the generator and the gate cannot disagree.
#   * the FORM of the three trees.  "deciduous trees with <the season's> crowns and dark trunks
#     ... the trunk base at the bottom centre" - a numeric-only AC cannot see a missing crown,
#     so: the crown must carry the season's declared hue families, the trunk band must be
#     measurably darker than the crown, the bottom row must carry content at the canvas centre
#     column, and the bottom row must be a TRUNK (a narrow run) rather than a crown sitting on
#     the ground.  Spring adds a second, per-tree claim - "light-green deciduous trees, one
#     variant (tree_b) in pink blossom" names WHICH tree is which colour - so the dominant
#     family is asserted per tree, and "light" is measured against the pines these trees replace.
#   * the FLOWER DENSITY, spring only.  "ground_b (same with more flowers, so the two can
#     alternate)" is a comparison between two delivered files, so it is measured as one.
# ---------------------------------------------------------------------------------------
TICKET_E_AUTUMN = {
    "tile_px": 32,
    "strip": (128, 32),
    "slots": (("ground_a", "xy"), ("ground_b", "xy"), ("path", "xy"), ("edge", "x")),
    "props": {
        "tree_a": {"texture_px": (64, 96), "footprint_px": (64, 32), "lift_px": 64},
        "tree_b": {"texture_px": (64, 128), "footprint_px": (64, 32), "lift_px": 96},
        "tree_small": {"texture_px": (32, 64), "footprint_px": (32, 32), "lift_px": 32},
    },
    "ticket": "SOU-274",
}

# SOU-275's own copy of the same table.  Identical values, typed separately: the sizes and the
# slot order are the contract the SOU-275 ticket states in its own words, and if a future ticket
# moves them for one season the other season's entry must not move with it.
TICKET_E_SPRING = {
    "tile_px": 32,
    "strip": (128, 32),
    "slots": (("ground_a", "xy"), ("ground_b", "xy"), ("path", "xy"), ("edge", "x")),
    "props": {
        "tree_a": {"texture_px": (64, 96), "footprint_px": (64, 32), "lift_px": 64},
        "tree_b": {"texture_px": (64, 128), "footprint_px": (64, 32), "lift_px": 96},
        "tree_small": {"texture_px": (32, 64), "footprint_px": (32, 32), "lift_px": 32},
    },
    "ticket": "SOU-275",
}

TICKET_E_SEASONS = {"autumn": TICKET_E_AUTUMN, "spring": TICKET_E_SPRING}


SEASON_TRUNK_FAMILY = ("wood_deep", "wood_dark", "wood", "plank_dark", "earth")
# The 1px outline the STYLE string MANDATES is not a material, so it is excluded from the
# "what is this band made of" denominator.  Measured reason (first autumn pass): counting it as
# a non-brown pixel made tree_b's trunk band read 49% "declared dark-brown" and tree_small 31%
# when the actual browns were 46% and 27% of the band and 84% of the non-outline pixels - a
# false failure produced by the checker, not by the art (the same class as a check that
# measures a quantity the AC never asked about).
SEASON_OUTLINE_FAMILY = ("outline", "ink", "metal_dark")
# "Dark trunks" (the ticket) as a measurement: a palette colour this dark is a trunk/shadow
# colour.  The measured share is printed, and the threshold sits far above what correct art
# produces (all three trees measured ~100%) so it catches a LIGHT trunk without risking a
# correct one.
SEASON_DARK_LUMINANCE = 90.0
SEASON_DARK_MIN_SHARE = 0.9
# The crown families are measured by PIXEL PREDICATE, not by palette name: what the ticket asks
# for is a colour the eye can see, and naming a family after the colours I happened to add would
# let a crown of the lodge's amber ramp pass as "ochre" while the ochre the ticket asked for is
# absent.
#
# Autumn (warm ramp), boundaries drawn where the ramp actually divides and CORRECTED once on the
# measurement rather than to make a red gate green.  First attempt put ochre at 42-65 degrees,
# which is above goldenrod (#DAA520, 43 deg) and far above the common ochre #CC7722 (32 deg) -
# so it called the art's golden-amber leaves "orange" and reported ochre:0% for every tree.
# Reading the actual crown pixels: the ramp has three separable regions - 6-16 deg
# (scarlet/crimson), 26-31 deg (pumpkin orange), 35-40 deg (golden amber: amber #e8a33d 35.8,
# leaf_orange_light #f7b452 35.6, brass #b98f3c 39.8) - which is exactly red, orange and ochre.
# The boundary is now 34 deg.
#
# NEGATIVE CONTROL, and this is why the correction is not goalpost-moving.  The first FLUX pass
# (seeds 27428/27435/27442, kept on disk as *.rejected-r1.raw.png) measured red 65%/27%/60%,
# orange 27%/27%/23% and ochre 4.9%/0.9%/9.3% per tree under the CORRECTED bands - so two of its
# three crowns still fail the per-tree all-three assertion below, which is exactly the defect
# that caused the re-roll.  (The first version of this check asserted the three families across
# the SET rather than per tree, and that weaker form would have PASSED the rejected pass on the
# strength of the sapling alone; it was tightened to per-tree on that measurement.)  A boundary
# that only failed to fire on the bad pass would have been a check wired to pass.
#
# Spring (green + blossom) CANNOT use hue for the blossom half: a soft pink is a warm hue.
# #dd7aa6 (blossom) has hue 26.7 deg and #f2adc9 24.4 deg - both inside autumn's "orange" band -
# so a hue band would call the blossoms orange and a hue-based check would be measuring nothing.
# The discriminator is the BLUE-vs-GREEN relation the reporters had to learn for the same reason
# (SOU-192): a pink has b >= g, while every brown, tan, cream and orange in the palette has
# b < g.  Checked against the whole palette: the pink predicate selects exactly the five
# `blossom*` colours and nothing else.
SEASON_CROWN_FAMILIES = {
    "autumn": {
        "red": lambda p: 0.0 <= _hue(p) < 20.0,
        "orange": lambda p: 20.0 <= _hue(p) < 34.0,
        "ochre": lambda p: 34.0 <= _hue(p) < 65.0,
    },
    "spring": {
        "green": lambda p: p[1] > p[0] and p[1] >= p[2],
        "blossom": lambda p: p[0] > p[1] and p[2] >= p[1],
    },
}
SEASON_CROWN_MIN_SHARE = 0.05
# Per-season rules.  `require_all` is the autumn claim ("a MIX of three colours in roughly equal
# parts ... deep scarlet red, bright orange, and golden ochre-yellow"): every tree must carry all
# three.  `dominant` is the spring claim, which is per TREE because the ticket names which tree
# is which colour ("light-green deciduous trees, one variant (tree_b) in pink blossom") - a
# set-level check would pass a set of three identical blossom trees.
SEASON_CROWN_RULES = {
    "autumn": {
        "require_all": ("red", "orange", "ochre"),
        "min_share": SEASON_CROWN_MIN_SHARE,
        "dominant": {},
        "what": "an orange, red AND ochre crown on every tree",
        "set_what": "deciduous trees with orange, red AND ochre crowns",
    },
    "spring": {
        "require_all": (),
        "min_share": SEASON_CROWN_MIN_SHARE,
        "dominant": {"tree_a": "green", "tree_b": "blossom", "tree_small": "green"},
        "dominant_min": 0.5,
        "light_green": True,
        "what": "a light-green crown (tree_b: pink blossom)",
        "set_what": "light-green deciduous trees, one variant (tree_b) in pink blossom",
    },
}
# "Light-green" (SOU-275) as a measurement whose ORACLE is the tree this set replaces.  The floor
# is the brightest green of the lodge's pine ramp - i.e. a spring crown is "light green" exactly
# when its green pixels are brighter than the brightest green of the winter tree it stands in
# for.  The ramp itself is the negative control: pine_deep 38.3, pine_dark 51.5, pine 74.4,
# pine_light 99.9, so a crown drawn in those greens fails the floor rather than defining it.
# Resolved in main_season from gen_lodge_art.PALETTE (no magic number in the source).
SEASON_PINE_RAMP = ("pine_deep", "pine_dark", "pine", "pine_light")
# The spring ground: "fresh green grass with tiny pink and white flower dots" (ground_a, "same
# with MORE flowers" ground_b).  Both halves are measured from the two PNGs - the base colour
# must be a green-family palette colour, and ground_b must carry strictly more blossom pixels
# than ground_a.
SEASON_FLOWER_FAMILY = "blossom"
SEASON_SEAM_LIMIT = 1.05
# The bottom row of a tree is its trunk.  A run wider than this share of the canvas is a crown
# touching the ground, not a trunk: the ticket says the trees have "dark trunks ... the trunk
# base at the bottom centre", and a bush with no visible trunk is the failure this catches.
SEASON_TRUNK_MAX_ROW_SHARE = 0.5


def _row_runs(px, y, w) -> list[int]:
    """Lengths of the contiguous opaque runs in one row."""
    runs, run = [], 0
    for x in range(w):
        if px[x, y][3]:
            run += 1
        elif run:
            runs.append(run)
            run = 0
    if run:
        runs.append(run)
    return runs


def _mean(values) -> float:
    return sum(values) / len(values) if values else 0.0


def _lum(p) -> float:
    return 0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2]


def _hue(p) -> float:
    """HSV hue in degrees - what the eye calls the colour, independent of my palette names."""
    import colorsys
    r, g, b = (v / 255.0 for v in p[:3])
    return colorsys.rgb_to_hsv(r, g, b)[0] * 360.0


def main_season(args, season_name: str) -> int:
    import gen_lodge_art as L
    import gen_season_art as S

    # The ticket table for THIS season (the oracle), and the season's own rules.
    TICKET_E = TICKET_E_SEASONS[season_name]
    ticket = TICKET_E["ticket"]
    crown_rules = SEASON_CROWN_RULES[season_name]
    crown_families = SEASON_CROWN_FAMILIES[season_name]
    # The "light-green" floor is the brightest green of the pine ramp this set replaces.
    light_floor = max(0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
                      for c in (S._hex_to_rgb(dict(L.PALETTE)[n]) for n in SEASON_PINE_RAMP))

    root = os.path.abspath(args.root or str(S.out_dir(season_name)))
    served = str(S.served_dir(season_name))
    report = Report()
    pal_rgb = S.palette_rgb(season_name)
    pal_hex = S.palette_hex(season_name)
    by_name = dict(S.palette_of(season_name))

    if not os.path.isdir(root):
        print(f"no art set at {root} - run the assemble pass first", file=sys.stderr)
        return EXIT_ERROR

    print("=" * 96)
    print(f"{season_name} v3 outdoor art set check ({ticket})   root={root}")
    print(f"palette: {len(pal_hex)} declared colours ({len(L.PALETTE)} lodge family + "
          f"{len(S.SEASONS[season_name]['extra'])} {season_name})")
    print("=" * 96)

    # ---- the generator's declaration vs the TICKET table ------------------------------
    declared_order = [n for n, _c, _s, _spec in S.SEASONS[season_name]["tiles"]]
    ticket_order = [n for n, _ax in TICKET_E["slots"]]
    if declared_order != ticket_order:
        report.fail(f"the generator's slot order {declared_order} != the {ticket} ticket table "
                    f"{ticket_order}")
    else:
        report.ok(f"the generator declares the ticket's four slots in order: "
                  f"{', '.join(declared_order)}")
    declared_seam = {n: s for n, _c, s, _spec in S.SEASONS[season_name]["tiles"]}
    seam_bad = [f"{n}: generator says {declared_seam.get(n)!r}, ticket says {ax!r}"
                for n, ax in TICKET_E["slots"] if declared_seam.get(n) != ax]
    if seam_bad:
        report.fail("seam-axis declaration does not match the ticket: " + "; ".join(seam_bad))
    else:
        report.ok("the generator declares the ticket's seam axes: "
                  + ", ".join(f"{n}={ax}" for n, ax in TICKET_E["slots"]))
    gen_props = {a["id"]: a for a in S.SEASONS[season_name]["props"]}
    if set(gen_props) != set(TICKET_E["props"]):
        report.fail(f"the generator carries {sorted(gen_props)} != the ticket's "
                    f"{sorted(TICKET_E['props'])}")
    else:
        report.ok(f"the generator carries the ticket's three trees: "
                  f"{', '.join(sorted(gen_props))}")
    for aid, want in TICKET_E["props"].items():
        got = gen_props.get(aid)
        if got is None:
            continue
        if tuple(got["texture_px"]) != want["texture_px"]:
            report.fail(f"{aid}: generator texture_px {got['texture_px']} != ticket "
                        f"{list(want['texture_px'])}")
        if tuple(got["footprint_px"]) != want["footprint_px"]:
            report.fail(f"{aid}: generator footprint_px {got['footprint_px']} != ticket "
                        f"{list(want['footprint_px'])}")
        if got["lift_px"] != want["lift_px"]:
            report.fail(f"{aid}: generator lift_px {got['lift_px']} != ticket "
                        f"{want['lift_px']}")
    if not [f for f in report.failures if "generator" in f]:
        report.ok("every tree's declared size, footprint and lift matches the ticket table")

    # ---- the palette FAMILY (the "start from the Lodge palette" clause) ----------------
    pal_path = os.path.join(root, "palette.json")
    if not os.path.exists(pal_path):
        report.fail("palette.json missing - the palette is not declared anywhere")
    else:
        declared = json.load(open(pal_path))
        hexes = declared.get("hex") or []
        if hexes != pal_hex:
            report.fail(f"palette.json declares {len(hexes)} colours but the generator declares "
                        f"{len(pal_hex)}")
        else:
            report.ok(f"palette.json declares {len(hexes)} colours, identical to the generator")
        lodge_hex = [h for n, h in L.PALETTE]
        missing = [h for h in lodge_hex if h not in hexes]
        if missing:
            report.fail(f"the declared palette is not the lodge family plus an extension: "
                        f"{len(missing)} of the lodge's {len(lodge_hex)} colours are absent, "
                        f"e.g. {missing[:3]} - {ticket} requires starting from the Lodge palette")
        else:
            report.ok(f"the declared palette is a SUPERSET of the lodge's {len(lodge_hex)} "
                      f"colours: same family, plus {len(hexes) - len(lodge_hex)} "
                      f"{season_name} colours")

        # The other half of "start from the Lodge palette and add <colours>": the additions must
        # be ADDITIONS, i.e. reachable and distinct.  A colour a hair away from a lodge one is a
        # palette entry the nearest-colour quantiser can never select, which is a dead entry that
        # still looks like a declared colour.  Measured for both seasons; the autumn module had
        # only claimed this in a comment.
        import math
        ext = S.SEASONS[season_name]["extra"]
        floor = S.MIN_PALETTE_DISTANCE
        closest = None
        for name, hexv in ext:
            rgb = S._hex_to_rgb(hexv)
            for other_name, other_hex in L.PALETTE:
                d = math.dist(rgb, S._hex_to_rgb(other_hex))
                if d < floor:
                    report.fail(f"palette extension colour {name} {hexv} is only {d:.1f} units "
                                f"from the lodge's {other_name} {other_hex} (floor {floor}) - a "
                                f"near-duplicate the quantiser can never reach")
                if closest is None or d < closest[0]:
                    closest = (d, name, other_name)
            for other_name, other_hex in ext:
                if other_name == name:
                    continue
                d = math.dist(rgb, S._hex_to_rgb(other_hex))
                if d < floor:
                    report.fail(f"palette extension colours {name} {hexv} and {other_name} "
                                f"{other_hex} are only {d:.1f} units apart (floor {floor})")
        if closest:
            report.ok(f"every one of the {len(ext)} {season_name} extension colours is at least "
                      f"{floor:.1f} units from every lodge colour and from every other extension "
                      f"colour (closest pair: {closest[1]} vs the lodge's {closest[2]}, "
                      f"{closest[0]:.1f})")

    # ---- tiles -----------------------------------------------------------------------
    print("\nTILES (32x32, full-bleed; seam asserted only on the axes the slot DECLARES)")
    print(f"{'slot':>4s} {'name':15s} {'axes':>4s} {'colours':>7s} {'transp':>7s} "
          f"{'wrap/max x':>10s} {'y':>6s}  result")
    for slot, (name, axes) in enumerate(TICKET_E["slots"]):
        path = os.path.join(root, "tiles", f"{name}.png")
        if not os.path.exists(path):
            report.fail(f"tiles/{name}.png missing (slot {slot})")
            continue
        img = Image.open(path)
        colours, off = palette_check(img, allowed=pal_rgb)
        alpha = alpha_profile(img)
        wrap = wrap_vs_max(img)
        problems = []
        if img.size != (TICKET_E["tile_px"], TICKET_E["tile_px"]):
            problems.append(f"size {img.size} != ({TICKET_E['tile_px']}, {TICKET_E['tile_px']})")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if alpha["transparent"]:
            problems.append(f"{alpha['transparent']} transparent pixels (a tile is full-bleed)")
        if "x" in axes and wrap["wrap_vs_max_x"] > SEASON_SEAM_LIMIT:
            problems.append(f"x seam: wrap step is {wrap['wrap_vs_max_x']}x the largest interior "
                            f"step (limit {SEASON_SEAM_LIMIT})")
        if "y" in axes and wrap["wrap_vs_max_y"] > SEASON_SEAM_LIMIT:
            problems.append(f"y seam: wrap step is {wrap['wrap_vs_max_y']}x the largest interior "
                            f"step (limit {SEASON_SEAM_LIMIT})")
        print(f"{slot:>4d} {name:15s} {axes:>4s} {len(colours):>7d} {alpha['transparent']:>7d} "
              f"{wrap['wrap_vs_max_x']:>10} {wrap['wrap_vs_max_y']:>6}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"tiles/{name}: " + "; ".join(problems))
        else:
            report.ok(f"tiles/{name} (slot {slot}): 32x32, {len(colours)} palette colours, "
                      f"opaque, wrap/max x={wrap['wrap_vs_max_x']} y={wrap['wrap_vs_max_y']} "
                      f"checked={axes}")

    # ---- the spring ground (SOU-275 only): green base, MORE flowers on ground_b -------
    if season_name == "spring":
        print("\nSPRING GROUND (the ticket's two ground claims, measured from the two PNGs)")
        counts = {}
        problems = []
        for name in ("ground_a", "ground_b"):
            path = os.path.join(root, "tiles", f"{name}.png")
            if not os.path.exists(path):
                problems.append(f"tiles/{name}.png missing")
                continue
            img = Image.open(path).convert("RGBA")
            px = img.load()
            w, h = img.size
            opaque = [px[x, y][:3] for y in range(h) for x in range(w) if px[x, y][3]]
            modal = Counter(opaque).most_common(1)[0][0]
            flowers = sum(1 for p in opaque if crown_families[SEASON_FLOWER_FAMILY](p))
            counts[name] = flowers
            green_base = crown_families["green"](modal)
            print(f"        {name:9s} base=#{modal[0]:02x}{modal[1]:02x}{modal[2]:02x} "
                  f"green={green_base}  flower px={flowers:3d} / {len(opaque)}")
            if not green_base:
                problems.append(f"{name}'s base colour #{modal[0]:02x}{modal[1]:02x}{modal[2]:02x} "
                                f"is not a green - the ticket says fresh green grass")
        if "ground_a" in counts and "ground_b" in counts:
            more = counts["ground_b"] > counts["ground_a"]
            print(f"        ground_b - ground_a = {counts['ground_b'] - counts['ground_a']:+d} "
                  f"blossom px  (ticket: 'same with MORE flowers')")
            if not more:
                problems.append(f"ground_b carries {counts['ground_b']} flower pixels against "
                                f"ground_a's {counts['ground_a']} - the ticket says ground_b is "
                                f"the same with MORE flowers")
        if problems:
            report.fail("spring ground: " + "; ".join(problems))
        else:
            report.ok(f"both ground tiles stand on a green-family base colour and ground_b "
                      f"carries {counts['ground_b']} blossom pixels against ground_a's "
                      f"{counts['ground_a']} (strictly more flowers, as the ticket says)")

    # ---- the strip: the file the renderer actually preloads ---------------------------
    print(f"\nTILESET STRIP ({TICKET_E['strip'][0]}x{TICKET_E['strip'][1]}, "
          f"{len(ticket_order)} slots) - order and content vs the ticket table")
    strip_path = os.path.join(root, "tiles.png")
    if not os.path.exists(strip_path):
        report.fail("tiles.png missing - this is the file main.js preloads")
    else:
        strip = Image.open(strip_path).convert("RGBA")
        if strip.size != TICKET_E["strip"]:
            report.fail(f"tiles.png is {strip.size}, expected {TICKET_E['strip']}")
        else:
            report.ok(f"tiles.png is {strip.size[0]}x{strip.size[1]} = {len(ticket_order)} "
                      f"tiles of {TICKET_E['tile_px']}px")
        colours, off = palette_check(strip, allowed=pal_rgb)
        if off:
            report.fail(f"tiles.png has {len(off)} off-palette colours e.g. {hexs(off[0])}")
        else:
            report.ok(f"tiles.png uses {len(colours)} palette colours only")
        mismatched = []
        for slot, name in enumerate(ticket_order):
            tile_path = os.path.join(root, "tiles", f"{name}.png")
            if not os.path.exists(tile_path):
                continue
            want = Image.open(tile_path).convert("RGBA")
            got = strip.crop((slot * TICKET_E["tile_px"], 0,
                              (slot + 1) * TICKET_E["tile_px"], TICKET_E["tile_px"]))
            if got.tobytes() != want.tobytes():
                mismatched.append(f"slot {slot} != {name}.png")
        if mismatched:
            report.fail("tiles.png slot order/content does not match the declared order: "
                        + "; ".join(mismatched))
        else:
            report.ok(f"every slot holds exactly the tile the ticket's order names "
                      f"({', '.join(ticket_order)})")

    # ---- the cross-tile seam (the ticket's "with themselves AND WITH EACH OTHER") ------
    print("\nCROSS-TILE SEAM (RE-MEASURED from the tiles on disk, not read from the record)")
    cross_path = os.path.join(root, "cross_seam.json")
    ground = [n for n, ax in TICKET_E["slots"] if ax == "xy"]
    if not os.path.exists(cross_path):
        report.fail("cross_seam.json missing - the cross-tile seam was never recorded, and "
                    "self-periodicity does not imply mutual tileability")
    else:
        cross = json.load(open(cross_path))
        for a in ground:
            for b in ground:
                key = f"{a}|{b}"
                rec = cross.get(key)
                if rec is None:
                    report.fail(f"cross_seam.json has no entry for {key}")
                    continue
                pa = os.path.join(root, "tiles", f"{a}.png")
                pb = os.path.join(root, "tiles", f"{b}.png")
                if not (os.path.exists(pa) and os.path.exists(pb)):
                    report.fail(f"cross seam {key}: a tile file is missing")
                    continue
                # MEASURE the join from the bytes.  An earlier version of this check read the
                # ratios out of cross_seam.json, which meant a mutated tile passed: the record
                # is a claim and the PNG is the artifact (SOU-142, "measure the artifact, never
                # the manifest").  The RED battery caught it - the "ground_b recoloured so it no
                # longer joins ground_a" mutation did not fire.
                m = S.cross_vs_max(Image.open(pa), Image.open(pb))
                axes = S.cross_seam_axes(a, b)
                kind = S.cross_seam_kind(a, b)
                problems = []
                if rec.get("kind") != kind or rec.get("asserted_axes") != axes:
                    problems.append(f"recorded kind/axes {rec.get('kind')!r}/"
                                    f"{rec.get('asserted_axes')!r} != the module's {kind!r}/"
                                    f"{axes!r}")
                if rec.get("ratio_x") != m["ratio_x"] or rec.get("ratio_y") != m["ratio_y"]:
                    problems.append(f"the record has drifted from the tiles: recorded "
                                    f"x={rec.get('ratio_x')} y={rec.get('ratio_y')}, measured "
                                    f"x={m['ratio_x']} y={m['ratio_y']}")
                if "x" in axes and m["ratio_x"] > SEASON_SEAM_LIMIT:
                    problems.append(f"x join {m['ratio_x']}x the worst interior step "
                                    f"(limit {SEASON_SEAM_LIMIT})")
                if "y" in axes and m["ratio_y"] > SEASON_SEAM_LIMIT:
                    problems.append(f"y join {m['ratio_y']}x the worst interior step "
                                    f"(limit {SEASON_SEAM_LIMIT})")
                line = (f"{a:9s} | {b:9s} x={m['ratio_x']:>5} y={m['ratio_y']:>5}  "
                        f"{kind:10s} asserted={axes}")
                print(f"        {line}  {'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
                if problems:
                    report.fail(f"cross seam {key}: " + "; ".join(problems))
                elif kind == "path-edge":
                    report.ok(f"cross seam {key}: the path's own edge against the ground "
                              f"(x={m['ratio_x']}x) - reported, not a grid; the vertical join IS "
                              f"asserted and measures y={m['ratio_y']}x")
                else:
                    report.ok(f"cross seam {key}: {kind}, both axes seamless "
                              f"(x={m['ratio_x']}x y={m['ratio_y']}x)")

    # ---- props: size, alpha, palette, and the FORM the ticket describes ---------------
    print("PROPS (exact declared size; the season's crown families; dark trunk, base at the "
          "bottom centre)")
    print(f"{'asset':12s} {'shipped':>9s} {'declared':>9s} {'crown families':>34s} "
          f"{'lum t/c':>8s} {'dark':>5s} {'brown':>6s} {'base run':>9s}  result")
    trunk_rgb = {S._hex_to_rgb(by_name[n]) for n in SEASON_TRUNK_FAMILY}
    outline_rgb = {S._hex_to_rgb(by_name[n]) for n in SEASON_OUTLINE_FAMILY}
    crown_families_seen: set[str] = set()
    for aid, want in TICKET_E["props"].items():
        path = os.path.join(root, "props", f"{aid}.png")
        if not os.path.exists(path):
            report.fail(f"props/{aid}.png missing")
            continue
        img = Image.open(path)
        w, h = img.size
        want_px = want["texture_px"]
        px = img.load()
        colours, off = palette_check(img, allowed=pal_rgb)
        alpha = alpha_profile(img)
        resid = magenta_residue(img, palette=pal_rgb)
        bottom = content_bottom_row(img)
        problems = []
        if (w, h) != want_px:
            problems.append(f"size {w}x{h} != declared {want_px[0]}x{want_px[1]}")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if not alpha["has_real_alpha"]:
            problems.append("no real alpha (the background must be transparent)")
        if alpha["partial"]:
            problems.append(f"{alpha['partial']} partial-alpha pixels")
        if resid["strong_magenta"] or resid["fringe"]:
            problems.append(f"magenta residue {resid}")
        if bottom != h - 1:
            problems.append(f"content stops at row {bottom} of {h} - the footprint's bottom "
                            f"edge is where the renderer anchors, so the tree would float")

        crown = [px[x, y] for y in range(0, int(h * 0.6)) for x in range(w) if px[x, y][3]]
        trunk = [px[x, y] for y in range(int(h * 0.75), h) for x in range(w) if px[x, y][3]]
        fam_hits = {name: sum(1 for p in crown if pred(p))
                    for name, pred in crown_families.items()}
        fam_share = {k: v / max(len(crown), 1) for k, v in fam_hits.items()}
        present = sorted(k for k, v in fam_share.items() if v >= crown_rules["min_share"])
        crown_families_seen.update(present)
        crown_txt = " ".join(f"{k}:{fam_share[k]:.0%}" for k in crown_families)
        if crown_rules["require_all"] and set(present) != set(crown_rules["require_all"]):
            problems.append(f"the crown carries only {present or 'no'} family at >= "
                            f"{crown_rules['min_share']:.0%} ({crown_txt}) - the ticket says each "
                            f"tree has {crown_rules['what']}")
        want_dominant = crown_rules["dominant"].get(aid)
        if want_dominant:
            best = max(fam_share, key=lambda k: fam_share[k])
            if best != want_dominant or fam_share[want_dominant] < crown_rules["dominant_min"]:
                problems.append(f"the crown's dominant family is {best} "
                                f"({fam_share[best]:.0%}) and {want_dominant} is "
                                f"{fam_share[want_dominant]:.0%}; the ticket says {aid} has "
                                f"{crown_rules['what']}, so {want_dominant} must be dominant at "
                                f">= {crown_rules['dominant_min']:.0%}")
        # "light-green": measured against the winter pine ramp this tree replaces.
        green_pred = crown_families.get("green")
        green_lum = None
        if crown_rules.get("light_green") and green_pred:
            greens = [p for p in crown if green_pred(p)]
            if not greens:
                problems.append("no green-family pixel in the crown at all")
            else:
                green_lum = _mean([_lum(p) for p in greens])
                if want_dominant == "green" and green_lum <= light_floor:
                    problems.append(f"the crown's green pixels average luminance {green_lum:.1f}, "
                                    f"at or below the brightest green of the pine it replaces "
                                    f"({light_floor:.1f}) - the ticket says LIGHT green")

        crown_l = _mean([_lum(p) for p in crown])
        trunk_l = _mean([_lum(p) for p in trunk])
        lum_txt = f"{trunk_l:.0f}/{crown_l:.0f}"
        if not trunk:
            problems.append("no opaque pixel in the trunk band (the bottom quarter)")
            dark_share, brown_share = 0.0, 0.0
        else:
            if trunk_l >= crown_l:
                problems.append(f"the trunk band is not darker than the crown ({trunk_l:.0f} vs "
                                f"{crown_l:.0f}) - the ticket says dark trunks")
            dark_share = sum(1 for p in trunk if _lum(p) < SEASON_DARK_LUMINANCE) / len(trunk)
            if dark_share < SEASON_DARK_MIN_SHARE:
                problems.append(f"only {dark_share:.0%} of the trunk band is dark (luminance < "
                                f"{SEASON_DARK_LUMINANCE:.0f}) - the ticket says dark trunks")
            material = [p for p in trunk if p[:3] not in outline_rgb]
            brown_share = (sum(1 for p in material if p[:3] in trunk_rgb) / len(material)
                           if material else 0.0)
            if not any(p[:3] in trunk_rgb for p in trunk):
                problems.append("no declared dark-brown material anywhere in the trunk band")

        runs = _row_runs(px, h - 1, w)
        run = max(runs) if runs else 0
        centre = range(max(0, w // 2 - 2), min(w, w // 2 + 3))
        if not any(px[x, h - 1][3] for x in centre):
            problems.append("the bottom row carries no content within 2px of the canvas centre "
                            "column - the trunk base is not at the bottom centre")
        if run > SEASON_TRUNK_MAX_ROW_SHARE * w:
            problems.append(f"the bottom row is a {run}px run on a {w}px canvas - that is a crown "
                            f"sitting on the ground, not a trunk")
        crown_extra = f" greenlum={green_lum:.0f}" if green_lum is not None else ""
        print(f"{aid:12s} {f'{w}x{h}':>9s} {f'{want_px[0]}x{want_px[1]}':>9s} "
              f"{crown_txt:>34s} {lum_txt:>8s} {dark_share:>5.0%} {brown_share:>6.0%} "
              f"{run:>9d}  {'ok' if not problems else 'FAIL: ' + '; '.join(problems)}"
              f"{crown_extra}")
        if problems:
            report.fail(f"{aid}: " + "; ".join(problems))
        else:
            report.ok(f"{aid}: {w}x{h} exactly, {len(colours)} palette colours, real alpha, no "
                      f"magenta, content reaches the canvas bottom (row {bottom}); crown families "
                      f"{crown_txt} ({present}){crown_extra}; trunk band is darker than the crown "
                      f"({trunk_l:.0f} vs {crown_l:.0f}), {dark_share:.0%} of it dark and "
                      f"{brown_share:.0%} of its non-outline pixels a declared dark brown; bottom "
                      f"row is a {run}px trunk run with content at the centre column")
    if crown_rules["require_all"]:
        if crown_families_seen != set(crown_rules["require_all"]):
            report.fail(f"across the three trees the crowns carry only "
                        f"{sorted(crown_families_seen)} of the ticket's "
                        f"{'/'.join(crown_rules['require_all'])} - the ticket describes the set as "
                        f"{crown_rules['set_what']}")
        else:
            report.ok(f"across the three trees the crowns carry all of the ticket's families: "
                      f"{', '.join(crown_rules['require_all'])}")
    else:
        report.ok(f"every tree's crown is the colour the ticket names for it "
                  f"({crown_rules['set_what']})")

    # ---- manifests, in BOTH trees, and the served copy being byte-identical -----------
    print("\nMANIFESTS + SERVED COPY (a manifest next to every PNG; the served file is the "
          "generated file)")
    published = ["tiles.png"] + [f"props/{a}.png" for a in TICKET_E["props"]]
    for rel in published:
        gen = os.path.join(root, rel)
        if not os.path.exists(gen):
            continue  # already reported as missing above
        man = gen + ".manifest.json"
        problems = []
        if not os.path.exists(man):
            report.fail(f"{rel}: no manifest next to it (the ticket asks for one per PNG)")
            print(f"{rel:22s} FAIL: no manifest")
            continue
        try:
            m = json.load(open(man))
        except Exception as exc:  # noqa: BLE001
            report.fail(f"{rel}.manifest.json is not valid JSON ({exc})")
            continue
        if m.get("model") != S.MODEL_ID:
            problems.append(f"model {m.get('model')!r} != {S.MODEL_ID!r}")
        if m.get("lora_strength") != 0.0:
            problems.append("lora_strength is not 0.0 (the LoRA blacks out - ADR-060)")
        if not (m.get("prompt") or m.get("derivation")):
            problems.append("no prompt and no derivation")
        if m.get("palette") != pal_hex:
            problems.append("the manifest's palette is not the declared one")
        if list(m.get("output_dims") or []) != list(Image.open(gen).size):
            problems.append(f"output_dims {m.get('output_dims')} != the file's "
                            f"{list(Image.open(gen).size)}")
        if rel.startswith("props/") and not m.get("seed"):
            problems.append("a generated prop with no seed")
        srv = os.path.join(served, rel)
        if not os.path.exists(srv):
            problems.append(f"not published: the renderer loads assets/seasons/{season_name}/{rel}")
        elif _sha256_file(srv) != _sha256_file(gen):
            problems.append("the served copy is NOT byte-identical to the generated file")
        print(f"{rel:22s} {str(m.get('kind')):9s} {str(m.get('seed')):>8s}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"{rel}: " + "; ".join(problems))
        else:
            report.ok(f"{rel}: manifest carries model+palette+prompt+dims, seed="
                      f"{m.get('seed') if m.get('seed') is not None else 'n/a (drawn)'}, and the "
                      f"served copy is byte-identical")

    print("\n" + "=" * 96)
    print("PER-ASSET EVIDENCE (the AC record; it prints on PASS too)")
    for line in report.lines:
        print(line)
    print("=" * 96)
    if report.failures:
        print(f"RESULT: FAIL - {len(report.failures)} finding(s)")
        for f in report.failures:
            print(f"  - {f}")
        return EXIT_FAIL
    print(f"RESULT: PASS - the {season_name} set is the ticket's four 32px slots in order in a "
          f"128x32 strip plus the three trees at exactly 64x96 / 64x128 / 32x64, on a palette "
          f"that is a superset of the lodge's with every extension colour measurably distinct, "
          f"with every declared seam axis measured (self AND cross-tile, the path's own edge "
          f"reported rather than asserted), every crown carrying {crown_rules['what']}, trunk "
          f"bands measurably darker than their crowns with the base at the bottom centre, a "
          f"manifest next to every PNG, and every served file byte-identical to the generated "
          f"one.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
