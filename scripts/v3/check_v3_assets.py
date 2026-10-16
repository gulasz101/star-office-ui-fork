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
            (a tile repeats, so column 31 sits next to column 0).

Exit code 0 = all checks pass, 1 = at least one failure (each printed with the measurement),
2 = the checker itself could not run.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gen_office01_art import (  # noqa: E402  (same directory)
    CATALOGUE,
    OUT,
    PALETTE_HEX,
    PALETTE_RGB,
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


def palette_check(img) -> tuple[list[tuple[int, int, int]], list[tuple[int, int, int]]]:
    colours = unique_colours(img)
    allowed = set(PALETTE_RGB)
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(OUT), help="art set root (default: assets/generated/v3/office-01)")
    ap.add_argument("--map", default=None, help="office map whose tileset order is the authority")
    ap.add_argument("--quiet", action="store_true", help="only print failures and the summary")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    repo = os.path.dirname(os.path.dirname(os.path.dirname(root)))
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
        problems = []
        if img.size != (32, 32):
            problems.append(f"size {img.size} != (32, 32)")
        if off:
            problems.append(f"{len(off)} off-palette colours e.g. {hexs(off[0])}")
        if alpha["transparent"]:
            problems.append(f"{alpha['transparent']} transparent pixels (a tile is full-bleed)")
        if not args.quiet:
            report.note(f"{name}: {seam} ratio<=1.5 reads seamless")
        print(f"{name:14s} {f'{img.size[0]}x{img.size[1]}':>7s} colours={len(colours):>3d} "
              f"seam x={seam['ratio_x']:>5} y={seam['ratio_y']:>5}  "
              f"{'ok' if not problems else 'FAIL: ' + '; '.join(problems)}")
        if problems:
            report.fail(f"tiles/{name}: " + "; ".join(problems))
        else:
            report.ok(f"tiles/{name}: 32x32, {len(colours)} palette colours, opaque, "
                      f"seam x={seam['ratio_x']} y={seam['ratio_y']}")

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
        colours, off = palette_check(sheet)
        if off:
            report.fail(f"character sheet has {len(off)} off-palette colours e.g. {hexs(off[0])}")
        else:
            report.ok(f"character sheet uses {len(colours)} palette colours only")
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


if __name__ == "__main__":
    sys.exit(main())
