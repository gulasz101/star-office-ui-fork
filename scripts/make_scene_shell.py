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


def zero_rgb_under_alpha(img: Image.Image) -> Image.Image:
    """Set RGB to black wherever alpha is 0.

    The keying step paints alpha=0 but keeps the magenta RGB underneath; a texture
    filter in the browser samples those RGB values and fringes the sprite.
    """
    img = img.convert("RGBA")
    px = img.load()
    w, h = img.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0 and (r or g or b):
                px[x, y] = (0, 0, 0, 0)
    return img


def repair_sheets() -> list[str]:
    """Fix magenta-under-alpha in every guest slot sheet we generated."""
    fixed = []
    for slot in (1, 2, 3, 4):
        p = FRONTEND / f"guest_anim_{slot}.webp"
        if not p.exists():
            continue
        im = Image.open(p).convert("RGBA")
        before = im.copy()
        im = zero_rgb_under_alpha(im)
        if im.tobytes() != before.tobytes():
            if not DRY:
                im.save(p, quality=95)
            fixed.append(str(p.relative_to(REPO)))
    return fixed


def main():
    profile = json.loads((REPO / "profiles" / "scene_shell_office.json").read_text())
    out_dir = GEN / "scene"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / "office_bg_small.raw.png"

    print(f"== scene shell: {profile['canvas']['w']}x{profile['canvas']['h']} ==")
    print(f"   clear zones: {[z['id'] for z in profile['clear_zones']]}")

    if not DRY:
        pipe, torch = frg.load_pipe()          # LoRA stays off (ADR-060 field result)
        img = frg.generate(pipe, torch, profile["prompt"],
                           profile["canvas"]["w"], profile["canvas"]["h"], seed=101)
        img.save(raw_path)
        print(f"   raw -> {raw_path.relative_to(REPO)}")

        pp = profile["postprocess"]
        final = frg.pixelize(img, (profile["canvas"]["w"] // pp["scale"],
                                   profile["canvas"]["h"] // pp["scale"]),
                             colors=pp["colors"])
        out_png = out_dir / "office_bg_small.png"
        final.save(out_png)
        if pp["scale"] != 1:
            final = final.resize((profile["canvas"]["w"], profile["canvas"]["h"]), Image.NEAREST)
        final = zero_rgb_under_alpha(final)

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
