#!/usr/bin/env python3
"""Gate: does a prop's base sit on the room's declared lattice?

WHY A GATE AND NOT AN OPINION
-----------------------------
"Does this object follow the floor" was being judged by eye, and eyes converge slowly and
disagree. It is arithmetic: the base silhouette of a prop standing on an isometric floor has
two ground edges whose slopes must match the lattice's own axes.

Measured 2026-10-15 on the same declared lattice:

    GIT-generated desk (prompted in projection)  +0.576 / -0.584   <- sits on the floor
    upstream desk-v3                             +0.003 / -0.007   <- horizontal base
    upstream sofa-idle-v3                        +1.043 / -1.147   <- ~45 degrees

The upstream props are drawn in a DIFFERENT projection, so no placement can align them.
That is why the room's furniture has to be generated per room, and this gate is how we
know a newly generated prop is usable before it reaches the scene.

Tolerance default 0.12 catches "wrong projection" (0.003, 1.04) while accepting the model's
natural 30-degree isometric output (0.577) against a 2:1 declaration (0.500).

Usage:
    python scripts/check_prop_alignment.py <sprite.png> [more.png ...]
    python scripts/check_prop_alignment.py --lattice 0.577 assets/generated/rooms/*/props/*.png
Exit code 0 = all props sit on the lattice, 1 = at least one does not.
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

DEFAULT_LATTICE = 0.577    # 30-degree true isometric (tan 30); DECIDED look, see room_geometry.ROOM
TOLERANCE = 0.07           # TIGHT on purpose: a 2:1 prop (0.50) is 13% off a 0.577 floor and
                           # reads wrong next to it, so it is rejected for re-roll rather than
                           # averaged in. The generator returns either 0.50 or 0.577, so the
                           # gate is what enforces one consistent look per room.


def base_slopes(path, crop=None):
    """Base-edge slopes of a prop, measured ROBUSTLY.

    A least-squares fit over each third of the bottom silhouette is NOT robust enough: a prop
    standing on thin legs or casters puts a few tall spikes in the profile and the fit returns
    nonsense (measured: a chair gave +1.527, -0.514/-2.739, +1.705 across three attempts, and a
    desk gave +0.109/-1.183 - all false rejections of props whose FOOTPRINT is fine).

    So: take the lower envelope (running max of y within a window), then fit each edge with
    Theil-Sen (median of pairwise slopes), which ignores a minority of spike columns. The window
    is expressed in pixels so it scales with the sprite.
    """
    im = Image.open(path).convert("RGBA")
    if crop:
        im = im.crop(crop)
    mask = np.asarray(im)[:, :, 3] > 128
    if mask.sum() == 0:
        return None
    ys, xs = np.nonzero(mask)
    x0, x1 = xs.min(), xs.max()
    bottom = []
    for x in range(x0, x1 + 1):
        col = np.nonzero(mask[:, x])[0]
        if len(col):
            bottom.append((x, col.max()))
    if len(bottom) < 16:
        return None
    bottom = np.array(bottom, dtype=float)

    # lower envelope: a column whose base is far above its neighbours (a gap between legs) is
    # replaced by the local maximum, so the envelope follows the feet rather than the gaps
    win = max(3, (x1 - x0) // 24)
    env = np.array([[x, np.max(bottom[max(0, i - win):i + win + 1, 1])]
                    for i, (x, _) in enumerate(bottom)])

    def theil_sen(seg):
        xs_, ys_ = seg[:, 0], seg[:, 1]
        n = len(xs_)
        if n < 8:
            return None
        ix = np.linspace(0, n - 1, min(n, 60)).astype(int)
        slopes = []
        for a in range(len(ix) - 1):
            for b in range(a + 1, len(ix)):
                dx = xs_[ix[b]] - xs_[ix[a]]
                if dx != 0:
                    slopes.append((ys_[ix[b]] - ys_[ix[a]]) / dx)
        return float(np.median(slopes)) if slopes else None

    third = max(8, len(env) // 3)
    out = []
    for seg in (env[:third], env[-third:]):
        s = theil_sen(seg)
        if s is None:
            return None
        out.append(s)
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    lattice = DEFAULT_LATTICE
    if "--lattice" in sys.argv:
        lattice = float(sys.argv[sys.argv.index("--lattice") + 1])
        if str(lattice) in args:
            args.remove(str(lattice))
    if not args:
        print(__doc__)
        return 2

    failures = 0
    print(f"== prop/base alignment gate (lattice slope {lattice:+.3f}, tolerance {TOLERANCE}) ==")
    for p in args:
        path = Path(p)
        if not path.exists():
            print(f"  {path.name:34s} MISSING")
            failures += 1
            continue
        # un-keyed generations still carry the magenta frame, so their "base" is the frame edge
        # (slope 0.000) and gating them reports nonsense. They are inputs, not deployable props.
        if ".raw." in path.name:
            print(f"  {path.name:34s} skipped (un-keyed generation, not a deployable prop)")
            continue
        sl = base_slopes(path)
        if sl is None:
            print(f"  {path.name:34s} EMPTY (no opaque pixels)")
            failures += 1
            continue
        left, right = sl
        ok = abs(abs(left) - lattice) <= TOLERANCE and abs(abs(right) - lattice) <= TOLERANCE
        verdict = "ON THE LATTICE" if ok else "WRONG PROJECTION"
        print(f"  {path.name:34s} left={left:+.3f} right={right:+.3f}  {verdict}")
        if not ok:
            failures += 1

    print(f"\n{len(args) - failures}/{len(args)} prop(s) sit on the lattice")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
