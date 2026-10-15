#!/usr/bin/env python3
"""FLUX.2-klein-4B room/sprite generator for the Star-Office-UI fork (Mac-side).

Why this lives on the Mac and not in the cluster: the homelab's only GPU is a
3 GB Maxwell GTX 970M already claimed by gpu-embedding/ollama-embeddings, so
image generation is a Mac-side batch step (ADR-060 amendment, ADR-061 inv. 11).

Model/licence: black-forest-labs/FLUX.2-klein-4B (Apache-2.0, not gated)
LoRA:         Limbicnation/pixel-art-lora (Apache-2.0, CC0 training data)

Hard rules enforced by this script:
  * writes only under assets/generated/  (never overwrites shipped art)
  * never img2img, never trains on Star-Office or LimeZu PNGs (text-only prompts)
  * every output gets a manifest.json with model, lora, strength, prompt, seed

Usage:
  scripts/flux_room_gen.py smoke  [--seed N] [--size 512]
  scripts/flux_room_gen.py scene  --profile profiles/andrzej.json [--seed N]
  scripts/flux_room_gen.py sprite --profile profiles/andrzej.json [--seed N] [--pose idle]

Scene sizing note: the live flat scene (`office_bg_small.webp`, what the browser
shows today) is 1280x720, and the existing art is pixel art upscaled ~2x. So a
scene is generated at 640x360 and NEAREST-upscaled 2x -> exact 1280x720 with
uniform 2x2 pixels, matching the shipped look. The per-room world of SOU-88
(`frontend/nav.js`) uses a different space: scene 1216x832, cell 16, floor band
612-820 -- room packs for that layer must use `--canvas 1216x832`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "assets" / "generated"

MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"
LORA_ID = "Limbicnation/pixel-art-lora"

STEPS = 4
GUIDANCE = 1.0  # ignored by the distilled model; kept for the manifest's sake
TRIGGER = "pixel art sprite, {desc}, game asset, transparent background"

# One shared world style so every room reads as the same building (ADR-060 D6:
# cozy 16-bit lodge). "no text/logo/watermark" is a hard acceptance criterion:
# the shipped art carries CENTRAL PERK / FRIENDS / JAWS signage, which is a
# trademark problem independent of the LimeZu licence.
WORLD = "cozy 16-bit pixel art mountain lodge interior"
NO_TEXT = "no text, no letters, no words, no signage, no logo, no watermark, no user interface"


def _torch():
    try:
        import torch
    except ImportError:
        sys.exit(
            "torch is not importable in this interpreter. Use the dedicated venv:\n"
            "  ~/.venvs/sou-flux/bin/python scripts/flux_room_gen.py ..."
        )
    return torch


def load_pipe(with_lora=False):
    """Load the pipeline onto MPS. Returns (pipe, torch).

    The pixel-art LoRA is NOT loaded by default: in diffusers it collapses output
    to black at every documented strength (see ADR-060 field result). Opt in with
    `with_lora=True` for experiments only - never for asset generation.
    """
    torch = _torch()
    from diffusers import Flux2KleinPipeline

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    if device == "cpu":
        print("[warn] MPS unavailable - CPU fallback will be slow", file=sys.stderr)

    t0 = time.time()
    pipe = Flux2KleinPipeline.from_pretrained(MODEL_ID, torch_dtype=torch.bfloat16)
    if with_lora:
        print(
            "[warn] loading pixel-art LoRA: known to produce BLACK FRAMES at "
            "strength >= ~0.25 in diffusers (rsLoRA alpha=128/rank=64). "
            "Experiment only - do not use for asset output.",
            file=sys.stderr,
        )
        pipe.load_lora_weights(LORA_ID)
    else:
        print("[load] pixel-art LoRA skipped (unusable in diffusers - see ADR-060)")
    pipe.to(device)
    print(f"[load] {MODEL_ID} on {device} in {time.time() - t0:.1f}s", flush=True)
    return pipe, torch


def generate(pipe, torch, prompt, width, height, seed, steps=STEPS, guidance=GUIDANCE):
    gen = torch.Generator(device="cpu").manual_seed(seed)
    t0 = time.time()
    # NOTE: Flux2KleinPipeline's first positional parameter is `image` (klein is a
    # unified generate+edit model), so the prompt MUST be passed by keyword -
    # positionally it lands in `image` and the pipeline raises
    # "Provide either `prompt` or `prompt_embeds`".
    image = pipe(
        prompt=prompt,
        height=height,
        width=width,
        num_inference_steps=steps,
        guidance_scale=guidance,
        generator=gen,
    ).images[0]
    print(f"[gen] {width}x{height} seed={seed} steps={steps} in {time.time() - t0:.1f}s", flush=True)
    return image


# ---------------------------------------------------------------------------
# post-processing
# ---------------------------------------------------------------------------

def _pil_consts():
    """Pillow moved resampling/dither constants; support old and new names."""
    from PIL import Image

    nearest = getattr(getattr(Image, "Resampling", Image), "NEAREST", None) or Image.NEAREST
    dither_none = getattr(getattr(Image, "Dither", Image), "NONE", None) or Image.NONE
    return nearest, dither_none


def pixelize(img, size, colors=24):
    """Nearest-neighbour downscale (no anti-alias) + palette quantisation.

    Pillow's default resize filters would blend, which destroys pixel art, and
    a drop shadow would break the transparent-sprite contract. So: NEAREST only,
    alpha preserved, palette capped, no dithering.
    """
    from PIL import Image

    nearest, dither_none = _pil_consts()

    img = img.convert("RGBA")
    if img.size != size:
        img = img.resize(size, nearest)
    # Quantise only the RGB channels; keep alpha untouched so sprites stay cut out.
    alpha = img.getchannel("A")
    rgb = img.convert("RGB").quantize(colors=colors, dither=dither_none).convert("RGB")
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def upscale_nearest(img, factor):
    nearest, _ = _pil_consts()

    w, h = img.size
    return img.resize((w * factor, h * factor), nearest)


def write_manifest(path: Path, **extra):
    payload = {
        "model": MODEL_ID,
        "lora": LORA_ID,
        "steps": STEPS,
        "guidance": GUIDANCE,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    payload.update(extra)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return payload


def load_profile(path):
    data = json.loads(Path(path).read_text())
    if not data.get("agent_id"):
        sys.exit(f"{path}: profile needs an 'agent_id'")
    return data


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_smoke(args):
    """Phase 1: prove the stack generates one image on this machine."""
    outdir = GENERATED / "smoke"
    outdir.mkdir(parents=True, exist_ok=True)
    desc = args.desc or "a small round robot holding a wrench"
    prompt = TRIGGER.format(desc=desc) + ", 32-bit pixel art"

    pipe, torch = load_pipe()
    results = []
    # LoRA at 1.0, softened to 0.7, and off - the card's test range plus a control.
    for label, strength in (("lora1.0", 1.0), ("lora0.7", 0.7), ("nolora", 0.0)):
        if strength == 0.0:
            pipe.unload_lora_weights()
        elif label == "lora0.7":
            pipe.load_lora_weights(LORA_ID, adapter_name="soft")
            pipe.set_adapters(["soft"], adapter_weights=[strength])

        img = generate(pipe, torch, prompt, args.size, args.size, args.seed)
        raw = outdir / f"smoke_{label}_s{args.seed}.png"
        img.save(raw)
        pix = pixelize(img, (64, 64), colors=20)
        pix_path = outdir / f"smoke_{label}_s{args.seed}_64px.png"
        pix.save(pix_path)
        results.append(
            write_manifest(
                outdir / f"smoke_{label}_s{args.seed}.manifest.json",
                prompt=prompt,
                seed=args.seed,
                lora_strength=strength,
                resolution=f"{args.size}x{args.size}",
                output_file=raw.name,
                output_dims=list(img.size),
                pixelized_file=pix_path.name,
                pixelized_dims=list(pix.size),
            )
        )
        print(f"[out] {raw}  + {pix_path}")
    return 0


def cmd_scene(args):
    """One agent's room background, sized for the layer it will be shown in."""
    profile = load_profile(args.profile)
    agent_id = profile["agent_id"]
    outdir = GENERATED / "rooms" / agent_id
    outdir.mkdir(parents=True, exist_ok=True)

    cw, ch = (int(x) for x in args.canvas.split("x"))
    scale = args.scale
    gen_w, gen_h = cw // scale, ch // scale

    prompt = (
        f"{WORLD}, {profile['room_brief']}, "
        f"palette: {', '.join(profile.get('palette', []))}, "
        f"three-quarter oblique orthographic view, walls and floor visible, "
        f"empty room, no characters, no people, flat colors, clean pixel edges, {NO_TEXT}"
    )

    pipe, torch = load_pipe()
    raw = generate(pipe, torch, prompt, gen_w, gen_h, args.seed)
    raw_path = outdir / "background.raw.png"
    raw.save(raw_path)

    final = upscale_nearest(pixelize(raw, (gen_w, gen_h), colors=args.colors), scale)
    final_path = outdir / "background.png"
    final.save(final_path)
    final.convert("RGB").save(outdir / "background.webp", quality=92)

    manifest = write_manifest(
        outdir / "manifest.json",
        agent_id=agent_id,
        display_name=profile.get("display_name", agent_id),
        personality_source=profile.get("personality_source", "inferred"),
        prompt=prompt,
        seed=args.seed,
        lora_strength=1.0,
        resolution=f"{gen_w}x{gen_h}",
        canvas=f"{cw}x{ch}",
        upscale=scale,
        palette_colors=args.colors,
        output_file=final_path.name,
        output_dims=list(final.size),
    )
    print(f"[out] {final_path} {final.size}")
    print(json.dumps(manifest, indent=2))
    return 0


def cmd_sprite(args):
    """One agent character sprite, at the office's guest-character frame size."""
    profile = load_profile(args.profile)
    agent_id = profile["agent_id"]
    outdir = GENERATED / "rooms" / agent_id / "character"
    outdir.mkdir(parents=True, exist_ok=True)

    pose = args.pose
    pose_brief = profile.get("poses", {}).get(pose, pose)
    desc = (
        f"{profile['character_brief']}, {pose_brief}, "
        f"palette: {', '.join(profile.get('palette', []))}, "
        f"single character, centered, feet at the bottom edge, facing the viewer, "
        f"front three-quarter view to match the room camera"
    )
    prompt = TRIGGER.format(desc=desc) + ", 32-bit pixel art"

    pipe, torch = load_pipe()
    raw = generate(pipe, torch, prompt, args.size, args.size, args.seed)
    raw_path = outdir / f"{pose}.raw.png"
    raw.save(raw_path)

    frame = pixelize(raw, (args.frame, args.frame), colors=args.colors)
    frame_path = outdir / f"{pose}.png"
    frame.save(frame_path)

    manifest = write_manifest(
        outdir / f"{pose}.manifest.json",
        agent_id=agent_id,
        pose=pose,
        personality_source=profile.get("personality_source", "inferred"),
        prompt=prompt,
        seed=args.seed,
        lora_strength=1.0,
        resolution=f"{args.size}x{args.size}",
        output_file=frame_path.name,
        output_dims=list(frame.size),
        frame_size=args.frame,
    )
    print(f"[out] {frame_path} {frame.size}")
    print(json.dumps(manifest, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("smoke", help="generate the Phase 1 smoke sprite(s)")
    s.add_argument("--seed", type=int, default=0)
    s.add_argument("--size", type=int, default=512)
    s.add_argument("--desc", type=str, default=None)
    s.set_defaults(func=cmd_smoke)

    sc = sub.add_parser("scene", help="generate one agent's room background")
    sc.add_argument("--profile", required=True)
    sc.add_argument("--seed", type=int, default=0)
    sc.add_argument("--canvas", default="1280x720", help="final size (live flat scene = 1280x720; SOU-88 nav space = 1216x832)")
    sc.add_argument("--scale", type=int, default=2, help="nearest-neighbour upscale factor")
    sc.add_argument("--colors", type=int, default=24)
    sc.set_defaults(func=cmd_scene)

    sp = sub.add_parser("sprite", help="generate one agent character sprite")
    sp.add_argument("--profile", required=True)
    sp.add_argument("--seed", type=int, default=0)
    sp.add_argument("--size", type=int, default=512)
    sp.add_argument("--frame", type=int, default=64, help="final frame size (guest chars are 64x64)")
    sp.add_argument("--pose", default="idle")
    sp.add_argument("--colors", type=int, default=20)
    sp.set_defaults(func=cmd_sprite)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    # 16 GB of weights + activations on a 32 GB unified-memory machine otherwise
    # trips the default MPS allocator ceiling.
    os.environ.setdefault("PYTORCH_MPS_HIGH_WATERMARK_RATIO", "0.0")
    sys.exit(main())
