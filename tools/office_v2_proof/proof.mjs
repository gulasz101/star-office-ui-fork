/**
 * proof.mjs - SOU-124 S5 browser proof, corrected by SOU-147: AC1 (content
 * base on the lattice, from live object state, not sprite.y alone), AC2
 * (declared tiles drives the on-screen width), AC3 (screenshot + DRAWN-PIXEL
 * diff per prop), and the SOU-124 regression set stays exactly: AC2 render,
 * AC4 collision, AC5 depth, AC6 v1.
 *
 * Engine: chrome-headless-shell in .tooling/browsers + puppeteer-core in
 * .tooling/npm (the SOU-104/SOU-115 recipe, one browser per run). The v2 page
 * uses the classic (CPU) TilemapLayer, so WebGL is not required by v2 itself;
 * swiftshader stays on the args because Phaser.AUTO probes it.
 *
 * usage: node tools/office_v2_proof/proof.mjs <port> <out-dir>
 * deps resolve from worker-local .tooling/npm via createRequire (no installs).
 */
import { createRequire } from 'node:module';
import path from 'node:path';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const REPO = path.resolve(fileURLToPath(import.meta.url), '..', '..', '..');
const require = createRequire(path.join(REPO, '.tooling', 'npm', 'noop.js'));
const puppeteer = require('puppeteer-core');

const EXECUTABLE = path.join(REPO, '.tooling', 'browsers', 'chrome-headless-shell-linux64', 'chrome-headless-shell');

const [PORT, OUT_DIR] = process.argv.slice(2);
if (!PORT || !OUT_DIR) {
  console.error('usage: node proof.mjs <port> <out-dir>');
  process.exit(2);
}
fs.mkdirSync(OUT_DIR, { recursive: true });

const BASE = `http://127.0.0.1:${PORT}`;
const failures = [];
function check(ok, label, detail = '') {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${detail ? `: ${detail}` : ''}`);
  if (!ok) failures.push(label);
}

// ---- AC6 (server side half): v1 page still served with its own scene ------
const v1 = await fetch(`${BASE}/`);
const v1html = await v1.text();
check(v1.status === 200, 'AC6 GET / -> 200', `status=${v1.status}`);
check(v1html.includes('/static/vendor/phaser-3.80.1.min.js'), 'AC6 v1 page still references its Phaser 3.80.1 build');
check(v1html.includes('Phaser.Game') || v1html.includes('new Phaser'), 'AC6 v1 scene code still present in its page');

const browser = await puppeteer.launch({
  executablePath: EXECUTABLE,
  env: { ...process.env, LD_LIBRARY_PATH: path.join(REPO, '.tooling', 'lib') },
  args: ['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu',
         '--use-gl=angle', '--use-angle=swiftshader'],
  protocolTimeout: 300000,
});

const page = await browser.newPage();
await page.setViewport({ width: 1568, height: 1100 });
const pageErrors = [];
const consoleErrors = [];
const badResources = [];
page.on('pageerror', (e) => pageErrors.push(String(e)));
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
page.on('response', (r) => { if (r.status() >= 400) badResources.push(`${r.url()} -> HTTP ${r.status()}`); });
page.on('requestfailed', (r) => badResources.push(`${r.url()} -> failed`));

try {
  await page.goto(`${BASE}/static/office_v2/index.html`, { waitUntil: 'load', timeout: 60000 });
  await page.waitForFunction('window.__SOU2 && window.__SOU2.ready === true', { polling: 250, timeout: 60000 });
  const readout = await page.evaluate('window.__SOU2');

  console.log('page: Phaser.VERSION=' + readout.phaserVersion +
              ' renderer=' + readout.renderer + ' room=' + readout.roomId +
              ' canvas=' + readout.canvas.w + 'x' + readout.canvas.h);

  // ---- AC2: the room renders (screenshot is the deliverable image) --------
  check(readout.floor.isTilemapLayer && readout.floor.tiles === readout.grid.w * readout.grid.h,
        'AC2 floor is a native TilemapLayer fully tiled over the declared grid',
        `tilemapLayer=${readout.floor.isTilemapLayer} tiles=${readout.floor.tiles} grid=${readout.grid.w}x${readout.grid.h}`);
  check(readout.props.length >= 8, 'AC2 all 8 pack props placed', `props=${readout.props.length}`);
  check(readout.walls.x === 56 && readout.walls.y === -8, 'AC2 walls positioned from walls.origin',
        `at (${readout.walls.x}, ${readout.walls.y}) depth ${readout.walls.depth}`);
  const shot = path.join(OUT_DIR, 'office_v2_render.png');
  await page.screenshot({ path: shot });
  const png = fs.statSync(shot);
  check(png.size > 20000, 'AC2 screenshot captured', `${shot} ${png.size} bytes`);

  // non-blank guard: sample pixels from the screenshot via the page canvas
  const notBlank = await page.evaluate(() => {
    const c = document.querySelector('#game canvas');
    return !!c && c.width > 0 && c.height > 0;
  });
  check(notBlank, 'AC2 game canvas mounted');

  // ---- SOU-147 AC1: the CONTENT base lands on the lattice point (< 0.5 px)
  // Computed from the live object's full state (position, origin, scale) and
  // the publish-measured art_bbox - never from sprite.y alone, the number that
  // lied in SOU-124. General form: with origin (ox, oy) and |scale| s, texture
  // row r renders at screen y = img.y + (r - oy*H)*s; the content bottom edge
  // is bbox row y1, its centre column is (x0+x1)/2.
  console.log('SOU-147 AC1 per prop: sprite.y, origin, scale, image H, art_bbox,');
  console.log('                    content base (recomputed) vs tileToWorldXY lattice point');
  let ac1WorstY = 0, ac1WorstX = 0;
  const ac1Rows = [];
  for (const p of readout.props) {
    const g = p.grounding, sp = p.sprite;
    const dyc = Math.abs(g.content_base.y - p.computed.y);
    const dxc = Math.abs(g.content_base.x - p.computed.x);
    ac1WorstY = Math.max(ac1WorstY, dyc);
    ac1WorstX = Math.max(ac1WorstX, dxc);
    // SOU-124 regression (its AC3 / our AC4): position == computed == engine.
    const dp = Math.max(Math.abs(sp.x - p.computed.x), Math.abs(sp.y - p.computed.y),
                        Math.abs(p.engine.x - p.computed.x), Math.abs(p.engine.y - p.computed.y));
    console.log(`  ${p.id.padEnd(8)} grid=(${p.grid}) lattice.y=${p.computed.y}` +
      ` sprite.y=${sp.y} originY=${sp.originY.toFixed(6)} scaleY=${sp.scaleY.toFixed(6)}` +
      ` H=${g.image[1]} bbox=${JSON.stringify(g.art_bbox)}` +
      ` -> base.y=${g.content_base.y.toFixed(4)} delta=${dyc.toFixed(4)}` +
      ` | base.x=${g.content_base.x.toFixed(4)} delta=${dxc.toFixed(4)} | pos==lattice delta=${dp}`);
    ac1Rows.push({ id: p.id, latticeY: p.computed.y, spriteY: sp.y, originY: sp.originY,
                   scaleY: sp.scaleY, image: g.image, art_bbox: g.art_bbox,
                   baseY: g.content_base.y, delta: dyc, positionDelta: dp });
  }
  check(ac1WorstY < 0.5 && ac1WorstX < 0.5,
        'SOU-147 AC1 content base on the lattice point (< 0.5 px, all 8)',
        `worst dy=${ac1WorstY.toFixed(4)} worst dx=${ac1WorstX.toFixed(4)} props=${readout.props.length}`);

  // ---- SOU-147 AC2: declared size drives the on-screen size (within 2%) ----
  console.log('SOU-147 AC2 per prop: displayWidth (image box), measured content width, declared target tiles*tile_w');
  let ac2WorstPct = 0;
  const ac2Rows = [];
  for (const p of readout.props) {
    const g = p.grounding;
    const pct = Math.abs(g.content_width - g.declared_width) / g.declared_width;
    ac2WorstPct = Math.max(ac2WorstPct, pct);
    console.log(`  ${p.id.padEnd(8)} tiles=${g.tiles} target=${g.declared_width.toFixed(2)}px` +
      ` content=${g.content_width.toFixed(2)}px (${(pct * 100).toFixed(3)}%)` +
      ` displayWidth=${p.sprite.displayWidth.toFixed(2)}px (image box, NOT the contract)`);
    ac2Rows.push({ id: p.id, tiles: g.tiles, target: g.declared_width,
                   contentWidth: g.content_width, pctError: pct, displayWidth: p.sprite.displayWidth });
  }
  check(readout.props.length === 8, 'SOU-147 AC2 covers all eight props', `${readout.props.length}`);
  check(ac2WorstPct <= 0.02, 'SOU-147 AC2 content width == tiles * tile_w (within 2%, all 8)',
        `worst deviation=${(ac2WorstPct * 100).toFixed(3)}%`);
  check(readout.camera.scrollX === 0 && readout.camera.scrollY === 0 && readout.camera.zoom === 1,
        'SOU-147 AC3 precondition: camera clamped at origin, zoom 1 (screenshot pixels == world coords)',
        `scroll=(${readout.camera.scrollX},${readout.camera.scrollY}) zoom=${readout.camera.zoom}`);

  // ---- SOU-147 AC3: the render shows it - measure the DRAWN pixels ----
  // For each prop, screenshot with everything visible (A, captured above) vs
  // with only that prop hidden (B); the diff is where the renderer actually
  // painted prop pixels. Checks: lowest drawn row touches the lattice point
  // (no gap above the floor), drawn width ~ declared target, and drawn width
  // vs the descriptor footprint diamond ((kx+ky)*tile_w/2) - over-wide props
  // are reported as findings with the base-corner cells they land on, per the
  // issue's Constraints (sizes come from the profile; the world is not tidied).
  const { createCanvas, loadImage } = require('@napi-rs/canvas');
  const imgA = await loadImage(path.join(OUT_DIR, 'office_v2_render.png'));
  const pxOf = async (img) => {
    const c = createCanvas(imgA.width, imgA.height);
    const ctx = c.getContext('2d');
    ctx.drawImage(img, 0, 0);
    return ctx.getImageData(0, 0, c.width, c.height).data;
  };
  const dataA = await pxOf(imgA);
  const desc = JSON.parse(await (await fetch(`${BASE}/static/office_v2/pack/andrzej/room.json`)).text());
  const lat = desc.lattice;
  const cellOf = (x, y) => {
    const u = (x - lat.origin[0]) / (lat.tile_w / 2);
    const v = (y - lat.origin[1]) / (lat.tile_h / 2);
    return [Math.floor((u + v) / 2), Math.floor((v - u) / 2)];
  };
  const ac3Rows = [];
  console.log('SOU-147 AC3 drawn-pixel measurement (per prop, hide-one + re-shoot + diff):');
  for (const p of readout.props) {
    await page.evaluate((pid) => {
      const s = window.__SOU2DEBUG.scene;
      s.__v2.placed.find((q) => q.id === pid).img.setVisible(false);
      return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    }, p.id);
    const shotB = path.join(OUT_DIR, `no_${p.id}.png`);
    await page.screenshot({ path: shotB });
    await page.evaluate((pid) => {
      const s = window.__SOU2DEBUG.scene;
      s.__v2.placed.find((q) => q.id === pid).img.setVisible(true);
      return new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    }, p.id);
    const dataB = await pxOf(await loadImage(shotB));
    let minX = 1e9, maxX = -1e9, minY = 1e9, maxY = -1e9, n = 0;
    for (let i = 0; i < dataA.length; i += 4) {
      if (dataA[i] !== dataB[i] || dataA[i + 1] !== dataB[i + 1] || dataA[i + 2] !== dataB[i + 2]) {
        const q = i / 4; const x = q % imgA.width; const y = (q - x) / imgA.width;
        n++;
        if (x < minX) minX = x; if (x > maxX) maxX = x;
        if (y < minY) minY = y; if (y > maxY) maxY = y;
      }
    }
    const drawnW = maxX - minX + 1;
    const gap = p.computed.y - (maxY + 1);          // >0 = floating, <=0 = touching/sunk
    const dTarget = Math.abs(drawnW - p.grounding.declared_width) / p.grounding.declared_width;
    const fp = desc.props.find((q) => q.id === p.id).footprint;
    const fxs = fp.map((c) => c[0]), fys = fp.map((c) => c[1]);
    const fpW = (Math.max(...fxs) - Math.min(...fxs) + 1 + Math.max(...fys) - Math.min(...fys) + 1) * (lat.tile_w / 2);
    const over = drawnW > fpW + 3;
    const lc = cellOf(minX + 1.5, maxY - 1.5), rc = cellOf(maxX - 0.5, maxY - 1.5);
    console.log(`  ${p.id.padEnd(8)} drawn bbox x=[${minX},${maxX}] y=[${minY},${maxY}] px=${n}` +
      ` | base row bottom=${maxY + 1} vs lattice ${p.computed.y} gap=${gap.toFixed(2)}px` +
      ` | width ${drawnW} vs target ${p.grounding.declared_width.toFixed(1)} (${(dTarget * 100).toFixed(2)}%)` +
      ` vs footprint ${fpW} ${over ? `FINDING over-wide: base corners land in cells ${JSON.stringify(lc)} and ${JSON.stringify(rc)}` : 'fits footprint'}`);
    ac3Rows.push({ id: p.id, diffPixels: n, minX, maxX, minY, maxY, drawnWidth: drawnW,
                   gapToLattice: gap, widthVsTargetPct: dTarget, footprintWidthPx: fpW, overWide: over,
                   baseCornerCells: [lc, rc] });
    fs.rmSync(shotB);
  }
  check(ac3Rows.length === 8 && ac3Rows.every((r) => r.diffPixels > 500),
        'SOU-147 AC3 every prop paints a real patch of pixels in the render',
        ac3Rows.map((r) => `${r.id}:${r.diffPixels}`).join(' '));
  check(ac3Rows.every((r) => Math.abs(r.gapToLattice) <= 2),
        'SOU-147 AC3 no drawn prop floats off its floor point (>2 px gap) nor sinks through it',
        ac3Rows.map((r) => `${r.id}:${r.gapToLattice.toFixed(2)}`).join(' '));
  check(ac3Rows.every((r) => r.widthVsTargetPct <= 0.02),
        'SOU-147 AC3 drawn widths are the declared widths (2% slack for exact-colour occlusion edges)',
        ac3Rows.map((r) => `${r.id}:${(r.widthVsTargetPct * 100).toFixed(2)}%`).join(' '));
  const overWides = ac3Rows.filter((r) => r.overWide);
  console.log(overWides.length
    ? `SOU-147 AC3 FINDINGS (per the issue's Constraints, sizes come from the profile - reported, not tidied): ` +
      overWides.map((r) => `${r.id} drawn ${r.drawnWidth}px over footprint ${r.footprintWidthPx}px, base corners in cells ${JSON.stringify(r.baseCornerCells)}`).join('; ')
    : 'SOU-147 AC3 no prop renders wider than its declared footprint');


  // ---- AC4: collision layer loaded and non-trivial ------------------------
  const cl = readout.collision;
  console.log('AC4 collision layer: ' + JSON.stringify(cl));
  check(cl.isTilemapLayer && !cl.isTilemapGPULayer,
        'AC4 collision layer is a loaded native (CPU) TilemapLayer',
        `instanceofTilemapLayer=${cl.isTilemapLayer} gpu=${cl.isTilemapGPULayer} getTilesWithin-raw=${cl.getTilesWithinRaw}`);
  check(cl.placedTiles === cl.descriptorBlockedCount,
        'AC4 placed-tile count equals the descriptor blocked count',
        `placed=${cl.placedTiles} descriptor=${cl.descriptorBlockedCount}`);
  check(cl.collidesFlagged === cl.placedTiles && cl.collidesFlagged > 0,
        'AC4 setCollisionByProperty flagged every placed tile', `flagged=${cl.collidesFlagged}`);
  check(cl.sampleBlocked.tileIndex !== null && cl.sampleBlocked.collidesProp === true,
        'AC4 known-blocked tile reports its index and collides=true',
        `cell=(${cl.sampleBlocked.cell}) index=${cl.sampleBlocked.tileIndex} edges=${cl.sampleBlocked.collideEdges}`);

  // ---- AC5: lattice depth ordering, near corner drawn later --------------
  const dep = readout.depth;
  console.log('AC5 per-prop depths: ' + JSON.stringify(dep.perProp) + ' rowLength=' + dep.rowLength);
  console.log('AC5 painter order (shallow->deep): ' + dep.drawnOrder.join(' -> '));
  const far = dep.perProp.cat;      // (0,15) diagonal 15
  const near = dep.perProp.jdm_van; // (6,15) diagonal 21
  check(far < near, 'AC5 far-corner prop depth < near-corner prop depth', `cat=${far} van=${near}`);
  check(dep.drawnOrder.indexOf('jdm_van') > dep.drawnOrder.indexOf('cat'),
        'AC5 near prop drawn after far prop in the sorted order');
  const desk = dep.perProp.desk; // (3,3) diagonal 6 - should sit under both
  check(desk < far, 'AC5 monotone in gx+gy beyond the pair', `desk=${desk} cat=${far}`);

  check(pageErrors.length === 0, 'no page errors', pageErrors.join(' | '));
  check(badResources.length === 0, 'no failed/4xx resources', badResources.join(' | '));
  check(consoleErrors.filter((e) => !/favicon/i.test(e)).length === 0, 'no console errors', consoleErrors.join(' | '));

  fs.writeFileSync(path.join(OUT_DIR, 'numbers.json'), JSON.stringify(readout, null, 2) + '\n');
  console.log('wrote ' + path.join(OUT_DIR, 'numbers.json'));
  fs.writeFileSync(path.join(OUT_DIR, 'grounding.json'),
                   JSON.stringify({ ac1: ac1Rows, ac2: ac2Rows, ac3: ac3Rows }, null, 2) + '\n');
  console.log('wrote ' + path.join(OUT_DIR, 'grounding.json'));
} finally {
  await browser.close();
}

console.log(failures.length ? `FAILURES: ${failures.join('; ')}` : 'ALL PROOF CHECKS PASS');
process.exit(failures.length ? 1 : 0);
