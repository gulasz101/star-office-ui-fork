#!/usr/bin/env python3
"""RED proof for the SOU-216 gate: mutate a COPY of the garage set and of the polish pass, and show
every new assertion fires.

A gate that has never failed is not evidence (the SOU-198/199/200 precedent). Each case targets ONE
assertion and the printed gate line names the measurement. Nothing here touches the real pack: every
file-level case runs against a fresh copy under /tmp, the declaration-drift case mutates the imported
table in memory, and the "shipped file was overwritten" case runs in-process against a throwaway git
repository built for it (the only way to make a *committed* file differ from the working tree without
touching the real one).

    python3 scripts/v3/red_proof_garage_polish.py
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import gen_garage_polish as G  # noqa: E402

OUT = Path("/tmp")
REPO = G.REPO


def fresh_garage(case: str) -> Path:
    dst = OUT / f"sou216-red-garage-{case}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(G.out_dir("garage"), dst)
    return dst


def fresh_polish(case: str) -> Path:
    dst = OUT / f"sou216-red-polish-{case}"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(G.out_dir("polish"), dst)
    return dst


def run(setname: str, root: Path) -> str:
    proc = subprocess.run(
        [sys.executable, str(HERE / "check_v3_assets.py"), "--set", setname, "--root", str(root),
         "--quiet"], capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={proc.returncode}\n" + "\n".join(lines)


def _prop(root: Path, aid: str) -> Path:
    return root / "props" / f"{aid}.png"


# ---------------------------------------------------------------------------------------------
# garage cases
# ---------------------------------------------------------------------------------------------
def garage_size(case_root: Path) -> None:
    p = _prop(case_root, "van_parked")
    img = Image.open(p)
    img.resize((img.size[0], img.size[1] - 4), Image.Resampling.NEAREST).save(p)


def garage_manifest(case_root: Path) -> None:
    _prop(case_root, "toolbox").with_name("toolbox.png.manifest.json").unlink()


def garage_bottom_row(case_root: Path) -> None:
    p = _prop(case_root, "oil_drum")
    img = Image.open(p).convert("RGBA")
    px = img.load()
    for x in range(img.size[0]):
        px[x, img.size[1] - 1] = (0, 0, 0, 0)
    img.save(p)


def garage_off_palette(case_root: Path) -> None:
    p = _prop(case_root, "pegboard")
    img = Image.open(p).convert("RGBA")
    px = img.load()
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            if px[x, y][3]:
                px[x, y] = (255, 0, 0, 255)
                break
        else:
            continue
        break
    img.save(p)


def garage_magenta(case_root: Path) -> None:
    p = _prop(case_root, "tire_stack")
    img = Image.open(p).convert("RGBA")
    px = img.load()
    for y in range(img.size[1] - 1, -1, -1):
        for x in range(img.size[0]):
            if px[x, y][3]:
                px[x, y] = (255, 0, 255, 255)
                break
        else:
            continue
        break
    img.save(p)


def garage_tile_ring(case_root: Path) -> None:
    """Break the calm border ring: the tile is no longer periodic by construction."""
    p = case_root / "tiles" / "garage_floor.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    px[0, 0] = G.lodge._rgb("amber") + (255,)
    px[31, 0] = G.lodge._rgb("led_teal") + (255,)
    img.save(p)


def garage_stain_outside(case_root: Path) -> None:
    """Put part of the 'oil stain' on the border ring: it is then not the same tile plus a stain."""
    p = case_root / "tiles" / "garage_floor_stain.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    for x in range(6, 12):
        px[x, 30] = G.lodge._rgb("ink") + (255,)
    img.save(p)


def garage_strip_order(case_root: Path) -> None:
    """Swap the two slots in the published strip: the declared order no longer describes it."""
    p = case_root / "tiles.png"
    img = Image.open(p).convert("RGBA")
    a = img.crop((0, 0, 32, 32))
    b = img.crop((32, 0, 64, 32))
    out = Image.new("RGBA", (64, 32), (0, 0, 0, 0))
    out.alpha_composite(b, (0, 0))
    out.alpha_composite(a, (32, 0))
    out.save(p)


def garage_index_lift(case_root: Path) -> None:
    """Drift the published lift table by one pixel: the wiring step would anchor it wrong."""
    import json

    p = case_root / "index.json"
    index = json.loads(p.read_text())
    index["prop_lift_for_placeholders_js"]["van_parked"] = 31
    p.write_text(json.dumps(index, indent=2) + "\n")


def garage_declaration_drift() -> str:
    """Widen the van's declared box in memory and show the ticket assertion fires."""
    import check_v3_assets as gate

    asset = next(a for a in G.GARAGE_PROPS if a["id"] == "van_parked")
    original = list(asset["texture_px"])
    asset["texture_px"] = [96, 120]
    asset["lift_px"] = 120 - 96
    buf = io.StringIO()
    try:
        args = argparse.Namespace(root=str(G.out_dir("garage")), map=None, quiet=True)
        with contextlib.redirect_stdout(buf):
            rc = gate.main_garage(args)
    finally:
        asset["texture_px"] = original
        asset["lift_px"] = original[1] - 96
    lines = [ln for ln in buf.getvalue().splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    return f"exit={rc}\n" + "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# polish cases
# ---------------------------------------------------------------------------------------------
def polish_soft_tile_unchanged(case_root: Path) -> None:
    """Ship the SHIPPED stone_floor as the soft variant: nothing got softer."""
    src = G.served_original("lodge", "tile", "stone_floor")
    shutil.copyfile(src, case_root / "lodge" / "tiles" / "stone_floor_soft.png")


def polish_log_tile_unchanged(case_root: Path) -> None:
    src = G.served_original("lodge", "tile", "wall_face")
    shutil.copyfile(src, case_root / "lodge" / "tiles" / "wall_face_soft.png")


def polish_shelf_not_fuller(case_root: Path) -> None:
    """Ship the nearly-empty shipped shelf under the new name: the room is exactly as wrong."""
    src = G.served_original("serverroom", "prop", "gear_shelf_a")
    shutil.copyfile(src, case_root / "serverroom" / "props" / "gear_shelf_full_a.png")


def polish_paper_letters(case_root: Path) -> None:
    """Put letter-shaped marks on the paper: a 'T' (a 5px bar over a 1px vertical stroke).

    Deliberately NOT a solid 3x2 block: a 3px-wide block reads as a short LINE under the shared
    threshold, which is how the first version of this mutation failed to fire - and that is the
    reason the eraser and the checker now use ONE number (MARK_MIN_RUN) instead of two.
    """
    p = case_root / "writer" / "props" / "paper_stacks_lined.png"
    img = Image.open(p).convert("RGBA")
    px = img.load()
    ink = G.lodge._rgb("slate_500") + (255,)
    for x in range(6, 11):
        px[x, 22] = ink                    # the bar
    for y in range(22, 27):
        px[8, y] = ink                     # the stem: 1px runs in every row it occupies
    img.save(p)


def polish_manifest_baseline(case_root: Path) -> None:
    """Corrupt the recorded sha256 of the replaced file: the comparison baseline is unverifiable."""
    import json

    p = case_root / "lodge" / "tiles" / "stone_floor_soft.png.manifest.json"
    m = json.loads(p.read_text())
    m["replaces_reference"]["sha256"] = "0" * 64
    p.write_text(json.dumps(m, indent=2) + "\n")


def polish_shipped_overwritten() -> str:
    """Overwrite a SHIPPED file and show the no-in-place-edit assertion fires.

    Runs in-process against a throwaway git repository that mirrors the served layout for the files
    the check reads, because the assertion is a comparison against a COMMITTED blob and the real
    repository must not be touched. The polish pack is still read from the real one.
    """
    import json
    import check_v3_assets as gate

    tmp = Path(tempfile.mkdtemp(prefix="sou216-red-repo-"))
    served = ["frontend/v3/assets/lodge/tiles/stone_floor.png",
              "frontend/v3/assets/lodge/tiles/wall_face.png",
              "frontend/v3/assets/lodge/tiles.png",
              "frontend/v3/assets/serverroom/props/gear_shelf_a.png",
              "frontend/v3/assets/serverroom/props/gear_shelf_b.png",
              "frontend/v3/assets/writer/props/paper_stacks.png"]
    for rel in served:
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    for home, fid, kind in (("lodge", "stone_floor_soft", "tiles"),
                            ("lodge", "wall_face_soft", "tiles"),
                            ("lodge", "tiles_soft", ""),
                            ("serverroom", "gear_shelf_full_a", "props"),
                            ("serverroom", "gear_shelf_full_b", "props"),
                            ("writer", "paper_stacks_lined", "props")):
        rel = (f"frontend/v3/assets/{home}/{fid}.png" if not kind
               else f"frontend/v3/assets/{home}/{kind}/{fid}.png")
        dst = tmp / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / rel, dst)
    subprocess.run(["git", "init", "-q"], cwd=str(tmp), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp), check=True)
    subprocess.run(["git", "-c", "user.email=r@e.d", "-c", "user.name=red", "commit", "-qm", "base"],
                   cwd=str(tmp), check=True)
    # now overwrite one shipped file in place
    victim = tmp / "frontend/v3/assets/serverroom/props/gear_shelf_a.png"
    img = Image.open(victim).convert("RGBA")
    px = img.load()
    px[0, 0] = (255, 0, 0, 255)
    img.save(victim)

    original_repo = gate.REPO
    buf = io.StringIO()
    try:
        gate.REPO = tmp
        args = argparse.Namespace(root=str(G.out_dir("polish")), map=None, quiet=True,
                                  shipped_ref="HEAD")
        with contextlib.redirect_stdout(buf):
            rc = gate.main_polish(args)
    finally:
        gate.REPO = original_repo
    lines = [ln for ln in buf.getvalue().splitlines() if "FAIL" in ln or ln.startswith("RESULT")]
    shutil.rmtree(tmp, ignore_errors=True)
    return f"exit={rc}\n" + "\n".join(lines)


GARAGE_CASES = [
    ("size", "prop shipped 4px shorter than the ticket's box", garage_size),
    ("manifest", "manifest deleted (one next to every file)", garage_manifest),
    ("bottom", "bottom row of the canvas cleared (the prop would float)", garage_bottom_row),
    ("palette", "one pixel recoloured off-palette", garage_off_palette),
    ("magenta", "one pixel painted bright magenta (key residue)", garage_magenta),
    ("tile-ring", "the calm border ring broken (no longer periodic by construction)", garage_tile_ring),
    ("stain-outside", "part of the oil stain painted onto the border ring", garage_stain_outside),
    ("strip-order", "the two tiles swapped inside tiles.png", garage_strip_order),
    ("index-lift", "the published lift table drifted by 1px", garage_index_lift),
]

POLISH_CASES = [
    ("soft-tile-not-softer", "the shipped strong stone_floor shipped as the soft variant",
     polish_soft_tile_unchanged),
    ("log-tile-not-softer", "the shipped stripy wall_face shipped as the soft variant",
     polish_log_tile_unchanged),
    ("shelf-not-fuller", "the nearly-empty shipped shelf shipped under the full_ name",
     polish_shelf_not_fuller),
    ("paper-letters", "glyph-shaped 2x3 marks painted on the paper", polish_paper_letters),
    ("manifest-baseline", "the recorded sha256 of the replaced file corrupted",
     polish_manifest_baseline),
]


def main() -> int:
    failures = 0
    for slug, label, fn in GARAGE_CASES:
        root = fresh_garage(slug)
        fn(root)
        out = run("garage", root)
        ok = "RESULT: FAIL" in out
        failures += 0 if ok else 1
        print("=" * 90)
        print(f"MUTATION [garage] {label}   -> {'FIRED' if ok else 'DID NOT FIRE'}")
        print(out)
    out = garage_declaration_drift()
    ok = "RESULT: FAIL" in out
    failures += 0 if ok else 1
    print("=" * 90)
    print(f"MUTATION [garage] the generator's own table widened outside the ticket's box "
          f"(in-memory, no file touched)   -> {'FIRED' if ok else 'DID NOT FIRE'}")
    print(out)

    for slug, label, fn in POLISH_CASES:
        root = fresh_polish(slug)
        fn(root)
        out = run("polish", root)
        ok = "RESULT: FAIL" in out
        failures += 0 if ok else 1
        print("=" * 90)
        print(f"MUTATION [polish] {label}   -> {'FIRED' if ok else 'DID NOT FIRE'}")
        print(out)

    out = polish_shipped_overwritten()
    ok = "RESULT: FAIL" in out and "DIFFERS" in out
    failures += 0 if ok else 1
    print("=" * 90)
    print(f"MUTATION [polish] a SHIPPED file overwritten in place (throwaway git repo)   -> "
          f"{'FIRED' if ok else 'DID NOT FIRE'}")
    print(out)

    print("=" * 90)
    if failures:
        print(f"RED PROOF INCOMPLETE: {failures} mutation(s) did not fire the gate")
        return 1
    print("RED PROOF OK: every mutation fired its assertion, so the gate is looking at what it "
          "claims to look at")
    return 0


if __name__ == "__main__":
    sys.exit(main())
