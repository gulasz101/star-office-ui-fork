#!/usr/bin/env python3
"""Pixelize one raw generation into a contract-conforming art asset (SOU-98, AC2).

The Mac-side batch step (ADR-060 as amended: FLUX.2-klein-4B + pixel-art LoRA,
Apache-2.0 weights) produces 512x512 RGBA. The renderer slices sheets per asset
by ``frameWidth``/``frameHeight`` and keys position/scale by filename, so a raw
generation only becomes a usable asset after a post-process that is driven by
``assets/asset-contract.json``. This script is that post-process. It does not
generate anything and it never touches the network: it takes an existing RGBA
file and writes a conforming one plus a provenance sidecar.

Rules it enforces (all from AC2):

* **nearest-neighbour downscale only** - bilinear/Lanczos/box are not reachable,
  so no anti-aliasing is ever introduced; alpha is preserved end to end;
* palette quantisation to ``--palette N`` (default 24, floor 16), median-cut,
  ``dither=NONE``, no drop shadow and no other effects;
* it **refuses to write** when the target size is not an exact multiple of the
  frame size of the asset being replaced, or when the requested frame size
  differs from the contract - no output file and no manifest are created;
* it writes ``<output>.manifest.json`` with the full provenance record
  ``{model, lora, lora_strength, prompt, seed, steps, guidance, source_file,
  output_file, output_dims, palette_colors, created_at}`` plus the frame grid it
  produced, which is what ``scripts/validate_generated_assets.py`` checks;
* it will not clobber shipped art: an existing output (or anything under
  ``frontend/`` or ``assets/`` outside ``assets/generated/``) needs ``--force``.

Usage::

    python3 scripts/pixelize_asset.py \\
        --input /tmp/raw_512.png \\
        --output assets/generated/pack-a/coffee-machine-v3-grid.webp \\
        --contract assets/asset-contract.json --asset coffee_machine \\
        --model black-forest-labs/FLUX.2-klein-4B --lora Limbicnation/pixel-art-lora \\
        --lora-strength 0.85 --prompt "cozy lodge coffee machine, no text" \\
        --seed 12345 --steps 24 --guidance 3.5

Without ``--target-size`` the target is the size the contract records for the
asset, so a replacement sheet keeps the frame count the animations were authored
against. Exit 0 = written, 1 = usage/IO problem, 2 = refused (contract violation,
palette below the floor, or a protected output path).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

PALETTE_DEFAULT = 24
PALETTE_FLOOR = 16

REQUIRED_MANIFEST_FIELDS = (
    "model",
    "lora",
    "lora_strength",
    "prompt",
    "seed",
    "steps",
    "guidance",
    "source_file",
    "output_file",
    "output_dims",
    "palette_colors",
    "created_at",
)

REFUSAL = 2


class Refusal(Exception):
    """A conformance violation: nothing is written."""


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_contract(path: str) -> dict:
    if not os.path.exists(path):
        raise Refusal(f"no contract at {path}; run scripts/extract_asset_contract.py first")
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def contract_asset(contract: dict, key: str) -> dict:
    assets = contract.get("assets", {})
    if key in assets:
        return assets[key]
    # Pack manifests and generator invocations name the logical asset
    # (`office_bg_small`) rather than the Phaser key (`office_bg`); resolve that
    # through the contract instead of refusing, the same way
    # scripts/validate_generated_assets.py does (SOU-104 AC5/AC6).
    for asset_key, entry in assets.items():
        names = {entry.get("logical_asset"), asset_key}
        names.update(entry.get("logical_assets") or [])
        for file_entry in entry.get("files") or []:
            names.add(file_entry.get("variant"))
            names.add(file_entry.get("logical_asset"))
        for name in names:
            if not name:
                continue
            if key in (name, os.path.splitext(name)[0]):
                return entry
    raise Refusal(f"asset '{key}' is not in the contract ({len(assets)} assets on file)")


def parse_size(text: str):
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", text or "")
    if not match:
        raise Refusal(f"--target-size must look like 512x512, got {text!r}")
    return int(match.group(1)), int(match.group(2))


def grid_is_exact(target_width, target_height, frame_width, frame_height):
    return (
        frame_width > 0
        and frame_height > 0
        and target_width % frame_width == 0
        and target_height % frame_height == 0
    )


def protect_output_path(output: str, force: bool) -> None:
    """Never overwrite shipped art: only assets/generated/ is a free write area.

    Anything already on disk needs ``--force``, and anything inside the shipped
    static dirs (``frontend/`` or ``assets/`` outside ``assets/generated/``)
    needs it even when the file does not exist yet, because writing new files
    next to the LimeZu pack is how an unreplaced asset gets mistaken for one.
    """
    root = _repo_root()
    absolute = os.path.abspath(output)
    inside_repo = absolute.startswith(root + os.sep)
    if inside_repo:
        relative = os.path.relpath(absolute, root).replace(os.sep, "/")
        protected = relative.startswith("frontend/") or (
            relative.startswith("assets/") and not relative.startswith("assets/generated/")
        )
        if protected and not force:
            raise Refusal(
                f"{relative} is inside the shipped static area; generated packs land in "
                f"assets/generated/<pack>/ and are swapped in deliberately (pass --force "
                f"to write here anyway)"
            )
    if os.path.exists(absolute) and not force:
        raise Refusal(
            f"refusing to overwrite existing output {output}; pass --force if that is "
            f"intended (shipped art is replaced deliberately, never by accident)"
        )


def resolve_targets(args, contract: dict | None):
    """(frame_width, frame_height, target_width, target_height, asset_key)."""
    asset = None
    asset_key = args.asset
    frame = (args.frame_width, args.frame_height)
    if args.contract:
        contract = contract or load_contract(args.contract)
        if not asset_key and args.output:
            base = os.path.basename(args.output)
            for key, entry in contract.get("assets", {}).items():
                if any(file_entry.get("variant") == base for file_entry in entry["files"]):
                    asset_key = key
                    break
        if asset_key:
            asset = contract_asset(contract, asset_key)
        if asset is not None:
            if asset.get("kind") == "image":
                # A `load.image` asset (backgrounds, shadows, the desk) is one
                # picture with no frame grid, so its only legal "frame" is the
                # whole file.  Refusing for lack of a frame size made the
                # contract unusable for exactly the assets that most need it
                # (SOU-104 AC5); anything else stays a 1 x 1 grid.
                contract_frame = (
                    asset.get("expected_width"),
                    asset.get("expected_height"),
                )
                if None in contract_frame:
                    raise Refusal(
                        f"asset '{asset_key}' has no size in the contract; pass "
                        f"--frame-width/--frame-height explicitly"
                    )
                if frame[0] is not None and frame != contract_frame:
                    raise Refusal(
                        f"frame size {frame[0]}x{frame[1]} differs from the contract for "
                        f"'{asset_key}' ({contract_frame[0]}x{contract_frame[1]}): this is "
                        f"a single-image asset, the renderer never slices it"
                    )
                frame = contract_frame
            else:
                contract_frame = (
                    asset.get("expected_frame_width"),
                    asset.get("expected_frame_height"),
                )
                if None in contract_frame:
                    raise Refusal(
                        f"asset '{asset_key}' has no frame size in the contract; pass "
                        f"--frame-width/--frame-height explicitly"
                    )
                if frame[0] is not None and frame != contract_frame:
                    raise Refusal(
                        f"frame size {frame[0]}x{frame[1]} differs from the contract for "
                        f"'{asset_key}' ({contract_frame[0]}x{contract_frame[1]}): the renderer "
                        f"slices this key at the contract size, so the sheet would be mis-sliced"
                    )
                frame = contract_frame
    if None in frame:
        raise Refusal("frame size is unknown: pass --contract/--asset or --frame-width/--frame-height")

    if args.target_size:
        target = parse_size(args.target_size)
    elif asset is not None and asset.get("expected_width") and asset.get("expected_height"):
        target = (asset["expected_width"], asset["expected_height"])
    else:
        raise Refusal(
            "target size is unknown: pass --target-size WxH (or --contract/--asset so the "
            "contract size applies)"
        )
    return frame[0], frame[1], target[0], target[1], asset_key


def pixelize(
    input_path: str,
    output_path: str,
    frame_width: int,
    frame_height: int,
    target_width: int,
    target_height: int,
    palette: int = PALETTE_DEFAULT,
    provenance: dict | None = None,
) -> dict:
    """Turn one raw RGBA file into a conforming asset and return its manifest."""
    from PIL import Image

    if palette < PALETTE_FLOOR:
        raise Refusal(
            f"palette {palette} is below the floor of {PALETTE_FLOOR} colours; the lodge "
            f"palette needs the headroom (AC2)"
        )
    if not os.path.exists(input_path):
        raise Refusal(f"no such input file: {input_path}")

    if not grid_is_exact(target_width, target_height, frame_width, frame_height):
        raise Refusal(
            f"target {target_width}x{target_height} is not an exact multiple of the frame "
            f"size {frame_width}x{frame_height}; refusing to write "
            f"({target_width / frame_width:.3f} x {target_height / frame_height:.3f} frames)"
        )

    with Image.open(input_path) as raw:
        source = raw.convert("RGBA")
    source_width, source_height = source.size

    # Nearest neighbour, never a smoothing filter: pixel art must stay hard-edged.
    resized = source.resize((target_width, target_height), Image.NEAREST)

    # Alpha is never a quantisation casualty: it is lifted out, the invisible
    # RGB under fully transparent pixels is flattened to one canonical black so
    # it cannot eat palette slots, the visible RGB is median-cut without dither,
    # and the original alpha goes back bit-exact.
    alpha = resized.getchannel("A")
    has_transparency = alpha.getextrema()[0] == 0
    mask = alpha.point(lambda value: 255 if value else 0)
    flat = Image.composite(resized, Image.new("RGBA", resized.size, (0, 0, 0, 0)), mask)
    cap = palette - 1 if has_transparency else palette
    if cap >= 256:
        result = flat.copy()
        result.putalpha(alpha)
    else:
        quantized = flat.convert("RGB").quantize(
            colors=cap, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
        result = quantized.convert("RGB").convert("RGBA")
        result.putalpha(alpha)

    directory = os.path.dirname(os.path.abspath(output_path))
    os.makedirs(directory, exist_ok=True)
    save_kwargs = {}
    extension = os.path.splitext(output_path)[1].lower()
    if extension == ".webp":
        save_kwargs = {"lossless": True, "quality": 100, "method": 6}
    result.save(output_path, **save_kwargs)

    with Image.open(output_path) as written:
        colours = (written.convert("RGBA").getcolors(maxcolors=1 << 20) or [])
        actual_colours = len(colours)
        out_width, out_height = written.size

    manifest = {
        "model": None,
        "lora": None,
        "lora_strength": None,
        "prompt": None,
        "seed": None,
        "steps": None,
        "guidance": None,
        "source_file": input_path,
        "output_file": os.path.basename(output_path),
        "output_dims": [out_width, out_height],
        "palette_colors": actual_colours,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "asset_key": asset_key_for(output_path, provenance),
        "frame_size": [frame_width, frame_height],
        "columns": out_width // frame_width,
        "rows": out_height // frame_height,
        "source_dims": [source_width, source_height],
        "resample": "NEAREST",
        "dither": "NONE",
    }
    manifest.update({k: v for k, v in (provenance or {}).items() if k in REQUIRED_MANIFEST_FIELDS})
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
    if missing:  # defensive: the required set is written above, so this is a bug guard
        raise Refusal(f"internal error, manifest is missing {missing}")
    return manifest


def asset_key_for(output_path: str, provenance: dict | None) -> str | None:
    return (provenance or {}).get("asset_key")


def manifest_path(output_path: str, override: str | None = None) -> str:
    return override or f"{output_path}.manifest.json"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, help="raw RGBA generation to post-process")
    parser.add_argument("--output", required=True, help="conforming file to write")
    parser.add_argument("--contract", help="assets/asset-contract.json (enables --asset)")
    parser.add_argument("--asset", help="contract key this file replaces")
    parser.add_argument("--frame-width", type=int, help="explicit frame width (else from contract)")
    parser.add_argument("--frame-height", type=int, help="explicit frame height (else from contract)")
    parser.add_argument("--target-size", help="WxH of the output sheet (else the contract size)")
    parser.add_argument("--palette", type=int, default=PALETTE_DEFAULT,
                        help=f"palette cap (default {PALETTE_DEFAULT}, floor {PALETTE_FLOOR})")
    parser.add_argument("--manifest", help="sidecar path (default: <output>.manifest.json)")
    parser.add_argument("--model")
    parser.add_argument("--lora")
    parser.add_argument("--lora-strength", type=float)
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file", help="read the prompt from a file instead")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--steps", type=int)
    parser.add_argument("--guidance", type=float)
    parser.add_argument("--force", action="store_true", help="allow overwriting an existing file")
    args = parser.parse_args(argv)

    prompt = args.prompt
    if args.prompt_file and os.path.exists(args.prompt_file):
        with open(args.prompt_file, "r", encoding="utf-8") as handle:
            prompt = handle.read().strip()

    try:
        contract = load_contract(args.contract) if args.contract else None
        frame_width, frame_height, target_width, target_height, asset_key = resolve_targets(args, contract)
        protect_output_path(args.output, args.force)
        if args.palette < PALETTE_FLOOR:
            raise Refusal(
                f"palette {args.palette} is below the floor of {PALETTE_FLOOR} colours "
                f"(AC2: default {PALETTE_DEFAULT}, floor {PALETTE_FLOOR})"
            )
        manifest = pixelize(
            args.input, args.output, frame_width, frame_height, target_width, target_height,
            palette=args.palette,
            provenance={
                "model": args.model,
                "lora": args.lora,
                "lora_strength": args.lora_strength,
                "prompt": prompt,
                "seed": args.seed,
                "steps": args.steps,
                "guidance": args.guidance,
                "asset_key": asset_key,
            },
        )
        manifest["asset_key"] = manifest.get("asset_key") or asset_key
    except Refusal as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return REFUSAL
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    sidecar = manifest_path(args.output, args.manifest)
    with open(sidecar, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")

    width, height = manifest["output_dims"]
    print(f"input  {manifest['source_file']}: {manifest['source_dims'][0]}x{manifest['source_dims'][1]} RGBA")
    print(f"output {args.output}: {width}x{height}, grid {manifest['columns']}x{manifest['rows']} "
          f"of {manifest['frame_size'][0]}x{manifest['frame_size'][1]}")
    print(f"palette {manifest['palette_colors']} colours (cap {args.palette}), "
          f"resample {manifest['resample']}, dither {manifest['dither']}")
    print(f"manifest {sidecar}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
