#!/usr/bin/env python3
"""RED proof for the SOU-200 gate: mutate a COPY of each set-C room and show the gate fails.

A gate that has never failed is not evidence (SOU-198/199 precedent). Each mutation targets one
assertion and the printed gate line names the measurement. Nothing here touches the real pack:
every case runs against a fresh copy under /tmp, and the class-bounds case mutates the imported
declaration in memory (never a file).

    python3 scripts/v3/red_proof_roomart_c.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_roomart_c as C  # noqa: E402

OUT = Path("/tmp")


def fresh(setname: str, case: str) -> Path:
    dst = OUT / f"sou200-red-{setname}-{case}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(C.out_dir(setname), dst)
    return dst


def run(setname: str, root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(HERE / "check_v3_assets.py"), "--set", setname,
         "--root", str(root), "--quiet"],
        capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={proc.returncode}\n" + "\n".join(lines)


def _first(setname: str) -> str:
    return C.ASSETS[setname][0]["id"]


def mutate_size(setname: str) -> None:
    aid = _first(setname)
    p = fresh(setname, "size") / "props" / f"{aid}.png"
    img = Image.open(p)
    img.resize((img.size[0], img.size[1] - 4), Image.Resampling.NEAREST).save(p)


def mutate_manifest(setname: str) -> None:
    aid = _first(setname)
    (fresh(setname, "manifest") / "props" / f"{aid}.png.manifest.json").unlink()


def mutate_bottom_row(setname: str) -> None:
    """Clear the last row of the canvas: the renderer anchors the footprint there, so the prop
    would float - this is the assertion that catches a mis-anchored prop."""
    aid = _first(setname)
    p = fresh(setname, "bottom") / "props" / f"{aid}.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    for x in range(img.size[0]):
        px[x, img.size[1] - 1] = (0, 0, 0, 0)
    img.save(p)


def mutate_off_palette(setname: str) -> None:
    aid = _first(setname)
    p = fresh(setname, "palette") / "props" / f"{aid}.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    placed = False
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            if px[x, y][3]:
                px[x, y] = (255, 0, 0, 255)
                placed = True
                break
        if placed:
            break
    img.save(p)


def mutate_magenta_residue(setname: str) -> None:
    aid = _first(setname)
    p = fresh(setname, "magenta") / "props" / f"{aid}.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    placed = False
    for y in range(img.size[1] - 1, -1, -1):
        for x in range(img.size[0]):
            if px[x, y][3]:
                px[x, y] = (255, 0, 255, 255)
                placed = True
                break
        if placed:
            break
    img.save(p)


def class_bounds_case(setname: str) -> str:
    """Widen one declared 1x1 box to 48x48 IN MEMORY and show the class assertion fires.

    This is the one defect a purely artifact-level mutation cannot reach: a generator whose own
    table describes a box outside the ticket's size class stamps a perfectly consistent artifact
    (the file matches the declaration) that is still wrong about the ticket. Run in-process so the
    mutated declaration is visible to the gate, and never written to disk.
    """
    import check_v3_assets as gate

    asset = next(a for a in C.ASSETS[setname] if a["klass"] == "1x1" and a["texture_px"][0] == 32)
    original = list(asset["texture_px"])
    asset["texture_px"] = [48, 48]
    asset["footprint_px"] = [48, 32]
    asset["lift_px"] = 48 - 32
    buf = io.StringIO()
    try:
        args = argparse.Namespace(root=str(C.out_dir(setname)), map=None, quiet=True)
        with contextlib.redirect_stdout(buf):
            rc = gate.main_set_c(args, setname)
    finally:
        asset["texture_px"] = original
        asset["footprint_px"] = [32, 32]
        asset["lift_px"] = original[1] - 32
    lines = [ln for ln in buf.getvalue().splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={rc}\n" + "\n".join(lines)


CASES = [
    ("size", "shipped size drifted 4px shorter than the ticket box", mutate_size),
    ("manifest", "manifest deleted (AC: one next to every file)", mutate_manifest),
    ("bottom", "bottom row of the canvas cleared (hovers)", mutate_bottom_row),
    ("palette", "one pixel recoloured off-palette", mutate_off_palette),
    ("magenta", "one pixel painted bright magenta (key residue)", mutate_magenta_residue),
]

for setname in C.SETS:
    for slug, label, fn in CASES:
        fn(setname)
        root = OUT / f"sou200-red-{setname}-{slug}"
        print("=" * 90)
        print(f"MUTATION [{setname}] {label}")
        print(run(setname, root))
    print("=" * 90)
    print(f"MUTATION [{setname}] generator declaration widened outside the ticket's 1x1 class "
          f"(in-memory, no file touched)")
    print(class_bounds_case(setname))
