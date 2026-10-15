# Dropped assets

Art removed from what the office serves. Files are kept here rather than deleted so the change is
reversible and so a `git pull` from upstream does not silently reinstate them into the served
directory.

## `posters-spritesheet.webp` — dropped 2026-10-15 (operator decision)

Was loaded as the `posters` spritesheet (640×1280, 32 frames of 160×160) and placed as one random
frame on the wall at (252, 66), changing on click.

**Why dropped:** a frame-by-frame review of the contact sheet showed **32 of 32 frames are real
film/TV posters with readable titles** — FRIENDS, Star Wars, The Matrix, Jurassic Park, The
Godfather, Pulp Fiction, Forrest Gump, The Shawshank Redemption, The Dark Knight, Inception, The
Lord of the Rings, Avatar, Back to the Future, E.T., Jaws, Gladiator, Indiana Jones, The Lion King,
Fight Club, Harry Potter. Zero generic frames. It also carries the "FRIENDS" sign that prompted the
"no text, no logos in generated art" invariant.

Replacing it would have meant generating ~20 original poster designs (the second half of the sheet
duplicates the first). The operator chose to **drop the feature instead** — it is pure decoration,
nothing he wants to maintain, and dropping removes the entire third-party-IP surface at zero cost.

**What was changed:**
- `frontend/index.html`: the spritesheet load is commented out with the reason, and the sprite
  placement block is removed. Nothing may place a `'posters'` sprite — the texture is not loaded, so
  a leftover reference would render nothing or throw.
- the file itself moved here, so `GET /static/posters-spritesheet.webp` now returns 404.

**Not changed, deliberately:**
- `frontend/game.js` and `electron-shell/` still reference the sheet. `game.js` is dead code that
  nothing loads (SOU-104), and the electron-shell standalone build is a separate artifact; neither
  is on the served path. Left alone rather than edited blind.
- `frontend/index.html` still has inert asset-metadata entries naming the sheet (an editor lookup
  table). Harmless with the texture unloaded, and removing them risks a UI that expects the key.

**Note on the public repo:** the sheet remains in git history, so the exposure is not fully
retroactive — rewriting history is not a call to make unilaterally. This change removes it from what
the running app serves.
