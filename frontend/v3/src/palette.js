// Per-agent hoodie colours (SOU-202): a hue swap of the generated character's saturated
// orange pixels. Skin (low saturation), hair, jeans and the outline fall outside the band and
// stay untouched. Pure; the renderer applies it to a canvas copy of the sheet.

const ORANGE = { hMin: 15, hMax: 40, sMin: 0.5 };

export function rgbToHsv(r, g, b) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const d = max - Math.min(r, g, b);
  let h = 0;
  if (d) {
    if (max === r) h = ((g - b) / d) % 6;
    else if (max === g) h = (b - r) / d + 2;
    else h = (r - g) / d + 4;
    h *= 60;
    if (h < 0) h += 360;
  }
  return [h, max ? d / max : 0, max];
}

export function hsvToRgb(h, s, v) {
  const c = v * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = v - c;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x]
    : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
}

/**
 * Recolour RGBA pixels in place. target = { h, s = 1, v = 1 }: new hue, and multipliers for
 * saturation and value (navy needs a darker value than orange has).
 */
export function swapHoodie(data, target) {
  if (!target) return data;
  const { h, s = 1, v = 1 } = target;
  for (let i = 0; i < data.length; i += 4) {
    if (data[i + 3] < 8) continue;
    const [ph, ps, pv] = rgbToHsv(data[i], data[i + 1], data[i + 2]);
    if (ph < ORANGE.hMin || ph > ORANGE.hMax || ps < ORANGE.sMin) continue;
    // keep the shading: offset each shade from the band's centre hue
    const [r, g, b] = hsvToRgb((h + (ph - 28) + 360) % 360, Math.min(1, ps * s), Math.min(1, pv * v));
    data[i] = r; data[i + 1] = g; data[i + 2] = b;
  }
  return data;
}
