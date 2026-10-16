#!/usr/bin/env python3
"""Pre-assemble check for a set-C raw: is the KEYED SILHOUETTE continuous?

Two defects this catches before the assemble pass turns them into shippable art:

1. a model that ignored the magenta field and painted the background in a colour the object also
   uses (a dark navy background with a dark stand column), so the border-seeded flood fill leaks
   INTO the object through a break in its outline and leaves the object in two pieces;
2. a standing object whose own middle rows key away, i.e. a lamp floating above its base.

Metric: after keying, every row between the top and the bottom content row must carry at least one
opaque pixel. Printed with the magenta share of the frame so the two causes stay distinguishable.

Usage::

    python3 scripts/v3/check_c_raws.py career/ring_light
"""
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

REPO = Path.home() / "sou-200"
sys.path.insert(0, str(REPO / "scripts" / "v3"))
from gen_office01_art import key_magenta  # noqa: E402


def main() -> int:
    spec = sys.argv[1]
    setn, aid = spec.split("/")
    raw = Image.open(REPO / f"assets/generated/v3/{setn}/{aid}.raw.png").convert("RGB")
    keyed, key = key_magenta(raw)
    w, h = raw.size

    rgb = Counter(raw.getdata())
    magenta = sum(c for px, c in rgb.items()
                  if px[0] > 180 and px[1] < 100 and px[2] > 180) / (w * h)

    px = keyed.load()
    rows = []
    for y in range(h):
        on = [x for x in range(w) if px[x, y][3]]
        rows.append((y, min(on), max(on), len(on)) if on else (y, None, None, 0))
    content = [r for r in rows if r[3]]
    top, bottom = content[0][0], content[-1][0]
    gaps = [r[0] for r in rows[top:bottom + 1] if not r[3]]
    widths = [r[3] for r in content]

    print(f"{spec}: {w}x{h}")
    print(f"  field magenta share: {magenta:.1%}   key colour: {key}")
    print(f"  keyed content rows {top}..{bottom} of {h}")
    print(f"  empty rows INSIDE the silhouette: {len(gaps)}"
          + (f" {gaps[:24]}{'...' if len(gaps) > 24 else ''}" if gaps else ""))
    print(f"  opaque px per row: min {min(widths)} median {sorted(widths)[len(widths)//2]} "
          f"max {max(widths)}")
    seam = len(gaps) / max(1, bottom - top + 1)
    verdict = "OK - silhouette continuous" if not gaps else (
        f"SUSPECT - {seam:.0%} of the object's own rows are empty; the object is in pieces")
    print(f"  verdict: {verdict}")
    return 0 if not gaps else 1


if __name__ == "__main__":
    sys.exit(main())
