"""Validate licence-clean generated art packs against the asset contract (AC4).

Licence-clean packs land in ``assets/generated/<pack>/`` (ADR-060 as amended),
each file written by ``scripts/pixelize_asset.py`` with a sidecar manifest.
This is the gate that says whether a pack may be swapped into the renderer:
every file in the tree must match ``assets/asset-contract.json`` exactly, and
every file must be able to prove where it came from.

Checks, all of them fatal findings:

* ``not_in_contract`` - the file names no asset in the contract, so the
  renderer would never load it (resolved by the manifest ``asset_key`` first,
  then by the filename against the logical asset names and variants);
* ``missing_manifest`` / ``unreadable_manifest`` - no sidecar, or one that is
  not JSON; an unprovenanced file cannot be trusted;
* ``manifest_field`` - a required provenance field is absent;
* ``dimensions`` - the real pixels are not the contract size for the asset, or
  the manifest disagrees with the file on disk;
* ``grid`` - the frame size is not the contract frame size, the sheet is not an
  exact multiple of it, or the resulting cols x rows is not the frame count the
  contract derives (this is the walk-off-the-tile guard).

An empty or missing ``assets/generated/`` is not a failure: no pack has been
generated yet, so the tree prints an explicit ``no packs yet`` line and exits
0. That keeps the gate safe to run in CI today instead of a traceback.

Usage::

    python3 scripts/validate_generated_assets.py
    python3 scripts/validate_generated_assets.py --generated-dir assets/generated/flux2
    python3 scripts/validate_generated_assets.py --contract path/to/contract.json

CPU-only, no network, no GPU, and it never writes to the tree it checks.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

from pixelize_asset import REQUIRED_MANIFEST_FIELDS  # noqa: E402  (same directory)

DEFAULT_GENERATED_DIR = os.path.join(REPO_ROOT, "assets", "generated")
DEFAULT_CONTRACT = os.path.join(REPO_ROOT, "assets", "asset-contract.json")
IMAGE_EXTENSIONS = (".png", ".webp", ".jpg", ".jpeg")

EXIT_FINDINGS = 1
EXIT_ERROR = 2


class ContractError(Exception):
    """The gate cannot run: the contract is missing or unreadable."""


def load_contract(path: str) -> dict:
    if not os.path.exists(path):
        raise ContractError(
            f"{path} not found - run scripts/extract_asset_contract.py first (AC1)"
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            contract = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ContractError(f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(contract, dict) or "assets" not in contract:
        raise ContractError(f"{path} has no 'assets' section")
    return contract


def index_contract(contract: dict) -> dict:
    """Map every name a pack file might answer to onto its contract asset."""
    by_name: dict[str, str] = {}
    for key, asset in (contract.get("assets") or {}).items():
        names = {key, asset.get("logical_asset")}
        names.update(asset.get("logical_assets") or [])
        for entry in asset.get("files") or []:
            names.add(entry.get("variant"))
            names.add(entry.get("logical_asset"))
            path = entry.get("path")
            if path:
                names.add(os.path.basename(path))
        for name in sorted(n for n in names if n):
            stem = os.path.splitext(name)[0]
            by_name.setdefault(stem, key)
    return by_name


def measure(path: str) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - only hit without pillow
        raise ContractError(f"pillow is required to measure {path}: {exc}") from exc
    with Image.open(path) as image:
        return image.size


def manifest_candidates(file_path: str) -> list[str]:
    """The sidecar paths a pack file may carry, in the order they are trusted."""
    stem = os.path.splitext(file_path)[0]
    return [f"{file_path}.manifest.json", f"{stem}.manifest.json"]


def find_manifest(file_path: str) -> tuple[str | None, list[str]]:
    looked = manifest_candidates(file_path)
    for candidate in looked:
        if os.path.exists(candidate):
            return candidate, looked
    return None, looked


def read_manifest(manifest_path: str) -> dict:
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"unreadable manifest: {exc}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("manifest is not a JSON object")
    return manifest


def resolve_asset(
    file_path: str, manifest: dict | None, by_name: dict, contract: dict
) -> tuple[str | None, str]:
    """Which contract asset is this file? ``asset_key`` wins over the name."""
    key = (manifest or {}).get("asset_key")
    if isinstance(key, str) and key in (contract.get("assets") or {}):
        return key, "manifest asset_key"
    stem = os.path.splitext(os.path.basename(file_path))[0]
    if stem in by_name:
        return by_name[stem], "filename"
    return None, "unresolved"


def frame_size_of(manifest: dict | None, asset: dict) -> tuple[int | None, int | None, str]:
    """Frame size to check against: the manifest's, else the contract's."""
    frame = (manifest or {}).get("frame_size")
    if isinstance(frame, list) and len(frame) == 2:
        return int(frame[0]), int(frame[1]), "manifest frame_size"
    return (
        asset.get("expected_frame_width"),
        asset.get("expected_frame_height"),
        "contract expected frame size",
    )


def check_file(
    file_path: str, root: str, contract: dict, by_name: dict
) -> tuple[list[str], list[str]]:
    """Return (findings, warnings) for one image file inside the pack tree."""
    findings: list[str] = []
    warnings: list[str] = []
    shown = os.path.relpath(file_path, root)

    manifest_path, looked = find_manifest(file_path)
    manifest: dict | None = None
    if manifest_path is None:
        findings.append(
            f"missing_manifest: {shown} has no sidecar manifest (looked for "
            f"{', '.join(os.path.relpath(p, root) for p in looked)}); a pack file "
            f"without provenance cannot be swapped in"
        )
    else:
        try:
            manifest = read_manifest(manifest_path)
        except ValueError as exc:
            findings.append(
                f"unreadable_manifest: {os.path.relpath(manifest_path, root)} is not "
                f"usable JSON ({exc})"
            )

    if manifest is not None:
        absent = [field for field in REQUIRED_MANIFEST_FIELDS if field not in manifest]
        if absent:
            findings.append(
                f"manifest_field: {shown} manifest is missing required field(s): "
                f"{', '.join(absent)}"
            )

    asset_key, how = resolve_asset(file_path, manifest, by_name, contract)
    if asset_key is None:
        findings.append(
            f"not_in_contract: {shown} names no asset in the contract (neither its "
            f"manifest asset_key nor its filename matched), so the renderer would "
            f"never load it"
        )
        return findings, warnings
    if how == "filename" and (manifest or {}).get("asset_key"):
        warnings.append(
            f"{shown} resolved to {asset_key} by filename; its manifest asset_key "
            f"{manifest['asset_key']!r} is not a contract asset"
        )

    asset = contract["assets"][asset_key]
    try:
        width, height = measure(file_path)
    except ContractError:
        raise
    except OSError as exc:
        findings.append(f"unreadable_file: {shown} cannot be opened ({exc})")
        return findings, warnings

    expected_width = asset.get("expected_width")
    expected_height = asset.get("expected_height")
    if expected_width and expected_height:
        if [width, height] != [expected_width, expected_height]:
            findings.append(
                f"dimensions: {shown} is {width}x{height}, the contract says "
                f"{expected_width}x{expected_height} for {asset_key}"
            )
    else:  # pragma: no cover - every contract asset carries a size
        warnings.append(f"no contract size for {asset_key}; {shown} left unchecked")

    declared = manifest.get("output_dims") if manifest else None
    if isinstance(declared, list) and len(declared) == 2:
        if [int(declared[0]), int(declared[1])] != [width, height]:
            findings.append(
                f"dimensions: {shown} manifest says {declared[0]}x{declared[1]} but the "
                f"file on disk is {width}x{height}"
            )

    contract_frame = (
        asset.get("expected_frame_width"),
        asset.get("expected_frame_height"),
    )
    frame_width, frame_height, frame_source = frame_size_of(manifest, asset)
    if frame_width and frame_height:
        if (
            contract_frame[0]
            and contract_frame[1]
            and (frame_width, frame_height) != contract_frame
        ):
            findings.append(
                f"grid: {shown} slices at {frame_width}x{frame_height} ({frame_source}) "
                f"but the contract frame size is {contract_frame[0]}x{contract_frame[1]} "
                f"for {asset_key}"
            )
        if width % frame_width or height % frame_height:
            findings.append(
                f"grid: {shown} is {width}x{height}, which is not an exact multiple of "
                f"the {frame_width}x{frame_height} frame size"
            )
        else:
            columns, rows = width // frame_width, height // frame_height
            expected_count = (asset.get("frame_count") or {}).get("total")
            if expected_count and columns * rows != expected_count:
                findings.append(
                    f"grid: {shown} gives a {columns}x{rows} = {columns * rows} frame "
                    f"grid, the contract derives {expected_count} frames for {asset_key}"
                )
            declared_count = (
                manifest.get("columns"),
                manifest.get("rows"),
            ) if manifest else (None, None)
            if (
                declared_count[0]
                and declared_count[1]
                and tuple(declared_count) != (columns, rows)
            ):
                findings.append(
                    f"grid: {shown} manifest declares {declared_count[0]}x{declared_count[1]} "
                    f"frames but the sheet is {columns}x{rows}"
                )

    if manifest is not None:
        for field in ("model", "prompt", "seed"):
            if manifest.get(field) in (None, ""):
                warnings.append(f"{shown} manifest has no {field}; provenance is thin")
        declared_output = manifest.get("output_file")
        if declared_output and os.path.basename(file_path) != declared_output:
            warnings.append(
                f"{shown} manifest names output_file {declared_output!r}, which is not "
                f"the file it sits beside"
            )
        palette = manifest.get("palette_colors")
        if isinstance(palette, int) and palette > 256:
            findings.append(f"palette: {shown} manifest reports {palette} colours, over one byte")

    return findings, warnings


def walk_images(root: str) -> list[str]:
    found = []
    for directory, subdirectories, names in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if not d.startswith("."))
        for name in sorted(names):
            if name.startswith("."):
                continue
            if os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS:
                found.append(os.path.join(directory, name))
    return found


def render_summary(
    root: str, contract: dict, files: list[str], findings: list[str], warnings: list[str]
) -> int:
    print(f"contract {os.path.relpath(DEFAULT_CONTRACT, REPO_ROOT)}: "
          f"{len(contract.get('assets') or {})} assets, schema {contract.get('schema_version')}")
    shown_root = os.path.relpath(root, REPO_ROOT) if root.startswith(REPO_ROOT) else root
    if not files:
        print(f"{shown_root}: no packs yet - the directory holds no image files, so the "
              f"contract gate has nothing to check (this is fine before the first "
              f"generation run)")
    else:
        for finding in findings:
            print(f"FINDING {finding}")
        for warning in warnings:
            print(f"warning {warning}")
        verdict = "FAIL" if findings else "PASS"
        print(f"{shown_root}: {verdict} - {len(files)} file(s) checked, "
              f"{len(findings)} finding(s), {len(warnings)} warning(s)")
    return EXIT_FINDINGS if findings else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--generated-dir", default=DEFAULT_GENERATED_DIR,
                        help="pack tree to walk (default: assets/generated)")
    parser.add_argument("--contract", default=DEFAULT_CONTRACT,
                        help="assets/asset-contract.json (default)")
    args = parser.parse_args(argv)

    try:
        contract = load_contract(args.contract)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    root = args.generated_dir if os.path.isabs(args.generated_dir) else os.path.abspath(
        os.path.join(os.getcwd(), args.generated_dir)
    )
    if not os.path.isdir(root):
        return render_summary(root, contract, [], [], [])

    by_name = index_contract(contract)
    files = walk_images(root)
    findings: list[str] = []
    warnings: list[str] = []
    try:
        for file_path in files:
            file_findings, file_warnings = check_file(file_path, root, contract, by_name)
            findings.extend(file_findings)
            warnings.extend(file_warnings)
    except ContractError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR
    return render_summary(root, contract, files, findings, warnings)


if __name__ == "__main__":
    sys.exit(main())
