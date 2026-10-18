#!/usr/bin/env python3
"""Publish the SOU-277 cat set into the served v3 static tree.

Same shape as `publish_lodge_art.py`: `assets/generated/v3/cats/` is the record (raws, prompts,
seeds and a manifest next to every file), and the browser can only load what the Flask app
serves out of `frontend/`, so this copies the strips into `frontend/v3/assets/cats/` and writes
an `index.json` recording each file's sha256, byte size and MEASURED size.

What `index.json` is for: SOU-278 (the sibling ticket) wires `CorridorCat` to these sheets, and
the one thing it must not have to re-derive is the vocabulary - the coat ids, the frame layout,
and where the sheet lives. It is published here, before the consumer's run, because a run works
from what it read at the top of its ticket (the SOU-283 lesson: publish the shared vocabulary
BEFORE the runs that must agree on it).

The copy is byte-identical (the script refuses and reports if it is not), and it does NOT touch
`main.js`, `ambience.js` or `placeholders.js` - wiring is SOU-278's ticket, and doing it here
would make an art ticket silently own a render change.

Usage::

    python3 scripts/v3/publish_cats_art.py           # copy + verify + index
    python3 scripts/v3/publish_cats_art.py --check    # verify only, write nothing
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

import gen_cats_art as C  # noqa: E402  (same directory: the set's own tables)

REPO = os.path.dirname(os.path.dirname(HERE))
SRC = os.path.join(REPO, "assets", "generated", "v3", "cats")
DST = os.path.join(REPO, "frontend", "v3", "assets", "cats")

NAMES = [c["id"] for c in C.COATS] + ["fight_cloud"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def pairs():
    out = [("palette.json", "palette.json")]
    for name in NAMES:
        out.append((f"props/{name}.png", f"{name}.png"))
        out.append((f"props/{name}.png.manifest.json", f"{name}.png.manifest.json"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()

    if not os.path.isdir(SRC):
        print(f"no generated set at {SRC}", file=sys.stderr)
        return 2

    from PIL import Image

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
        entries.append({"served_path": f"assets/cats/{dst_rel}",
                        "source": f"assets/generated/v3/cats/{src_rel}",
                        "sha256": sha256(src), "bytes": os.path.getsize(src)})

    # the vocabulary the CONSUMER (SOU-278) needs, plus the measured px of every file
    sheets = []
    for coat in C.COATS:
        served = os.path.join(DST, f"{coat['id']}.png")
        row = {
            "key": coat["id"],
            "label": coat["label"],
            "served_path": f"assets/cats/{coat['id']}.png",
            "texture_key_suggestion": f"cat-{coat['id']}",
            "sheet_px": list(C.TICKET_SIZE["coat_strip_px"]),
            "frame_px": list(C.TICKET_SIZE["frame_px"]),
            "frames": C.FRAMES,
            "frame_order": C.FRAME_LABELS,
            "facing": "frames face right; the renderer mirrors the sheet for left",
            "coat_source": ("shipped in SOU-198 (the lodge cat), re-derived"
                            if coat.get("walk_shipped") else
                            f"reference edit of {coat['reference']}"),
        }
        if os.path.exists(served):
            row["texture_px_actual"] = list(Image.open(served).size)
        sheets.append(row)
    cloud = {
        "key": "fight_cloud",
        "served_path": "assets/cats/fight_cloud.png",
        "texture_key_suggestion": "cat-fight-cloud",
        "sheet_px": list(C.TICKET_SIZE["cloud_strip_px"]),
        "frame_px": list(C.TICKET_SIZE["cloud_frame_px"]),
        "frames": C.CLOUD_FRAMES,
        "play": "at the midpoint between two fighting cats, both hidden",
    }
    cp = os.path.join(DST, "fight_cloud.png")
    if os.path.exists(cp):
        cloud["texture_px_actual"] = list(Image.open(cp).size)

    index_path = os.path.join(DST, "index.json")
    index = {
        "pack": "cats-v1",
        "set": "cats",
        "issue": "SOU-277",
        "consumer": "SOU-278 (CorridorCat: a random coat per cat, chase / play / groom / fight)",
        "generated_from": "assets/generated/v3/cats",
        "model": "black-forest-labs/FLUX.2-klein-4B",
        "lora": "not used (collapses to black in diffusers - ADR-060)",
        "view": "side view (profile), facing right",
        "palette": "assets/generated/v3/cats/palette.json (the LODGE's 61 colours, imported - "
                   "one palette family with the room the cats walk through)",
        "set_scale": json.loads((open(os.path.join(SRC, "assemble_summary.json")).read())
                                )["set_scale"] if
        os.path.exists(os.path.join(SRC, "assemble_summary.json")) else None,
        "set_scale_note": "every pose of every coat is fitted at ONE scale, measured from the "
                          "shipped lodge cat, so the cat does not change size as it sits down",
        "coat_keys": [c["id"] for c in C.COATS],
        "random_coat_pool": "pick without replacement from `coat_keys` (7 coats before a repeat)",
        "sheets": sheets,
        "fight_cloud": cloud,
        "frame_contract": {
            "0-3": "walk cycle, right",
            "4": "sitting",
            "5": "curled up asleep",
            "6": "crouched, tail up (pounce)",
            "7": "arched back, fur up (hiss / fight)",
            "source": "SOU-277 ticket table - the same order in all seven coats",
        },
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
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
          f"{len(sheets)} coats + 1 cloud)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
