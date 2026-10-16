#!/usr/bin/env python3
"""Publish the SOU-216 garage bay + polish pass into the served v3 static tree (SOU-216).

Same shape as `publish_roomart_c.py` / `publish_lodge_art.py`: the generated pack under
`assets/generated/v3/` is the record, and the browser can only load what the app serves out of
`frontend/`, so this copies the files into their served locations and writes the contract the wiring
step consumes.

Where the files land, and why:

  * the GARAGE set goes to `frontend/v3/assets/garage/` - props + their manifests + `tiles.png`
    (the 2-slot concrete strip) + the individual tiles + `index.json`, exactly like every other set;
  * each POLISH file goes into its HOME SET's directory under its NEW name
    (`assets/lodge/tiles/stone_floor_soft.png`, `assets/serverroom/props/gear_shelf_full_a.png`, ...)
    because that is where the room's wiring reads it - and nothing already there is touched;
  * the two soft lodge tiles also ship as their own strip `assets/lodge/tiles_soft.png` (64x32). They
    are NOT appended to the shipped `assets/lodge/tiles.png`: that file is consumed by the shipped
    wiring and appending slots would shift ids under it (the ticket says new files, new names);
  * `frontend/v3/assets/polish-216.json` is the single machine-readable record of the whole polish
    pass: what each file replaces, the sha256 of the file it replaces, the comparison metrics, the
    lift where a prop is involved, and the strip slot order - so the wiring step re-derives nothing.

The copy is byte-identical and the publisher refuses and reports if it is not.

It does NOT touch `frontend/v3/maps/`, `placeholders.js` or any JS: wiring the garage room and
swapping the polished keys over is a separate step (Claude Code's, per the ticket).

Usage::

    python3 scripts/v3/publish_garage_polish.py              # garage + polish, copy + verify
    python3 scripts/v3/publish_garage_polish.py --check       # verify only, write nothing
    python3 scripts/v3/publish_garage_polish.py --only garage
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

import gen_garage_polish as G  # noqa: E402  (same directory: the set's own tables)

ISSUE = "SOU-216"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def garage_pairs():
    """(source, destination) absolute paths for every garage file, in the served layout."""
    src_root = G.out_dir("garage")
    dst_root = G.served_garage()
    rels = [("tiles.png", "tiles.png"),
            ("tiles.png.manifest.json", "tiles.png.manifest.json"),
            ("index.json", "index.json")]
    for name, _cls, _axes, _spec in G.GARAGE_TILES:
        rels.append((f"tiles/{name}.png", f"tiles/{name}.png"))
        rels.append((f"tiles/{name}.png.manifest.json", f"tiles/{name}.png.manifest.json"))
    for asset in G.GARAGE_PROPS:
        aid = asset["id"]
        rels.append((f"props/{aid}.png", f"props/{aid}.png"))
        rels.append((f"props/{aid}.png.manifest.json", f"props/{aid}.png.manifest.json"))
    return [(str(src_root / a), str(dst_root / b)) for a, b in rels]


def polish_pairs():
    """(source, destination, home) for every polish file, in its home set's served layout."""
    out = []
    for name, home, _axes, _spec in G.POLISH_TILES:
        out.append((str(G.polished_out(home) / "tiles" / f"{name}.png"),
                    str(G.served_home(home) / "tiles" / f"{name}.png"), home))
        out.append((str(G.polished_out(home) / "tiles" / f"{name}.png.manifest.json"),
                    str(G.served_home(home) / "tiles" / f"{name}.png.manifest.json"), home))
    for name, home, _members, _size in G.POLISH_STRIPS:
        out.append((str(G.polished_out(home) / f"{name}.png"),
                    str(G.served_home(home) / f"{name}.png"), home))
        out.append((str(G.polished_out(home) / f"{name}.png.manifest.json"),
                    str(G.served_home(home) / f"{name}.png.manifest.json"), home))
    for asset in G.POLISH_PROPS:
        home, aid = asset["home"], asset["id"]
        out.append((str(G.polished_out(home) / "props" / f"{aid}.png"),
                    str(G.served_home(home) / "props" / f"{aid}.png"), home))
        out.append((str(G.polished_out(home) / "props" / f"{aid}.png.manifest.json"),
                    str(G.served_home(home) / "props" / f"{aid}.png.manifest.json"), home))
    return out


def copy_and_verify(pairs, check):
    entries, mismatches = [], []
    for item in pairs:
        if len(item) == 2:
            src, dst = item
            home = "garage"
        else:
            src, dst, home = item
        if not os.path.exists(src):
            mismatches.append(f"missing source {src}")
            continue
        if not check:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
        if not os.path.exists(dst):
            mismatches.append(f"not published: {dst}")
            continue
        if sha256(src) != sha256(dst):
            mismatches.append(f"byte mismatch: {dst}")
        entries.append({"served_path": os.path.relpath(dst, str(G.REPO)),
                        "source": os.path.relpath(src, str(G.REPO)),
                        "home_set": home, "sha256": sha256(src), "bytes": os.path.getsize(src)})
    return entries, mismatches


def polish_record(entries):
    """The wiring contract for the polish pass: what replaces what, with the numbers."""
    tiles, props = [], []
    for name, home, _axes, _spec in G.POLISH_TILES:
        man_path = G.polished_out(home) / "tiles" / f"{name}.png.manifest.json"
        man = json.loads(man_path.read_text())
        tiles.append({
            "key": name, "home_set": home,
            "served_path": f"assets/{home}/tiles/{name}.png",
            "texture_px": [G.TILE, G.TILE],
            "replaces": man.get("replaces"),
            "replaces_sha256": (man.get("replaces_reference") or {}).get("sha256"),
            "block_contrast": man.get("block_contrast"),
            "log_contrast": man.get("log_contrast"),
            "replaces_block_contrast": (man.get("replaces_reference") or {}).get("block_contrast"),
            "replaces_log_contrast": (man.get("replaces_reference") or {}).get("log_contrast"),
            "seam_checked_axes": man.get("seam_axes"),
            "note": man.get("note", ""),
        })
    strips = []
    for name, home, members, size in G.POLISH_STRIPS:
        strips.append({"key": name, "home_set": home,
                       "served_path": f"assets/{home}/{name}.png",
                       "tile_px": G.TILE, "columns": len(members), "firstgid": 1,
                       "order": list(members),
                       "why": "the soft tiles are NEW slots in their OWN strip: appending them to "
                              "assets/lodge/tiles.png would rewrite a file the shipped wiring "
                              "already consumes and shift slot ids under it"})
    for asset in G.POLISH_PROPS:
        home, aid = asset["home"], asset["id"]
        man = json.loads((G.polished_out(home) / "props" / f"{aid}.png.manifest.json").read_text())
        props.append({
            "key": aid, "home_set": home,
            "served_path": f"assets/{home}/props/{aid}.png",
            "texture_px": list(asset["texture_px"]),
            "footprint_px": list(asset["footprint_px"]),
            "footprint_tiles": list(asset["footprint_tiles"]),
            "lift_px": asset["lift_px"],
            "flat": asset["flat"],
            "replaces": asset["replaces"],
            "replaces_sha256": (man.get("replaces_reference") or {}).get("sha256"),
            "packing": man.get("packing"),
            "line_marks": man.get("line_marks"),
            "ruled_lines": man.get("ruled_lines"),
            "note": asset.get("note", ""),
        })
    return {
        "issue": ISSUE,
        "set": "polish",
        "generated_from": f"assets/generated/v3/{G.POLISH_DIR}",
        "rule": "new files, new names: NOTHING shipped is overwritten, so wiring a key over is "
                "reversible by wiring it back",
        "tiles": tiles,
        "strips": strips,
        "props": props,
        "prop_lift_for_placeholders_js": {a["id"]: a["lift_px"] for a in G.POLISH_PROPS},
        "files": entries,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=("garage", "polish"), default=None,
                    help="publish one half only (default: both)")
    ap.add_argument("--check", action="store_true", help="verify the served copy, write nothing")
    args = ap.parse_args()

    rc = 0
    if args.only in (None, "garage"):
        src_root = str(G.out_dir("garage"))
        dst_root = str(G.served_garage())
        if not os.path.isdir(src_root):
            print(f"no generated set at {src_root}", file=sys.stderr)
            return 2
        entries, mismatches = copy_and_verify(garage_pairs(), args.check)
        print(("checked" if args.check else "published") + f" {len(entries)} garage files to "
              f"{dst_root}")
        if mismatches:
            rc = 1
            print("MISMATCH:")
            for m in mismatches:
                print(f"  - {m}")
        else:
            print(f"OK: every published garage file is byte-identical to its source "
                  f"({len(entries)} files)")

    if args.only in (None, "polish"):
        pairs = polish_pairs()
        if not os.path.isdir(str(G.out_dir("polish"))):
            print(f"no polish pack at {G.out_dir('polish')}", file=sys.stderr)
            return 2
        entries, mismatches = copy_and_verify(pairs, args.check)
        print(("checked" if args.check else "published") + f" {len(entries)} polish files into their "
              f"home sets")
        if mismatches:
            rc = 1
            print("MISMATCH:")
            for m in mismatches:
                print(f"  - {m}")
        else:
            print(f"OK: every published polish file is byte-identical to its source "
                  f"({len(entries)} files)")
        record_path = os.path.join(str(G.REPO), "frontend", "v3", "assets", "polish-216.json")
        if args.check:
            if not os.path.exists(record_path):
                rc = 1
                print(f"MISMATCH: {record_path} missing (the wiring contract)")
            else:
                have = json.loads(open(record_path).read())
                fresh = polish_record(entries)
                for key in ("tiles", "strips", "props", "prop_lift_for_placeholders_js"):
                    if have.get(key) != fresh.get(key):
                        rc = 1
                        print(f"MISMATCH: polish-216.json {key} is stale vs the generated pack")
                print("OK: frontend/v3/assets/polish-216.json matches the generated pack")
        else:
            os.makedirs(os.path.dirname(record_path), exist_ok=True)
            with open(record_path, "w") as fh:
                json.dump(polish_record(entries), fh, indent=2)
                fh.write("\n")
            print(f"wrote {record_path}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
