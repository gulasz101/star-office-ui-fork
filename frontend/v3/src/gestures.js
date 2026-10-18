// Touch and framing maths (SOU-299), kept free of Phaser so the Node tests import it.

export const distance = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
export const midpoint = (a, b) => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

// two fingers apart -> the zoom the pinch started at, scaled by how far they spread
export function pinchZoom(startZoom, startDist, dist, min, max) {
  if (!(startDist > 0) || !(dist > 0)) return clamp(startZoom, min, max);
  return clamp(startZoom * (dist / startDist), min, max);
}

// the smallest zoom at which the world still covers the whole screen: below it the view
// would show the void past the forest's edge
export function coverZoom(view, world) {
  if (!(world.width > 0) || !(world.height > 0)) return 0;
  return Math.max(view.width / world.width, view.height / world.height);
}

// a phone: the short side is small, whatever the orientation
export const isPhone = (view) => Math.min(view.width, view.height) < 600;

// screen px at the bottom the HUD covers (roster strip, controls) and the overview avoids
export const reservedBottom = (view) => (isPhone(view) ? 64 : 0);

/**
 * Overview zoom: fit the building, but never so far out that the forest stops covering the
 * screen, and in portrait on a phone never so far out that rooms turn into specks: the
 * building's height fills at least `portraitShare` of the screen, the rest is a pan away.
 */
export function overviewZoom({ view, building, world, fit, portraitShare = 0.34 }) {
  let z = Math.max(fit, coverZoom(view, world));
  if (isPhone(view) && view.height > view.width && building.height > 0) {
    z = Math.max(z, (view.height * portraitShare) / building.height);
  }
  return z;
}
