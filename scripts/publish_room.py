#!/usr/bin/env python3
"""Publish generated room packs into the SERVED tree, and nothing else (SOU-115).

LAYERING (the contract the office renderer is built on)
-------------------------------------------------------
* ``assets/generated/rooms/<agent>/`` is the **source of truth**. It is written
  by the generators (``scripts/gen_room_set.py``, ``scripts/finish_room_props.py``)
  and is NEVER served.
* ``frontend/rooms/`` is the **served copy**. It is produced ONLY by this script.
  Flask serves ``frontend/`` at ``/static``, so a published room answers at
  ``/static/rooms/...``. Do not hand-edit ``frontend/rooms/**`` and do not point
  a generator at it; regenerate with this script instead.

SERVED CONTRACT
---------------
``/static/rooms/index.json``::

    { "rooms": { "<agent>": { "room": "rooms/<agent>/room.json",
                              "props": "rooms/<agent>/props/",
                              "prop_count": N } } }

``room.json`` keeps the declared geometry verbatim and only RENDERS THE NAMES
the app expects: ``lattice -> floor``, ``walls -> wall``, ``tiles ->
target_tiles``. Per-prop ``base_slopes`` and ``gate`` are merged from the
prop manifests (the ``decor`` exemption rides along inside ``gate``; anything
the renderer does not use it ignores - SOU-115 AC7).

Geometry is DECLARED and DRAWN, never inferred from pixels (ADR-065): the
renderer computes ``grid_to_screen(gx + 0.5, gy + 0.5)`` from ``floor.origin``
and the floor's own tile vectors, reproducing ``scripts/room_geometry.py``.
This script therefore never re-derives, rescales or re-measures anything - it
copies bytes and renames fields.

Usage:
    python3 scripts/publish_room.py            # publish every generated room
    python3 scripts/publish_room.py andrzej    # ...or only the named agents
    python3 scripts/publish_room.py --check    # exit 1 if served tree drifts
"""
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated" / "rooms"
SERVED = REPO / "frontend" / "rooms"

# Keys of the source room.json that pass into the served room.json untouched.
PASSTHROUGH = ("agent_id", "display_name", "canvas", "projection", "wall_seam_y")


def discover_agents(explicit=None):
    if explicit:
        return list(explicit)
    if not GEN.is_dir():
        return []
    return sorted(p.name for p in GEN.iterdir() if (p / "room.json").exists())


def normalize_room(src: dict) -> dict:
    """Source-of-truth room.json -> served contract. Renames only; no geometry work.

    The lattice values (origin, tile_w, tile_h) are copied as-is: the renderer's
    grid_to_screen uses the room's OWN floor vectors - nothing here may "fix"
    them from the background image (ADR-065, SOU-115 AC3).
    """
    pub = {k: src[k] for k in PASSTHROUGH if k in src}
    lat = src["lattice"]
    pub["floor"] = {"origin": lat["origin"], "tile_w": lat["tile_w"], "tile_h": lat["tile_h"]}
    if "slope" in lat:
        pub["floor"]["slope"] = lat["slope"]
    walls = src.get("walls", {})
    pub["wall"] = {"planes": walls.get("planes", []), "rise_px": walls.get("rise_px")}
    for k in ("baseboard_px", "seam", "anchor"):
        if k in walls:
            pub["wall"][k] = walls[k]

    anchors = []
    for a in src.get("prop_anchors", []):
        prop = {
            "id": a["id"],
            "file": a["file"],
            "grid": list(a["grid"]),
            "target_tiles": a["tiles"],
        }
        if "decor" in a:                      # tolerate and pass through (AC7)
            prop["decor"] = a["decor"]
        # merge the measurement fields from the prop's own manifest
        manifest = GEN / src["agent_id"] / "props" / (Path(a["file"]).stem + ".manifest.json")
        if manifest.exists():
            man = json.loads(manifest.read_text())
            for k in ("base_slopes", "gate"):
                if k in man:
                    prop[k] = man[k]
        anchors.append(prop)
    pub["prop_anchors"] = anchors
    return pub


def _dump_json(path: Path, data) -> str:
    """Deterministic JSON so re-publishing is byte-stable (idempotency gate)."""
    text = json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    path.write_text(text)
    return text


def publish_agent(agent: str) -> dict:
    src_dir = GEN / agent
    room = json.loads((src_dir / "room.json").read_text())
    out_dir = SERVED / agent
    props_out = out_dir / "props"
    props_out.mkdir(parents=True, exist_ok=True)

    # copy ONLY the anchored prop PNGs - never raw intermediates, never rejected/.
    # Byte-identical by construction: the served copy is a faithful mirror, not a render.
    for a in room.get("prop_anchors", []):
        src = src_dir / a["file"]
        if not src.exists():
            raise SystemExit(f"publish_room: missing anchored prop {src} (generate first)")
        shutil.copy2(src, props_out / Path(a["file"]).name)

    preview = room.get("preview") or "room_preview.png"
    if (src_dir / preview).exists():
        shutil.copy2(src_dir / preview, out_dir / preview)

    _dump_json(out_dir / "room.json", normalize_room(room))
    return {
        "room": f"rooms/{agent}/room.json",
        "props": f"rooms/{agent}/props/",
        "prop_count": len(room.get("prop_anchors", [])),
    }


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_index(entries: dict) -> str:
    return _dump_json(SERVED / "index.json", {"rooms": entries})


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    check = "--check" in argv
    agents = discover_agents(args or None)
    if not agents:
        print("publish_room: no generated room packs found; nothing to publish")
        return 0

    if check:
        # Verify the served tree matches what publishing would produce today.
        expected_index = {"rooms": {}}
        for agent in agents:
            room = json.loads((GEN / agent / "room.json").read_text())
            expected_index["rooms"][agent] = {
                "room": f"rooms/{agent}/room.json",
                "props": f"rooms/{agent}/props/",
                "prop_count": len(room.get("prop_anchors", [])),
            }
        drift = []
        idx_path = SERVED / "index.json"
        if not idx_path.exists() or json.loads(idx_path.read_text()) != expected_index:
            drift.append("index.json")
        for agent in agents:
            want = normalize_room(json.loads((GEN / agent / "room.json").read_text()))
            got_path = SERVED / agent / "room.json"
            if not got_path.exists() or json.loads(got_path.read_text()) != want:
                drift.append(f"{agent}/room.json")
            for a in want["prop_anchors"]:
                src = GEN / agent / a["file"]
                dst = SERVED / agent / "props" / Path(a["file"]).name
                if not dst.exists() or _sha(src) != _sha(dst):
                    drift.append(f"{agent}/props/{Path(a['file']).name}")
        if drift:
            print("publish_room --check: served tree drifts: " + ", ".join(drift))
            return 1
        print(f"publish_room --check: OK ({len(agents)} room(s) published, in sync)")
        return 0

    SERVED.mkdir(parents=True, exist_ok=True)
    entries = {}
    for agent in agents:
        entries[agent] = publish_agent(agent)
        print(f"   published {agent}: {entries[agent]['prop_count']} props -> "
              f"/static/rooms/{agent}/")
    build_index(entries)
    print(f"publish_room: /static/rooms/index.json lists {len(entries)} room(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
