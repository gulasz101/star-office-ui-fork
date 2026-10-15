#!/usr/bin/env python3
"""Generate a room's furniture set from a JSON profile, in the room's declared projection.

This is the answer to the operator's instruction: "just generate the new assets per room -
this desk, server rack, whatever is needed for a particular agent". It implements ADR-065:

  * the room declares its geometry (imported from room_geometry.ROOM);
  * every prop is generated IN that projection, stated numerically in the prompt, because
    "isometric" alone is not a specification (measured: asking for 2:1 returns 0.577);
  * every prop is keyed, then GATED by scripts/check_prop_alignment.py - a prop whose base
    slopes do not match the lattice never enters the room;
  * every prop is anchored to a declared grid cell, so placement is arithmetic;
  * the room is composited so the result can be LOOKED at, and a preview saved.

One command regenerates a room from its profile:
    python scripts/gen_room_set.py profiles/rooms/andrzej_room.json
"""
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated" / "rooms"

# 1024x1024 brought the M1 Max to ~100 MB free and its VAE decode stalled for minutes
# (measured 2026-10-15). A prop renders at ~100-200 px in the scene, so 640 is ample and
# leaves the machine room to breathe. Override with PROP_SIZE=<n>.
PROP_SIZE = int(os.environ.get("PROP_SIZE", "640"))

spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
frg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frg)
spec2 = importlib.util.spec_from_file_location("gip", REPO / "scripts" / "gen_iso_prop.py")
gip = importlib.util.module_from_spec(spec2)
spec2.loader.exec_module(gip)
spec3 = importlib.util.spec_from_file_location("geo", REPO / "scripts" / "room_geometry.py")
geo = importlib.util.module_from_spec(spec3)
spec3.loader.exec_module(geo)
spec4 = importlib.util.spec_from_file_location("cpa", REPO / "scripts" / "check_prop_alignment.py")
cpa = importlib.util.module_from_spec(spec4)
spec4.loader.exec_module(cpa)


def main():
    profile = json.loads(Path(sys.argv[1]).read_text())
    agent = profile["agent_id"]
    out_dir = GEN / agent / "props"
    out_dir.mkdir(parents=True, exist_ok=True)
    palette = ", ".join(profile["palette"]["warm"] + profile["palette"]["accent"])
    # The lattice is the ROOM's declared slope, taken from the single source of truth instead
    # of restated here. DECIDED look: 30-degree true isometric (Pharaoh/Zeus) - ADR-065.
    lattice = geo.ROOM["floor"]["tile_h"] / geo.ROOM["floor"]["tile_w"]
    max_attempts = 3

    pipe, torch = frg.load_pipe()      # LoRA off (ADR-060 field result)
    placed, rejected = [], []
    for prop in profile["props"]:
        # restart-safe: a prop already on disk is reused, but ONLY after re-checking it against
        # the CURRENT lattice - a resumed run must not inherit a prop that the gate would now
        # reject (the gate was tightened from 0.12 to 0.07 when the 30-degree look was chosen).
        done_png = out_dir / f"{prop['id']}.png"
        done_man = out_dir / f"{prop['id']}.manifest.json"
        if done_png.exists() and done_man.exists() and "--force" not in sys.argv:
            prev = json.loads(done_man.read_text())
            sl = prev.get("base_slopes") or []
            if len(sl) == 2 and all(abs(abs(s) - lattice) <= cpa.TOLERANCE for s in sl):
                placed.append({"id": prop["id"], "grid": prop["grid"], "tiles": prop["tiles"],
                               "base_slopes": sl})
                print(f"   {prop['id']:8s} reused, still on the {lattice:.3f} lattice {sl}")
                continue
            print(f"   {prop['id']:8s} on disk but FAILS the {lattice:.3f} gate {sl} -> re-rolling")

        prompt = (
            f"pixel art game asset: {prop['subject']}. {gip.PROJECTION}"
            "Single object, centred, nothing else in frame, no room, no floor drawn. "
            f"Palette limited to {palette} plus dark grey, with crisp thin dark outlines and "
            "flat cel shading, no antialiasing, no blur, no checkerboard pattern, "
            "The object rests DIRECTLY on the flat magenta background: do NOT draw any ground "
            "patch, base, platform, pedestal, mat, rug, plinth or contact shadow under it - the "
            "magenta background extends right up to the object's feet. "
            "plain flat magenta background #FF00FF filling the whole frame. 32-bit pixel art, game asset."
        )

        # GATE + RE-ROLL. The generator returns ~0.50 (2:1) or ~0.577 (30 deg) for the SAME
        # instruction - measured, not assumed - so an attempt that misses the room's lattice is
        # retried with a new seed. Accepting it would put two projections in one room; silently
        # averaging them would hide that. A prop that never lands is rejected and listed.
        accepted = None
        for attempt in range(max_attempts):
            seed = 900 + len(placed) * 10 + attempt
            raw = frg.generate(pipe, torch, prompt, PROP_SIZE, PROP_SIZE, seed=seed)
            raw.save(out_dir / f"{prop['id']}.raw.png")
            keyed, key = gip.key_magenta(raw)
            keyed.save(out_dir / f"{prop['id']}.png")
            slopes = cpa.base_slopes(out_dir / f"{prop['id']}.png")
            if slopes is None:
                print(f"   {prop['id']:8s} attempt {attempt + 1}: empty after keying (key={key})")
                continue
            left, right = slopes
            ok = (abs(abs(left) - lattice) <= cpa.TOLERANCE
                  and abs(abs(right) - lattice) <= cpa.TOLERANCE)
            print(f"   {prop['id']:8s} attempt {attempt + 1}: slopes {left:+.3f}/{right:+.3f} "
                  f"{'ON LATTICE' if ok else 'off the lattice -> re-roll'}")
            if ok:
                accepted = (left, right, key, seed)
                break
        if accepted is None:
            rejected.append({"id": prop["id"],
                             "reason": f"no attempt within {cpa.TOLERANCE} of {lattice:.3f} "
                                       f"in {max_attempts} tries"})
            continue
        left, right, key, seed = accepted

        (out_dir / f"{prop['id']}.manifest.json").write_text(json.dumps({
            "model": frg.MODEL_ID, "lora": frg.LORA_ID, "lora_strength": 0.0,
            "steps": frg.STEPS, "guidance": frg.GUIDANCE, "seed": seed,
            "prompt": prompt, "key_colour": list(key)[:3],
            "base_slopes": [round(left, 3), round(right, 3)], "lattice_slope": round(lattice, 3),
            "grid": prop["grid"], "target_tiles": prop["tiles"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "note": ("Generated IN the room's declared 30-degree isometric projection; gated by "
                     "check_prop_alignment (re-rolled if the generator returned a 2:1 prop)."),
        }, indent=2) + "\n")
        placed.append({"id": prop["id"], "grid": prop["grid"], "tiles": prop["tiles"],
                       "base_slopes": [round(left, 3), round(right, 3)]})

    # composite the room so it can be looked at
    img, _ = geo.draw_room(show_grid=False)
    for p in placed:
        geo.place(img, out_dir / f"{p['id']}.png", p["grid"][0], p["grid"][1], target_tiles=p["tiles"])
    preview = GEN / agent / "room_preview.png"
    img.save(preview)

    room = {
        "agent_id": agent, "display_name": profile.get("display_name", agent),
        "canvas": geo.ROOM["canvas"], "projection": profile["projection"],
        "lattice": {"origin": geo.ROOM["floor"]["origin"], "tile_w": geo.ROOM["floor"]["tile_w"],
                    "tile_h": geo.ROOM["floor"]["tile_h"], "slope": lattice,
                    "screen": "screen(gx,gy) = (ox + (gx-gy)*tw/2, oy + (gx+gy)*th/2)"},
        "wall_seam_y": geo.ROOM["wall"]["seam_y"],
        "prop_anchors": [{"id": p["id"], "file": f"props/{p['id']}.png", "grid": p["grid"], "tiles": p["tiles"]} for p in placed],
        "rejected": rejected,
        "preview": preview.name,
    }
    (GEN / agent / "room.json").write_text(json.dumps(room, indent=2) + "\n")
    print(f"== room {agent}: {len(placed)} placed, {len(rejected)} rejected ==")
    print(f"   room.json + preview -> assets/generated/rooms/{agent}/")
    print("ROOM_SET_DONE")


if __name__ == "__main__":
    main()
