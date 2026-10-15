# Asset contract: renderer authority and conflict rules

Scope: how `assets/asset-contract.json` is produced, which source file wins when
two of them describe the same asset differently, and what the tooling does about
it. Written up for SOU-104; the extraction tool itself is
`scripts/extract_asset_contract.py` (built in SOU-98).

Generated art arrives as raw RGBA and is dropped into a renderer that slices
every sheet with an explicit `frameWidth` / `frameHeight`, positions decorations
by filename in `asset-positions.json` / `asset-defaults.json`, and has no single
native sprite size. The contract is the one machine-checkable answer to "what
size must this pack be", so a pack that would walk a sprite off the tile is
rejected before it ships.

## 1. The ruling: which file is the renderer

`frontend/` holds three files that declare asset loads. They are not equally
authoritative, and pretending they were is what produced eight fake "conflicts".

| File | Role | Authority | Why |
| --- | --- | --- | --- |
| `frontend/index.html` | `authored` | **yes** | The page itself is authored here. Flask serves it for the browser at `/` (`backend/app.py` renders `FRONTEND_INDEX_FILE`), and the inline Phaser code is what runs. |
| `frontend/electron-standalone.html` | `derived_snapshot` | **yes, must agree** | Electron loads `/electron-standalone`. The file is a copy of the authored page; `backend/app.py` (`ensure_electron_standalone_snapshot`) and `electron-shell/main.js` regenerate it from `index.html` when it is missing. It can drift from its parent only by a human editing it directly. |
| `frontend/game.js` | `legacy` | **no** | No shipped page loads it: the `/` route serves `index.html`, which loads no `game.js`. Mainline says the same about this file ("`frontend/game.js` is a stale copy and editing it does nothing"). It is kept as a readable reference for the older standalone layout and is parsed for drift only. |

Consequences, in order of preference when something has to change:

1. Change the art pack to the geometry the authored page declares.
2. Change the authored page (`index.html`) and regenerate the snapshot, so the
   two live sources keep matching.
3. Align `game.js` to whatever the live page does. Never the reverse: editing
   `game.js` alone cannot fix a running page, and a "fix" that only moves
   `game.js` is drift, not resolution.

The contract stores this in a `renderer` section (`single_source_of_truth`,
`authoritative_sources`, `non_authoritative_sources`, a `surfaces` entry per
parsed file, and the decision text) so a machine can re-derive the same answer.

## 2. Conflict versus drift

The extractor splits its findings by who disagrees, because the blast radius is
different:

| Kind | Raised when | Effect |
| --- | --- | --- |
| `conflict` | Two live sources disagree (authored page against its own snapshot); a load kind changed for one key (`image` against `spritesheet`); an authoritative slicing is not an exact multiple of the file on disk; an authoritative animation reads frames past the end of its sheet. | **Always fatal**, exit 1. A page can be mis-sliced today. |
| `error` | A single source declares geometry that the file on disk cannot support (frame size that does not divide the sheet, a sheet size that is unknown because the file is missing while other sources of the same asset exist). | **Fatal**, exit 1. |
| `drift` | A non-authoritative (legacy) source disagrees with the resolved authority: different frame size, different file, or an animation range past the sheet that only the legacy file plays. | Warning, exit 0; **fatal under `--strict`**. Dead code cannot mis-slice a page, so it must not block a build, but the disagreement is recorded rather than hidden. |

`--strict` is the CI posture: run it and the tree has to be honest everywhere,
including `game.js`. A resolved tree reports `0 conflict(s), 0 fatal violation(s),
0 drift note(s)`.

Each finding carries `{key, kind, source, message, resolved_by, authoritative}`.
`resolved_by` names the file whose value won, so the message reads as a decision
and not as a mystery.

## 3. Rulings on the assets that used to conflict

Before SOU-104 the extractor reported 8 conflicts and 5 warnings and still
exited 0; the geometry below is what both live sources and the files on disk now
agree on. `frame` is the slicing the renderer uses, `sheet` is the measured file
size, `grid` is the derived frame layout.

| Key | Logical asset | Frame | Sheet | Grid | Animation | Legacy value that had to move |
| --- | --- | --- | --- | --- | --- | --- |
| `error_bug` | `error-bug-spritesheet-grid` | 220 x 220 | 1760 x 1980 | 8 x 9 = 72 | `error_bug` 0..71 | `game.js` sliced 180 x 180 and played 0..95, i.e. frames 72..95 do not exist |
| `star_working` | `star-working-spritesheet-grid` | 300 x 300 | 2400 x 1500 | 8 x 5 = 40 | `star_working` 0..37 | `game.js` sliced 230 x 144 (not a multiple of the sheet) and played 0..191 |
| `sync_anim` | `sync-animation-v3-grid` | 256 x 256 | 1792 x 1792 | 7 x 7 = 49 | `sync_anim` from 1 to `frameTotal` - 2 | `game.js` loaded `sync-animation-spritesheet-grid` and played 1..52 |
| `flowers` | `flowers-bloom-v2` | 128 x 128 | 512 x 512 | 4 x 4 = 16 | frame 0 / short loop | `game.js` loaded `flowers-spritesheet` at 65 x 65; the authored page sizes it through `FLOWERS_FRAME_W` / `FLOWERS_FRAME_H`, flagged `dynamic_frame_size` |
| `star_idle` | `star-idle-v5` | 256 x 256 | 2048 x 1536 | 8 x 6 = 48 | `star_idle` 0..`frameTotal` - 1 | `game.js` loaded `star-idle-spritesheet` at 128 x 128 and played 0..29 |
| `coffee_machine` | `coffee-machine-v3-grid` | 230 x 230 | 2760 x 1840 | 12 x 8 = 96 | `coffee_machine` 0..`frameTotal` - 2 | `game.js` loaded `coffee-machine-spritesheet` and played 0..95 |

Comment-only fixes came with them, because a stale comment in the authored page
is the seed of the next wrong pack: the `error_bug` load and animation comments
said "180x180, 96 frames" and now say 220 x 220, 8 x 9 = 72 frames, and the
flower comment said "65x65, 16 frames" and now says 128 x 128.

## 4. Single-image (`load.image`) assets

Backgrounds, desks and shadows are loaded with `this.load.image(key, path)`. They
were previously invisible to the contract, which left exactly the assets a
generated pack most often replaces without any size rule. They are recorded with
`kind: "image"`, the whole-file `expected_width` / `expected_height`, and
`expected_frame_width` / `expected_frame_height` / `frame_count` deliberately
`null`: there is no frame grid, so inventing one would be a new kind of wrong.

| Key | Logical asset | Size |
| --- | --- | --- |
| `office_bg` | `office_bg_small` (`frontend/office_bg_small.webp`) | 1280 x 720 |
| `sofa_idle` | `sofa-idle-v3` | 256 x 256 |
| `sofa_shadow` | `sofa-shadow-v1` | 256 x 256 |
| `coffee_machine_shadow` | `coffee-machine-shadow-v1` | 230 x 230 |
| `desk_v2` | `desk-v3` | 276 x 214 |

`office_bg` is the case to remember: the Phaser key is `office_bg`, the file and
the generator's `asset_key` are `office_bg_small`. A pack manifest may name
either; both resolve to the same contract entry.

Three keys are loaded only by `game.js` (`sofa_busy`, `star_researching`, `desk`).
They stay in the contract with `scope: "legacy_only"` and `live: false` so a
reader can see they are not gone from the repository, only from the shipped page,
and their missing files are reported per file (`file_not_found`) rather than as a
fatal error.

## 5. Working with the contract

```sh
python3 scripts/extract_asset_contract.py              # regenerate the JSON
python3 scripts/extract_asset_contract.py --check      # committed JSON is stale? exit 1
python3 scripts/extract_asset_contract.py --strict     # drift is fatal too
python3 scripts/validate_generated_assets.py --generated-dir assets/generated
python3 scripts/pixelize_asset.py --input raw.png --output out.webp \
    --contract assets/asset-contract.json --asset office_bg
```

The extraction is deterministic (no timestamps in the output), so `--check` is a
real gate: change a `load.*` call or a file on disk and the committed contract is
stale until regenerated. Regenerate it in the same commit as the change.

* `validate_generated_assets.py` checks a pack before it is swapped in: a file
  that names no contract asset is `not_in_contract`, a wrong overall size is
  `dimensions`, a slicing that does not divide the sheet or does not match the
  derived frame grid is `grid`, and missing provenance is a warning. A manifest
  `asset_key` that names the logical asset instead of the Phaser key resolves
  through the contract's name index; an image asset is treated as a 1 x 1 grid.
* `pixelize_asset.py` refuses a frame size that differs from the contract, so the
  only way to produce a mis-sliced sheet is to bypass the tool. For a
  `load.image` asset the whole file is the frame, and the contract size is enough
  to run it.
* Adding an asset means adding the `load.*` call to `index.html`, regenerating
  the snapshot from it, regenerating the contract, then drawing to that geometry.

Tests that hold this in place live in `backend/tests/test_asset_contract.py` and
`backend/tests/test_asset_contract_resolution.py`.
