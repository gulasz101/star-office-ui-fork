"""SOU-104: one geometry per asset, one authority, and ``load.image`` coverage.

SOU-98 shipped the contract extractor, but running it against the real tree
showed two defects (SOU-104):

1. ``frontend/game.js`` and the Phaser code inlined in ``frontend/index.html``
   slice the same sheets at different frame sizes, and the contract recorded the
   disagreement without resolving it. Only one of those files is reachable: Flask
   serves ``frontend/index.html`` at ``/``, Electron serves the derived snapshot
   ``frontend/electron-standalone.html`` at ``/electron-standalone``, and no
   shipped page loads ``game.js`` at all. The file dimensions are ground truth -
   a slicing that is not an exact multiple of them is provably wrong.
2. The extractor only recorded ``load.spritesheet`` calls, so an asset loaded
   with ``this.load.image(...)`` - ``office_bg_small``, the live background that
   is rendering right now - could not be expressed in the contract, and a valid
   replacement pack failed the gate as ``not_in_contract``.

These tests are the RED half of SOU-104: they fail on the tree as SOU-98 left it
and pass once the authority is recorded, the legacy declarations are reconciled,
unresolvable conflicts are fatal and single images are covered. CPU-only, no
network, no GPU, nothing committed by the tests themselves - synthetic sheets are
built with pillow in a temp dir.

Run from ``backend/``::

    PYTHONPATH=../.tooling/site python3 -m pytest tests/test_asset_contract_resolution.py -v
"""

from __future__ import annotations

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
INDEX_HTML = os.path.join(REPO_ROOT, "frontend", "index.html")
DOC = os.path.join(REPO_ROOT, "docs", "asset-contract.md")

AUTHORED = "frontend/index.html"
SNAPSHOT = "frontend/electron-standalone.html"
LEGACY = "frontend/game.js"

# The four assets SOU-104 lists as conflicted, plus the two that only disagreed
# about which file to load. Each row is (key, frame w, frame h, sheet w, sheet h,
# frames) taken from the file on disk, which is the ground truth AC2 names.
RESOLVED_GEOMETRY = {
    "error_bug": (220, 220, 1760, 1980, 72),
    "star_working": (300, 300, 2400, 1500, 40),
    "sync_anim": (256, 256, 1792, 1792, 49),
    "flowers": (128, 128, 512, 512, 16),
    "star_idle": (256, 256, 2048, 1536, 48),
    "coffee_machine": (230, 230, 2760, 1840, 96),
}

# Live ``load.image`` assets (Phaser key -> (logical asset, width, height)), the
# coverage gap AC5 names: backgrounds and single sprites, no frame grid anywhere.
LIVE_IMAGE_ASSETS = {
    "office_bg": ("office_bg_small", 1280, 720),
    "sofa_idle": ("sofa-idle-v3", 256, 256),
    "sofa_shadow": ("sofa-shadow-v1", 256, 256),
    "coffee_machine_shadow": ("coffee-machine-shadow-v1", 230, 230),
    "desk_v2": ("desk-v3", 276, 214),
}

REQUIRED_MANIFEST_FIELDS = (
    "model", "lora", "lora_strength", "prompt", "seed", "steps", "guidance",
    "source_file", "output_file", "output_dims", "palette_colors", "created_at",
)

try:
    from PIL import Image

    _PIL = True
    _PIL_ERROR = None
except ImportError as exc:  # pragma: no cover - only hit without pillow
    _PIL = None
    _PIL_ERROR = str(exc)


def load_script(name: str):
    path = os.path.join(SCRIPTS_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise AssertionError(f"missing implementation: scripts/{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _child_env() -> dict:
    """Absolute ``.tooling/site`` on PYTHONPATH for script subprocesses."""
    env = dict(os.environ)
    site = os.path.join(REPO_ROOT, ".tooling", "site")
    if os.path.isdir(site):
        existing = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
        env["PYTHONPATH"] = os.pathsep.join([site, *existing])
    return env


def run_script(name: str, *args, cwd: str = REPO_ROOT) -> subprocess.CompletedProcess:
    path = os.path.join(SCRIPTS_DIR, f"{name}.py")
    if not os.path.exists(path):
        raise AssertionError(f"missing implementation: scripts/{name}.py")
    return subprocess.run(
        [sys.executable, path, *[str(a) for a in args]],
        capture_output=True, text=True, cwd=cwd, env=_child_env(),
    )


def noisy_image(size=(480, 240), colors: int = 40, grid: int = 10):
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    palette = [((i * 37) % 256, (i * 73) % 256, (i * 11) % 256) for i in range(colors)]
    for by in range(grid):
        for bx in range(grid):
            red, green, blue = palette[(bx * grid + by) % colors]
            for y in range(size[1] * by // grid, size[1] * (by + 1) // grid):
                for x in range(size[0] * bx // grid, size[0] * (bx + 1) // grid):
                    image.putpixel((x, y), (red, green, blue, 255))
    return image


def provenance_args() -> list:
    return [
        "--model", "black-forest-labs/FLUX.2-klein-4B",
        "--lora", "Limbicnation/pixel-art-lora",
        "--lora-strength", "0.85",
        "--prompt", "cozy lodge background, no text",
        "--seed", "12345", "--steps", "24", "--guidance", "3.5",
    ]


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class RendererAuthorityTest(unittest.TestCase):
    """AC1: the contract names the renderer that is actually live."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = load_script("extract_asset_contract")
        cls.contract = cls.extractor.extract(REPO_ROOT)

    def test_contract_records_the_renderer_decision(self) -> None:
        renderer = self.contract.get("renderer")
        self.assertIsInstance(renderer, dict, "the contract must carry a renderer section")
        self.assertTrue(renderer.get("decision"), "renderer.decision must state the ruling")
        self.assertIn("index.html", renderer["decision"])

    def test_each_surface_names_the_file_it_serves(self) -> None:
        surfaces = surface_roles(self.contract["renderer"]["surfaces"])
        self.assertEqual(surfaces.get(AUTHORED), "authored")
        self.assertEqual(surfaces.get(SNAPSHOT), "derived_snapshot")
        self.assertEqual(surfaces.get(LEGACY), "legacy")
        by_route = {
            s.get("route"): s["file"]
            for s in self.contract["renderer"]["surfaces"] if s.get("route")
        }
        self.assertEqual(by_route.get("/"), AUTHORED)
        self.assertEqual(by_route.get("/electron-standalone"), SNAPSHOT)

    def test_no_shipped_page_loads_the_legacy_renderer(self) -> None:
        # Proof, not prose: no HTML file under frontend/ or desktop-pet/ references
        # game.js in a script src.
        hits = []
        for base in ("frontend", "desktop-pet", "electron-shell"):
            root = os.path.join(REPO_ROOT, base)
            for directory, _sub, names in os.walk(root):
                for name in names:
                    if not name.endswith((".html", ".js")):
                        continue
                    path = os.path.join(directory, name)
                    with open(path, "r", encoding="utf-8") as handle:
                        text = handle.read()
                    if 'src="game.js' in text or "src='/game.js" in text or \
                            '<script src="/static/game.js' in text or \
                            '<script src="game.js' in text:
                        hits.append(os.path.relpath(path, REPO_ROOT))
        self.assertEqual(hits, [], f"game.js is loaded by {hits}")

    def test_authored_source_is_authoritative_for_every_live_asset(self) -> None:
        offenders = {
            key: entry["expected_source"]
            for key, entry in self.contract["assets"].items()
            if entry["live"] and entry["expected_source"] != AUTHORED
        }
        self.assertEqual(offenders, {}, "the authored page must win the authority contest")

    def test_the_decision_is_written_up_for_people_too(self) -> None:
        self.assertTrue(os.path.exists(DOC), "docs/asset-contract.md must record the decision")
        with open(DOC, encoding="utf-8") as handle:
            text = handle.read()
        for key in RESOLVED_GEOMETRY:
            self.assertIn(key, text, f"{DOC} must rule on {key}")
        self.assertIn("office_bg_small", text)


def surface_roles(surfaces: list) -> dict:
    """``{file: role}`` from the contract's renderer surfaces list."""
    return {entry["file"]: entry.get("role", "") for entry in surfaces}


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class ReconciledGeometryTest(unittest.TestCase):
    """AC2: the geometry in the contract is the one the file on disk supports."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = load_script("extract_asset_contract")
        cls.contract = cls.extractor.extract(REPO_ROOT)

    def test_the_tree_reports_no_conflicts_no_errors_and_no_drift(self) -> None:
        self.assertEqual(self.contract["conflicts"], [], "AC2 resolves the conflicts")
        self.assertEqual(self.contract["errors"], [], "no source may slice a non-multiple")
        self.assertEqual(self.contract.get("drift", ["missing drift section"]), [])

    def test_each_conflicted_asset_carries_the_on_disk_geometry(self) -> None:
        for key, (fw, fh, width, height, total) in RESOLVED_GEOMETRY.items():
            with self.subTest(key=key):
                entry = self.contract["assets"][key]
                self.assertEqual(
                    (entry["expected_frame_width"], entry["expected_frame_height"]),
                    (fw, fh),
                )
                self.assertEqual((entry["expected_width"], entry["expected_height"]),
                                 (width, height))
                self.assertEqual(entry["frame_count"]["total"], total)
                self.assertEqual(
                    entry["frame_count"]["columns"] * entry["frame_count"]["rows"], total)

    def test_no_live_source_slices_past_its_sheet(self) -> None:
        for key, entry in self.contract["assets"].items():
            if not entry["live"] or not entry.get("frame_count"):
                continue
            for anim in entry["anims"]:
                if anim["end"] is None:
                    continue
                with self.subTest(key=key, source=anim["source"]):
                    self.assertLess(
                        anim["end"], entry["frame_count"]["total"],
                        "an authoritative animation must stay inside its grid")

    def test_every_live_slicing_is_an_exact_multiple_of_the_file(self) -> None:
        for key, entry in self.contract["assets"].items():
            if entry.get("kind") == "image" or not entry["live"]:
                continue
            for file_entry in entry["files"]:
                if not file_entry["exists"] or not file_entry["frame_width"]:
                    continue
                with self.subTest(key=key, variant=file_entry["variant"]):
                    self.assertEqual(
                        file_entry["width"] % file_entry["frame_width"], 0)
                    self.assertEqual(
                        file_entry["height"] % file_entry["frame_height"], 0)

    def test_the_live_page_no_longer_comments_a_geometry_it_does_not_use(self) -> None:
        with open(INDEX_HTML, encoding="utf-8") as handle:
            text = handle.read()
        for stale in ("180x180, 96 frames", "animation (96 frames)",
                      "Flower spritesheet (65x65, 16 frames)"):
            self.assertNotIn(stale, text, f"{stale!r} contradicts the code next to it")
        self.assertIn("220x220", text, "the error_bug load comment must state 220x220")


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class ConflictGateTest(unittest.TestCase):
    """AC3: a conflict that can reach a running page has to stop the build."""

    def setUp(self) -> None:
        self.extractor = load_script("extract_asset_contract")
        self.workdir = tempfile.mkdtemp(prefix="sou104-")
        self.addCleanup(shutil.rmtree, self.workdir, True)
        os.makedirs(os.path.join(self.workdir, "frontend"), exist_ok=True)
        for name in ("asset-positions.json", "asset-defaults.json"):
            with open(os.path.join(self.workdir, name), "w", encoding="utf-8") as handle:
                handle.write("{}\n")

    def _write(self, name: str, text: str) -> None:
        with open(os.path.join(self.workdir, name), "w", encoding="utf-8") as handle:
            handle.write(text)

    def _sheet(self, name: str, size=(480, 240)) -> None:
        noisy_image(size=size).save(os.path.join(self.workdir, "frontend", name))

    def _load(self, frame: str, extra: str = "") -> str:
        return (
            "<html><script>\n"
            f"  this.load.spritesheet('walk', '/static/walk.webp', {{ {frame} }});\n"
            f"  this.anims.create({{ frames: this.anims.generateFrameNumbers('walk',"
            f" {{ {extra} }}) }});\n"
            "</script></html>\n"
        )

    def _extract(self, *args):
        out = os.path.join(self.workdir, "contract.json")
        return run_script(
            "extract_asset_contract", "--repo-root", self.workdir, "--out", out, *args)

    def test_the_two_live_surfaces_disagreeing_is_fatal(self) -> None:
        # Both slicings divide the sheet exactly (120 -> 4x2, 60 -> 8x4), so the
        # only defect is that the two surfaces that can both reach a user
        # disagree about the frame size. Today that is recorded and exit 0.
        self._sheet("walk.webp")
        self._write(AUTHORED, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        self._write(SNAPSHOT, self._load("frameWidth: 60, frameHeight: 60",
                                         "start: 0, end: 3"))
        proc = self._extract()
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("CONFLICT", proc.stdout + proc.stderr)
        contract = json.loads(open(os.path.join(self.workdir, "contract.json"),
                                   encoding="utf-8").read())
        self.assertTrue([c for c in contract["conflicts"] if c["key"] == "walk"])

    def test_an_authoritative_slicing_that_is_not_a_multiple_is_fatal(self) -> None:
        self._sheet("walk.webp", size=(500, 240))
        self._write(AUTHORED, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        self._write(SNAPSHOT, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        proc = self._extract()
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("not_an_exact_multiple", proc.stdout + proc.stderr)

    def test_an_authoritative_animation_reading_past_the_sheet_is_fatal(self) -> None:
        self._sheet("walk.webp")
        self._write(AUTHORED, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 99"))
        self._write(SNAPSHOT, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 99"))
        proc = self._extract()
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("animation_reads_past_the_sheet", proc.stdout + proc.stderr)

    def test_a_legacy_only_disagreement_is_drift_and_not_fatal(self) -> None:
        self._sheet("walk.webp")
        self._write(AUTHORED, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        self._write(SNAPSHOT, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        self._write(LEGACY, self._load("frameWidth: 180, frameHeight: 180",
                                       "start: 0, end: 99"))
        proc = self._extract()
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        contract = json.loads(open(os.path.join(self.workdir, "contract.json"),
                                   encoding="utf-8").read())
        self.assertEqual(contract["conflicts"], [],
                         "dead code cannot conflict with a live renderer")
        self.assertEqual(contract["errors"], [])
        kinds = {d["kind"] for d in contract["drift"]}
        self.assertEqual(kinds, {"frame_size", "animation_reads_past_the_sheet"})
        for finding in contract["drift"]:
            self.assertFalse(finding["fatal"])
            self.assertEqual(finding["source"], LEGACY)
            self.assertEqual(finding["authoritative"]["frame_width"], 120)
        self.assertIn("DRIFT", proc.stdout + proc.stderr)

    def test_strict_mode_makes_legacy_drift_fatal(self) -> None:
        self._sheet("walk.webp")
        self._write(AUTHORED, self._load("frameWidth: 120, frameHeight: 120",
                                         "start: 0, end: 3"))
        self._write(LEGACY, self._load("frameWidth: 180, frameHeight: 180"))
        proc = self._extract("--strict")
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)

    def test_the_reconciled_repo_passes_strict(self) -> None:
        proc = run_script("extract_asset_contract", "--strict")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("0 conflict", proc.stdout + proc.stderr)


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class LoadImageAssetTest(unittest.TestCase):
    """AC5: ``load.image`` assets are in the contract, with no invented frame grid."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.extractor = load_script("extract_asset_contract")
        cls.contract = cls.extractor.extract(REPO_ROOT)
        cls.assets = cls.contract["assets"]

    def test_live_single_image_assets_are_recorded(self) -> None:
        for key, (logical, width, height) in LIVE_IMAGE_ASSETS.items():
            with self.subTest(key=key):
                entry = self.assets[key]
                self.assertEqual(entry["kind"], "image")
                self.assertEqual(entry["logical_asset"], logical)
                self.assertEqual((entry["expected_width"], entry["expected_height"]),
                                 (width, height))
                self.assertTrue(entry["live"])

    def test_image_assets_get_no_frame_grid_and_no_grid_error(self) -> None:
        for key in LIVE_IMAGE_ASSETS:
            with self.subTest(key=key):
                entry = self.assets[key]
                self.assertIsNone(entry["expected_frame_width"])
                self.assertIsNone(entry["expected_frame_height"])
                self.assertIsNone(entry["frame_count"])
                self.assertEqual(entry["errors"], [])

    def test_the_legacy_only_single_image_is_recorded_as_not_live(self) -> None:
        entry = self.assets["desk"]
        self.assertEqual(entry["kind"], "image")
        self.assertFalse(entry["live"])
        self.assertEqual(entry["scope"], "legacy_only")
        self.assertTrue(any(not f["exists"] for f in entry["files"]))

    def test_summary_counts_image_and_spritesheet_assets_apart(self) -> None:
        summary = self.contract["summary"]
        self.assertEqual(summary["image_assets"], len(LIVE_IMAGE_ASSETS) + 1)
        self.assertEqual(
            summary["spritesheet_assets"],
            sum(1 for a in self.assets.values() if a.get("kind") == "spritesheet"),
        )

    def test_runtime_swapped_declarations_are_counted_not_invented(self) -> None:
        # index.html also calls scene.load.image(newKey, url) when an operator
        # swaps an asset at runtime: no literal key, so it must show up as a
        # counted dynamic declaration instead of silently vanishing.
        by_file = {s["file"]: s for s in self.contract["sources"]}
        self.assertGreaterEqual(by_file[AUTHORED]["dynamic_load_calls"], 1)
        self.assertGreaterEqual(by_file[SNAPSHOT]["dynamic_load_calls"], 1)

    def test_validator_accepts_a_background_pack_keyed_by_its_logical_asset(self) -> None:
        """AC5/AC6: manifest asset_key office_bg_small names the file, not the Phaser key."""
        workdir = tempfile.mkdtemp(prefix="sou104-pack-")
        self.addCleanup(shutil.rmtree, workdir, True)
        pack = os.path.join(workdir, "generated", "rooms", "andrzej")
        os.makedirs(pack)
        target = os.path.join(pack, "background.png")
        noisy_image(size=(1280, 720)).save(target)
        manifest = {field: None for field in REQUIRED_MANIFEST_FIELDS}
        manifest.update({
            "model": "black-forest-labs/FLUX.2-klein-4B", "lora": None,
            "lora_strength": 0.0, "prompt": "cozy lodge interior", "seed": 11,
            "steps": 4, "guidance": 1.0, "source_file": "background.raw.png",
            "output_file": "background.png", "output_dims": [1280, 720],
            "palette_colors": 24, "created_at": "2026-10-15T10:29:23+00:00",
        })
        manifest["asset_key"] = "office_bg_small"
        with open(target + ".manifest.json", "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        proc = run_script(
            "validate_generated_assets",
            "--generated-dir", os.path.join(workdir, "generated"),
            "--contract", CONTRACT_JSON,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertNotIn("not_in_contract", proc.stdout + proc.stderr)

    def test_validator_still_rejects_a_wrongly_sized_background(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou104-badpack-")
        self.addCleanup(shutil.rmtree, workdir, True)
        pack = os.path.join(workdir, "generated", "rooms", "andrzej")
        os.makedirs(pack)
        target = os.path.join(pack, "office_bg_small.png")
        noisy_image(size=(1281, 720)).save(target)
        with open(target + ".manifest.json", "w", encoding="utf-8") as handle:
            json.dump({
                "model": "black-forest-labs/FLUX.2-klein-4B", "lora": None,
                "lora_strength": 0.0, "prompt": "p", "seed": 1, "steps": 4,
                "guidance": 1.0, "source_file": "x.png",
                "output_file": "office_bg_small.png", "output_dims": [1280, 720],
                "palette_colors": 24, "created_at": "2026-10-15T10:29:23+00:00",
                "asset_key": "office_bg_small",
            }, handle)
        proc = run_script(
            "validate_generated_assets",
            "--generated-dir", os.path.join(workdir, "generated"),
            "--contract", CONTRACT_JSON,
        )
        self.assertEqual(proc.returncode, 1, msg=proc.stdout + proc.stderr)
        self.assertIn("dimensions", proc.stdout + proc.stderr)

    def test_pixelize_writes_a_frame_less_asset_from_the_contract(self) -> None:
        workdir = tempfile.mkdtemp(prefix="sou104-image-")
        self.addCleanup(shutil.rmtree, workdir, True)
        raw = os.path.join(workdir, "raw.png")
        noisy_image(size=(512, 512)).save(raw)
        out = os.path.join(workdir, "office_bg_small.png")
        proc = run_script(
            "pixelize_asset", "--input", raw, "--output", out,
            "--contract", CONTRACT_JSON, "--asset", "office_bg", *provenance_args(),
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        with Image.open(out) as image:
            self.assertEqual(image.size, (1280, 720))
        manifest = json.load(open(out + ".manifest.json", encoding="utf-8"))
        self.assertEqual(manifest["output_dims"], [1280, 720])
        self.assertEqual(manifest["frame_size"], [1280, 720])


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class RealGeneratedTreeTest(unittest.TestCase):
    """AC6: the actual replacement pack on sou-flux-art-pipeline must pass.

    The pack is not on this branch (it lives on ``sou-flux-art-pipeline``), so the
    proof runs against a checkout handed to the test by the environment. Without
    that checkout the test skips rather than passing silently.
    """

    TREE = os.environ.get(
        "SOU104_GENERATED_TREE",
        os.path.join(REPO_ROOT, ".tooling", "SOU-104", "flux-tree"),
    )

    def setUp(self) -> None:
        generated = os.path.join(self.TREE, "assets", "generated")
        if not os.path.isdir(generated):
            self.skipTest(f"no generated tree at {generated} (checkout "
                          "sou-flux-art-pipeline into .tooling/SOU-104/flux-tree)")
        self.generated = generated

    def test_the_background_pack_resolves_and_passes(self) -> None:
        proc = run_script("validate_generated_assets",
                          "--generated-dir", self.generated)
        self.assertNotIn("not_in_contract", proc.stdout + proc.stderr,
                         msg=proc.stdout + proc.stderr)
        self.assertNotIn("rooms/andrzej/background.png", proc.stdout + proc.stderr,
                         "the background pack must not be a finding")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_office_bg_small_is_in_the_contract_it_validated_against(self) -> None:
        contract = json.load(open(CONTRACT_JSON, encoding="utf-8"))
        entry = contract["assets"].get("office_bg")
        self.assertIsNotNone(entry, "the committed contract must cover the background")
        self.assertEqual((entry["expected_width"], entry["expected_height"]),
                         (1280, 720))


@unittest.skipUnless(_PIL, f"pillow missing: {_PIL_ERROR}")
class CommittedContractCurrentTest(unittest.TestCase):
    """AC7: the gate itself must be green on what is committed."""

    def test_check_mode_passes_on_the_committed_contract(self) -> None:
        proc = run_script("extract_asset_contract", "--check")
        self.assertEqual(proc.returncode, 0, msg=proc.stdout + proc.stderr)

    def test_contract_sections_the_resolution_needs(self) -> None:
        contract = json.load(open(CONTRACT_JSON, encoding="utf-8"))
        for section in ("renderer", "assets", "conflicts", "drift", "errors", "summary"):
            self.assertIn(section, contract, f"committed contract lost {section}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
