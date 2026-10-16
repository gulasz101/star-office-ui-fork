#!/usr/bin/env python3
"""RED proof for the SOU-199 gate: mutate a COPY of each set-B room and show the gate fails.

A gate that has never failed is not evidence (SOU-198 precedent). Each mutation targets one
assertion, and the printed gate line names the measurement.

    python3 scripts/v3/red_proof_roomart_b.py            # writes /tmp/sou199-red-* and runs the gate
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_roomart_b as B  # noqa: E402

OUT = Path("/tmp")


def fresh(setname: str) -> Path:
    dst = OUT / f"sou199-red-{setname}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(B.out_dir(setname), dst)
    return dst


def run(setname: str, root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(HERE / "check_v3_assets.py"), "--set", setname,
         "--root", str(root), "--quiet"],
        capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={proc.returncode}\n" + "\n".join(lines)


def mutate_size(setname: str) -> None:
    dst = fresh(setname) / "props" / "workbench.png"
    img = Image.open(dst)
    img.resize((img.size[0], img.size[1] - 4), Image.Resampling.NEAREST).save(dst)


def mutate_manifest(setname: str) -> None:
    (fresh(setname) / "props" / "stool.png.manifest.json").unlink()


def mutate_bleed(setname: str) -> None:
    dst = fresh(setname) / "props" / "belt_h.png"
    img = Image.open(dst).convert("RGBA")
    px = img.load()
    for y in range(6, 12):
        for x in range(4, 20):
            px[x, y] = (0, 0, 0, 0)
    img.save(dst)


def mutate_frame_identical(setname: str) -> None:
    dst = fresh(setname) / "props" / "assembler.png"
    img = Image.open(dst).convert("RGBA")
    second = img.crop((0, 0, 64, 80))
    img.paste(second, (64, 0))
    img.save(dst)


def mutate_declaration(setname: str) -> None:
    """Drift the generator's own table away from the ticket: the gate must catch its own source."""
    dst = fresh(setname) / "props" / "cable_coil.png"
    img = Image.open(dst)
    img.resize((img.size[0] - 2, img.size[1]), Image.Resampling.NEAREST).save(dst)


CASES = {
    "serverroom": [("prop resized 4px shorter", mutate_size),
                   ("manifest deleted", mutate_manifest),
                   ("declared size drifted (width -2)", mutate_declaration)],
    "factory": [("belt_h: transparent hole punched", mutate_bleed),
                ("assembler: frame 2 overwritten with frame 1", mutate_frame_identical)],
}

for setname, cases in CASES.items():
    for label, fn in cases:
        fn(setname)
        root = OUT / f"sou199-red-{setname}"
        print("=" * 90)
        print(f"MUTATION [{setname}] {label}")
        print(run(setname, root))
