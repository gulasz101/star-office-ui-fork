#!/usr/bin/env python3
"""Publish the office-01 art set into the served v3 static tree (SOU-158).

The generated pack lives under `assets/generated/v3/office-01/` (the record, with the raw
FLUX output and a manifest next to every file). The browser can only load what the Flask app
serves out of `frontend/`, so this copies the set into `frontend/v3/assets/office-01/` and
writes an `index.json` naming each file's sha256.

Same shape as `scripts/publish_room.py` / the v2 pack publisher: the copy is byte-identical
(the script refuses and reports if it is not), and the index is what makes that checkable
later without keeping two trees in your head.

It does NOT edit `frontend/v3/maps/office-01.json` or any JS: switching the renderer onto
this art is SOU-159/160 (character + wiring), and doing it here would make an art ticket
silently own a render change. The map's tileset entry only has to point at
`../assets/office-01/tiles.png`; that path is what this publishes.

Usage::

    python3 scripts/v3/publish_office01_art.py            # copy + verify + index
    python3 scripts/v3/publish_office01_art.py --check     # verify only, write nothing
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "assets", "generated", "v3", "office-01")
DST = os.path.join(REPO, "frontend", "v3", "assets", "office-01")

# source -> served name. The tileset sheet is served as tiles.png because that is the name
# the map's tileset entry already uses; the rest keep their ids, which are the map's prop keys.
PAIRS = [
    ("tiles/tileset.png", "tiles.png"),
    ("tiles/tileset.png.manifest.json", "tiles.png.manifest.json"),
    ("character/office-01.png", "character/office-01.png"),
    ("character/office-01.png.manifest.json", "character/office-01.png.manifest.json"),
    ("props/desk.png", "props/desk.png"),
    ("props/chair.png", "props/chair.png"),
    ("props/rack.png", "props/rack.png"),
    ("props/bookshelf.png", "props/bookshelf.png"),
    ("props/coffee.png", "props/coffee.png"),
    ("props/rug.png", "props/rug.png"),
    ("props/couch.png", "props/couch.png"),
    ("props/bass.png", "props/bass.png"),
    ("props/amp.png", "props/amp.png"),
    ("props/cat.png", "props/cat.png"),
    ("props/van.png", "props/van.png"),
    ("props/spool.png", "props/spool.png"),
    ("props/crate.png", "props/crate.png"),
]
PROP_IDS = [os.path.basename(src) for src, dst in PAIRS if dst.startswith("props/")]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print(f"no generated set at {SRC}", file=sys.stderr)
        return 2

    entries = []
    mismatches = []
    for src_rel, dst_rel in PAIRS:
        src = os.path.join(SRC, src_rel)
        dst = os.path.join(DST, dst_rel)
        if not os.path.exists(src):
            mismatches.append(f"missing source {src_rel}")
            continue
        if not args.check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        if not os.path.exists(dst):
            mismatches.append(f"not published: {dst_rel}")
            continue
        same = sha256(src) == sha256(dst)
        if not same:
            mismatches.append(f"byte mismatch: {dst_rel}")
        entries.append({"served_path": f"assets/office-01/{dst_rel}", "source": f"assets/generated/v3/office-01/{src_rel}",
                        "sha256": sha256(src), "bytes": os.path.getsize(src)})
    # prop manifests travel with their PNG
    if not args.check:
        for fname in sorted(os.listdir(os.path.join(SRC, "props"))):
            if fname.endswith(".png.manifest.json"):
                src = os.path.join(SRC, "props", fname)
                dst = os.path.join(DST, "props", fname)
                shutil.copyfile(src, dst)
                entries.append({"served_path": f"assets/office-01/props/{fname}",
                                "source": f"assets/generated/v3/office-01/props/{fname}",
                                "sha256": sha256(src), "bytes": os.path.getsize(src)})

    index_path = os.path.join(DST, "index.json")
    if not args.check:
        index = {
            "pack": "office-01-v1",
            "issue": "SOU-158",
            "generated_from": "assets/generated/v3/office-01",
            "model": "black-forest-labs/FLUX.2-klein-4B",
            "palette": "assets/generated/v3/office-01/palette.json",
            "prop_keys": [pid[:-4] for pid in PROP_IDS],
            "character_sheet": {"path": "assets/office-01/character/office-01.png", "cols": 8, "rows": 4,
                                "frame_px": [32, 48],
                                "facings": ["down", "left", "right", "up"],
                                "columns": "0 idle, 1-4 walk, 5-6 work/typing, 7 sleep"},
            "tileset": {"path": "assets/office-01/tiles.png", "tile_px": 32, "columns": 8, "firstgid": 1,
                        "order": ["floor_wood_a", "floor_wood_b", "garage_concrete", "wall_cap",
                                  "wall_face", "wall_base", "door_mat", "garage_stripe"]},
            "files": entries,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        with open(index_path, "w") as fh:
            json.dump(index, fh, indent=2)
            fh.write("\n")
        print(f"published {len(entries)} files to {DST}")
    else:
        print(f"checked {len(entries)} files against {SRC}")

    if mismatches:
        print("MISMATCH:")
        for m in mismatches:
            print(f"  - {m}")
        return 1
    print(f"OK: every published file is byte-identical to its source ({len(entries)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
