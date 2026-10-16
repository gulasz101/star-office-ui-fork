#!/usr/bin/env python3
"""publish_iso_pack.py - copy the schema-2 renderer-v2 pack into the served static tree.

SOU-124 S1. The generating source of truth stays ``assets/generated/rooms/<id>/``
(scripts/gen_iso_room.py). The v2 scene is served by Flask from the frontend static
tree (``frontend/`` == /static), so the exact files it loads are published here as
byte-identical copies - the same precedent as v1's ``frontend/rooms/**`` served tree
(scripts/publish_room.py), except schema-2 packs are skipped by that v1 publisher
(SOU-123 Ruling 1) and are handled by this one instead.

Published per room (and nothing else):
    room.json, room.map.json, walls.png,
    tiles/floor.png, tiles/collision.png,
    props/<id>.png            (not *.raw.png, not *.manifest.json, not rejected/)
plus ``frontend/office_v2/pack/index.json`` listing rooms sorted by id, so the
scene carries zero agent literals (SOU-115 ruling: identity comes from data).

Usage:
    python3 scripts/publish_iso_pack.py           # (re)publish the served copy
    python3 scripts/publish_iso_pack.py --check   # verify byte-identity, touch nothing

--check exits 0 only when the served tree matches the source byte-for-byte,
file-for-file, with no extra files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "assets" / "generated" / "rooms"
DEST_DIR = ROOT / "frontend" / "office_v2" / "pack"

# Files a room publishes, relative to the room directory. props are a glob.
FLAT_FILES = ("room.json", "room.map.json", "walls.png")
TILE_FILES = ("tiles/floor.png", "tiles/collision.png")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def schema2_rooms() -> list[str]:
    """Room ids whose room.json declares schema 2 (renderer-v2)."""
    rooms = []
    for room_dir in sorted(SRC_DIR.glob("*")):
        descriptor = room_dir / "room.json"
        if not descriptor.is_file():
            continue
        try:
            data = json.loads(descriptor.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if data.get("schema") == 2:
            rooms.append(room_dir.name)
    return rooms


def room_files(room_dir: Path) -> list[str]:
    """Sorted relative paths published for one room."""
    files = [p for p in FLAT_FILES if (room_dir / p).is_file()]
    files += [p for p in TILE_FILES if (room_dir / p).is_file()]
    props_dir = room_dir / "props"
    if props_dir.is_dir():
        for png in sorted(props_dir.glob("*.png")):
            name = png.name
            if name.endswith(".raw.png") or not png.is_file():
                continue
            files.append(f"props/{name}")
    return sorted(files)


def build_index(rooms: list[str]) -> dict:
    return {
        "schema": 2,
        "generated_by": "scripts/publish_iso_pack.py (SOU-124)",
        "rooms": [{"id": r, "descriptor": "room.json"} for r in sorted(rooms)],
    }


def expected_tree(rooms: list[str]) -> dict[str, Path]:
    """relpath under DEST_DIR -> source file, for every published byte."""
    tree: dict[str, Path] = {}
    for room in sorted(rooms):
        room_dir = SRC_DIR / room
        for rel in room_files(room_dir):
            tree[f"{room}/{rel}"] = room_dir / rel
    return tree


def publish(check_only: bool) -> int:
    rooms = schema2_rooms()
    if not rooms:
        print("FAIL no schema-2 packs found under assets/generated/rooms/")
        return 1

    tree = expected_tree(rooms)
    failures = 0

    if check_only:
        # 1) every expected file present and byte-identical
        for rel, src in sorted(tree.items()):
            dst = DEST_DIR / rel
            if not dst.is_file():
                print(f"FAIL missing {rel}")
                failures += 1
                continue
            src_sha, dst_sha = _sha256(src), _sha256(dst)
            if src_sha != dst_sha:
                print(f"FAIL differs {rel} src={src_sha[:12]} dst={dst_sha[:12]}")
                failures += 1
            else:
                print(f"OK {rel} sha256={src_sha[:12]}")
        # 2) index.json exactly what we would write
        want = (json.dumps(build_index(rooms), indent=2) + "\n").encode()
        got = (DEST_DIR / "index.json").read_bytes() if (DEST_DIR / "index.json").is_file() else b""
        if got != want:
            print("FAIL index.json drifted from the room list")
            failures += 1
        else:
            print("OK index.json")
        # 3) no extra files in the served copy
        present = {
            p.relative_to(DEST_DIR).as_posix()
            for p in DEST_DIR.rglob("*")
            if p.is_file() and p.name != "index.json"
        }
        extras = sorted(present - set(tree))
        for extra in extras:
            print(f"FAIL extra file {extra}")
            failures += 1
        print(f"{'PASS' if failures == 0 else 'FAIL'} byte-identity: "
              f"{len(tree)} files, {failures} failure(s)")
        return 0 if failures == 0 else 1

    DEST_DIR.mkdir(parents=True, exist_ok=True)
    for rel, src in sorted(tree.items()):
        dst = DEST_DIR / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.is_file() or _sha256(src) != _sha256(dst):
            shutil.copyfile(src, dst)
    (DEST_DIR / "index.json").write_bytes((json.dumps(build_index(rooms), indent=2) + "\n").encode())
    print(f"published {len(tree)} files for rooms {sorted(rooms)} -> {DEST_DIR.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify served tree byte-identity; touch nothing")
    args = ap.parse_args()
    return publish(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
