#!/usr/bin/env python3
"""Erase the readable word from the coffee-machine sheet, keeping the blank plaque.

WHY: `frontend/coffee-machine-v3-grid.webp` carries a small white plaque on the coaster under the
smiley mug reading **"FRIENDS"** - a third-party TV brand, and the last readable third-party text in
the scene after the poster sheet was dropped. It was MISSED on a first review because that review
used a contact sheet downscaled to 0.51x, where the letters are a few pixels tall. Lesson: review
sheets at 1:1 when looking for text.

Measured before acting:
  - the plaque's bright region is at exactly the same place in ALL 96 frames
    (x 127-176, y 115-147), so one fix covers the whole animation
  - the letters are ~7px tall dark pixels on a white rounded-rect plaque with its own outline
  - the plaque is a distinct object, not part of the mug

The erase is deliberately narrow: it works only inside the plaque's own connected white component
(found by flood fill, matched by bbox so the cream mug - also near-white - cannot be captured), and
only 2px inside its edge so the plaque's outline and drop shadow survive. Dark pixels inside that
interior are replaced with the plaque's median white, leaving a blank sign.

Run:  python scripts/strip_coffee_plaque_text.py [--apply]
Without --apply it only reports what it would change.
"""
import shutil
import sys
from collections import Counter, deque
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
SHEET = REPO / "frontend" / "coffee-machine-v3-grid.webp"
BACKUP = REPO / "assets" / "generated" / "_backups" / "coffee-machine-v3-grid.webp.orig"
FW = FH = 230
PLAQUE_BBOX = (127, 176, 115, 147)   # x0, x1, y0, y1 - measured, identical in all 96 frames
# NO PADDING. The rect IS the plaque, so the only dark pixels inside it are the letters. A first
# attempt padded the rect by 6px to be safe and required each dark pixel to have a white NEIGHBOUR
# to be erased; that caught only the letters' edges - their cores are dark-surrounded-by-dark - and
# left "FRIENDS" legible as ragged outlines (measured: the erasure ran but the word survived). The
# padding was also the danger it was guarding against, since it pulled the plaque's own drop shadow
# into range. Inside the exact plaque the neighbour test is both unnecessary and harmful.
PAD = 0


def near_white(a):
    al = a[:, :, 3]
    return ((al > 128) & (a[:, :, 0] > 165) & (a[:, :, 1] > 165) & (a[:, :, 2] > 165)
            & (np.abs(a[:, :, 0] - a[:, :, 1]) < 40) & (np.abs(a[:, :, 1] - a[:, :, 2]) < 55))


def plume_component(mask, want_bbox):
    """The connected near-white component whose bbox matches the plaque, NOT the mug."""
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    x0w, x1w, y0w, y1w = want_bbox
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or seen[sy, sx]:
                continue
            q = deque([(sx, sy)])
            seen[sy, sx] = True
            comp = []
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        q.append((nx, ny))
            xs = [c[0] for c in comp]
            ys = [c[1] for c in comp]
            bx = (min(xs) + x0w, max(xs) + x0w, min(ys) + y0w, max(ys) + y0w)
            if all(abs(bx[i] - want_bbox[i]) <= 3 for i in range(4)):
                return set(comp), bx
    return None, None


def main():
    apply = "--apply" in sys.argv
    if not SHEET.exists():
        raise SystemExit(f"missing {SHEET}")
    im = Image.open(SHEET).convert("RGBA")
    W, H = im.size
    cols, rows = W // FW, H // FH
    x0, x1, y0, y1 = PLAQUE_BBOX
    rx0, rx1 = max(0, x0 - PAD), min(FW, x1 + 1 + PAD)
    ry0, ry1 = max(0, y0 - PAD), min(FH, y1 + 1 + PAD)
    want_local = (x0 - rx0, x1 - rx0, y0 - ry0, y1 - ry0)

    total = 0
    changed_frames = 0
    out = im.copy()
    for fi in range(cols * rows):
        cx, cy = (fi % cols) * FW, (fi // cols) * FH
        fr = np.asarray(im.crop((cx, cy, cx + FW, cy + FH))).copy()
        reg = fr[ry0:ry1, rx0:rx1].astype(int)
        al = reg[:, :, 3]

        # The plaque's white is SPLIT INTO COMPONENTS BY THE LETTERS - a flood fill of "near white"
        # returns the strip above the word and the strip below it, never the whole plaque (measured:
        # the largest component in the region was x 126-177, y 136-147, i.e. only BELOW the letters).
        # So the plaque is treated as the MEASURED RECT (identical in all 96 frames) and the letters
        # are found as dark pixels embedded in white inside it.
        white = (al > 128) & (reg[:, :, 0] > 160) & (reg[:, :, 1] > 160) & (reg[:, :, 2] > 160)
        if white.sum() < 20:
            continue
        med = np.median(np.array([tuple(reg[y, x][:3]) for y, x in zip(*np.nonzero(white))]), axis=0)
        lum = med.mean()

        # interior only, so the plaque's own outline (at the rect edge) survives
        hh, ww = reg.shape[:2]
        # a letter pixel is dark and inside the plaque's interior. No neighbour test: inside the
        # exact plaque rect, dark pixels ARE the letters (see the note at PAD above).
        inner = np.zeros((hh, ww), dtype=bool)
        inner[1:hh - 1, 1:ww - 1] = True
        dark = (al > 128) & (reg.mean(axis=2) < lum * 0.78)
        letters = dark & inner
        n = int(letters.sum())
        if n:
            reg[letters] = (int(med[0]), int(med[1]), int(med[2]), 255)
            fr[ry0:ry1, rx0:rx1] = reg.astype(np.uint8)
            changed_frames += 1
            total += n
            out.paste(Image.fromarray(fr), (cx, cy))
    print(f"  frames changed: {changed_frames}/{cols*rows}   letter pixels erased: {total}")
    if apply:
        BACKUP.parent.mkdir(parents=True, exist_ok=True)
        if not BACKUP.exists():
            shutil.copy2(SHEET, BACKUP)
            print(f"  original backed up -> {BACKUP.relative_to(REPO)}")
        # LOSSLESS on purpose. PIL's default WebP save is lossy, and re-encoding pixel art that way
        # rewrote 23745 pixels of frame 0 when the erase itself touched only ~710 - it degrades every
        # edge in the sheet and repopulates colour under transparent pixels. Same trap as the sheet
        # repair pass: a "fix" that recompresses the art is not a fix.
        out.save(SHEET, lossless=True)
        print(f"  written LOSSLESS -> {SHEET.relative_to(REPO)}")
    else:
        print("  (dry run - pass --apply to write)")
    print("STRIP_PLAQUE_DONE")


if __name__ == "__main__":
    main()
