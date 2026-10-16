#!/usr/bin/env python3
"""Publish the lodge art set into the served v3 static tree (SOU-198).

Same shape as `scripts/v3/publish_office01_art.py`: the generated pack under
`assets/generated/v3/lodge/` is the record (raws + a manifest next to every file), and the
browser can only load what the Flask app serves out of `frontend/`, so this copies the set into
`frontend/v3/assets/lodge/` and writes an `index.json` recording each file's sha256 and size.

Two things this one does that the office publisher does not, both of them lessons from SOU-158's
review:

  * **it publishes the SHIPPED pixel size, not the declared one.** SOU-158's review caught five
    props whose canvas had grown past the size table that had been published for the wiring step
    to consume. `index.json` therefore carries `texture_px_declared` AND `texture_px_actual` per
    prop, measured from the file it just copied;
  * **it publishes the renderer's lift**, so the wiring step does not have to re-derive
    `texture_h - footprint_h` from the ticket. The lift is what `placeholders.js` PROP_STYLE must
    carry for each new key.

The copy is byte-identical (the script refuses and reports if it is not). It does NOT touch
`frontend/v3/maps/lodge.json`, `placeholders.js` or any JS: wiring the set into the renderer is a
separate step, and doing it here would make an art ticket silently own a render change.

Usage::

    python3 scripts/v3/publish_lodge_art.py            # copy + verify + index
    python3 scripts/v3/publish_lodge_art.py --check     # verify only, write nothing
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
sys.path.insert(0, HERE)

import gen_lodge_art as L  # noqa: E402  (same directory: the set's own tables)

REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "assets", "generated", "v3", "lodge")
DST = os.path.join(REPO, "frontend", "v3", "assets", "lodge")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pairs():
    """source -> served name, in the served tree's layout."""
    out = [("tiles/tileset.png", "tiles.png"),
           ("tiles/tileset.png.manifest.json", "tiles.png.manifest.json")]
    for name, _cls, _seam, _spec in L.TILES:
        out.append((f"tiles/{name}.png", f"tiles/{name}.png"))
        out.append((f"tiles/{name}.png.manifest.json", f"tiles/{name}.png.manifest.json"))
    for asset in L.PROPS:
        aid = asset["id"]
        out.append((f"props/{aid}.png", f"props/{aid}.png"))
        out.append((f"props/{aid}.png.manifest.json", f"props/{aid}.png.manifest.json"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print(f"no generated set at {SRC}", file=sys.stderr)
        return 2

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - the publisher is run with the venv normally
        Image = None

    entries, mismatches = [], []
    for src_rel, dst_rel in pairs():
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
        if sha256(src) != sha256(dst):
            mismatches.append(f"byte mismatch: {dst_rel}")
        entries.append({"served_path": f"assets/lodge/{dst_rel}",
                        "source": f"assets/generated/v3/lodge/{src_rel}",
                        "sha256": sha256(src), "bytes": os.path.getsize(src)})

    props = []
    for asset in L.PROPS:
        aid = asset["id"]
        served = os.path.join(DST, "props", f"{aid}.png")
        row = {"key": aid, "served_path": f"assets/lodge/props/{aid}.png",
               "sprite_sheet": asset["kind"] == "sheet"}
        if asset["kind"] == "sheet":
            row.update({"sheet_px": [128, 32], "frame_px": [32, 32], "frames": 4,
                        "order": "frames 0-3 = walk cycle, facing right (wander mirrors for left)"})
        else:
            row.update({"texture_px_declared": asset["texture_px"],
                        "footprint_px": asset["footprint_px"],
                        "footprint_tiles": asset["footprint_tiles"],
                        "lift_px": asset["lift_px"]})
        if Image is not None and os.path.exists(served):
            row["texture_px_actual"] = list(Image.open(served).size)
        props.append(row)

    index_path = os.path.join(DST, "index.json")
    index = {
        "pack": "lodge-v1",
        "set": "lodge",
        "issue": "SOU-198",
        "generated_from": "assets/generated/v3/lodge",
        "model": "black-forest-labs/FLUX.2-klein-4B",
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "camera": "top-down 3/4, light from the top-left (same projection vocabulary as office-01)",
        "palette": "assets/generated/v3/lodge/palette.json",
        "tileset": {
            "path": "assets/lodge/tiles.png",
            "tile_px": 32, "columns": 16, "firstgid": 1,
            "order": [name for name, _c, _s, _spec in L.TILES],
            "order_authority": "the SOU-198 ticket table (no lodge map in the tree yet)",
            "slot_class": {name: cls for name, cls, _s, _spec in L.TILES},
            "slot_seam_axes": {name: seam for name, _c, seam, _spec in L.TILES},
        },
        # PROP_STYLE entries the renderer needs: key -> lift in px, i.e. the renderer draws the
        # prop as `footprint_w x (footprint_h + lift)` and anchors it bottom-centre.
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"] for a in L.PROPS
                                          if a["kind"] != "sheet"},
        "props": props,
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "texture_px_actual is measured from the published file; where it differs from "
                "texture_px_declared the canvas grew because the silhouette is taller than the "
                "placeholder box (width is the contract - the renderer draws the prop at the "
                "footprint width).",
    }
    if not args.check:
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
    print(f"OK: every published file is byte-identical to its source ({len(entries)} files, "
          f"{len(props)} prop entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
