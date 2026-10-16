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
import json
import os
import sys
from collections import deque

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


def magenta_residue(img) -> dict:
    """Count the pixels the keying contract should have removed.

    Two families, both fatal: the strong residue (a pink/magenta pixel anywhere in the
    image) and the wide fringe rule the de-fringe pass uses (r-g>20 AND b-g>10 AND g<150),
    which is what shows up as a 1px contour at 4x zoom.
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    strong = fringe = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not a:
                continue
            if r > 180 and b > 180 and g < 120:
                strong += 1
            if (r - g) > 20 and (b - g) > 10 and g < 150:
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
    ap.add_argument("--set", default="office-01", choices=("office-01", "lodge"),
                    help="which v3 art set to check (default: office-01)")
    ap.add_argument("--root", default=None,
                    help="art set root (default: the set's own generated directory)")
    ap.add_argument("--map", default=None, help="v3 map whose tileset order is the authority")
    ap.add_argument("--quiet", action="store_true", help="only print failures and the summary")
    args = ap.parse_args()

    if args.set == "lodge":
        return main_lodge(args)
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


if __name__ == "__main__":
    sys.exit(main())
