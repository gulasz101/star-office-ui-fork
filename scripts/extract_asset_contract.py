#!/usr/bin/env python3
"""Extract the art asset contract from the renderer source (SOU-98 AC1, SOU-104).

Generated art arrives as raw 512x512 RGBA and cannot be dropped into the fork
as-is: the renderer slices every sheet with an explicit ``frameWidth`` /
``frameHeight``, ``asset-positions.json`` / ``asset-defaults.json`` key position
and scale by filename, and there is no single native sprite size (128x128,
230x144, 256x256, 160x160, 230x230, 180x251, 65x65, guest characters 128x64).
This script turns that per-asset reality into one machine-checkable JSON so a
pack that misses the frame count can be rejected before it walks a sprite off
the tile.

It reads the renderer sources **read-only** and emits
``assets/asset-contract.json``:

* every ``this.load.spritesheet('<key>', <path>, { frameWidth, frameHeight })``
  **and** every ``this.load.image('<key>', <path>)`` call, per source file -
  single images are recorded with their real dimensions and no frame grid, which
  is what backgrounds (``office_bg_small``), desks and shadows need (SOU-104 AC5);
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
  ``asset-defaults.json`` under their filename keys;
* the ``renderer`` section: which file is live on which surface, and which of the
  sources is the authority (SOU-104 AC1).

Authority, because the same assets used to be declared twice with different
answers. ``frontend/index.html`` is the authored page - Flask serves it at ``/``.
``frontend/electron-standalone.html`` is a *derived snapshot* of it: Electron
loads ``/electron-standalone``, and the file is produced by copying the authored
page when it is missing, so its declarations have to agree. ``frontend/game.js``
is a legacy copy that no shipped page loads (mainline says the same: "index.html
loads NO game.js - frontend/game.js is a stale copy and editing it does nothing"),
so it is parsed and reported but is never an authority.

That splits the old single ``conflicts`` list in two (SOU-104 AC3):

* ``conflicts`` - a disagreement that can reach a running page: the authored page
  against its own snapshot, a load kind that changed, an authoritative slicing
  that is not an exact multiple of the file, or an authoritative animation
  reading past its sheet. **Always fatal**, exit 1.
* ``drift`` - a non-authoritative (legacy) source disagreeing with the resolved
  authority. Reported with the value that won, kept a warning so dead code can
  never block a build, and fatal under ``--strict``.

Usage::

    python3 scripts/extract_asset_contract.py                 # (re)generate the contract
    python3 scripts/extract_asset_contract.py --check         # fail if the committed file is stale
    python3 scripts/extract_asset_contract.py --strict        # legacy drift is fatal too
    python3 scripts/extract_asset_contract.py --repo-root DIR --out FILE

Stdlib + pillow only, no network, no image generation. Output is deterministic
(no wall-clock), so ``--check`` is a real gate. Exit 0 = clean, 1 = conflict,
fatal violation or stale committed artifact.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import namedtuple

FrameCount = namedtuple("FrameCount", "columns rows total")

SCHEMA_VERSION = 2
GENERATOR = "scripts/extract_asset_contract.py"

AUTHORED_SOURCE = "frontend/index.html"
SNAPSHOT_SOURCE = "frontend/electron-standalone.html"
LEGACY_SOURCE = "frontend/game.js"

# Renderer sources that carry asset declarations. The two HTML files are the ones
# a user can reach; game.js is the stale copy no shipped page loads.
RENDER_SOURCES = (AUTHORED_SOURCE, SNAPSHOT_SOURCE, LEGACY_SOURCE)
SOURCE_ORDER = {
    AUTHORED_SOURCE: "authored",
    SNAPSHOT_SOURCE: "derived_snapshot",
    LEGACY_SOURCE: "legacy",
}
ROLE_RANK = {"authored": 0, "derived_snapshot": 1, "legacy": 2, "unknown": 3}

# Declarations that can reach a running page. Only these can *conflict*.
AUTHORITATIVE_SOURCES = (AUTHORED_SOURCE, SNAPSHOT_SOURCE)

RENDERER_DECISION = (
    "The live renderer is the Phaser code inlined in frontend/index.html: Flask "
    "serves that file for the browser at '/', Electron loads '/electron-standalone', "
    "which is frontend/electron-standalone.html - a snapshot copied from the same "
    "authored page. frontend/game.js is loaded by no shipped page, so it is a legacy "
    "reference and never an authority. For every asset the contract therefore records "
    "one geometry: the authored page's slicing, kept honest by the file dimensions on "
    "disk, which must divide it exactly."
)

RENDERER_SURFACES = (
    {
        "surface": "browser",
        "route": "/",
        "file": AUTHORED_SOURCE,
        "role": "authored",
        "served_by": "backend/app.py index() reads FRONTEND_INDEX_FILE",
        "note": "the only file a person edits for the web view",
    },
    {
        "surface": "electron",
        "route": "/electron-standalone",
        "file": SNAPSHOT_SOURCE,
        "role": "derived_snapshot",
        "served_by": "backend/app.py electron_standalone_page(); electron-shell/main.js "
                     "loads that URL into its windows",
        "note": "ensure_electron_standalone_snapshot() copies frontend/index.html here "
                "when the file is missing, so its asset declarations must match the "
                "authored page - a difference is a conflict",
    },
    {
        "surface": "none",
        "route": None,
        "file": LEGACY_SOURCE,
        "role": "legacy",
        "served_by": None,
        "note": "no shipped page references this file; mainline records the same "
                "(SOU-88 follow-up commit b9fe446: 'index.html loads NO game.js - "
                "frontend/game.js is a stale copy and editing it does nothing'). "
                "Parsed for drift only, never authoritative",
    },
)

PLACEMENT_FILES = ("asset-positions.json", "asset-defaults.json")

STATIC_PREFIX = "/static/"
KNOWN_EXTS = (".webp", ".png")

SPRITESHEET_RE = re.compile(
    r"this\.load\.spritesheet\(\s*['\"](?P<key>[^'\"]+)['\"]\s*,\s*"
    r"(?P<expr>[^,]+),\s*\{\s*frameWidth\s*:\s*(?P<fw>[^,}]+?)\s*,\s*"
    r"frameHeight\s*:\s*(?P<fh>[^,}]+?)\s*\}"
)

# this.load.image('<key>', <path>) - a single image, no frame grid. The path
# expression may be a concatenation (getExt / supportsWebP / cache buster), and
# those contain no semicolons, so the argument list ends at the first ');'.
IMAGE_RE = re.compile(
    r"this\.load\.image\(\s*['\"](?P<key>[^'\"]+)['\"]\s*,\s*(?P<expr>[^;]*?)\s*\)\s*;"
)

# Every load call, literal key or not: the difference is how many assets the
# page swaps in at runtime, which the contract must not pretend to have seen.
LOAD_CALL_RE = re.compile(r"\.load\.(?:image|spritesheet)\(")

ANIM_RE = re.compile(
    r"generateFrameNumbers\(\s*['\"](?P<key>[^'\"]+)['\"]\s*,\s*\{\s*"
    r"start\s*:\s*(?P<start>[^,}]+?)\s*,\s*end\s*:\s*(?P<end>[^,}]+?)\s*\}"
)

_STRING_LITERAL_RE = re.compile(r"['\"]([^'\"]*)['\"]")
_INT_RE = re.compile(r"^\s*\d+\s*$")


class FrameSizeError(Exception):
    """A sheet whose pixel size is not an exact multiple of its frame size."""


def source_role(rel: str) -> str:
    """``authored`` / ``derived_snapshot`` / ``legacy`` for a renderer source."""
    return SOURCE_ORDER.get(rel, "unknown")


def is_authoritative(rel: str) -> bool:
    return rel in AUTHORITATIVE_SOURCES


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
    """Resolve a load path expression to (logical asset, [variant files]).

    Handles the shapes in the renderer: a plain quoted path with a cache buster,
    a base plus ``getExt('<file>.png')``, and a base plus the ``supportsWebP``
    ternary.
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


def _new_asset(key: str) -> dict:
    return {
        "key": key,
        "kind": "spritesheet",
        "logical_asset": None,
        "logical_assets": [],
        "anims": [],
        "files": [],
        "placement": [],
        "sources": [],
        "live": False,
        "scope": "legacy_only",
        "authority_role": None,
        "dynamic_frame_size": False,
        "expected_frame_width": None,
        "expected_frame_height": None,
        "expected_width": None,
        "expected_height": None,
        "expected_source": None,
        "frame_count": None,
        "errors": [],
    }


def _measure_file(repo_root, rel, role, live, kind, expr, name, variant,
                  frame_width, frame_height):
    """One declared file: real dimensions, derived grid, per-file problems."""
    path = os.path.join(STATIC_PREFIX.strip("/"), variant) if variant else None
    disk_rel = os.path.join("frontend", variant) if variant else None
    dims = image_size(os.path.join(repo_root, disk_rel)) if disk_rel and os.path.exists(
        os.path.join(repo_root, disk_rel)
    ) else None
    file_entry = {
        "source": rel,
        "role": role,
        "live": live,
        "authoritative": is_authoritative(rel),
        "kind": kind,
        "ref": re.sub(r"\s+", " ", expr),
        "logical_asset": name,
        "variant": variant,
        "path": disk_rel,
        "exists": bool(dims),
        "width": dims[0] if dims else None,
        "height": dims[1] if dims else None,
        "mode": dims[2] if dims else None,
        "frame_width": frame_width if kind == "spritesheet" else None,
        "frame_height": frame_height if kind == "spritesheet" else None,
        "frame_count": None,
        "errors": [],
    }
    if dims and kind == "spritesheet":
        try:
            count = derive_frame_count(dims[0], dims[1], frame_width, frame_height)
            file_entry["frame_count"] = _count_entry(count)
        except FrameSizeError as exc:
            file_entry["error"] = "not_an_exact_multiple"
            file_entry["error_message"] = str(exc)
    elif dims and kind == "image":
        # A single image is one frame and needs no grid: expected_width/height is
        # the whole conformance test for it.
        pass
    elif variant:
        file_entry["errors"].append("file_not_found")
    return file_entry


def extract(repo_root: str) -> dict:
    """Parse the renderer and return the contract document for ``repo_root``."""
    assets: dict = {}
    errors: list = []
    conflicts: list = []
    drift: list = []
    sources_meta = []
    source_texts = {}

    for rel in RENDER_SOURCES:
        text = _read(repo_root, rel)
        source_texts[rel] = text
        role = source_role(rel)
        live = is_authoritative(rel)
        declared = 0
        spritesheets = images = 0

        for match in SPRITESHEET_RE.finditer(text):
            spritesheets += 1
            declared += 1
            key = match.group("key")
            expr = match.group("expr").strip()
            frame_width, dynamic_w = resolve_frame_size(match.group("fw"), text)
            frame_height, dynamic_h = resolve_frame_size(match.group("fh"), text)
            name, variants = parse_ref(expr)
            entry = assets.setdefault(key, _new_asset(key))
            entry["kind"] = "spritesheet"
            if rel not in entry["sources"]:
                entry["sources"].append(rel)
            entry["live"] = entry["live"] or live
            entry["dynamic_frame_size"] = entry["dynamic_frame_size"] or (
                dynamic_w or dynamic_h
            )
            for variant in variants or [None]:
                file_entry = _measure_file(
                    repo_root, rel, role, live, "spritesheet", expr, name, variant,
                    frame_width, frame_height,
                )
                entry["files"].append(file_entry)
            if name and name not in entry["logical_assets"]:
                entry["logical_assets"].append(name)

        for match in IMAGE_RE.finditer(text):
            images += 1
            declared += 1
            key = match.group("key")
            expr = match.group("expr").strip()
            name, variants = parse_ref(expr)
            entry = assets.setdefault(key, _new_asset(key))
            # kind stays whatever the first declaration of this key was; a second,
            # different kind is reported as a conflict or drift below.
            if rel not in entry["sources"]:
                entry["sources"].append(rel)
            entry["live"] = entry["live"] or live
            for variant in variants or [None]:
                file_entry = _measure_file(
                    repo_root, rel, role, live, "image", expr, name, variant,
                    None, None,
                )
                entry["files"].append(file_entry)
            if name and name not in entry["logical_assets"]:
                entry["logical_assets"].append(name)

        sources_meta.append({
            "file": rel,
            "role": role,
            "live": live,
            "authoritative": live,
            "spritesheets": spritesheets,
            "images": images,
            "declarations": declared,
            # load calls with a variable key or url (the runtime asset swap): the
            # contract cannot describe what they load, so it counts them instead.
            "dynamic_load_calls": max(0, len(LOAD_CALL_RE.findall(text)) - declared),
        })

    # Animations: literal frame ranges per sheet key, so the contract can check
    # that no animation reads past the end of the sheet it is built from.
    for rel, text in source_texts.items():
        for match in ANIM_RE.finditer(text):
            key = match.group("key")
            if key not in assets:
                continue
            start, _ = resolve_frame_size(match.group("start"), text)
            end, _ = resolve_frame_size(match.group("end"), text)
            anim = {"source": rel, "role": source_role(rel), "key": key,
                    "start": start, "end": end,
                    "authoritative": is_authoritative(rel)}
            if anim not in assets[key]["anims"]:
                assets[key]["anims"].append(anim)

    placement = _placement(repo_root)
    placement_by_variant: dict = {}
    for name, payload in placement.items():
        for filename, values in payload.items():
            placement_by_variant.setdefault(filename, []).append(
                {"file": name, "filename": filename, **(values if isinstance(values, dict) else {})}
            )

    for key, entry in assets.items():
        ranked = sorted(
            entry["files"],
            key=lambda f: (
                not f["authoritative"],
                ROLE_RANK.get(f["role"], 3),
                not f["exists"],
                f["source"],
            ),
        )
        authoritative = ranked[0] if ranked else None
        if authoritative is None:
            continue
        entry["kind"] = authoritative["kind"]
        entry["expected_source"] = authoritative["source"]
        entry["authority_role"] = authoritative["role"]
        entry["logical_asset"] = authoritative["logical_asset"]
        entry["expected_frame_width"] = authoritative["frame_width"]
        entry["expected_frame_height"] = authoritative["frame_height"]
        entry["scope"] = "live" if entry["live"] else "legacy_only"
        if authoritative["exists"]:
            entry["expected_width"] = authoritative["width"]
            entry["expected_height"] = authoritative["height"]
            entry["frame_count"] = authoritative["frame_count"]
        for item in placement_by_variant.get(authoritative["variant"], []):
            entry["placement"].append(item)

        auth_files = [f for f in entry["files"] if f["authoritative"]]
        legacy_files = [f for f in entry["files"] if not f["authoritative"]]

        # ---- conflicts: claims that can reach a running page ----
        sizes = {(f["frame_width"], f["frame_height"]) for f in auth_files if f["frame_width"]}
        if len(sizes) > 1:
            conflicts.append({
                "key": key,
                "kind": "frame_size",
                "fatal": True,
                "logical_asset": entry["logical_asset"],
                "message": (
                    f"{len(sizes)} of the {len(auth_files)} authoritative declarations "
                    f"slice '{key}' differently: the page and its Electron snapshot "
                    f"disagree, so 'correct' has more than one answer"
                ),
                "sources": _slices(auth_files),
            })
        auth_logical = {f["logical_asset"] for f in auth_files if f["logical_asset"]}
        if len(auth_logical) > 1:
            conflicts.append({
                "key": key,
                "kind": "logical_asset",
                "fatal": True,
                "logical_asset": entry["logical_asset"],
                "message": f"authoritative sources disagree on the file '{key}' loads "
                           f"({', '.join(sorted(auth_logical))})",
                "logical_assets": sorted(auth_logical),
                "sources": sorted({f["source"] for f in auth_files}),
            })
        auth_kinds = {f["kind"] for f in auth_files}
        if len(auth_kinds) > 1:
            conflicts.append({
                "key": key,
                "kind": "load_kind",
                "fatal": True,
                "logical_asset": entry["logical_asset"],
                "message": f"authoritative sources load '{key}' both as "
                           f"{', '.join(sorted(auth_kinds))}; a sheet is not an image",
                "sources": sorted({f["source"] for f in auth_files}),
            })

        # An authoritative slicing that is not a multiple of the file on disk is
        # provably wrong geometry, not a disagreement: same gate, different kind.
        for file_entry in auth_files:
            if file_entry.get("error"):
                problem = {
                    "key": key,
                    "source": file_entry["source"],
                    "file": file_entry["path"],
                    "type": file_entry["error"],
                    "message": file_entry["error_message"],
                    "fatal": True,
                }
                errors.append(problem)
                conflicts.append({
                    "key": key,
                    "kind": file_entry["error"],
                    "fatal": True,
                    "logical_asset": file_entry["logical_asset"],
                    "message": file_entry["error_message"],
                    "sources": [file_entry["source"]],
                })

        # ---- drift: a non-authoritative source against the resolved authority ----
        # One finding per (source, kind): a sheet is declared by two variants and a
        # duplicate row would only make the report noisy.
        by_legacy_source: dict = {}
        for file_entry in legacy_files:
            by_legacy_source.setdefault(file_entry["source"], []).append(file_entry)
        for source, files in sorted(by_legacy_source.items()):
            measurable = [f for f in files if f["exists"]] or files
            representative = min(
                measurable, key=lambda f: (f["variant"] or "").endswith(".webp"))
            findings = []
            if representative["kind"] != entry["kind"]:
                findings.append(_drift_finding(
                    key, "load_kind", source, representative["role"],
                    representative["path"], _declared(representative), entry,
                    _drift_message("load_kind", key, representative, entry)))
            if representative["kind"] == "spritesheet" and entry["kind"] == "spritesheet":
                if representative["frame_width"] and (
                    representative["frame_width"], representative["frame_height"]
                ) != (entry["expected_frame_width"], entry["expected_frame_height"]):
                    findings.append(_drift_finding(
                        key, "frame_size", source, representative["role"],
                        representative["path"], _declared(representative), entry,
                        _drift_message("frame_size", key, representative, entry)))
            if representative["logical_asset"] and representative["logical_asset"] != \
                    entry["logical_asset"]:
                findings.append(_drift_finding(
                    key, "logical_asset", source, representative["role"],
                    representative["path"], _declared(representative), entry,
                    _drift_message("logical_asset", key, representative, entry)))
            broken = [f for f in measurable if f.get("error")]
            for file_entry in broken:
                findings.append(_drift_finding(
                    key, file_entry["error"], source, file_entry["role"],
                    file_entry["path"], _declared(file_entry), entry,
                    file_entry["error_message"]))
            drift.extend(findings)

        # Walk-off-the-tile guard: a literal frame range must stay inside the grid.
        count = entry["frame_count"]
        if count and entry["kind"] == "spritesheet":
            for anim in entry["anims"]:
                if anim["end"] is None or anim["end"] < count["total"]:
                    continue
                message = (
                    f"animation uses frames {anim['start']}..{anim['end']} but the sheet "
                    f"only holds {count['total']}"
                )
                if anim["authoritative"]:
                    errors.append({
                        "key": key,
                        "source": anim["source"],
                        "file": None,
                        "type": "animation_reads_past_the_sheet",
                        "message": message,
                        "fatal": True,
                    })
                    conflicts.append({
                        "key": key,
                        "kind": "animation_reads_past_the_sheet",
                        "fatal": True,
                        "logical_asset": entry["logical_asset"],
                        "message": message,
                        "sources": [anim["source"]],
                    })
                else:
                    drift.append(_drift_finding(
                        key, "animation_reads_past_the_sheet", anim["source"],
                        anim["role"], None,
                        {"kind": entry["kind"], "start": anim["start"],
                         "end": anim["end"], "logical_asset": None},
                        entry, message))

    for entry in assets.values():
        entry["files"].sort(key=lambda f: (f["source"], f["kind"], f["variant"] or ""))
        entry["anims"].sort(key=lambda a: (a["source"], a["key"], str(a["end"])))
        entry["logical_assets"].sort()
        entry["placement"].sort(key=lambda p: (p["file"], p["filename"]))
        kinds = {e["type"] for e in errors if e["key"] == entry["key"]}
        kinds |= {c["kind"] for c in conflicts if c["key"] == entry["key"]}
        entry["errors"] = sorted(kinds)

    errors.sort(key=lambda e: (not e["fatal"], e["source"], e["key"], e["type"]))
    conflicts.sort(key=lambda c: (c["key"], c["kind"]))
    drift.sort(key=lambda d: (d["key"], d["kind"], d["source"]))

    fatal = [e for e in errors if e["fatal"]]
    legacy_assets = [a for a in assets.values() if a["scope"] == "legacy_only"]
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "renderer": {
            "decision": RENDERER_DECISION,
            "authoritative_sources": list(AUTHORITATIVE_SOURCES),
            "single_source_of_truth": AUTHORED_SOURCE,
            "non_authoritative_sources": [LEGACY_SOURCE],
            "surfaces": [dict(surface) for surface in RENDERER_SURFACES],
        },
        "sources": sources_meta,
        "summary": {
            "assets": len(assets),
            "spritesheet_assets": sum(1 for a in assets.values() if a["kind"] == "spritesheet"),
            "image_assets": sum(1 for a in assets.values() if a["kind"] == "image"),
            "live_assets": sum(1 for a in assets.values() if a["scope"] == "live"),
            "legacy_only_assets": len(legacy_assets),
            "spritesheet_declarations": sum(s["spritesheets"] for s in sources_meta),
            "image_declarations": sum(s["images"] for s in sources_meta),
            "dynamic_load_calls": sum(s["dynamic_load_calls"] for s in sources_meta),
            "placement_entries": sum(len(v) for v in placement.values()),
            "conflicts": len(conflicts),
            "fatal_conflicts": len([c for c in conflicts if c["fatal"]]),
            "drift": len(drift),
            "errors": len(errors),
            "fatal_errors": len(fatal),
            "legacy_only_errors": len([e for e in errors if not e["fatal"]]),
        },
        "assets": assets,
        "placement": placement,
        "conflicts": conflicts,
        "drift": drift,
        "errors": errors,
    }


def _slices(files: list) -> list:
    """One row per (source, frame size) among the given file declarations."""
    seen = set()
    rows = []
    for file_entry in sorted(files, key=lambda f: f["source"]):
        identity = (file_entry["source"], file_entry["frame_width"],
                    file_entry["frame_height"], file_entry["kind"])
        if not file_entry["frame_width"] or identity in seen:
            continue
        rows.append({
            "source": file_entry["source"],
            "role": file_entry["role"],
            "live": file_entry["authoritative"],
            "kind": file_entry["kind"],
            "frame_width": file_entry["frame_width"],
            "frame_height": file_entry["frame_height"],
        })
        seen.add(identity)
    return rows


def _declared(file_entry: dict) -> dict:
    """What a non-authoritative source claims, as one comparable record."""
    return {
        "kind": file_entry["kind"],
        "frame_width": file_entry["frame_width"],
        "frame_height": file_entry["frame_height"],
        "logical_asset": file_entry["logical_asset"],
    }


def _drift_finding(key, kind, source, role, file_path, declared, entry, message) -> dict:
    """A non-authoritative claim, recorded together with the value that won."""
    authoritative = {
        "source": entry["expected_source"],
        "kind": entry["kind"],
        "frame_width": entry["expected_frame_width"],
        "frame_height": entry["expected_frame_height"],
        "logical_asset": entry["logical_asset"],
    }
    if entry.get("frame_count"):
        authoritative["frame_total"] = entry["frame_count"]["total"]
    return {
        "key": key,
        "kind": kind,
        "source": source,
        "role": role,
        "file": file_path,
        "declared": declared,
        "authoritative": authoritative,
        "message": message,
        # Dead code cannot mis-slice a live page, so this stays a warning: it is
        # fatal under --strict instead of blocking a build forever.
        "fatal": False,
        "disposition": "non_authoritative_source",
    }


def _drift_message(kind: str, key: str, file_entry: dict, entry: dict) -> str:
    where = file_entry["source"]
    if kind == "logical_asset":
        return (f"{where} loads '{key}' from {file_entry['logical_asset']}, the live "
                f"renderer loads {entry['logical_asset']}")
    if kind == "load_kind":
        return (f"{where} loads '{key}' as {file_entry['kind']}, the live renderer "
                f"loads it as {entry['kind']}")
    return (f"{where} slices '{key}' at {file_entry['frame_width']}x"
            f"{file_entry['frame_height']}, the live renderer slices it at "
            f"{entry['expected_frame_width']}x{entry['expected_frame_height']} "
            f"({entry['expected_source']})")


def render_contract(repo_root: str) -> str:
    """Deterministic JSON text for the contract (no wall-clock anywhere)."""
    return json.dumps(extract(repo_root), indent=2, sort_keys=True) + "\n"


def report(contract: dict, stream=sys.stdout) -> None:
    summary = contract["summary"]
    renderer = contract["renderer"]
    print(
        f"contract: {summary['assets']} assets "
        f"({summary['spritesheet_assets']} sheets + {summary['image_assets']} images), "
        f"{summary['spritesheet_declarations']} sheet + "
        f"{summary['image_declarations']} image declarations across "
        f"{len(contract['sources'])} sources, "
        f"{summary['placement_entries']} placement entries, "
        f"{summary['dynamic_load_calls']} runtime load calls",
        file=stream,
    )
    print(f"authority: {renderer['single_source_of_truth']} "
          f"(browser '/' and the Electron snapshot must agree); "
          f"legacy reference: {', '.join(renderer['non_authoritative_sources'])}",
          file=stream)
    for source in contract["sources"]:
        print(
            f"  {source['file']}: {source['spritesheets']} sheets + "
            f"{source['images']} images ({source['role']})"
            + (f", {source['dynamic_load_calls']} runtime calls"
               if source["dynamic_load_calls"] else ""),
            file=stream,
        )
    for conflict in contract["conflicts"]:
        sizes = ""
        if conflict["kind"] == "frame_size":
            sizes = " -> " + ", ".join(
                f"{s['source']} {s['frame_width']}x{s['frame_height']}"
                for s in conflict["sources"]
            )
        print(f"  CONFLICT {conflict['key']}: {conflict['message']}{sizes}", file=stream)
    for error in contract["errors"]:
        tag = "ERROR" if error["fatal"] else "WARN (legacy source)"
        print(
            f"  {tag} {error['type']} {error['key']} [{error['source']}] {error['message']}",
            file=stream,
        )
    for finding in contract["drift"]:
        print(
            f"  DRIFT {finding['key']} ({finding['kind']}) [{finding['source']}]: "
            f"{finding['message']} - resolved to "
            f"{finding['authoritative']['source']}, not fatal",
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
        help="treat drift from non-authoritative sources as fatal too")
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

    summary = contract["summary"]
    conflicts = [c for c in contract["conflicts"] if c["fatal"]]
    fatal = [e for e in contract["errors"] if e["fatal"]]
    drift = contract["drift"]
    if conflicts:
        print(f"{len(conflicts)} conflict(s): two live claims about the same asset, "
              f"or authoritative geometry the file on disk cannot support - resolve "
              f"them, do not ship a pack against two answers", file=sys.stderr)
    if fatal:
        print(f"{len(fatal)} fatal violation(s) in live sources", file=sys.stderr)
    if conflicts or fatal:
        return 1
    print(f"0 conflict(s), {len(fatal)} fatal violation(s), {len(drift)} drift note(s) "
          f"in {summary['assets']} assets")
    if drift and args.strict:
        print(f"{len(drift)} drift note(s) in non-authoritative sources; --strict "
              f"makes them fatal", file=sys.stderr)
        return 1
    if drift:
        print(f"{len(drift)} drift note(s) from non-authoritative sources; --strict "
              f"to fail on them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
