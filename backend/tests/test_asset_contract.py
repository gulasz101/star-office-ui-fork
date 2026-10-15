"""Art asset contract, pixelize post-processor and pack validator (SOU-98).

Licence-clean art is generated Mac-side on FLUX.2-klein-4B (ADR-060 as amended,
ADR-061 invariants 10 and 11) and arrives as raw 512x512 RGBA. The renderer
slices every sheet with an explicit ``frameWidth``/``frameHeight`` and keys
position/scale by filename, and there is no single native sprite size, so the
only safe way in is a machine-checkable contract. This module is that contract's
gate:

* ``scripts/extract_asset_contract.py`` (AC1) parses the spritesheet calls out of
  ``frontend/game.js``, ``frontend/index.html`` and
  ``frontend/electron-standalone.html``, resolves the ``getExt()``/``supportsWebP``
  path expressions to one logical asset, reads the real dimensions with pillow,
  derives the frame count (cols x rows) and treats a sheet whose size is not an
  exact multiple of the frame size as a hard error, then emits
  ``assets/asset-contract.json``.
* ``scripts/pixelize_asset.py`` (AC2) turns one raw generation into a conforming
  file: nearest-neighbour only, palette capped at N (default 24, floor 16), no
  dithering, alpha preserved, refuses a non-multiple target, and writes a
  sidecar manifest.
* ``scripts/validate_generated_assets.py`` (AC4) walks ``assets/generated/`` and
  fails a pack whose files do not conform, while passing an empty directory with
  an explicit "no packs yet" line.

These tests are the RED half of SOU-98: they fail while the three scripts do not
exist and pass once they do. CPU-only, no network, no GPU, no image generation,
no art committed - synthetic sheets are built with pillow in a temp dir.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_asset_contract.py -v

or as part of the suite the way CI does it (repo root)::

    python -m pytest backend/tests -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND_DIR)
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
CONTRACT_JSON = os.path.join(REPO_ROOT, "assets", "asset-contract.json")

READ_ONLY_SOURCES = (
    "frontend/game.js",
    "frontend/index.html",
    "frontend/electron-standalone.html",
    "frontend/nav.js",
    "frontend/layout.js",
    "asset-positions.json",
    "asset-defaults.json",
)

try:
    from PIL import Image

    _PIL = True
    _PIL_ERROR = None
except ImportError as exc:  # pragma: no cover - only hit without pillow
    _PIL = None
    _PIL_ERROR = str(exc)

MANIFEST_REQUIRED_FIELDS = (
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


def load_script(name: str):
    """Import ``scripts/<name>.py`` by path (scripts/ is not a package).

    A missing implementation is a test failure that names the file, which is
    exactly the RED state SOU-98 T1 is supposed to reach.
    """
    path = os.path.join(SCRIPTS_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise AssertionError(f"missing implementation: scripts/{name}.py (SOU-98)")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _child_env() -> dict:
    """Subprocess env with an absolute .tooling/site on PYTHONPATH.

    The suite is normally run from ``backend/`` with a *relative* PYTHONPATH;
    script subprocesses run from the repo root, so the entry has to be absolute
    or pillow/pytest extras silently disappear.
    """
    env = dict(os.environ)
    site = os.path.join(REPO_ROOT, ".tooling", "site")
    if os.path.isdir(site):
        existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        env["PYTHONPATH"] = os.pathsep.join([site, *existing])
    return env


def run_script(name: str, *args: str, cwd: str = REPO_ROOT) -> subprocess.CompletedProcess:
    path = os.path.join(SCRIPTS_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise AssertionError(f"missing implementation: scripts/{name}.py (SOU-98)")
    return subprocess.run(
        [sys.executable, path, *[str(a) for a in args]],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=_child_env(),
    )


def digest_tree(root: str) -> dict:
    """sha256 of every file under ``root`` (used to prove sources stay read-only)."""
    out = {}
    for rel in sorted(READ_ONLY_SOURCES):
        path = os.path.join(root, rel)
        if os.path.exists(path):
            with open(path, "rb") as handle:
                out[rel] = hashlib.sha256(handle.read()).hexdigest()
    return out


def stripe_image(size=(512, 512), blocks: int = 8) -> "Image.Image":
    """Hard-edged two-colour stripes: nearest-neighbour keeps exactly 2 colours."""
    image = Image.new("RGBA", size, (10, 10, 10, 255))
    step = max(1, size[0] // blocks)
    for column in range(0, size[0], step * 2):
        for x in range(column, min(column + step, size[0])):
            for y in range(size[1]):
                image.putpixel((x, y), (240, 240, 240, 255))
    return image


def noisy_image(size=(512, 512), colors: int = 80, grid: int = 10) -> "Image.Image":
    """A synthetic raw generation: many flat colours, alpha on the left half.

    The blocks tile the canvas exactly (integer partition, no seam of base
    pixels at the right and bottom edges), so a test can assert on the very
    last column and get a real answer.
    """
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    palette = [((i * 37) % 256, (i * 73) % 256, (i * 11) % 256) for i in range(colors)]
    for by in range(grid):
        for bx in range(grid):
            red, green, blue = palette[(bx * grid + by) % colors]
            alpha = 0 if bx < grid // 2 else 255
            for y in range(size[1] * by // grid, size[1] * (by + 1) // grid):
                for x in range(size[0] * bx // grid, size[0] * (bx + 1) // grid):
                    image.putpixel((x, y), (red, green, blue, alpha))
    return image


def provenance_args() -> list:
    return [
        "--model", "black-forest-labs/FLUX.2-klein-4B",
        "--lora", "Limbicnation/pixel-art-lora",
        "--lora-strength", "0.85",
        "--prompt", "cozy lodge coffee machine sprite, no text",
        "--seed", "12345",
        "--steps", "24",
        "--guidance", "3.5",
    ]


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class ExtractorFrameCountTest(unittest.TestCase):
    """AC1: the contract is derived from the real renderer and the real sheets."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = load_script("extract_asset_contract")
        cls.contract = cls.extractor.extract(REPO_ROOT)
        cls.assets = cls.contract["assets"]

    def test_derive_frame_count_from_real_sheet_dimensions(self) -> None:
        derive = self.extractor.derive_frame_count
        self.assertEqual(tuple(derive(2048, 1536, 256, 256)), (8, 6, 48))
        self.assertEqual(tuple(derive(1760, 1980, 220, 220)), (8, 9, 72))
        self.assertEqual(tuple(derive(512, 512, 128, 128)), (4, 4, 16))
        self.assertEqual(tuple(derive(7200, 251, 180, 251)), (40, 1, 40))

    def test_star_idle_contract_entry_matches_the_file_on_disk(self) -> None:
        entry = self.assets["star_idle"]
        self.assertEqual(entry["expected_width"], 2048)
        self.assertEqual(entry["expected_height"], 1536)
        self.assertEqual(entry["expected_frame_width"], 256)
        self.assertEqual(entry["expected_frame_height"], 256)
        self.assertEqual(entry["frame_count"]["total"], 48)
        self.assertEqual(entry["frame_count"]["columns"], 8)
        self.assertEqual(entry["frame_count"]["rows"], 6)

    def test_error_bug_sheet_grid_matches_the_live_frame_size(self) -> None:
        entry = self.assets["error_bug"]
        self.assertEqual(entry["expected_frame_width"], 220)
        self.assertEqual(entry["frame_count"]["total"], 72)

    def test_webp_png_and_cache_busted_refs_collapse_to_one_logical_asset(self) -> None:
        # game.js uses '/static/error-bug-spritesheet-grid' + (supportsWebP ? '.webp' : '.png')
        # index.html uses '/static/error-bug-spritesheet-grid.webp?v={{VERSION_TIMESTAMP}}'
        files = self.assets["error_bug"]["files"]
        sources = {entry["source"] for entry in files}
        self.assertIn("frontend/game.js", sources)
        self.assertIn("frontend/index.html", sources)
        logical = {entry["logical_asset"] for entry in files}
        self.assertEqual(logical, {"error-bug-spritesheet-grid"})
        self.assertEqual(
            self.extractor.logical_asset("/static/star-idle-v5.png?v={{VERSION_TIMESTAMP}}"),
            "star-idle-v5",
        )
        self.assertEqual(
            self.extractor.logical_asset("/static/plants-spritesheet" + ".webp"),
            "plants-spritesheet",
        )

    def test_every_live_spritesheet_key_is_in_the_contract(self) -> None:
        for key in (
            "star_idle", "plants", "posters", "coffee_machine", "serverroom",
            "error_bug", "cats", "star_working", "sync_anim", "flowers",
            "guest_anim_1", "guest_anim_6",
        ):
            self.assertIn(key, self.assets, f"contract is missing spritesheet key {key}")

    def test_frame_size_given_as_a_js_identifier_is_resolved(self) -> None:
        # flowers is loaded with frameWidth: FLOWERS_FRAME_W, which the runtime
        # probe reassigns to 128; the default (65) would slice 512x512 into a
        # non-multiple grid.
        entry = self.assets["flowers"]
        self.assertTrue(entry["dynamic_frame_size"])
        self.assertEqual(entry["expected_frame_width"], 128)
        self.assertEqual(entry["expected_frame_height"], 128)
        self.assertEqual(entry["frame_count"]["total"], 16)

    def test_anim_frame_ranges_are_recorded_for_the_walk_off_the_tile_guard(self) -> None:
        entry = self.assets["star_working"]
        live = [
            a for a in entry["anims"]
            if a["end"] is not None and a["source"] in ("frontend/index.html",
                                                         "frontend/electron-standalone.html")
        ]
        self.assertTrue(live, "star_working uses a literal generateFrameNumbers end")
        self.assertLess(max(a["end"] for a in live), entry["frame_count"]["total"])
        # Every range is attributed to the source that declares it and to whether
        # that source is authoritative, which is what lets an over-long range be
        # graded (fatal conflict against a warning) instead of guessed.  The
        # grading itself is proved in backend/tests/test_asset_contract_resolution.py
        # against a synthetic tree; SOU-104 reconciled game.js, so this tree must
        # now report no source reading past its sheet at all.
        self.assertTrue(
            {(a["source"], a["authoritative"]) for a in entry["anims"]}
            >= {("frontend/index.html", True), ("frontend/game.js", False)},
            "the legacy range must still be recorded, not dropped from the contract",
        )
        for bucket in ("errors", "conflicts", "drift"):
            over = [
                finding for finding in self.contract[bucket]
                if finding.get("type") == "animation_reads_past_the_sheet"
                or finding.get("kind") == "animation_reads_past_the_sheet"
            ]
            self.assertEqual(over, [], f"an animation reads past its sheet ({bucket})")

    def test_placement_entries_are_keyed_by_filename(self) -> None:
        placement = self.contract["placement"]
        positions = placement["asset-positions.json"]
        defaults = placement["asset-defaults.json"]
        self.assertIn("desk-v3.webp", positions)
        self.assertEqual(positions["desk-v3.webp"]["x"], 218.0)
        self.assertEqual(positions["desk-v3.webp"]["y"], 417.0)
        self.assertIn("flowers-bloom-v2.webp", defaults)
        self.assertEqual(defaults["flowers-bloom-v2.webp"]["scale"], 0.8)
        # ...and the placement is attached to the asset whose file it names
        self.assertTrue(self.assets["flowers"]["placement"])

    def test_extractor_never_writes_to_the_sources_it_reads(self) -> None:
        before = digest_tree(REPO_ROOT)
        self.extractor.extract(REPO_ROOT)
        run_script("extract_asset_contract", "--out", os.path.join(tempfile.mkdtemp(prefix="sou98-"), "c.json"))
        self.assertEqual(before, digest_tree(REPO_ROOT))

    def test_missing_legacy_reference_is_recorded_not_invented(self) -> None:
        # game.js still asks for star-researching-spritesheet.*, which is not in the repo
        entry = self.assets["star_researching"]
        self.assertTrue(any(not f["exists"] for f in entry["files"]))
        self.assertIsNone(entry["frame_count"])


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class NonMultipleSheetTest(unittest.TestCase):
    """AC1: a sheet that is not an exact multiple of its frame size is an error."""

    def setUp(self) -> None:
        self.extractor = load_script("extract_asset_contract")

    def test_derive_frame_count_raises_when_not_an_exact_multiple(self) -> None:
        with self.assertRaises(self.extractor.FrameSizeError):
            self.extractor.derive_frame_count(500, 500, 120, 120)
        with self.assertRaises(self.extractor.FrameSizeError):
            self.extractor.derive_frame_count(1760, 1980, 180, 180)

    def _fake_repo(self, root: str) -> None:
        os.makedirs(os.path.join(root, "frontend"), exist_ok=True)
        html = (
            "<html><script>\n"
            "  this.load.spritesheet('bad_sheet', '/static/bad-sheet' + getExt('bad-sheet.png'),"
            " { frameWidth: 120, frameHeight: 120 });\n"
            "  this.load.spritesheet('good_sheet', '/static/good-sheet.webp?v={{VERSION_TIMESTAMP}}',"
            " { frameWidth: 120, frameHeight: 120 });\n"
            "</script></html>\n"
        )
        with open(os.path.join(root, "frontend", "index.html"), "w", encoding="utf-8") as handle:
            handle.write(html)
        noisy_image(size=(500, 500)).save(os.path.join(root, "frontend", "bad-sheet.png"))
        noisy_image(size=(480, 240)).save(os.path.join(root, "frontend", "good-sheet.webp"))
        for name in ("asset-positions.json", "asset-defaults.json"):
            with open(os.path.join(root, name), "w", encoding="utf-8") as handle:
                handle.write("{}\n")

    def test_extract_records_a_hard_error_and_invents_no_frame_count(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou98-nonmultiple-")
        self.addCleanup(shutil.rmtree, workdir, True)
        self._fake_repo(workdir)
        contract = self.extractor.extract(workdir)
        self.assertIsNone(contract["assets"]["bad_sheet"]["frame_count"])
        self.assertEqual(contract["assets"]["good_sheet"]["frame_count"]["total"], 8)
        errors = [e for e in contract["errors"] if e["type"] == "not_an_exact_multiple"]
        self.assertTrue(errors, "a not_an_exact_multiple error must be reported")
        self.assertEqual(errors[0]["key"], "bad_sheet")

    def test_strict_mode_exits_nonzero_on_a_non_multiple_sheet(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou98-strict-")
        self.addCleanup(shutil.rmtree, workdir, True)
        self._fake_repo(workdir)
        out = os.path.join(workdir, "contract.json")
        proc = run_script(
            "extract_asset_contract", "--repo-root", workdir, "--out", out, "--strict"
        )
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("not_an_exact_multiple", proc.stdout + proc.stderr)


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class FrameSizeConflictTest(unittest.TestCase):
    """AC1/AC2: sources that disagree on a frame size, and a candidate that does not match."""

    def setUp(self) -> None:
        self.extractor = load_script("extract_asset_contract")
        self.contract = self.extractor.extract(REPO_ROOT)
        self.assets = self.contract["assets"]

    def test_the_legacy_source_and_the_live_page_now_agree(self) -> None:
        # SOU-98 recorded this as a conflict because frontend/game.js sliced
        # error_bug at 180x180 while the live page sliced it at 220x220. SOU-104
        # ruled the live page authoritative and realigned the legacy copy, so the
        # tree must now agree on the geometry that the sheet on disk supports -
        # with game.js still parsed, so agreement is not the result of ignoring it.
        self.assertEqual(self.contract["conflicts"], [])
        self.assertEqual(self.contract["drift"], [])
        entry = self.assets["error_bug"]
        declared = {
            (f["source"], f["frame_width"], f["frame_height"])
            for f in entry["files"] if f["exists"]
        }
        self.assertEqual({(width, height) for _, width, height in declared},
                         {(220, 220)})
        self.assertEqual(
            {s for s, _, _ in declared},
            {"frontend/index.html", "frontend/electron-standalone.html", "frontend/game.js"},
        )
        self.assertEqual((entry["expected_frame_width"], entry["expected_frame_height"]),
                         (220, 220))

    def test_pixelize_refuses_a_candidate_whose_frame_size_differs_from_the_contract(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou98-conflict-")
        self.addCleanup(shutil.rmtree, workdir, True)
        raw = os.path.join(workdir, "raw.png")
        noisy_image().save(raw)
        out = os.path.join(workdir, "pack", "error-bug-spritesheet-grid.webp")
        proc = run_script(
            "pixelize_asset",
            "--input", raw,
            "--output", out,
            "--contract", CONTRACT_JSON,
            "--asset", "error_bug",
            "--frame-width", 110,
            "--frame-height", 110,
            "--target-size", "1760x1980",
            *provenance_args(),
        )
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("contract", (proc.stdout + proc.stderr).lower())
        self.assertFalse(os.path.exists(out), "a refused candidate must not be written")
        self.assertFalse(os.path.exists(out + ".manifest.json"))


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class PixelizeConformanceTest(unittest.TestCase):
    """AC2: nearest-neighbour, palette cap, alpha, no dither, refuse a bad target."""

    def setUp(self) -> None:
        self.pixelize = load_script("pixelize_asset")
        self.workdir = tempfile.mkdtemp(prefix="sou98-pixelize-")
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.raw = os.path.join(self.workdir, "raw_512.png")
        noisy_image().save(self.raw)

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return run_script("pixelize_asset", *args)

    def test_output_dimensions_are_exactly_the_target(self) -> None:
        out = os.path.join(self.workdir, "asset.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            self.assertEqual(image.size, (512, 512))

    def test_downscale_is_nearest_neighbour_only(self) -> None:
        stripes = os.path.join(self.workdir, "stripes.png")
        stripe_image().save(stripes)
        out = os.path.join(self.workdir, "small.png")
        proc = self._run(
            "--input", stripes, "--output", out,
            "--frame-width", 64, "--frame-height", 64, "--target-size", "256x256",
            "--palette", "16",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            rgba = image.convert("RGBA")
            colours = rgba.getcolors(maxcolors=1 << 16) or []
        # Any blending resample would invent intermediate greys.
        self.assertEqual(len(colours), 2, f"expected 2 exact colours, got {colours}")
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertEqual(manifest["resample"], "NEAREST")

    def test_palette_is_capped_at_n(self) -> None:
        out = os.path.join(self.workdir, "capped.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            "--palette", "16",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            colours = image.convert("RGBA").getcolors(maxcolors=1 << 20) or []
        self.assertLessEqual(len(colours), 16)
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertEqual(manifest["palette_colors"], len(colours))

    def test_default_palette_is_24(self) -> None:
        out = os.path.join(self.workdir, "default24.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertLessEqual(manifest["palette_colors"], 24)

    def test_palette_below_the_floor_is_refused(self) -> None:
        out = os.path.join(self.workdir, "too_few.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            "--palette", "8",
            *provenance_args(),
        )
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(out))

    def test_alpha_is_preserved(self) -> None:
        out = os.path.join(self.workdir, "alpha.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            rgba = image.convert("RGBA")
            self.assertEqual(rgba.mode, "RGBA")
            self.assertEqual(rgba.getpixel((0, 0))[3], 0, "transparent half must stay transparent")
            self.assertEqual(rgba.getpixel((rgba.size[0] - 1, 0))[3], 255)

    def test_no_dithering_is_applied(self) -> None:
        out = os.path.join(self.workdir, "nodither.png")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 128, "--frame-height", 128, "--target-size", "512x512",
            "--palette", "24",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            rgba = image.convert("RGBA")
            right = rgba.size[0] - 12
            # One flat block of the opaque half. A dithered quantise speckles
            # neighbours through it; a hard palette swap never does.
            block = {rgba.getpixel((x, y)) for y in range(8, 12) for x in range(right, right + 4)}
        self.assertEqual(len(block), 1, f"dithering invented colours inside a flat block: {sorted(block)}")
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertEqual(manifest["dither"], "NONE")
        self.assertEqual(manifest["resample"], "NEAREST")

    def test_a_target_that_is_not_an_exact_multiple_refuses_to_write(self) -> None:
        out = os.path.join(self.workdir, "pack", "coffee-machine-v3-grid.webp")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--frame-width", 230, "--frame-height", 230, "--target-size", "500x500",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 2, msg=proc.stdout + proc.stderr)
        self.assertIn("exact multiple", proc.stdout + proc.stderr)
        self.assertFalse(os.path.exists(out), "refusal must not write the asset")
        self.assertFalse(os.path.exists(out + ".manifest.json"))

    def test_output_defaults_to_the_contract_size_for_the_asset(self) -> None:
        out = os.path.join(self.workdir, "flowers-bloom-v2.webp")
        proc = self._run(
            "--input", self.raw, "--output", out,
            "--contract", CONTRACT_JSON, "--asset", "flowers",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            self.assertEqual(image.size, (512, 512))
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertEqual(manifest["frame_size"], [128, 128])


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class ManifestFieldTest(unittest.TestCase):
    """AC2: the sidecar manifest carries the full provenance record."""

    def test_manifest_contains_every_required_field(self) -> None:
        pixelize = load_script("pixelize_asset")
        self.assertEqual(
            set(pixelize.REQUIRED_MANIFEST_FIELDS), set(MANIFEST_REQUIRED_FIELDS)
        )
        workdir = tempfile.mkdtemp(prefix="sou98-manifest-")
        self.addCleanup(shutil.rmtree, workdir, True)
        raw = os.path.join(workdir, "raw.png")
        noisy_image().save(raw)
        out = os.path.join(workdir, "asset.webp")
        proc = run_script(
            "pixelize_asset",
            "--input", raw, "--output", out,
            "--contract", CONTRACT_JSON, "--asset", "flowers",
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        for field in MANIFEST_REQUIRED_FIELDS:
            self.assertIn(field, manifest, f"manifest is missing {field}")
        self.assertEqual(manifest["model"], "black-forest-labs/FLUX.2-klein-4B")
        self.assertEqual(manifest["seed"], 12345)
        self.assertEqual(manifest["output_dims"], [512, 512])
        self.assertIsInstance(manifest["palette_colors"], int)
        self.assertTrue(manifest["source_file"].endswith("raw.png"))
        self.assertEqual(os.path.basename(manifest["output_file"]), "asset.webp")
        # created_at must be a parseable ISO-8601 timestamp
        import datetime

        datetime.datetime.fromisoformat(manifest["created_at"])


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class ValidatorTest(unittest.TestCase):
    """AC4: packs under assets/generated/ must conform, and an empty tree must pass."""

    def setUp(self) -> None:
        self.validator = load_script("validate_generated_assets")
        self.workdir = tempfile.mkdtemp(prefix="sou98-validator-")
        self.addCleanup(shutil.rmtree, self.workdir, True)
        self.raw = os.path.join(self.workdir, "raw.png")
        noisy_image().save(self.raw)

    def _pixelize(self, pack: str, name: str = "flowers-bloom-v2.webp", asset: str = "flowers"):
        out = os.path.join(pack, name)
        proc = run_script(
            "pixelize_asset",
            "--input", self.raw, "--output", out,
            "--contract", CONTRACT_JSON, "--asset", asset,
            *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        return out

    def _validate(self, generated_dir: str) -> subprocess.CompletedProcess:
        return run_script(
            "validate_generated_assets",
            "--generated-dir", generated_dir, "--contract", CONTRACT_JSON,
        )

    def test_conforming_pack_passes(self) -> None:
        pack = os.path.join(self.workdir, "pack-a")
        os.makedirs(pack, exist_ok=True)
        self._pixelize(pack)
        proc = self._validate(pack)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_empty_generated_dir_passes_with_a_no_packs_line(self) -> None:
        empty = os.path.join(self.workdir, "empty")
        os.makedirs(empty, exist_ok=True)
        proc = self._validate(empty)
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("no packs yet", proc.stdout.lower())

    def test_missing_generated_dir_passes_without_a_traceback(self) -> None:
        proc = self._validate(os.path.join(self.workdir, "does-not-exist"))
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("Traceback", proc.stdout + proc.stderr)
        self.assertIn("no packs yet", proc.stdout.lower())

    def test_repo_default_is_assets_generated(self) -> None:
        proc = run_script("validate_generated_assets")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("no packs yet", proc.stdout.lower())

    def test_wrong_dimensions_fail(self) -> None:
        pack = os.path.join(self.workdir, "bad-dims")
        os.makedirs(pack, exist_ok=True)
        out = self._pixelize(pack)
        os.remove(out)
        os.remove(out + ".manifest.json")
        noisy_image(size=(640, 640)).save(out)
        manifest_path = out + ".manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "model": "black-forest-labs/FLUX.2-klein-4B", "lora": None,
                    "lora_strength": None, "prompt": "p", "seed": 1, "steps": 20,
                    "guidance": 3.5, "source_file": "x.png",
                    "output_file": os.path.basename(out), "output_dims": [640, 640],
                    "palette_colors": 16, "created_at": "2026-10-15T00:00:00+00:00",
                    "frame_size": [128, 128],
                },
                handle,
            )
        proc = self._validate(pack)
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("dimensions", proc.stdout + proc.stderr)
        self.assertIn(os.path.basename(out), proc.stdout + proc.stderr)

    def test_wrong_sheet_grid_fails(self) -> None:
        pack = os.path.join(self.workdir, "bad-grid")
        os.makedirs(pack, exist_ok=True)
        out = self._pixelize(pack)
        manifest_path = out + ".manifest.json"
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        # 512x512 sliced at 64x64 is 64 frames; the contract says 16.
        manifest["frame_size"] = [64, 64]
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        proc = self._validate(pack)
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("grid", proc.stdout + proc.stderr)

    def test_missing_manifest_fails(self) -> None:
        pack = os.path.join(self.workdir, "no-manifest")
        os.makedirs(pack, exist_ok=True)
        out = self._pixelize(pack)
        os.remove(out + ".manifest.json")
        proc = self._validate(pack)
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("manifest", proc.stdout + proc.stderr)

    def test_manifest_missing_a_required_field_fails(self) -> None:
        pack = os.path.join(self.workdir, "thin-manifest")
        os.makedirs(pack, exist_ok=True)
        out = self._pixelize(pack)
        manifest_path = out + ".manifest.json"
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        del manifest["seed"]
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        proc = self._validate(pack)
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("seed", proc.stdout + proc.stderr)

    def test_a_file_that_matches_no_contract_asset_fails(self) -> None:
        pack = os.path.join(self.workdir, "stray")
        os.makedirs(pack, exist_ok=True)
        noisy_image(size=(256, 256)).save(os.path.join(pack, "not-in-contract.png"))
        proc = self._validate(pack)
        self.assertNotEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("not-in-contract.png", proc.stdout + proc.stderr)


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class CommittedContractIsCurrentTest(unittest.TestCase):
    """AC1/AC5: the generated JSON is committed, deterministic and self-checking."""

    def test_committed_contract_exists(self) -> None:
        self.assertTrue(os.path.exists(CONTRACT_JSON), "assets/asset-contract.json must be committed")

    def test_check_mode_passes_on_the_committed_file(self) -> None:
        proc = run_script("extract_asset_contract", "--check")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_output_is_deterministic(self) -> None:
        extractor = load_script("extract_asset_contract")
        first = extractor.render_contract(REPO_ROOT)
        second = extractor.render_contract(REPO_ROOT)
        self.assertEqual(first, second)

        keys = set()

        def walk(node) -> None:
            if isinstance(node, dict):
                for name, value in node.items():
                    keys.add(name)
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(json.loads(first))
        for token in ("generated_at", "created_at", "extracted_at", "timestamp"):
            self.assertNotIn(token, keys, "the contract must not carry wall-clock keys")


if __name__ == "__main__":
    unittest.main(verbosity=2)
