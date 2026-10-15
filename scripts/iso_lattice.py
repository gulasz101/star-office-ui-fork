#!/usr/bin/env python3
"""The ONE importable implementation of the declared isometric lattice (SOU-123).

``scripts/room_geometry.py`` declared the lattice and Pillow drew it; from here
on the ENGINE must see the same arithmetic. Phaser computes isometric tile ->
world in ``src/tilemaps/components/IsometricTileToWorldXY.js`` (verified at
phaserjs/phaser@02d8931b and vendored in this repo as
``frontend/vendor/phaser-3.80.1.min.js``, webpack module 14127)::

    var c = l + o/2*(t-e), d = u + (t+e)*(a/2);

i.e. with layer origin (l,u), base tile size (o,a) and tile indices (t,e)::

    x = l + (tileWidth  / 2) * (tileX - tileY)
    y = u + (tileY + tileX) * (tileHeight / 2)

``screen()`` below reproduces that operation EXACTLY - same order, same
half-tile factors - so a Tiled map authored here and a Phaser
``IsometricTilemapLayer`` agree to the bit. ``backend/tests/test_iso_room.py``
pins the identity; a mismatch there stops the renderer plan (ADR-067).

The inverse ``grid_of()`` mirrors Phaser's ``IsometricWorldToTileXY``
(module 96897: ``f = .5*((t-=c)/c + e/d); p = .5*(-t/c + e/d)``) without its
half-tile sampling offset: it maps a screen point to FLOAT tile coordinates,
which is what "which cell is this pixel on" needs.

TILE_W/TILE_H are the declared 96x55 box (55/96 ~= tan 30 degrees, the ADR-061
look decided by the operator). THE ORIGIN IS NEVER A LITERAL HERE: callers pass
it from the room profile / ``room_geometry.ROOM`` (SOU-123 deliverable 1).
"""

TILE_W = 96
TILE_H = 55

# The formula string the v2 descriptor carries, and the only screen formula in
# the v2 data path (tile coordinates everywhere else).
SCREEN_FORMULA = "screen(gx,gy) = (ox + (gx-gy)*tw/2, oy + (gx+gy)*th/2)"


def screen(gx, gy, origin, tile_w=TILE_W, tile_h=TILE_H):
    """Grid cell (gx, gy) -> screen point, exactly Phaser's
    IsometricTileToWorldXY: x = ox + (tile_w/2)*(gx-gy), y = oy + (gx+gy)*(tile_h/2).
    ``origin`` is the (ox, oy) of the floor's far corner, from the profile or
    ``room_geometry``."""
    ox, oy = origin
    return (ox + (tile_w / 2) * (gx - gy),
            oy + (gx + gy) * (tile_h / 2))


def grid_of(x, y, origin, tile_w=TILE_W, tile_h=TILE_H):
    """Screen point -> FLOAT grid coordinates; the exact inverse of ``screen``."""
    ox, oy = origin
    hw = tile_w / 2
    hh = tile_h / 2
    dx = (x - ox) / hw
    dy = (y - oy) / hh
    return (0.5 * (dx + dy), 0.5 * (dy - dx))
