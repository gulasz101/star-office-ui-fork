#!/usr/bin/env python3
"""Prove `check_cats_assets.py` can fail - the mutation battery behind the SOU-277 gate.

A gate's green is worth nothing until it has been shown to go RED on the defect it exists to
catch (SOU-200/216/275 lesson: a battery case can be a NO-OP that inherits the clean copy's PASS,
and a gate whose input path never resolves skips its assertion and still prints a green summary).

So: copy the served + generated trees into a scratch root, apply ONE mutation per case, run the
real gate against the copy, and require the exit code AND the expected check to fail. Cases that
cannot be built report NOT PROVEN and are counted as holes - never as passes. The negative
control is the pristine copy, which must PASS.

Every path in the copy comes from ONE declared root (`--served`/`--generated`), so a case cannot
silently measure a different tree than the one it mutated.

Usage::

    python3 scripts/v3/red_proof_cats.py            # writes into a scratch dir, deletes nothing
    python3 scripts/v3/red_proof_cats.py --keep     # keep the scratch root for inspection
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
PY = sys.executable


def _copy_trees(root: Path):
    served = root / "frontend" / "v3" / "assets" / "cats"
    generated = root / "assets" / "generated" / "v3" / "cats"
    lodge = root / "frontend" / "v3" / "assets" / "lodge"
    served.parent.mkdir(parents=True, exist_ok=True)
    (root / "assets" / "generated" / "v3").mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "frontend" / "v3" / "assets" / "cats", served)
    shutil.copytree(REPO / "assets" / "generated" / "v3" / "cats", generated)
    shutil.copytree(REPO / "frontend" / "v3" / "assets" / "lodge", lodge)
    return served, generated, lodge


def run_gate(served: Path, generated: Path, lodge: Path, script: Path | None = None):
    cmd = [PY, str(script or (HERE / "check_cats_assets.py")), "--served", str(served),
           "--generated", str(generated), "--lodge-served", str(lodge)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


# --------------------------------------------------------------------------------------
# mutations. Each takes the copy's roots and edits files in place.
# (expect: a substring the gate's FAIL line must contain, so the RIGHT check fired)
# --------------------------------------------------------------------------------------
def m_size(m):
    p = m["served"] / "black.png"
    im = Image.open(p)
    im.crop((0, 0, im.size[0] - 1, im.size[1])).save(p)


def m_alpha(m):
    p = m["served"] / "white.png"
    Image.open(p).convert("RGB").save(p)


def m_baseline(m):
    p = m["served"] / "siamese.png"
    im = Image.open(p).convert("RGBA")
    out = Image.new("RGBA", im.size, (0, 0, 0, 0))
    for i in range(8):
        cell = im.crop((i * 32, 0, (i + 1) * 32, 32))
        out.paste(cell, (i * 32, -2 if i == 5 else 0))
    out.save(p)


def m_copy_swap(m):
    shutil.copyfile(m["served"] / "tabby_orange.png", m["served"] / "black.png")


def m_no_manifest(m):
    (m["served"] / "grey.png.manifest.json").unlink()


def m_swap_poses(m):
    p = m["served"] / "calico.png"
    im = Image.open(p).convert("RGBA")
    out = im.copy()
    for i, j in ((4, 5), (5, 4)):
        out.paste(im.crop((j * 32, 0, (j + 1) * 32, 32)), (i * 32, 0))
    out.save(p)


def m_walk_pixel(m):
    p = m["served"] / "tabby_orange.png"
    im = Image.open(p).convert("RGBA")
    px = im.load()
    for y in range(32):
        for x in range(32):
            if px[x, y][3]:
                px[x, y] = (255, 255, 255, 255) if px[x, y] != (255, 255, 255, 255) else (0, 0, 0, 255)
                im.save(p)
                return


def m_freeze_cloud(m):
    p = m["served"] / "fight_cloud.png"
    im = Image.open(p).convert("RGBA")
    im.paste(im.crop((0, 0, 48, 32)), (3 * 48, 0))
    im.save(p)


def m_off_palette(m):
    p = m["served"] / "grey.png"
    im = Image.open(p).convert("RGBA")
    px = im.load()
    for y in range(32):
        for x in range(32):
            if px[x, y][3]:
                px[x, y] = (0, 255, 0, 255)
                im.save(p)
                return


def m_duplicate_pose(m):
    p = m["served"] / "tuxedo.png"
    im = Image.open(p).convert("RGBA")
    im.paste(im.crop((4 * 32, 0, 5 * 32, 32)), (7 * 32, 0))
    im.save(p)


def m_missing_file(m):
    (m["served"] / "siamese.png").rename(m["served"] / "siamese.png.off")


def m_licence_model(m):
    p = m["served"] / "black.png.manifest.json"
    d = json.loads(p.read_text())
    d["model"] = "Qwen/Qwen-Image-2.1"
    p.write_text(json.dumps(d, indent=2))


def m_licence_mode(m):
    p = m["served"] / "grey.png.manifest.json"
    d = json.loads(p.read_text())
    d["poses"]["walk"]["mode"] = "img2img of the upstream Star-Office sheet"
    p.write_text(json.dumps(d, indent=2))


def m_licence_source_missing(m):
    p = m["generated"] / "tabby_orange_sit.keyed.png"
    if not p.exists():
        raise FileNotFoundError(p)
    p.unlink()


def m_drop_manifest_field(m):
    p = m["served"] / "siamese.png.manifest.json"
    d = json.loads(p.read_text())
    d.pop("seed")
    p.write_text(json.dumps(d, indent=2))


def m_ticket_declaration(m):
    """Widen a declared size in the GENERATOR, not in the artifact (SOU-200's class)."""
    src = m["scripts"] / "gen_cats_art.py"
    text = src.read_text()
    old = '"coat_strip_px": (256, 32)'
    if old not in text:
        raise AssertionError("declaration anchor not found")
    src.write_text(text.replace(old, '"coat_strip_px": (260, 32)'))


CASES = [
    ("AC1 size: one column short", m_size, "says RGBA 256x32"),
    ("AC1 mode: RGBA dropped to RGB", m_alpha, "says RGBA 256x32"),
    ("BASELINE: a frame lifted off the anchor row", m_baseline, "must reach row 31"),
    ("COATS: black painted with the orange tabby sheet", m_copy_swap, "signature"),
    ("AC3: a coat with no manifest", m_no_manifest, "no manifest beside it"),
    ("ALIGNMENT: sit/sleep swapped in one coat", m_swap_poses, "different shape from the others"),
    ("COPY: one pixel changed in the shipped walk", m_walk_pixel, "differ from the shipped lodge"),
    ("CLOUD: a frozen (duplicated) cloud frame", m_freeze_cloud, "no change against frame 0"),
    ("PALETTE: an off-palette pixel", m_off_palette, "not lodge palette colours"),
    ("FRAMES: hiss replaced by a copy of sit", m_duplicate_pose, "byte-identical"),
    ("AC1: a coat file missing entirely", m_missing_file, "not in the served tree"),
    ("LICENCE: manifest claims Qwen-Image-2.1", m_licence_model, "is not"),
    ("LICENCE: a pose recorded as upstream img2img", m_licence_mode, "outside the licence rule"),
    ("LICENCE: the reference edit's own output deleted", m_licence_source_missing,
     "is not in"),
    ("AC3: manifest without its seed", m_drop_manifest_field, "missing"),
    ("DECLARATION: the generator's own table widened", m_ticket_declaration, "the generator "
     "declares"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    root = Path(tempfile.mkdtemp(prefix="red-proof-cats-"))
    served, generated, lodge = _copy_trees(root)
    scripts = root / "scripts" / "v3"
    shutil.copytree(HERE, scripts)

    print(f"scratch root: {root}\n")

    # ---- negative control: the pristine copy MUST pass -------------------------------
    code, out = run_gate(served, generated, lodge)
    print("NEGATIVE CONTROL (pristine copy, the gate must PASS):")
    tail = [ln for ln in out.splitlines() if ln.startswith("RESULT:")]
    print(f"  exit={code} {tail[-1] if tail else '(no RESULT line)'}")
    if code != 0:
        print("\nthe gate fails on the PRISTINE tree - the battery proves nothing about any "
              "mutation below.")
        for ln in out.splitlines():
            if ln.strip().startswith("FAIL"):
                print(f"  {ln.strip()}")
        return 2
    print("  control is green - the mutations below must each turn it red\n")

    holes, results = [], []
    for ci, (name, fn, expect) in enumerate(CASES):
        slug = name.split(":")[-1].strip().replace(" ", "_").replace("/", "-")[:40]
        # per-case dir keyed by INDEX: the case names share prefixes ("AC1 ...", "LICENCE ..."),
        # so a name-derived dir collided and crashed the battery mid-run (12 cases in).
        case_root = root / "cases" / f"{ci:02d}-{slug}"
        shutil.copytree(served, case_root / "served")
        shutil.copytree(generated, case_root / "generated")
        shutil.copytree(lodge, case_root / "lodge")
        m = {"served": case_root / "served", "generated": case_root / "generated",
             "lodge": case_root / "lodge", "scripts": scripts}
        # restore the shared scripts copy: the DECLARATION case edits it, and a later case must
        # never inherit that edit (a mutation that leaks into the next case is a silent no-op
        # control - the exact class this battery exists to catch).
        shutil.copyfile(HERE / "gen_cats_art.py", scripts / "gen_cats_art.py")
        try:
            fn(m)
        except Exception as exc:  # a case that cannot be built is NOT a pass
            print(f"NOT PROVEN  {name}\n            could not build the mutation: {exc!r}")
            holes.append(name)
            continue
        code, out = run_gate(m["served"], m["generated"], m["lodge"], scripts /
                             "check_cats_assets.py")
        fails = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("FAIL")]
        hit = [ln for ln in fails if expect in ln]
        if code != 0 and hit:
            results.append((name, True, hit[0]))
            print(f"PROVEN      {name}\n            {hit[0]}")
        elif code != 0 and not hit:
            holes.append(name)
            print(f"WRONG CHECK {name}\n            the gate went red, but not on {expect!r}:")
            for ln in fails[:3]:
                print(f"              {ln}")
        else:
            holes.append(name)
            print(f"HOLE        {name}\n            the gate stayed GREEN (exit {code})")

    proven = sum(1 for _n, ok, _l in results if ok)
    print(f"\n{proven}/{len(CASES)} mutation cases proven, {len(holes)} holes"
          + (f": {holes}" if holes else ""))
    print("RESULT: PASS" if not holes else "RESULT: FAIL (holes in the battery)")
    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    else:
        print(f"kept {root}")
    return 0 if not holes else 1


if __name__ == "__main__":
    sys.exit(main())
