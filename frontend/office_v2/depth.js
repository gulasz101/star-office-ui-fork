/**
 * depth.js — the ONE depth rule of the isometric renderer v2 (SOU-124 / AC5).
 *
 * Painter's algorithm on the iso lattice: a cell's distance from the camera
 * grows with its diagonal gx+gy (+gx is southeast, +gy southwest - see the
 * SOU-123 axis compass). So depth must increase with gx+gy, with a
 * deterministic tie-break by gx inside one diagonal. This module is the ONLY
 * place in the v2 renderer where a depth number exists; the scene may not
 * carry constants (the ~10 ad-hoc setDepth() numbers of v1 are exactly what
 * this replaces). Unit-tested by frontend/tests/depth.test.mjs.
 *
 * rowLength is the grid width (from the map, not a literal at the call site):
 * for any two cells, (gx+gy)*rowLength + gx strictly increases with gx+gy as
 * long as 0 <= gx < rowLength.
 */

/**
 * Lattice depth for a prop (or character) standing on grid cell (gx, gy).
 * Higher = nearer the camera = drawn later.
 */
export function depthKey(gx, gy, rowLength) {
  return (gx + gy) * rowLength + gx;
}

/**
 * Fixed world ranks. Both are below every lattice cell (depthKey >= 0 for
 * every in-grid cell), and the wall plane is behind the floor tilemap.
 */
export const WALL_DEPTH = -2;
export const FLOOR_DEPTH = -1;
