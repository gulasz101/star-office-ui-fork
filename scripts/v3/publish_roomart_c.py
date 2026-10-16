#!/usr/bin/env python3
"""Publish the v3 art set C (four personality rooms) into the served static tree (SOU-200).

Same shape as `scripts/v3/publish_roomart_b.py`, one script for all four set-C rooms because the
sets differ only in their tables:

  * the generated pack under `assets/generated/v3/<set>/` is the record (raws + a manifest next to
    every shipped file);
  * the browser can only load what Flask serves out of `frontend/`, so the props, their manifests
    and an `index.json` are copied into `frontend/v3/assets/<set>/`;
  * `index.json` carries the SHIPPED pixel size as well as the declared one (the SOU-158 review
    caught five props whose canvas had grown past the published size table), the ticket's size
    class, and the `lift_px` per key, which is what `placeholders.js` PROP_STYLE has to carry - so
    the wiring step re-derives nothing.

The copy is byte-identical and the publisher refuses and reports if it is not.

It does NOT touch `frontend/v3/maps/rooms/<set>.json`, `placeholders.js` or any JS: wiring set C
into the renderer is a separate step (as it was for the lodge, SOU-208, and for set B, SOU-212/213),
and doing it here would make an art ticket silently own a render change.

Set C ships NO tiles: the four rooms reuse the lodge material tiles (this set's rooms all stand on
`room_plank_a`, which is also the backdrop the contact sheets are drawn on) through their room maps'
own tileset reference. `index.json` records that reuse instead of copying a second tileset in.

Usage::

    python3 scripts/v3/publish_roomart_c.py --set mystery
    python3 scripts/v3/publish_roomart_c.py --set writer --check   # verify only, write nothing
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

import gen_roomart_c as C  # noqa: E402  (same directory: the set's own tables)


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", required=True, choices=tuple(C.SETS),
                    help="which set-C room to publish")
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()
    setname = args.set

    src_root = C.out_dir(setname)
    dst_root = C.served_dir(setname)
    pairs = []
    for asset in C.ASSETS[setname]:
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
    for asset in C.ASSETS[setname]:
        aid = asset["id"]
        served = os.path.join(dst_root, "props", f"{aid}.png")
        row = {"key": aid,
               "served_path": f"assets/{setname}/props/{aid}.png",
               "multi_frame": asset["frames"] > 1,
               "frames": asset["frames"],
               "frame_px": asset["texture_px"],
               "ticket_size_class": asset["klass"],
               "texture_px_declared": asset["texture_px"],
               "footprint_px": asset["footprint_px"],
               "footprint_tiles": asset["footprint_tiles"],
               "lift_px": asset["lift_px"],
               "full_bleed": asset["full_bleed"],
               "flat": asset["flat"],
               "note": asset.get("note", "")}
        if Image is not None and os.path.exists(served):
            row["texture_px_actual"] = list(Image.open(served).size)
            row["sheet_layout"] = None
        assets.append(row)

    index_path = os.path.join(dst_root, "index.json")
    index = {
        "pack": C.SETS[setname]["pack"],
        "set": setname,
        "issue": "SOU-200",
        "title": C.SETS[setname]["title"],
        "generated_from": f"assets/generated/v3/{setname}",
        "model": C.MODEL_ID,
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "camera": "top-down 3/4, light from the top-left (same projection vocabulary as office-01)",
        "palette": f"assets/generated/v3/{setname}/palette.json",
        "palette_shared_with": "lodge (SOU-198), serverroom + factory (SOU-199)",
        "tiles": {
            "ships": False,
            "reused_from": "lodge",
            "tileset": "assets/lodge/tiles.png",
            "floor_tile_for_this_room": C.SETS[setname]["backdrop"],
            "why": "set C is four rooms of the same lodge, not four new rooms: each room map already "
                   "references the lodge tileset, so this set ships props only and reuses the lodge "
                   "material tiles (all four of these rooms stand on room_plank_a) rather than "
                   "shipping a second tileset.",
        },
        "contact_sheets": [f"assets/generated/v3/{setname}/contact_sheet.png",
                           f"assets/generated/v3/{setname}/contact_sheet_4x.png"],
        "room_map": f"frontend/v3/maps/rooms/{C.SETS[setname]['map']}",
        # PROP_STYLE entries the renderer needs: key -> lift px. The renderer draws the prop as
        # `footprint_w x (footprint_h + lift)` and anchors it bottom-centre.
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"] for a in C.ASSETS[setname]},
        "assets": assets,
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "texture_px_actual is measured from the published file. Every asset in this set is "
                "fitted INSIDE its declared box (SOU-200 asks for the exact size given within the "
                "ticket's size classes), so declared and actual agree by construction - the field "
                "is here because SOU-158's review had to reconcile that drift by hand.",
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
