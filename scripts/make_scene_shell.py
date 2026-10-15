#!/usr/bin/env python3
"""Generate the room SHELL backdrop - the fix for the doubled-furniture scene.

Background: the first backdrop I generated was a *furnished* room, but the engine
also places its own furniture sprites at hardcoded coordinates (desk_v2 at 218,417;
coffee_machine at 659,397; serverroom at 1021,142; posters at 252,66; sofa at
IDLE_SOFA_ANCHOR). Result: painted desks duplicated the sprite desks, and the agent
avatars rendered 'inside' a painted desk.

So the backdrop's contract is: ROOM SHELL ONLY - walls, floor, wall-mounted
decoration - with the four engine-managed zones left as clear, empty floor. See
profiles/scene_shell_office.json for the zones and the measured palette.

Also fixes a defect in the sprite pipeline found while inspecting this: the keying
step maps the magenta key to alpha=0 but leaves the magenta RGB under it, so every
generated sheet carries #ff23ff in fully transparent pixels. Invisible on an opaque
canvas, magenta fringing the moment a browser filters the texture.

Usage: python scripts/make_scene_shell.py [--dry-run]
"""
import importlib.util
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
GEN = REPO / "assets" / "generated"
BACKUPS = GEN / "_backups"
FRONTEND = REPO / "frontend"

spec = importlib.util.spec_from_file_location("frg", REPO / "scripts" / "flux_room_gen.py")
frg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(frg)

DRY = "--dry-run" in sys.argv
FROM_RAW = "--from-raw" in sys.argv   # re-post-process the existing raw; no GPU, no regeneration


def zero_rgb_under_alpha(img: Image.Image) -> int:
    """Set RGB to black wherever alpha is 0; return how many pixels were fixed.

    The keying step paints alpha=0 but keeps the magenta RGB underneath. Invisible on
    an opaque canvas, but a browser's texture filter samples those RGB values and can
    fringe the sprite magenta at the edges.
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    fixed = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 and (r or g or b):
                px[x, y] = (0, 0, 0, 0)
                fixed += 1
    return fixed


def magenta_under_alpha(img: Image.Image) -> int:
    """Count fully transparent pixels that still carry magenta-ish RGB."""
    px = img.convert("RGBA").load()
    w, h = img.size
    n = 0
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 and r > 100 and b > 100 and g < 120:
                n += 1
    return n


def repair_sheets() -> list[str]:
    """Fix magenta-under-alpha in every guest slot sheet we generated.

    Two traps learned the hard way here:
      * a byte-inequality check is the WRONG skip condition - these are lossy WebP, so a
        decode/re-encode round trip is never byte-identical and every run would recompress
        the art. Count the actual defect instead.
      * saving the repaired sheet as lossy WebP REPOPULATES colour in transparent pixels,
        so the fix must be written losslessly or it does not survive its own save.
    """
    fixed = []
    for slot in (1, 2, 3, 4):
        p = FRONTEND / f"guest_anim_{slot}.webp"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGBA")
        n = magenta_under_alpha(im)
        if n == 0:
            continue
        if not DRY:
            zero_rgb_under_alpha(im)
            im.save(p, lossless=True, quality=100)
            after = magenta_under_alpha(Image.open(p).convert("RGBA"))
            fixed.append(f"{p.relative_to(REPO)} ({n} px, {after} left after save)")
        else:
            fixed.append(f"{p.relative_to(REPO)} ({n} px, dry-run)")
    return fixed


def main():
    profile = json.loads((REPO / "profiles" / "scene_shell_office.json").read_text())
    out_dir = GEN / "scene"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "office_bg_small.raw.png"

    print(f"== scene shell: {profile['canvas']['w']}x{profile['canvas']['h']} ==")
    print(f"   clear zones: {[z['id'] for z in profile['clear_zones']]}")

    if not DRY:
        if FROM_RAW:
            if not raw_path.exists():
                raise SystemExit(f"--from-raw needs an existing {raw_path}")
            img = Image.open(raw_path).convert("RGB")
            print(f"   reusing raw -> {raw_path.relative_to(REPO)} (no generation)")
        else:
            pipe, torch = frg.load_pipe()      # LoRA stays off (ADR-060 field result)
            img = frg.generate(pipe, torch, profile["prompt"],
                               profile["canvas"]["w"], profile["canvas"]["h"], seed=101)
            img.save(raw_path)
            print(f"   raw -> {raw_path.relative_to(REPO)}")

        pp = profile["postprocess"]
        final = frg.pixelize(img, (profile["canvas"]["w"] // pp["scale"],
                                   profile["canvas"]["h"] // pp["scale"]),
                             colors=pp["colors"])
        if pp["scale"] != 1:
            final = final.resize((profile["canvas"]["w"], profile["canvas"]["h"]), Image.NEAREST)
        zero_rgb_under_alpha(final)      # in place; returns a count, do not rebind
        out_png = out_dir / "office_bg_small.png"
        final.save(out_png)

        # deployable replacement
        target = FRONTEND / "office_bg_small.webp"
        prev = BACKUPS / "office_bg_small.webp.prev-generated"
        if target.exists() and not prev.exists():
            shutil.copy2(target, prev)
        if not (BACKUPS / "office_bg_small.webp.orig").exists():
            print("   WARNING: no .orig backup of the SHIPPED backdrop present")
        final.save(target, quality=95)
        print(f"   installed -> frontend/office_bg_small.webp ({final.size})")

        manifest = {
            "model": frg.MODEL_ID, "lora": frg.LORA_ID, "lora_strength": 0.0,
            "steps": frg.STEPS, "guidance": frg.GUIDANCE, "seed": 101,
            "prompt": profile["prompt"], "palette_colors": pp["colors"],
            "asset_key": profile["asset_key"], "kind": profile["kind"],
            "source_file": str(raw_path.relative_to(REPO)),
            "output_file": target.name, "output_dims": list(final.size),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "clear_zones": profile["clear_zones"],
            "note": ("Room SHELL only, by design: the engine places desk/coffee/server/poster sprites at "
                     "fixed coordinates, so painting furniture here duplicates them. LoRA not applied "
                     "(unusable through diffusers at documented strengths, ADR-060 field result)."),
        }
        (out_dir / "office_bg_small.png.manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"   manifest -> {out_dir.name}/office_bg_small.png.manifest.json")

    fixed = repair_sheets()
    print(f"== sheets repaired (magenta under alpha): {fixed or 'none needed'} ==")
    print("SCENE_SHELL_DONE")


if __name__ == "__main__":
    main()
