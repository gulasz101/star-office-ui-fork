// Loading screen (SOU-300). The markup and styles live in index.html, so the loader is on
// screen from the very first paint; this module only feeds it Phaser's progress and takes it
// away once the Lodge has drawn its first frame.

// what the label says at a given load fraction (0..1), or before any progress is known
export function loaderText(fraction) {
  if (!Number.isFinite(fraction)) return 'lighting the lodge…';
  if (fraction >= 1) return 'opening the doors…';
  return `lighting the lodge… ${Math.floor(Math.max(0, fraction) * 100)}%`;
}

const root = () => (typeof document === 'undefined' ? null : document.getElementById('loader'));

export function loaderProgress(fraction) {
  const el = root();
  if (!el) return;
  el.classList.remove('indeterminate');
  const fill = el.querySelector('.loader-fill');
  const label = el.querySelector('.loader-label');
  if (fill) fill.style.width = `${Math.round(Math.min(1, Math.max(0, fraction)) * 100)}%`;
  if (label) label.textContent = loaderText(fraction);
}

export function loaderDone() {
  const el = root();
  if (!el || el.classList.contains('done')) return;
  clearTimeout(window.__loaderSlow);
  el.classList.add('done');
  // gone from the DOM after the fade, so nothing sits over the canvas
  setTimeout(() => el.remove(), 450);
}
