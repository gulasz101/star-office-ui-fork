#!/usr/bin/env python3
"""Publish the v3 art set B (server room / factory) into the served static tree (SOU-199).

Same shape as `scripts/v3/publish_lodge_art.py`, one script for both set-B rooms because the two
sets differ only in their tables:

  * the generated pack under `assets/generated/v3/<set>/` is the record (raws + a manifest next to
    every shipped file);
  * the browser can only load what Flask serves out of `frontend/`, so the props, their manifests
    and an `index.json` are copied into `frontend/v3/assets/<set>/`;
  * `index.json` carries the SHIPPED pixel size as well as the declared one (the SOU-158 review
    caught five props whose canvas had grown past the published size table), the frame count and
    frame size for the animated assets, and the `lift_px` per key, which is what
    `placeholders.js` PROP_STYLE has to carry - so the wiring step re-derives nothing.

The copy is byte-identical and the publisher refuses and reports if it is not.

It does NOT touch `frontend/v3/maps/rooms/<set>.json`, `placeholders.js` or any JS: wiring set B
into the renderer is a separate step (as it was for the lodge, SOU-208), and doing it here would
make an art ticket silently own a render change.

Set B ships NO tiles: the two rooms reuse the lodge material tiles (`stone_floor` for the server
room, `metal_grate` for the factory) through their room maps' own tileset reference. `index.json`
records that reuse instead of copying a second tileset into the tree.

Usage::

    python3 scripts/v3/publish_roomart_b.py --set serverroom
    python3 scripts/v3/publish_roomart_b.py --set factory --check   # verify only, write nothing
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

import gen_roomart_b as B  # noqa: E402  (same directory: the set's own tables)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", required=True, choices=tuple(B.SETS),
                    help="which set-B room to publish")
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()
    setname = args.set

    src_root = B.out_dir(setname)
    dst_root = B.served_dir(setname)
    pairs = []
    for asset in B.ASSETS[setname]:
        aid = asset["id"]
        pairs.append((f"props/{aid}.png", f"props/{aid}.png"))
        pairs.append((f"props/{aid}.png.manifest.json", f"props/{aid}.png.manifest.json"))

    if not os.path.isdir(src_root):
        print(f"no generated set at {src_root}", file=sys.stderr)
        return 2

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        Image = None

    entries, mismatches = [], []
    for src_rel, dst_rel in pairs:
        src = os.path.join(src_root, src_rel)
        dst = os.path.join(dst_root, dst_rel)
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
        entries.append({"served_path": f"assets/{setname}/{dst_rel}",
                        "source": f"assets/generated/v3/{setname}/{src_rel}",
                        "sha256": sha256(src), "bytes": os.path.getsize(src)})

    assets = []
    for asset in B.ASSETS[setname]:
        aid = asset["id"]
        served = os.path.join(dst_root, "props", f"{aid}.png")
        row = {"key": aid,
               "served_path": f"assets/{setname}/props/{aid}.png",
               "multi_frame": asset["frames"] > 1,
               "frames": asset["frames"],
               "frame_px": asset["texture_px"],
               "texture_px_declared": asset["texture_px"],
               "footprint_px": asset["footprint_px"],
               "footprint_tiles": asset["footprint_tiles"],
               "lift_px": asset["lift_px"],
               "full_bleed": asset["full_bleed"],
               "flat": asset["flat"],
               "note": asset.get("note", "")}
        if Image is not None and os.path.exists(served):
            row["texture_px_actual"] = list(Image.open(served).size)
            row["sheet_layout"] = ("frames left to right, frame k at x = k * "
                                   f"{asset['texture_px'][0]}") if asset["frames"] > 1 else None
        assets.append(row)

    index_path = os.path.join(dst_root, "index.json")
    index = {
        "pack": B.SETS[setname]["pack"],
        "set": setname,
        "issue": "SOU-199",
        "title": B.SETS[setname]["title"],
        "generated_from": f"assets/generated/v3/{setname}",
        "model": B.MODEL_ID,
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "camera": "top-down 3/4, light from the top-left (same projection vocabulary as office-01)",
        "palette": f"assets/generated/v3/{setname}/palette.json",
        "palette_shared_with": "lodge (SOU-198) - one palette for the whole lodge family",
        "tiles": {
            "ships": False,
            "reused_from": "lodge",
            "tileset": "assets/lodge/tiles.png",
            "floor_tile_for_this_room": B.SETS[setname]["backdrop"],
            "why": "set B is a room set, not a room: the server room and the factory are rooms of "
                   "the same lodge building, so they reuse the lodge material tiles through their "
                   "room maps rather than shipping a second 512x32 tileset.",
        },
        "room_map": f"frontend/v3/maps/rooms/{B.SETS[setname]['map']}",
        # PROP_STYLE entries the renderer needs: key -> lift px. The renderer draws the prop as
        # `footprint_w x (footprint_h + lift)` and anchors it bottom-centre.
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"] for a in B.ASSETS[setname]},
        "assets": assets,
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "texture_px_actual is measured from the published file. Every asset in this set is "
                "fitted INSIDE its declared box (SOU-199 asks for the exact size given), so "
                "declared and actual agree by construction - the field is here because SOU-158's "
                "review had to reconcile that drift by hand.",
    }
    if not args.check:
        os.makedirs(dst_root, exist_ok=True)
        with open(index_path, "w") as fh:
            json.dump(index, fh, indent=2)
            fh.write("\n")
        print(f"published {len(entries)} files to {dst_root}")
    else:
        print(f"checked {len(entries)} files against {src_root}")

    if mismatches:
        print("MISMATCH:")
        for m in mismatches:
            print(f"  - {m}")
        return 1
    print(f"OK: every published file is byte-identical to its source ({len(entries)} files, "
          f"{len(assets)} asset entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
