#!/usr/bin/env python3
"""Re-key room props from their .raw generations with the corrected de-fringe.

WHY: a first de-fringe pass used (r-g)>25 as the pink discriminator and stripped 8650 px off
the desk, because warm wood and cream satisfy it too (#c08a60 gives r-g=54). The props were
written before that was noticed, so this rebuilds them from the untouched `.raw.png` files
rather than trying to patch the damage - the raw generation is the pristine input, and the
keying step is cheap and deterministic.

Discriminator now: magenta/pink have b >= g; brown, tan and cream have b < g. Requiring
(b-g) >= -5 keeps every warm tone and still catches the off-key contour.

After re-keying, every prop is re-measured by the alignment gate - a re-keyed prop must still
prove it sits on the lattice, since the de-fringe changes its silhouette.

Usage: python scripts/rekey_props.py [agent_id]
"""
import importlib.util
import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated" / "rooms"


def load(name):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


gip = load("gen_iso_prop")
cpa = load("check_prop_alignment")


def main():
    agent = sys.argv[1] if len(sys.argv) > 1 else "andrzej"
    # props where the LOOSE boundary pass must be skipped: it can eat a pale, low-silhouette
    # prop (measured: the curled-up cat lost 2552 px of body edge and then failed the gate).
    # The strict pass alone still removes key-coloured residue; only the off-key contour stays.
    strict_only = set()
    if "--strict-only" in sys.argv:
        i = sys.argv.index("--strict-only")
        strict_only = set(sys.argv[i + 1:])
        agent = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else agent

    props_dir = GEN / agent / "props"
    raws = sorted(p for p in props_dir.glob("*.raw.png"))
    if strict_only:
        raws = [p for p in raws if p.name.replace(".raw.png", "") in strict_only]
    if not raws:
        raise SystemExit(f"no matching .raw.png files in {props_dir}")

    for raw in raws:
        prop_id = raw.name.replace(".raw.png", "")
        out = props_dir / f"{prop_id}.png"
        keyed, key = gip.key_magenta(Image.open(raw))
        a = np.asarray(keyed.convert("RGBA")).copy()
        alpha = a[:, :, 3]

        under = (alpha == 0) & ((a[:, :, 0] != 0) | (a[:, :, 1] != 0) | (a[:, :, 2] != 0))
        n_under = int(under.sum())
        a[under] = (0, 0, 0, 0)

        r, g, b = a[:, :, 0].astype(int), a[:, :, 1].astype(int), a[:, :, 2].astype(int)
        partial = (alpha > 0) & (alpha < 128) & (r > 120) & (b > 100) & (g < 130)
        a[partial] = (0, 0, 0, 0)

        opaque = a[:, :, 3] > 128
        if prop_id in strict_only:
            n_edge = 0
        else:
            ring = np.zeros_like(opaque)
            for dy in (-2, -1, 0, 1, 2):
                for dx in (-2, -1, 0, 1, 2):
                    ring |= np.roll(np.roll(opaque, dy, 0), dx, 1)
            ring &= opaque
            edge = ring & (r > 110) & (b > 90) & (g < 150) & ((r - g) > 25) & ((b - g) >= -5)
            n_edge = int(edge.sum())
            a[edge] = (0, 0, 0, 0)

        Image.fromarray(a).save(out)
        slopes = cpa.base_slopes(out)
        verdict = "?"
        if slopes:
            ok = all(abs(abs(s) - 0.573) <= cpa.TOLERANCE for s in slopes)
            verdict = "ON LATTICE" if ok else "OFF -> prop needs regeneration"
        print(f"  {prop_id:9s} re-keyed (key {key[:3]})  under-alpha {n_under:7d}  "
              f"contour {n_edge:5d}  opaque {100*(a[:,:,3]>128).mean():4.1f}%  {verdict}")
    print("REKEY_DONE")


if __name__ == "__main__":
    main()
