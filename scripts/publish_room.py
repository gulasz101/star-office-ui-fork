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
* ``schema`` says which contract a source pack speaks. ``1`` (or absent) is the
  v1 pixel/lattice contract below and is publishable. ``2`` is the grid-native
  descriptor ``scripts/gen_iso_room.py`` emits for renderer v2 (SOU-123), which
  the v1 served contract cannot express: the whole point of v2 is that the app
  derives pixels from the lattice, and ``index.html`` is not touched until
  SOU-128. A schema-2 pack is therefore SKIPPED here, and whatever its agent
  already has in the served tree is carried over byte-identically - v1 keeps
  serving until SOU-128 replaces the renderer (SOU-123 Ruling 1).

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


def pack_schema(agent_dir: Path) -> int:
    """Declared schema of a generated pack (``1``/absent = the v1 pixel+lattice
    contract the served tree speaks, ``2`` = the grid-native v2 descriptor of
    SOU-123).  Unreadable packs are treated as v1 so the publisher never
    silently drops a room it does not understand."""
    room = agent_dir / "room.json"
    if not room.exists():
        return 0
    try:
        return int(json.loads(room.read_text()).get("schema", 1))
    except (ValueError, TypeError, OSError):
        return 1


def discover_agents(explicit=None):
    """Generated packs this publisher may serve: schema 1 only.

    WHY the filter exists (SOU-123 Ruling 1): ``assets/generated/rooms/<agent>/
    room.json`` may now be a schema-2 v2 descriptor whose whole point is that
    the app derives pixels from the lattice. The v1 served contract cannot
    express it and ``frontend/index.html`` is untouched until SOU-128, so a v2
    pack is skipped here and its existing served entry is carried over
    byte-identically by ``_existing_entries()``.
    """
    if explicit:
        candidates = list(explicit)
    elif not GEN.is_dir():
        return []
    else:
        candidates = sorted(p.name for p in GEN.iterdir() if (p / "room.json").exists())
    return [a for a in candidates if pack_schema(GEN / a) == 1]


def v2_agents():
    """Packs that are published elsewhere (renderer v2), listed for reporting."""
    if not GEN.is_dir():
        return []
    return sorted(p.name for p in GEN.iterdir() if pack_schema(p) == 2)


def _existing_entries() -> dict:
    """The served ``index.json`` entries as they stand, in file order.

    Re-publishing must not disturb a room this run does not produce, so the
    index is rebuilt from these and then overwritten agent by agent - a
    byte-identical result whenever the served tree is already in sync.
    """
    idx = SERVED / "index.json"
    if not idx.exists():
        return {}
    try:
        return dict(json.loads(idx.read_text()).get("rooms", {}))
    except (ValueError, OSError):
        return {}


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
    v2 = v2_agents()
    if not agents and not v2:
        print("publish_room: no generated room packs found; nothing to publish")
        return 0

    if check:
        # Verify the served tree matches what publishing would produce today.
        # A schema-2 pack has no v1 contract to recompute, so its existing
        # served entry is expected as-is (it may not be rewritten by anyone).
        expected_index = {"rooms": _existing_entries()}
        for agent in agents:
            room = json.loads((GEN / agent / "room.json").read_text())
            expected_index["rooms"][agent] = {
                "room": f"rooms/{agent}/room.json",
                "props": f"rooms/{agent}/props/",
                "prop_count": len(room.get("prop_anchors", [])),
            }
        drift = []
        idx_path = SERVED / "index.json"
        if not idx_path.exists():
            # SOU-137: the served index may be deliberately DISABLED (at
            # sou-flux-art-pipeline 1fa3cfb it was renamed to
            # index.json.disabled so the office renders while v2 lands).
            # Absent index + nothing publishable is a coherent state, not drift.
            if agents or expected_index["rooms"]:
                drift.append("index.json")
        elif json.loads(idx_path.read_text()) != expected_index:
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
    entries = _existing_entries()
    for agent in agents:
        entries[agent] = publish_agent(agent)
        print(f"   published {agent}: {entries[agent]['prop_count']} props -> "
              f"/static/rooms/{agent}/")
    for agent in v2:
        kept = "carried over" if agent in entries else "not published"
        print(f"   skipped {agent}: schema 2 (renderer v2) has no v1 served "
              f"contract until SOU-128 - entry {kept}")
    if not entries and not (SERVED / "index.json").exists():
        # SOU-137: nothing to serve and no live index - do NOT create one.
        # Writing an empty index here would resurrect the served state that
        # sou-flux-art-pipeline 1fa3cfb deliberately disabled (the office
        # renders while v2 lands) and re-arm the loader with zero rooms.
        print("publish_room: served index absent/disabled and no schema-1 pack; "
              "index.json NOT written")
        return 0
    build_index(entries)
    print(f"publish_room: /static/rooms/index.json lists {len(entries)} room(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
