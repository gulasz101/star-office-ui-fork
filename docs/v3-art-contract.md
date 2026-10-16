# v3 art contract — office-01, top-down 3/4 (SOU-158)

Scope: what the v3 art set is, which files fix its sizes, how it is generated and
regenerated, and what the gate actually checks. Companion to `docs/asset-contract.md`
(which covers the v1/v2 renderer contract and is about the renderer that is being
replaced). v2's isometric art route (SOU-122, ADR-064/065) is closed.

## 1. The camera changed, nothing else

v3 renders **top-down 3/4** (Pokemon / Stardew interiors): tile 32×32, office module
16×12 tiles, character frames 32×48, 4 facings. The model, the licence rules and the
post-process pipeline are the same as v2: `black-forest-labs/FLUX.2-klein-4B`
(Apache-2.0) on the M1 Max via MPS, **no LoRA** (it collapses to black in diffusers —
ADR-060), text-only prompts (never img2img, never trained on Star-Office or LimeZu
pixels), no marks or text in the art.

What does NOT carry over is the v2 alignment gate: `scripts/check_prop_alignment.py`
tests base slopes against a 30-degree isometric lattice. In top-down 3/4 a prop's
footprint is an axis-aligned rectangle, so a slope gate fails correct art and proves
nothing. Do not "restore" it here.

## 2. The consumer fixes the sizes — do not restate them

| Source | Fixes |
|---|---|
| `frontend/v3/maps/office-01.json`, object layer `props` | each prop's footprint rect **and its key** (desk, chair, rack, bookshelf, coffee, rug, couch, bass, amp, cat, van, spool, crate) |
| `frontend/v3/src/placeholders.js` (`PROP_STYLE`) | the lift per prop: the renderer draws `footprint_w × (footprint_h + lift)` |
| `frontend/v3/src/office.js` | anchor: props at the footprint's **bottom-centre** (`setOrigin(0.5, 1)`), the character at its feet |
| `frontend/v3/src/placeholders.js` (`SHEET_COLS`, `FACINGS`, `FRAME`) | the character sheet: 8 columns × 4 rows of 32×48, rows `down, left, right, up`, columns `idle, walk 1-4, work(typing) 1-2, sleep` → 256×192 |
| `frontend/v3/maps/office-01.json`, `tilesets[0].tiles` | the tileset slot order (`floor_wood_a, floor_wood_b, garage_concrete, wall_cap, wall_face, wall_base, door_mat, garage_stripe`) |

Because the art is built to those numbers, loading it is a **source swap, not a code
change**: point the tileset's `image` at `../assets/office-01/tiles.png`, load the 13
prop PNGs by the map's own keys, and hand the character sheet to the same
`ensureCharacterSheet` slot. Collision, y-sorting and zone geometry are untouched —
it is one art-sized texture per prop, and the width is exactly the footprint width.

Sizes therefore follow, and a prop may be **taller** than its declared box: the
silhouette overhangs upward (the van's footprint is 96×128, its PNG 96×168 because
40px overhangs). Height is free, width is the contract.

## 3. One palette, declared once

`assets/generated/v3/office-01/palette.json` declares 32 colours. It is the v2 room
palette the owner signed off (`#c1a46b #e2d3b4 #ac9968 #ffea99 #a3be8c #88c0d0` + shell
blues) extended with the wood / beige / amber / CRT ramps the new props need. Every
asset — tiles, props, the sheet — is quantised to exactly those colours by
nearest-RGB mapping with alpha binarised at 96, and RGB is zeroed wherever alpha is 0
(the old pipeline shipped transparent pixels still carrying magenta: invisible until
something blends them). The **character sheet** is quantised through a documented
11-colour subset (`character_ramp` in the sheet manifest) so all four facings share one
outfit by construction — four independent generations returned four different
characters, which is the most visible "different game" defect there is.

## 4. Two passes, and why

`scripts/v3/gen_office01_art.py`:

* **`generate`** — FLUX pass. Needs torch + MPS + the cached weights (the `sou-flux`
  venv). Writes `<id>.raw.png` (the untouched model output) and `<id>.prompt.txt`.
  Generation runs at the **target aspect ratio**, not square, so the fit is a scale and
  never a crop.
* **`assemble`** — CPU-only, seconds, and a **pure function of the raws**: border-seeded
  magenta key + de-fringe (blue-vs-green discriminator, so warm browns survive) →
  content bbox → uniform NEAREST fit → palette quantise → per-file manifest → character
  sheet → tileset sheet → contact sheets. A layout or palette change costs no GPU time.
* **`clean`** — removes derived output only. The raws cost ~36s each on MPS and are the
  only thing that makes `assemble` reproducible; `rm -rf` on the set directory threw
  away 22 of them once and cost a second full GPU pass.

`scripts/v3/publish_office01_art.py` copies the set byte-identically into
`frontend/v3/assets/office-01/` (the served tree) and writes `index.json` with a sha256
per file. The browser can only load what `frontend/` serves; `assets/generated/**` is the
record and is not served.

## 5. What the gate checks (`scripts/v3/check_v3_assets.py`)

CPU-only, read-only, exit 1 on any finding, and it prints the per-asset evidence on PASS
as well as FAIL:

* **size** — width equals the footprint width exactly (the renderer draws the prop at
  that width), height ≥ declared;
* **anchor** — the content reaches the canvas's bottom row, because that edge is what
  the renderer anchors; an object stopping early would hover;
* **alpha** — real alpha (both fully transparent and fully opaque pixels), no partial
  alpha;
* **magenta** — no strong magenta and no wide-fringe pixel anywhere at any opacity;
* **palette** — every opaque pixel is a declared colour (checked as set membership);
* **sheet** — 256×192, all 32 frames non-empty, and **every animated frame differs from
  its row's idle frame** (this is what caught typing frames that were identical to idle
  in the narrow side-profile rows);
* **tileset** — 8 slots of 32px in the order `office-01.json` itself declares, because
  the map is the consumer and its order is the authority;
* **seam** — for each tile, the wrap-around edge step against the mean interior step.
  A generated texture is NOT seamless: the floor measured 5.4× before the crop search
  and 2.9× after. Seamlessness is a measurement, never an adjective.

## 6. Known limitations (state them, do not bury them)

* **Walk and typing columns are derived, not drawn.** Idle and sleep are direct model
  outputs; the other columns are deterministic whole-pixel shifts of the same base
  (leg halves ±2px with a bob; arm bands ±1px). The sheet manifest carries a per-frame
  `derivation` map. A hand-animated cycle for the down row is the obvious next step.
* **Floor tile seams.** `floor` wraps at 1.38×/2.94× and `floor_b` at 2.56×/4.39× the
  interior step. The map alternates the two, which masks it, but a hand-drawn floor tile
  is the honest fix if a seam shows on screen.
* **The side-profile rows are narrow** (11px inside a 32px frame) because the model drew
  thin profiles; the sheet is consistent, but a profile that reads flat at 1:1 is a
  fair criticism.
