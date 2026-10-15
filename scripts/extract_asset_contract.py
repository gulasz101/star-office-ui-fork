#!/usr/bin/env python3
"""Extract the art asset contract from the renderer source (SOU-98, AC1).

Generated art arrives as raw 512x512 RGBA and cannot be dropped into the fork
as-is: the renderer slices every sheet with an explicit ``frameWidth`` /
``frameHeight``, ``asset-positions.json`` / ``asset-defaults.json`` key position
and scale by filename, and there is no single native sprite size (128x128,
230x144, 256x256, 160x160, 230x230, 180x251, 65x65, guest characters 128x64).
This script turns that per-asset reality into one machine-checkable JSON so a
pack that misses the frame count can be rejected before it walks a sprite off
the tile.

It reads ``frontend/game.js``, ``frontend/index.html`` and
``frontend/electron-standalone.html`` **read-only** and emits
``assets/asset-contract.json``:

* every ``this.load.spritesheet('<key>', <path>, { frameWidth, frameHeight })``
  call, per source file;
* the path expression resolved - ``getExt('x.png')`` and
  ``(supportsWebP ? '.webp' : '.png')`` variants collapse to one logical asset,
  ``?v={{VERSION_TIMESTAMP}}`` cache-busting is dropped;
* frame sizes given as JS identifiers (``FLOWERS_FRAME_W``) resolved to the last
  literal assignment in the same file and flagged ``dynamic_frame_size``;
* the actual image dimensions on disk (pillow) and the derived frame count
  (columns x rows), with a **hard error** when the sheet size is not an exact
  multiple of the frame size;
* the literal ``generateFrameNumbers('<key>', { start, end })`` ranges, so an
  animation that reads past the end of its sheet is an error too;
* the ``x`` / ``y`` / ``scale`` entries of ``asset-positions.json`` and
  ``asset-defaults.json`` under their filename keys.

``frontend/game.js`` is parsed as required, but no shipped page loads it (both
``index.html`` and ``electron-standalone.html`` carry their own ``preload()``),
so it is marked legacy: its stale frame sizes are reported, and they only become
fatal under ``--strict``. Violations in the live sources are always fatal.

Usage::

    python3 scripts/extract_asset_contract.py                 # (re)generate the contract
    python3 scripts/extract_asset_contract.py --check         # fail if the committed file is stale
    python3 scripts/extract_asset_contract.py --strict        # every violation is fatal
    python3 scripts/extract_asset_contract.py --repo-root DIR --out FILE

Stdlib + pillow only, no network, no image generation. Output is deterministic
(no wall-clock), so ``--check`` is a real gate. Exit 0 = clean, 1 = fatal
violation or stale committed artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import namedtuple

FrameCount = namedtuple("FrameCount", "columns rows total")

SCHEMA_VERSION = 1
GENERATOR = "scripts/extract_asset_contract.py"

# Renderer sources that carry spritesheet declarations. game.js is the legacy
# Phaser entry (no shipped page loads it); the two live pages are self-contained.
SPRITESHEET_SOURCES = (
    "frontend/game.js",
    "frontend/index.html",
    "frontend/electron-standalone.html",
)
LIVE_SOURCES = ("frontend/index.html", "frontend/electron-standalone.html")

PLACEMENT_FILES = ("asset-positions.json", "asset-defaults.json")

STATIC_PREFIX = "/static/"
KNOWN_EXTS = (".webp", ".png")

SPRITESHEET_RE = re.compile(
    r"this\.load\.spritesheet\(\s*['\"](?P<key>[^'\"]+)['\"]\s*,\s*"
    r"(?P<expr>[^,]+),\s*\{\s*frameWidth\s*:\s*(?P<fw>[^,}]+?)\s*,\s*"
    r"frameHeight\s*:\s*(?P<fh>[^,}]+?)\s*\}"
)

ANIM_RE = re.compile(
    r"generateFrameNumbers\(\s*['\"](?P<key>[^'\"]+)['\"]\s*,\s*\{\s*"
    r"start\s*:\s*(?P<start>[^,}]+?)\s*,\s*end\s*:\s*(?P<end>[^,}]+?)\s*\}"
)

_STRING_LITERAL_RE = re.compile(r"['\"]([^'\"]*)['\"]")
_INT_RE = re.compile(r"^\s*\d+\s*$")


class FrameSizeError(Exception):
    """A sheet whose pixel size is not an exact multiple of its frame size."""


def derive_frame_count(width, height, frame_width, frame_height) -> FrameCount:
    """cols x rows for a sheet of ``width`` x ``height`` sliced at the frame size.

    Raises :class:`FrameSizeError` when either axis is not an exact multiple -
    that is the defect that silently drops the last, partial frame and makes a
    walk cycle read garbage.
    """
    if not width or not height:
        raise FrameSizeError("image has no measurable dimensions")
    if not frame_width or not frame_height:
        raise FrameSizeError("frame size is not a positive integer")
    if width % frame_width or height % frame_height:
        raise FrameSizeError(
            f"{width}x{height} is not an exact multiple of frame {frame_width}x{frame_height} "
            f"({width / frame_width:.3f} x {height / frame_height:.3f})"
        )
    columns = width // frame_width
    rows = height // frame_height
    return FrameCount(columns, rows, columns * rows)


def _strip_static(ref: str) -> str:
    path = ref.strip()
    if path.startswith(STATIC_PREFIX):
        path = path[len(STATIC_PREFIX):]
    return path


def logical_asset(ref: str) -> str:
    """Extension- and cache-bust-free name of the asset a path refers to.

    ``/static/star-idle-v5.png?v={{VERSION_TIMESTAMP}}`` -> ``star-idle-v5``;
    ``/static/plants-spritesheet.webp`` -> ``plants-spritesheet``.
    """
    path = _strip_static(ref)
    path = path.split("?", 1)[0].split("#", 1)[0]
    stem, ext = os.path.splitext(path)
    if ext.lower() in KNOWN_EXTS:
        return stem
    return path


def resolve_frame_size(text: str, source_text: str):
    """Frame size literal, or the last literal assigned to a JS identifier."""
    token = text.strip()
    if _INT_RE.match(token):
        return int(token), False
    ident = re.match(r"[A-Za-z_$][\w$]*", token)
    if not ident:
        return None, True
    name = ident.group(0)
    matches = re.findall(rf"\b{re.escape(name)}\s*=\s*(\d+)\b", source_text)
    if matches:
        return int(matches[-1]), True
    return None, True


def parse_ref(expr: str):
    """Resolve a spritesheet path expression to (logical asset, [variant files]).

    Handles the three shapes in the renderer: a plain quoted path with a cache
    buster, a base plus ``getExt('<file>.png')``, and a base plus the
    ``supportsWebP`` ternary.
    """
    literals = _STRING_LITERAL_RE.findall(expr)
    primary = None
    explicit_exts = []
    helper_args = []
    for literal in literals:
        if literal.startswith(STATIC_PREFIX):
            primary = literal
        elif literal.lower() in KNOWN_EXTS:
            explicit_exts.append(literal.lower())
        elif os.path.splitext(literal)[1].lower() in KNOWN_EXTS:
            helper_args.append(literal)
    if primary is None:
        return None, []

    base = _strip_static(primary).split("?", 1)[0]
    stem, ext = os.path.splitext(base)
    if ext.lower() in KNOWN_EXTS:
        name = stem
        variants = [os.path.basename(base)]
        sibling_ext = ".png" if ext.lower() == ".webp" else ".webp"
        variants.append(name + sibling_ext)
    else:
        name = base
        exts = list(explicit_exts)
        if helper_args:
            # getExt('<file>.png') returns .png or .webp depending on browser
            # support and the layout config, so both spellings are possible loads.
            helper_ext = os.path.splitext(helper_args[0])[1].lower()
            if not exts:
                exts = [helper_ext] + [e for e in KNOWN_EXTS if e != helper_ext]
            name = os.path.splitext(_strip_static(helper_args[0]))[0] or name
        exts = exts or list(KNOWN_EXTS)
        variants = [name + e for e in dict.fromkeys(exts)]

    return name, list(dict.fromkeys(variants))


def image_size(path: str):
    """(width, height, mode) measured with pillow, or None when unreadable."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - pillow is in backend/requirements.txt
        raise SystemExit("pillow is required to measure image dimensions: pip install pillow")
    try:
        with Image.open(path) as image:
            return image.size[0], image.size[1], image.mode
    except Exception:
        return None


def _read(repo_root: str, rel: str) -> str:
    path = os.path.join(repo_root, rel)
    if not os.path.exists(path):
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _placement(repo_root: str) -> dict:
    out = {}
    for rel in PLACEMENT_FILES:
        path = os.path.join(repo_root, rel)
        payload = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                try:
                    payload = json.load(handle)
                except ValueError:
                    payload = {}
        out[rel] = payload if isinstance(payload, dict) else {}
    return out


def _count_entry(count) -> dict | None:
    if count is None:
        return None
    return {"columns": count.columns, "rows": count.rows, "total": count.total}


def extract(repo_root: str) -> dict:
    """Parse the renderer and return the contract document for ``repo_root``."""
    assets: dict = {}
    errors: list = []
    sources_meta = []
    source_texts = {}

    for rel in SPRITESHEET_SOURCES:
        text = _read(repo_root, rel)
        source_texts[rel] = text
        found = 0
        for match in SPRITESHEET_RE.finditer(text):
            found += 1
            key = match.group("key")
            expr = match.group("expr").strip()
            frame_width, dynamic_w = resolve_frame_size(match.group("fw"), text)
            frame_height, dynamic_h = resolve_frame_size(match.group("fh"), text)
            dynamic = dynamic_w or dynamic_h
            name, variants = parse_ref(expr)
            live = rel in LIVE_SOURCES
            entry = assets.setdefault(
                key,
                {
                    "key": key,
                    "logical_asset": None,
                    "logical_assets": [],
                    "anims": [],
                    "files": [],
                    "placement": [],
                    "sources": [],
                    "live": False,
                    "dynamic_frame_size": False,
                    "expected_frame_width": None,
                    "expected_frame_height": None,
                    "expected_width": None,
                    "expected_height": None,
                    "expected_source": None,
                    "frame_count": None,
                },
            )
            if rel not in entry["sources"]:
                entry["sources"].append(rel)
            entry["live"] = entry["live"] or live
            entry["dynamic_frame_size"] = entry["dynamic_frame_size"] or dynamic

            for variant in variants or [None]:
                path = os.path.join(STATIC_PREFIX.strip("/"), variant) if variant else None
                disk_rel = os.path.join("frontend", variant) if variant else None
                dims = image_size(os.path.join(repo_root, disk_rel)) if disk_rel and os.path.exists(
                    os.path.join(repo_root, disk_rel)
                ) else None
                file_entry = {
                    "source": rel,
                    "live": live,
                    "ref": re.sub(r"\s+", " ", expr),
                    "logical_asset": name,
                    "variant": variant,
                    "path": disk_rel,
                    "exists": bool(dims),
                    "width": dims[0] if dims else None,
                    "height": dims[1] if dims else None,
                    "mode": dims[2] if dims else None,
                    "frame_width": frame_width,
                    "frame_height": frame_height,
                    "frame_count": None,
                    "errors": [],
                }
                if dims:
                    try:
                        count = derive_frame_count(dims[0], dims[1], frame_width, frame_height)
                        file_entry["frame_count"] = _count_entry(count)
                    except FrameSizeError as exc:
                        problem = {
                            "key": key,
                            "source": rel,
                            "file": disk_rel,
                            "type": "not_an_exact_multiple",
                            "message": str(exc),
                            "fatal": live,
                        }
                        errors.append(problem)
                        file_entry["errors"].append(problem["type"])
                elif variant:
                    file_entry["errors"].append("file_not_found")
                entry["files"].append(file_entry)
            if name and name not in entry["logical_assets"]:
                entry["logical_assets"].append(name)
        sources_meta.append({"file": rel, "live": rel in LIVE_SOURCES, "spritesheets": found})

    # Animations: literal frame ranges per sheet key, so the contract can check
    # that no animation reads past the end of the sheet it is built from.
    for rel, text in source_texts.items():
        for match in ANIM_RE.finditer(text):
            key = match.group("key")
            if key not in assets:
                continue
            start, _ = resolve_frame_size(match.group("start"), text)
            end, _ = resolve_frame_size(match.group("end"), text)
            anim = {"source": rel, "key": key, "start": start, "end": end}
            if anim not in assets[key]["anims"]:
                assets[key]["anims"].append(anim)

    placement = _placement(repo_root)
    placement_by_variant: dict = {}
    for name, payload in placement.items():
        for filename, values in payload.items():
            placement_by_variant.setdefault(filename, []).append(
                {"file": name, "filename": filename, **(values if isinstance(values, dict) else {})}
            )

    conflicts: list = []
    for key, entry in assets.items():
        # Authoritative slice = the live page's own numbers, prefer a file that
        # actually exists on disk.
        ranked = sorted(
            entry["files"],
            key=lambda f: (not f["live"], not f["exists"], f["source"]),
        )
        authoritative = ranked[0] if ranked else None
        if authoritative is not None:
            entry["expected_source"] = authoritative["source"]
            entry["logical_asset"] = authoritative["logical_asset"]
            entry["expected_frame_width"] = authoritative["frame_width"]
            entry["expected_frame_height"] = authoritative["frame_height"]
            if authoritative["exists"]:
                entry["expected_width"] = authoritative["width"]
                entry["expected_height"] = authoritative["height"]
                entry["frame_count"] = authoritative["frame_count"]
        for item in placement_by_variant.get(authoritative["variant"], []) if authoritative else []:
            entry["placement"].append(item)

        sizes = {(f["frame_width"], f["frame_height"]) for f in entry["files"] if f["frame_width"]}
        if len(sizes) > 1:
            seen = set()
            slices = []
            for file_entry in sorted(entry["files"], key=lambda f: f["source"]):
                identity = (
                    file_entry["source"],
                    file_entry["frame_width"],
                    file_entry["frame_height"],
                )
                if not file_entry["frame_width"] or identity in seen:
                    continue
                slices.append(
                    {
                        "source": file_entry["source"],
                        "live": file_entry["live"],
                        "frame_width": file_entry["frame_width"],
                        "frame_height": file_entry["frame_height"],
                    }
                )
                seen.add(identity)
            conflicts.append(
                {
                    "key": key,
                    "kind": "frame_size",
                    "logical_asset": entry["logical_asset"],
                    "sources": slices,
                }
            )
        if len(entry["logical_assets"]) > 1:
            conflicts.append(
                {
                    "key": key,
                    "kind": "logical_asset",
                    "logical_asset": entry["logical_asset"],
                    "logical_assets": sorted(entry["logical_assets"]),
                    "sources": sorted(entry["sources"]),
                }
            )

        # Walk-off-the-tile guard: a literal animation range must stay inside the
        # derived grid.
        count = entry["frame_count"]
        if count:
            for anim in entry["anims"]:
                if anim["end"] is None or anim["end"] < count["total"]:
                    continue
                problem = {
                    "key": key,
                    "source": anim["source"],
                    "file": None,
                    "type": "animation_reads_past_the_sheet",
                    "message": (
                        f"animation uses frames {anim['start']}..{anim['end']} but the sheet "
                        f"only holds {count['total']}"
                    ),
                    "fatal": anim["source"] in LIVE_SOURCES,
                }
                errors.append(problem)

    for entry in assets.values():
        entry["files"].sort(key=lambda f: (f["source"], f["variant"] or ""))
        entry["anims"].sort(key=lambda a: (a["source"], a["key"], str(a["end"])))
        entry["logical_assets"].sort()
        entry["placement"].sort(key=lambda p: (p["file"], p["filename"]))
    errors.sort(key=lambda e: (not e["fatal"], e["source"], e["key"], e["type"]))
    conflicts.sort(key=lambda c: (c["key"], c["kind"]))

    fatal = [e for e in errors if e["fatal"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "sources": sources_meta,
        "summary": {
            "assets": len(assets),
            "spritesheet_declarations": sum(s["spritesheets"] for s in sources_meta),
            "placement_entries": sum(len(v) for v in placement.values()),
            "conflicts": len(conflicts),
            "errors": len(errors),
            "fatal_errors": len(fatal),
            "legacy_only_errors": len([e for e in errors if not e["fatal"]]),
        },
        "assets": assets,
        "placement": placement,
        "conflicts": conflicts,
        "errors": errors,
    }


def render_contract(repo_root: str) -> str:
    """Deterministic JSON text for the contract (no wall-clock anywhere)."""
    return json.dumps(extract(repo_root), indent=2, sort_keys=True) + "\n"


def report(contract: dict, stream=sys.stdout) -> None:
    summary = contract["summary"]
    print(
        f"contract: {summary['assets']} assets, "
        f"{summary['spritesheet_declarations']} spritesheet declarations across "
        f"{len(contract['sources'])} sources, "
        f"{summary['placement_entries']} placement entries",
        file=stream,
    )
    for source in contract["sources"]:
        tag = "live" if source["live"] else "legacy"
        print(f"  {source['file']}: {source['spritesheets']} spritesheets ({tag})", file=stream)
    for error in contract["errors"]:
        tag = "ERROR" if error["fatal"] else "WARN (legacy source)"
        print(
            f"  {tag} {error['type']} {error['key']} [{error['source']}] {error['message']}",
            file=stream,
        )
    for conflict in contract["conflicts"]:
        if conflict["kind"] == "frame_size":
            sizes = ", ".join(
                f"{s['source']} {s['frame_width']}x{s['frame_height']}" for s in conflict["sources"]
            )
            print(f"  CONFLICT {conflict['key']}: {sizes}", file=stream)
        else:
            print(
                f"  CONFLICT {conflict['key']}: sources disagree on the asset file "
                f"({', '.join(conflict['logical_assets'])})",
                file=stream,
            )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--repo-root",
        default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        help="checkout to parse (default: this repo)")
    parser.add_argument(
        "--out",
        default=None,
        help="where to write the contract (default: <repo-root>/assets/asset-contract.json)")
    parser.add_argument(
        "--check", action="store_true",
        help="do not write; fail if the contract on disk differs from this code")
    parser.add_argument(
        "--strict", action="store_true",
        help="treat legacy-source violations as fatal too")
    args = parser.parse_args(argv)

    repo_root = os.path.abspath(args.repo_root)
    out_path = args.out or os.path.join(repo_root, "assets", "asset-contract.json")
    text = render_contract(repo_root)
    contract = json.loads(text)

    if args.check:
        if not os.path.exists(out_path):
            print(f"STALE {out_path}: missing, run scripts/extract_asset_contract.py", file=sys.stderr)
            return 1
        with open(out_path, "r", encoding="utf-8") as handle:
            if handle.read() != text:
                print(f"STALE {out_path}: re-extraction differs, commit the regenerated contract",
                      file=sys.stderr)
                return 1
        print(f"contract is current: {out_path}")
        return 0

    report(contract)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"wrote {out_path}")

    fatal = [e for e in contract["errors"] if e["fatal"]]
    legacy = [e for e in contract["errors"] if not e["fatal"]]
    if fatal:
        print(f"{len(fatal)} fatal violation(s) in live sources", file=sys.stderr)
        return 1
    if legacy and args.strict:
        print(f"{len(legacy)} violation(s) in legacy sources, --strict makes them fatal",
              file=sys.stderr)
        return 1
    if legacy:
        print(f"{len(legacy)} legacy-source violation(s); --strict to fail on them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
