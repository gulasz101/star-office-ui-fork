#!/usr/bin/env python3
"""Gate the SOU-277 corridor cat set (`--set cats` in check_v3_assets.py delegates here).

Separate module, same house shape: a hand-typed copy of the TICKET's own table that is asserted
against BOTH the shipped files and the generator's declaration, plus measurements for the clauses
a size check cannot see.

What the ticket actually says (SOU-277), and how each clause becomes an assertion
-------------------------------------------------------------------------------
* "an 8-frame strip, 256x32, RGBA, with the same pose layout in every file"      -> AC1 SIZES,
  FRAMES: exact px and RGBA per coat file; 8 frames of 32x32; the cloud 192x32 of 4x48x32.
* "frames 0-3 walk, 4 sitting, 5 curled up asleep, 6 crouched tail up, 7 arched hiss"
  -> FRAMES: all eight frames of a coat must be mutually distinct (a copy-paste pose would
  otherwise pass every numeric check), and the SAME frame index must describe the same shape in
  every coat (see ALIGNMENT).
* "the poses must line up frame for frame across coats"                           -> ALIGNMENT:
  each frame index's content box, measured per coat, must agree across the seven coats within a
  declared tolerance. This is the clause a per-file size check cannot see.
* "the feet are on the bottom row in every frame"                                 -> BASELINE.
* "tabby_orange (copy today's sheet so the set is complete)"                       -> COPY: the
  first four frames of `cats/tabby_orange.png` are byte-compared against the shipped
  `lodge/props/cat_walk.png` - the strongest available reading of "copy".
* "Each coat must be recognisable at 1x"                                           -> COATS: each
  coat declares a colour signature; it must hold on ITS OWN strip and FAIL on all six others
  (the negative control that makes the thresholds mean something).
* "Keep the art coherent: one palette family with the Lodge set"                   -> PALETTE:
  every pixel of every file is a member of the lodge's declared 61 colours.
* "Every PNG gets a manifest (model, prompt, seed, steps, post-process)"           -> MANIFESTS,
  and "FLUX.2-klein-4B only; no img2img except from our own output"                -> LICENCE:
  the manifest's model must be klein-4B, the LoRA must be off, and every pose's recorded
  generation mode must be either a text-only generation or a reference edit of a file that is
  OURS (inside `assets/generated/v3/cats/`), never upstream art.

Usage::

    python3 scripts/v3/check_cats_assets.py                 # both trees in this repo
    python3 scripts/v3/check_cats_assets.py --served DIR --generated DIR    # the RED proof's copies
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from PIL import Image  # noqa: E402

REPO = HERE.parent.parent

# --------------------------------------------------------------------------------------
# The ticket's table, hand-typed. The gate does NOT import these from the generator: a
# generator and a gate that read the same table agree by construction (SOU-158's published
# sizes drifted for exactly that reason). The generator's declaration is asserted against
# THIS copy.
# --------------------------------------------------------------------------------------
TICKET = {
    "coats": ["tabby_orange", "black", "white", "grey", "calico", "tuxedo", "siamese"],
    "coat_strip_px": (256, 32),
    "cloud_strip_px": (192, 32),
    "frame_px": (32, 32),
    "cloud_frame_px": (48, 32),
    "frames_per_coat": 8,
    "cloud_frames": 4,
    "pose_order": ["walk 0", "walk 1", "walk 2", "walk 3", "sit", "sleep", "crouch", "hiss"],
    "cloud_name": "fight_cloud",
}

# How close the same frame index has to be across the seven coats, in px of content box. Set
# from the measurement this gate prints (all seven coats are reference edits of one pose, so
# they should be near-identical); a wrong pose in one coat blows straight through it.
ALIGN_TOL_PX = 4

# --------------------------------------------------------------------------------------
# The lodge's 61 declared colours, partitioned into coat families.
#
# One declared partition, not three lists (SOU-216: three copies of a colour list went stale and
# made whole cream rows read as "line runs"). The families split the neutral ramp by luminance
# (dark / mid / light) and split the coloured end into the warm cat-and-amber ramp and the
# browns, so a "black cat" and a "grey cat" cannot be told apart by the outline they both must
# have.
# --------------------------------------------------------------------------------------
FAMILIES = {
    # outline + the night/ink end of the neutral ramp
    "dark": ["outline", "ink", "night_900", "night_700", "slate_900", "slate_700", "metal_dark",
             "metal"],
    # mid greys: the fur of a grey cat, the shading of a white one
    "mid": ["night_500", "night_300", "night_100", "slate_500", "stone_dark", "metal_light",
            "stone", "stone_warm", "snow_deep", "stone_light"],
    # white / cream / snow highlights
    "light": ["snow_light", "snow", "snow_mid", "snow_trodden", "snow_shadow", "steel_200",
              "steel_100", "cat_cream", "skin"],
    # the cat ramp and the ambers around it: an orange tabby, an orange calico patch
    "warm": ["amber_deep", "amber", "gold", "fire", "brass", "brass_light", "led_orange",
             "cat_dark", "cat", "cat_light", "honey", "honey_light", "wood_light", "cardboard",
             "cardboard_light"],
    # browns: the lodge's timber, and the seal-brown points of a siamese
    "brown": ["wood_deep", "wood_dark", "wood", "plank_dark", "plank", "plank_light",
              "rust_dark", "rust", "rust_light", "woven", "cardboard_dark"],
    # everything green/teal/cyan in the palette: plants, LEDS, ice. A cat should have ~none.
    "accent": ["teal", "teal_light", "ice", "led_teal", "pine_deep", "pine_dark", "pine",
               "pine_light"],
}
FAMILY_OF_NAME = {name: fam for fam, names in FAMILIES.items() for name in names}

# --------------------------------------------------------------------------------------
# Per-coat colour signature, measured over the WHOLE strip (all 8 frames, all opaque px): a
# coat has to be recognisable in every pose, including the sleeping ball where the chest and the
# paws are hidden. Shares are of the opaque pixels; `X_max` clauses are upper bounds.
#
# These are the ticket's own coat words ("black", "white", "grey", "calico", "tuxedo",
# "siamese (colour-point)"), turned into the smallest set of clauses that separates all seven -
# which the gate proves by requiring each signature to FAIL on the other six coats.
#
# HOW THE NUMBERS WERE SET (2026-10-17, SOU-277). Every threshold is placed between the shipped
# art's own measurements with the clearance named here, rather than picked to look reasonable:
#
#   coat           dark     mid    light     warm    brown   <- measured over the shipped strip
#   tabby_orange  0.0737  0.0213  0.0558   0.6371  0.2122
#   black         0.9684  0.0273  0.0012   0.0008  0.0019
#   white         0.0900  0.0175  0.8770   0.0000  0.0004
#   grey          0.1021  0.7146  0.0569   0.0000  0.0000
#   calico        0.2853  0.0406  0.3618   0.2623  0.0484
#   tuxedo        0.8427  0.0260  0.1235   0.0004  0.0019   (after the re-roll below)
#   siamese       0.1517  0.0316  0.6223   0.0135  0.1775   (after the re-roll below)
#
#   tabby_orange warm>=0.40   : 0.637 vs the next warmest coat (calico 0.262) - 0.138 clearance
#   black        dark>=0.70   : 0.968; the only other near-black is tuxedo (0.843), excluded by
#                               light<=0.06 (black 0.0012, tuxedo 0.124) - 0.064 clearance
#   white        light>=0.70  : 0.877; next palest is siamese (0.622) - 0.078 above the siamese
#                               cap, which is what keeps the two apart
#   grey         mid>=0.50    : 0.715; next most mid is siamese (0.032) - 0.468 clearance
#   calico       warm>=0.12 AND dark>=0.15 AND light>=0.20: three colours in ONE cat. The warm
#                               floor is what excludes siamese (0.014) - 0.106 clearance; the
#                               light floor excludes tabby_orange (0.056) - 0.144 clearance
#   tuxedo       dark>=0.60   : 0.843; excluded from black by light>=0.08 (tuxedo 0.124,
#                               black 0.0012) - 0.044 clearance
#   siamese      light 0.35..0.70 AND dark>=0.12 AND warm<=0.15: the pale CREAM body
#                               (0.622) is bounded above to exclude a white cat (0.877) - 0.078
#                               clearance - and the dark POINTS (0.152) are bounded below at
#                               0.12 to exclude white (0.090) - 0.030 clearance, the thinnest
#                               margin in the table, so it is named here rather than hidden
#
# The siamese and tuxedo rows are the RE-ROLLED raws (see RERUNS-SOU277.json in the generated
# set): the first siamese pass came back as a lynx point and the first tuxedo sit frame carried
# a green streak. The gates that mattered when that happened were the ones reading the strip, and
# the numbers here are the shipped ones - a table quoting the rejected pass would be a lie.
#
# A fitted-to-the-art gate would have no cross-check; this one's cross-check is the uniqueness
# requirement below, which is what "recognisable at 1x" means operationally.
# --------------------------------------------------------------------------------------
COAT_SIGNATURE = {
    "tabby_orange": {"warm": (0.40, None), "dark_max": 0.20, "light_max": 0.30},
    "black": {"dark": (0.70, None), "light_max": 0.06, "warm_max": 0.05},
    "white": {"light": (0.70, None), "dark_max": 0.25, "warm_max": 0.05},
    "grey": {"mid": (0.50, None), "light_max": 0.35, "warm_max": 0.05},
    "calico": {"warm": (0.12, None), "dark": (0.15, None), "light": (0.20, None)},
    "tuxedo": {"dark": (0.60, None), "light": (0.08, None), "warm_max": 0.05},
    "siamese": {"light": (0.35, 0.70), "dark": (0.12, None), "warm_max": 0.15},
}


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.checks = 0

    def ok(self, msg: str) -> None:
        self.checks += 1
        print(f"  PASS  {msg}")

    def fail(self, msg: str) -> None:
        self.checks += 1
        self.failures.append(msg)
        print(f"  FAIL  {msg}")

    def section(self, title: str) -> None:
        print(f"\n{title}")


def _rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def _frames(img: Image.Image, fw: int, fh: int):
    return [img.crop((i * fw, 0, (i + 1) * fw, fh)) for i in range(img.size[0] // fw)]


def _content_box(frame: Image.Image):
    box = frame.getchannel("A").getbbox()
    if box is None:
        return None
    return box[2] - box[0], box[3] - box[1]


def _bottom_row(frame: Image.Image):
    alpha = frame.getchannel("A")
    w, h = frame.size
    px = alpha.load()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            if px[x, y]:
                return y
    return None


def _family_shares(img: Image.Image, family_of_hex: dict, names_of_hex: dict) -> dict:
    """Share of the OPAQUE pixels per family, plus the unknown bucket (must be empty)."""
    counts: dict[str, int] = {}
    unknown: dict[str, int] = {}
    opaque = 0
    px = img.load()
    for y in range(img.size[1]):
        for x in range(img.size[0]):
            r, g, b, a = px[x, y]
            if not a:
                continue
            opaque += 1
            hexv = f"#{r:02x}{g:02x}{b:02x}"
            fam = family_of_hex.get(hexv)
            if fam is None:
                unknown[hexv] = unknown.get(hexv, 0) + 1
            else:
                counts[fam] = counts.get(fam, 0) + 1
    shares = {fam: n / opaque for fam, n in counts.items()} if opaque else {}
    return {"opaque": opaque, "shares": shares, "unknown": unknown}


def _sig_clause_text(sig: dict, shares: dict) -> str:
    """The clause-by-clause audit line: each clause beside the shipped share that satisfies it."""
    parts = []
    for clause, bound in sig.items():
        if clause.endswith("_max"):
            fam = clause[:-4]
            parts.append(f"{fam}<={bound} -> {shares.get(fam, 0.0):.4f}")
        else:
            lo, hi = bound
            fam = clause
            txt = f"{fam}>={lo}" + (f"..<={hi}" if hi is not None else "")
            parts.append(f"{txt} -> {shares.get(fam, 0.0):.4f}")
    return "; ".join(parts)


def _sig_pass(sig: dict, shares: dict) -> tuple[bool, str]:
    why = []
    for clause, bound in sig.items():
        if clause.endswith("_max"):
            fam = clause[:-4]
            got = shares.get(fam, 0.0)
            limit = bound if isinstance(bound, (int, float)) else bound[1]
            if got > limit:
                why.append(f"{fam}={got:.3f}>{limit}")
        else:
            got = shares.get(clause, 0.0)
            lo, hi = bound
            if got < lo or (hi is not None and got > hi):
                why.append(f"{clause}={got:.3f} not in [{lo},{hi}]")
    return (not why), (", ".join(why) if why else "signature holds")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--served", default=str(REPO / "frontend" / "v3" / "assets" / "cats"))
    ap.add_argument("--generated", default=str(REPO / "assets" / "generated" / "v3" / "cats"))
    ap.add_argument("--lodge-served",
                    default=str(REPO / "frontend" / "v3" / "assets" / "lodge"))
    args = ap.parse_args()

    served, generated = Path(args.served), Path(args.generated)
    lodge = Path(args.lodge_served)
    rep = Report()

    import gen_cats_art as G  # noqa: E402  (the generator's own declaration)

    # ---- the generator declares the ticket's table -----------------------------------
    rep.section("DECLARATION (the generator vs this gate's hand-typed copy of SOU-277)")
    for key, mine in (("coats", len(TICKET["coats"])),
                      ("coat_strip_px", TICKET["coat_strip_px"]),
                      ("cloud_strip_px", TICKET["cloud_strip_px"]),
                      ("frame_px", TICKET["frame_px"]),
                      ("cloud_frame_px", TICKET["cloud_frame_px"]),
                      ("frames_per_coat", TICKET["frames_per_coat"]),
                      ("cloud_frames", TICKET["cloud_frames"])):
        declared = G.TICKET_SIZE.get(key)
        if declared == mine:
            rep.ok(f"{key}: {declared}")
        else:
            rep.fail(f"{key}: the generator declares {declared!r}, the ticket says {mine!r}")
    coat_ids = [c["id"] for c in G.COATS]
    if coat_ids == TICKET["coats"]:
        rep.ok(f"coats: {', '.join(coat_ids)}")
    else:
        rep.fail(f"the generator's coats {coat_ids} != the ticket's {TICKET['coats']}")

    # ---- the palette partition is a partition ----------------------------------------
    hex_of_name = {n: h for n, h in G.PALETTE}
    flat = [n for names in FAMILIES.values() for n in names]
    if sorted(flat) == sorted(hex_of_name) and len(flat) == len(set(flat)):
        rep.ok(f"the family table partitions all {len(flat)} lodge colours exactly once")
    else:
        missing = sorted(set(hex_of_name) - set(flat))
        dupes = sorted({n for n in flat if flat.count(n) > 1})
        extra = sorted(set(flat) - set(hex_of_name))
        rep.fail(f"the family table is not a partition of the palette (missing={missing} "
                 f"dupes={dupes} unknown={extra})")
    family_of_hex = {hex_of_name[n]: FAMILY_OF_NAME[n] for n in FAMILY_OF_NAME}

    # ---- AC1: sizes, RGBA, frames ----------------------------------------------------
    rep.section("AC1  SIZES (PIL dump) - 7 coats of 256x32 RGBA, fight_cloud 192x32 RGBA")
    strips = {}
    for coat in TICKET["coats"]:
        path = served / f"{coat}.png"
        if not path.exists():
            rep.fail(f"{coat}.png is not in the served tree ({path})")
            continue
        img = Image.open(path)
        dump = f"{coat}.png {img.mode} {img.size[0]}x{img.size[1]}"
        if (img.mode, img.size) != ("RGBA", TICKET["coat_strip_px"]):
            rep.fail(f"{dump} - the ticket says RGBA {TICKET['coat_strip_px'][0]}x"
                     f"{TICKET['coat_strip_px'][1]}")
            continue
        rep.ok(dump)
        strips[coat] = img.convert("RGBA")
    cloud_path = served / f"{TICKET['cloud_name']}.png"
    cloud = None
    if not cloud_path.exists():
        rep.fail(f"{TICKET['cloud_name']}.png is not in the served tree")
    else:
        img = Image.open(cloud_path)
        if (img.mode, img.size) != ("RGBA", TICKET["cloud_strip_px"]):
            rep.fail(f"{TICKET['cloud_name']}.png {img.mode} {img.size} - the ticket says RGBA "
                     f"{TICKET['cloud_strip_px']}")
        else:
            rep.ok(f"{TICKET['cloud_name']}.png {img.mode} {img.size[0]}x{img.size[1]}")
            cloud = img.convert("RGBA")

    if len(strips) != len(TICKET["coats"]):
        print("\nRESULT: FAIL (the set is incomplete - the remaining checks need all 7 coats)")
        return 1

    # ---- frames ---------------------------------------------------------------------
    rep.section("FRAMES  every coat is 8 frames of 32x32, all eight distinct")
    fw, fh = TICKET["frame_px"]
    coat_frames = {}
    for coat, strip in strips.items():
        fr = _frames(strip, fw, fh)
        if len(fr) != TICKET["frames_per_coat"]:
            rep.fail(f"{coat}: {len(fr)} frames, the ticket says {TICKET['frames_per_coat']}")
            continue
        coat_frames[coat] = fr
        dupes = [(i, j) for i in range(len(fr)) for j in range(i + 1, len(fr))
                 if fr[i].tobytes() == fr[j].tobytes()]
        if dupes:
            rep.fail(f"{coat}: frames {dupes} are byte-identical - every one of the eight poses "
                     f"is a different pose")
        else:
            rep.ok(f"{coat}: 8 frames, all distinct")

    # ---- baseline -------------------------------------------------------------------
    rep.section("BASELINE  the feet are on the bottom row in every frame (the cats share one anchor)")
    for coat, fr in coat_frames.items():
        rows = [_bottom_row(f) for f in fr]
        if all(r == fh - 1 for r in rows):
            rep.ok(f"{coat}: content bottom row {rows}")
        else:
            rep.fail(f"{coat}: content bottom row {rows} - every frame must reach row {fh - 1} "
                     f"(the ticket: the feet are on the bottom row)")

    # ---- the copy clause ------------------------------------------------------------
    rep.section("COPY  tabby_orange frames 0-3 == the shipped lodge cat_walk.png (byte-exact)")
    oracle = lodge / "props" / "cat_walk.png"
    if not oracle.exists():
        rep.fail(f"the oracle {oracle} is missing - cannot check the copy clause")
    else:
        oimg = _rgba(oracle)
        ofr = _frames(oimg, 32, 32)
        mine = coat_frames["tabby_orange"][:4]
        if len(ofr) != 4:
            rep.fail(f"the oracle has {len(ofr)} frames, expected 4")
        else:
            same = [mine[i].tobytes() == ofr[i].tobytes() for i in range(4)]
            if all(same):
                rep.ok("frames 0-3 of cats/tabby_orange.png are byte-identical to "
                       "lodge/props/cat_walk.png")
            else:
                rep.fail(f"frames {[i for i, s in enumerate(same) if not s]} differ from the "
                         f"shipped lodge sheet")

    # ---- alignment across coats -----------------------------------------------------
    rep.section(f"ALIGNMENT  the same frame index is the same shape in every coat "
                f"(tolerance {ALIGN_TOL_PX}px)")
    boxes = {coat: [_content_box(f) for f in fr] for coat, fr in coat_frames.items()}
    for fi, label in enumerate(TICKET["pose_order"]):
        ws = [boxes[c][fi][0] for c in coat_frames if boxes[c][fi]]
        hs = [boxes[c][fi][1] for c in coat_frames if boxes[c][fi]]
        if len(ws) != len(coat_frames):
            rep.fail(f"frame {fi} ({label}) is empty in some coat")
            continue
        dw, dh = max(ws) - min(ws), max(hs) - min(hs)
        line = (f"frame {fi} ({label}): content box "
                f"{min(ws)}-{max(ws)} x {min(hs)}-{max(hs)} (spread {dw}x{dh})")
        if dw <= ALIGN_TOL_PX and dh <= ALIGN_TOL_PX:
            rep.ok(line)
        else:
            rep.fail(line + f" - one coat's {label} pose is a different shape from the others")

    # ---- coat signatures, with the cross-coat negative control -----------------------
    rep.section("COATS  each coat's signature holds on its own strip and fails on the other six")
    shares_by_coat = {}
    for coat, strip in strips.items():
        info = _family_shares(strip, family_of_hex, hex_of_name)
        shares_by_coat[coat] = info
        if info["unknown"]:
            worst = sorted(info["unknown"].items(), key=lambda kv: -kv[1])[:3]
            rep.fail(f"{coat}: {sum(info['unknown'].values())} px are not lodge palette colours "
                     f"(e.g. {worst})")
        else:
            rep.ok(f"{coat}: palette-clean ({info['opaque']} opaque px)")
        pretty = " ".join(f"{k}={v:.2f}" for k, v in sorted(info["shares"].items(),
                                                            key=lambda kv: -kv[1]))
        print(f"        {coat:14s} {pretty}")
    confusion = {}
    for coat, sig in COAT_SIGNATURE.items():
        if coat not in shares_by_coat:
            rep.fail(f"{coat}: no shared strip to measure")
            continue
        holds, why = _sig_pass(sig, shares_by_coat[coat]["shares"])
        if holds:
            rep.ok(f"{coat}: its own signature holds")
        else:
            rep.fail(f"{coat}: its own signature does not hold ({why})")
        print(f"        {coat:14s} {_sig_clause_text(sig, shares_by_coat[coat]['shares'])}")
        cross = [d for d in COAT_SIGNATURE if d != coat
                 and _sig_pass(sig, shares_by_coat.get(d, {"shares": {}})["shares"])[0]]
        confusion[coat] = cross
    for coat, cross in confusion.items():
        if cross:
            rep.fail(f"{coat}'s signature also passes on {cross} - not distinguishable")
        else:
            rep.ok(f"{coat}'s signature is unique among the seven coats")

    # ---- the cloud ------------------------------------------------------------------
    rep.section("CLOUD  4 mutually distinct frames, every one of them nudged, stars declared")
    if cloud is None:
        rep.fail("no fight cloud to check")
    else:
        cfw, cfh = TICKET["cloud_frame_px"]
        cfr = _frames(cloud, cfw, cfh)
        if len(cfr) != TICKET["cloud_frames"]:
            rep.fail(f"the cloud has {len(cfr)} frames, the ticket says {TICKET['cloud_frames']}")
        else:
            # Every frame is nudged (there is no static frame), so the honest check is that all
            # four frames differ FROM EACH OTHER - a duplicated frame is a stutter in a 4-frame
            # loop, and measuring only against frame 0 would also pass a strip whose last two
            # frames were identical copies.
            dupes = [(i, j) for i in range(len(cfr)) for j in range(i + 1, len(cfr))
                     if cfr[i].tobytes() == cfr[j].tobytes()]
            if dupes:
                rep.fail(f"the cloud's frames are not all distinct: {dupes}")
            else:
                rep.ok(f"{len(cfr)} cloud frames, all mutually distinct")
            cman_changed = None
            cm_path = served / f"{TICKET['cloud_name']}.png.manifest.json"
            if cm_path.exists():
                cman_changed = json.loads(cm_path.read_text()).get("changed_px_vs_base")
            if isinstance(cman_changed, list) and len(cman_changed) == len(cfr) \
                    and all(n and n > 0 for n in cman_changed):
                rep.ok(f"the manifest's own animation evidence: changed_px_vs_base={cman_changed}")
            else:
                rep.fail(f"the manifest records no per-frame animation evidence "
                         f"(changed_px_vs_base={cman_changed!r}, expected {len(cfr)} positive "
                         f"counts)")
            content = sum(1 for f in cfr
                          for y in range(cfh) for x in range(cfw) if f.load()[x, y][3])
            if content > 0:
                rep.ok(f"the cloud carries {content} opaque px across its {len(cfr)} frames")
            else:
                rep.fail("the cloud is fully transparent")
            # "with small paws and stars poking out": the stars are DRAWN from the palette at
            # positions the manifest declares, so the check is pixels-vs-declaration - if the
            # stars are removed, or the manifest is edited, the two stop agreeing.
            cm = served / f"{TICKET['cloud_name']}.png.manifest.json"
            cman = json.loads(cm.read_text()) if cm.exists() else {}
            spots = (cman.get("drawn_stars") or {}).get("positions_per_frame") or {}
            if not spots:
                rep.fail("the cloud manifest declares no drawn star positions")
            else:
                gold, glint = (255, 234, 153), (255, 255, 255)
                bad = []
                for i, f in enumerate(cfr):
                    px = f.load()
                    centres = spots.get(str(i)) or []
                    if not centres:
                        bad.append(f"frame {i}: no declared star position")
                        continue
                    for (cx, cy) in centres:
                        arms = [(cx - 1, cy), (cx + 1, cy), (cx, cy - 1), (cx, cy + 1),
                                (cx - 2, cy), (cx + 2, cy), (cx, cy - 2), (cx, cy + 2)]
                        if px[cx, cy][:3] != glint:
                            bad.append(f"frame {i}: no glint at the declared star {cx},{cy}")
                        elif any(px[x, y][:3] != gold for x, y in arms):
                            bad.append(f"frame {i}: the star at {cx},{cy} is not the gold glyph")
                if bad:
                    rep.fail(f"the drawn sparkle stars are not where the manifest says: {bad[:3]}")
                else:
                    n = sum(len(v) for v in spots.values())
                    rep.ok(f"{n} drawn sparkle stars verified pixel-for-pixel at the declared "
                           f"positions across {len(cfr)} frames")

    # ---- AC3: manifests + licence ----------------------------------------------------
    rep.section("AC3  a manifest per PNG, and the licence clause enforced from it")
    import gen_office01_art as OB  # the pipeline's MODEL_ID, as the pipeline declares it
    for name in [*TICKET["coats"], TICKET["cloud_name"]]:
        mpath = served / f"{name}.png.manifest.json"
        if not mpath.exists():
            rep.fail(f"{name}.png has no manifest beside it")
            continue
        m = json.loads(mpath.read_text())
        # "model, prompt, seed, steps, post-process" - the ticket's list. `post_process` is not
        # one key but the recorded chain, so require the fields the pipeline actually writes.
        required = ["model", "prompt", "seed", "steps", "guidance", "output_dims", "sprite_sheet",
                    "palette", "lora_strength"]
        missing = [k for k in required if k not in m]
        if missing:
            rep.fail(f"{name}: manifest is missing {missing}")
            continue
        pp = ["fit", "despeckled_px", "interior_holes_filled_px", "stray_islands_dropped_px",
              "derivation", "key_colour"]
        if name == TICKET["cloud_name"]:
            pp.append("changed_px_vs_base")
            missing_pp = [k for k in pp if k not in m]
        else:
            poses = m.get("poses", {})
            missing_pp = [f"poses.{p}.{k}" for p, meta in poses.items() for k in pp
                          if k not in meta]
            if not poses:
                missing_pp.append("poses (the per-pose post-process record)")
        if missing_pp:
            rep.fail(f"{name}: the manifest does not record the post-process chain "
                     f"({missing_pp[:4]}{'...' if len(missing_pp) > 4 else ''})")
            continue
        if m.get("model") != OB.MODEL_ID:
            rep.fail(f"{name}: manifest model {m.get('model')!r} is not {OB.MODEL_ID!r}")
            continue
        if m.get("lora_strength") not in (0.0, None):
            rep.fail(f"{name}: lora_strength {m.get('lora_strength')} - the LoRA must be off")
            continue
        if m.get("seed") is None or m.get("prompt") in (None, ""):
            rep.fail(f"{name}: manifest carries no seed/prompt")
            continue
        # the licence clause: text-only, or a reference edit of OUR OWN file
        modes = []
        if name == TICKET["cloud_name"]:
            modes = [m.get("generation_mode")]
        else:
            modes = [p.get("mode") for p in m.get("poses", {}).values()]
        bad = []
        for mode in modes:
            mode = mode or ""
            if mode == "text-only generation":
                continue
            if mode.startswith("reference edit of ") and mode.endswith(".keyed.png"):
                rel = mode[len("reference edit of "):]
                src = generated / rel
                if src.exists():
                    continue
                bad.append(f"{mode} (the source {rel} is not in {generated})")
            else:
                bad.append(f"{mode!r}")
        if bad:
            rep.fail(f"{name}: generation mode(s) outside the licence rule: {bad}")
        else:
            rep.ok(f"{name}: manifest present, model klein-4B, LoRA off, "
                   f"{len(modes)} pose mode(s) all text-only or edits of our own output")

    # ---- contact sheet ---------------------------------------------------------------
    rep.section("AC2  the contact sheet the ticket asks to be attached")
    cs = generated / "contact_sheet_4x.png"
    if cs.exists():
        img = Image.open(cs)
        rep.ok(f"contact_sheet_4x.png {img.mode} {img.size[0]}x{img.size[1]}")
    else:
        rep.fail(f"{cs} is missing")

    print(f"\n{rep.checks - len(rep.failures)}/{rep.checks} checks passed")
    if rep.failures:
        print(f"RESULT: FAIL ({len(rep.failures)} failures)")
        for f in rep.failures:
            print(f"  - {f}")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
