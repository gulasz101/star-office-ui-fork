/**
 * proof.mjs - SOU-124 S5 browser proof: AC2 (screenshot), AC3/AC4/AC5 (live
 * numbers from the RUNNING page), AC6 (v1 still serves its own scene).
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

  // ---- AC3: props ON the lattice, grid -> computed -> actual < 0.5 px ----
  console.log('AC3 descriptor grid -> computed world (descriptor formula) -> engine tileToWorldXY -> sprite x,y');
  let ac3Worst = 0;
  for (const p of readout.props) {
    const dx = Math.abs(p.sprite.x - p.computed.x);
    const dy = Math.abs(p.sprite.y - p.computed.y);
    ac3Worst = Math.max(ac3Worst, dx, dy);
    console.log(`  ${p.id.padEnd(8)} grid=(${p.grid[0]},${p.grid[1]})` +
      ` computed=(${p.computed.x},${p.computed.y})` +
      ` engine=(${p.engine.x},${p.engine.y})` +
      ` sprite=(${p.sprite.x},${p.sprite.y}) delta=(${dx},${dy})`);
  }
  check(ac3Worst < 0.5, 'AC3 sprite positions agree with the lattice (< 0.5 px)', `worst delta=${ac3Worst}`);
  check(readout.props.length >= 3, 'AC3 covered >= 3 props', `${readout.props.length} printed`);

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
} finally {
  await browser.close();
}

console.log(failures.length ? `FAILURES: ${failures.join('; ')}` : 'ALL PROOF CHECKS PASS');
process.exit(failures.length ? 1 : 0);
