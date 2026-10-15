#!/usr/bin/env python3
"""Clean keying residue out of room props, then recomposite.

Two residues, both measured on the Andrzej room props (2026-10-15):

  1. RGB left under fully transparent pixels - up to 337k pixels per prop - because
     gen_iso_prop.key_magenta set alpha=0 but kept the magenta RGB. Invisible while nothing
     blends, but the same defect class as the guest sheets, and it makes any future smoothing,
     scaling or mip-mapping fringe magenta. Zeroed here.
  2. PARTIAL-alpha magenta on the sprite edge (alpha between 1 and 127). This one is VISIBLE:
     the compositor honours partial alpha, so edge pixels blend magenta into the scene - a
     review of the assembled room saw it as "the minivan has a magenta bounding outline".
     Removed by dropping the alpha of magenta-ish partial pixels to 0.

Usage: python scripts/clean_prop_keys.py [agent_id]
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated" / "rooms"


def magentaish(a):
    r, g, b = a[:, :, 0].astype(int), a[:, :, 1].astype(int), a[:, :, 2].astype(int)
    return (r > 120) & (b > 100) & (g < 130) & ((r - g) > 30) & ((b - g) > 10)


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else "andrzej"
    props = sorted((GEN / agent / "props").glob("*.png"))
    props = [p for p in props if ".raw." not in p.name]
    for p in props:
        a = np.asarray(Image.open(p).convert("RGBA")).copy()
        alpha = a[:, :, 3]
        mag = magentaish(a)

        under = (alpha == 0) & ((a[:, :, 0] != 0) | (a[:, :, 1] != 0) | (a[:, :, 2] != 0))
        n_under = int(under.sum())
        a[under] = (0, 0, 0, 0)

        partial = (alpha > 0) & (alpha < 128) & mag
        n_partial = int(partial.sum())
        a[partial] = (0, 0, 0, 0)

        # BOUNDARY de-fringe. Measured on the minivan: zero partial-alpha magenta and zero
        # opaque pixels matching the strict rule, yet the assembled room showed a magenta
        # bounding outline - because the model draws a PINKISH contour that is off-key rather
        # than the key colour itself (4.6k such pixels on the van).
        #
        # The discriminator must be BLUE vs GREEN, not red vs green. A first attempt used
        # (r-g)>25 alone and stripped 8650 px off the desk: warm wood and cream satisfy it too
        # (#c08a60 has r-g=54). Magenta and pink have b >= g; brown, tan and cream have b < g,
        # so requiring (b-g) >= -5 keeps every warm tone and still catches the contour.
        opaque = alpha > 128
        ring = np.zeros_like(opaque)
        for dy in (-2, -1, 0, 1, 2):
            for dx in (-2, -1, 0, 1, 2):
                ring |= np.roll(np.roll(opaque, dy, 0), dx, 1)
        ring &= opaque
        r, g, b = a[:, :, 0].astype(int), a[:, :, 1].astype(int), a[:, :, 2].astype(int)
        warm_safe = (r > 110) & (b > 90) & (g < 150) & ((r - g) > 25) & ((b - g) >= -5)
        edge = ring & warm_safe
        n_edge = int(edge.sum())
        a[edge] = (0, 0, 0, 0)

        if n_under or n_partial or n_edge:
            Image.fromarray(a).save(p)
        print(f"  {p.name:14s} rgb-under-alpha: {n_under:7d}   partial-alpha: {n_partial:5d}   "
              f"boundary contour: {n_edge:5d}")
    print("CLEAN_KEYS_DONE")


if __name__ == "__main__":
    main()
