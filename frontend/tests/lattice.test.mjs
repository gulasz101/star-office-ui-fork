/**
 * lattice.test.mjs - SOU-124 AC1: the engine agrees with Python.
 * Run: node --test frontend/tests/lattice.test.mjs
 *
 * Boots the REAL vendored engine build
 * (frontend/static/vendor/phaser-4.2.1.min.js, sha256
 * 66348b1b5141e49b7d5ebbe688cddcb502eab1cb00f21c538686a5b2c5abe4de) in Node
 * and asserts map/floorLayer.tileToWorldXY(gx, gy) equals the Python
 * scripts/iso_lattice.py screen(gx, gy) for the shipped 12x18 pack, on >= 5
 * cells including all four floor corners. This is the cross-language proof
 * that the lattice is ONE lattice (ADR-067).
 *
 * Shims used (named per the issue): jsdom for the DOM + @napi-rs/canvas
 * (Skia) for canvas 2D and image decode - the exact combo proven on this
 * repo with the real Phaser 3 build (SOU-104). Phaser boots as
 * Phaser.HEADLESS ("no renderer - DOM still required", the documented unit
 * testing mode). Dependencies resolve from the worker-local .tooling/npm
 * (provisioned since SOU-104; no new installs).
 */
import test from 'node:test';
import assert from 'node:assert';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';
import http from 'node:http';
import vm from 'node:vm';
import { execFileSync } from 'node:child_process';

const REPO = path.resolve(fileURLToPath(import.meta.url), '..', '..', '..');
const require = createRequire(path.join(REPO, '.tooling', 'npm', 'noop.js'));
const { JSDOM } = require('jsdom');
const { createCanvas, loadImage } = require('@napi-rs/canvas');

const ROOM_DIR = path.join('assets', 'generated', 'rooms', 'andrzej');
const PHASER_FILE = path.join(REPO, 'frontend', 'static', 'vendor', 'phaser-4.2.1.min.js');

/** Ask Python - the other language of the contract - for its numbers. */
function pythonTable(cells, origin, tileW, tileH) {
  const script = `
import json, sys
sys.path.insert(0, "scripts")
from iso_lattice import screen
origin = tuple(json.loads(sys.argv[1]))
tw, th = float(sys.argv[2]), float(sys.argv[3])
cells = json.loads(sys.argv[4])
out = []
for gx, gy in cells:
    x, y = screen(gx, gy, origin, tw, th)
    out.append({"gx": gx, "gy": gy, "x": x, "y": y})
print(json.dumps(out))
`;
  const stdout = execFileSync('python3', ['-c', script, JSON.stringify(origin), String(tileW), String(tileH), JSON.stringify(cells)], {
    cwd: REPO,
    encoding: 'utf8',
  });
  return JSON.parse(stdout);
}

function serveRepo(port) {
  const server = http.createServer((req, res) => {
    const rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '');
    const file = path.join(REPO, rel);
    if (!file.startsWith(REPO) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404); res.end('nope'); return;
    }
    res.writeHead(200, { 'content-type': file.endsWith('.json') ? 'application/json' : 'image/png' });
    res.end(fs.readFileSync(file));
  });
  return new Promise((resolve) => server.listen(port, '127.0.0.1', () => resolve(server)));
}

/** Boot the vendored build in jsdom and return the engine's numbers. */
async function engineTable(cells, origin) {
  const server = await serveRepo(0);
  const port = server.address().port;
  const base = `http://127.0.0.1:${port}`;
  try {
    const dom = new JSDOM('<!DOCTYPE html><html><body><div id="game"></div></body></html>', {
      url: `${base}/${ROOM_DIR}/`,
      pretendToBeVisual: true,
      runScripts: 'dangerously',
    });
    const { window } = dom;

    window.HTMLCanvasElement.prototype.getContext = function (type) {
      if (type !== '2d') return null;
      if (!this.__canvas || this.__canvas.width !== this.width || this.__canvas.height !== this.height) {
        this.__canvas = createCanvas(this.width || 300, this.height || 150);
      }
      return this.__canvas.getContext('2d');
    };
    class NodeImage {
      constructor() { this.width = 0; this.height = 0; this.complete = false; this.onload = null; this.onerror = null; this._src = null; }
      set src(value) {
        this._src = value;
        const target = value.startsWith('data:') ? Buffer.from(value.split(',')[1], 'base64')
          : value.startsWith('blob:sou124-') ? blobStore.get(value).arrayBuffer().then((ab) => Buffer.from(ab))
          : `${base}${value.startsWith('/') ? '' : '/'}${value}`;
        Promise.resolve(target)
          .then((t) => loadImage(t))
          .then((image) => {
            this.width = image.width; this.height = image.height; this.complete = true;
            if (this.onload) this.onload();
          })
          .catch((error) => { if (this.onerror) this.onerror(new Error(String(error))); });
      }
      get src() { return this._src; }
    }
    window.Image = NodeImage;
    window.self = window;

    // Phaser 4's PNGLoader fetches bytes via XHR (responseType blob) and decodes
    // through URL.createObjectURL(blob) -> img.src. jsdom implements neither the
    // object-URL store nor a PNG decoder, so the URLs are parked here and
    // NodeImage resolves them through Skia.
    const blobStore = new Map();
    let blobSeq = 0;
    window.URL.createObjectURL = (blob) => {
      const url = `blob:sou124-${++blobSeq}`;
      blobStore.set(url, blob);
      return url;
    };
    window.URL.revokeObjectURL = (url) => { blobStore.delete(url); };

    // Run the shipped bundle INSIDE the jsdom realm (window.eval), so Phaser
    // sees the same DOM/canvas/Image objects a browser would. NOTE (finding):
    // vm.createContext(window) + vm.runInContext breaks Phaser 4.2.1 - the
    // contextified global fails jsdom's EventTarget impl check at Game boot
    // ("not a valid instance of EventTarget"); runScripts: 'dangerously' +
    // window.eval is the working realm for 4.x.
    const phaserSrc = fs.readFileSync(PHASER_FILE, 'utf8');
    window.eval(phaserSrc);
    const Phaser = window.Phaser;
    assert.ok(Phaser, 'vendored Phaser bundle did not define window.Phaser');
    assert.strictEqual(Phaser.VERSION, '4.2.1', `expected the pinned engine 4.2.1, got ${Phaser.VERSION}`);

    const descriptor = JSON.parse(fs.readFileSync(path.join(REPO, ROOM_DIR, 'room.json'), 'utf8'));

    const numbers = await (async () => {
      let game = null;
      try {
        return await new Promise((resolve, reject) => {
          const timer = setTimeout(() => reject(new Error('engine boot timed out after 30s')), 30000);
          const scene = {
            key: 'lattice',
            preload() {
              this.load.tilemapTiledJSON('room', `/${ROOM_DIR}/${descriptor.map}`);
              this.load.image('tiles-floor', `/${ROOM_DIR}/${descriptor.tileset.image}`);
            },
            create() {
              try {
                const map = this.add.tilemap('room');
                const tileset = map.addTilesetImage('floor', 'tiles-floor');
                assert.ok(tileset, 'addTilesetImage("floor") returned null - tileset name mismatch');
                const layer = map.createLayer('floor', tileset, origin[0], origin[1], false);
                assert.ok(layer instanceof Phaser.Tilemaps.TilemapLayer,
                  'floor layer is not a (CPU) TilemapLayer');
                const out = cells.map(([gx, gy]) => {
                  const p = layer.tileToWorldXY(gx, gy);
                  return { gx, gy, x: p.x, y: p.y };
                });
                clearTimeout(timer);
                resolve({ out, orientation: map.orientation, tileW: map.tileWidth, tileH: map.tileHeight });
              } catch (e) {
                clearTimeout(timer);
                reject(e);
              }
            },
          };
          game = new Phaser.Game({
            type: Phaser.HEADLESS,
            width: descriptor.canvas.w,
            height: descriptor.canvas.h,
            parent: 'game',
            scene,
          });
        });
      } finally {
        if (game) game.destroy(true);
        window.close();
      }
    })();
    return numbers;
  } finally {
    server.close();
  }
}

test('AC1: vendored Phaser 4.2.1 tileToWorldXY == Python iso_lattice.screen (all four corners)', async () => {
  assert.ok(fs.existsSync(PHASER_FILE), `vendored build missing: ${PHASER_FILE}`);
  const descriptor = JSON.parse(fs.readFileSync(path.join(REPO, ROOM_DIR, 'room.json'), 'utf8'));
  const { origin, tile_w: tileW, tile_h: tileH } = descriptor.lattice;
  const gw = descriptor.grid.w, gh = descriptor.grid.h;

  // >= 5 cells INCLUDING all four corners of the declared 12x18 floor,
  // plus the van anchor, the desk anchor and the build.stand zone anchor.
  const cells = [
    [0, 0], [gw - 1, 0], [0, gh - 1], [gw - 1, gh - 1],
    [6, 15], [3, 3], [5, 14],
  ];
  assert.ok(cells.length >= 5, 'need at least five probe cells');

  const py = pythonTable(cells, origin, tileW, tileH);
  const eng = await engineTable(cells, origin);

  console.log(`engine: Phaser 4.2.1 (vendored, HEADLESS boot) orientation=${eng.orientation} tile=${eng.tileW}x${eng.tileH}; python: scripts/iso_lattice.py screen()`);
  console.log('AC1 lattice table (engine vs python):');
  let worst = 0;
  for (const p of py) {
    const e = eng.out.find((o) => o.gx === p.gx && o.gy === p.gy);
    assert.ok(e, `engine missing cell (${p.gx},${p.gy})`);
    const dx = Math.abs(e.x - p.x), dy = Math.abs(e.y - p.y);
    worst = Math.max(worst, dx, dy);
    const corner = (p.gx === 0 || p.gx === gw - 1) && (p.gy === 0 || p.gy === gh - 1) ? ' CORNER' : '';
    console.log(`  (${p.gx},${p.gy})${corner.padEnd(7)} python=(${p.x},${p.y})  engine=(${e.x},${e.y})  delta=(${dx},${dy})`);
  }
  console.log(`  MAX |delta| = ${worst}`);
  assert.strictEqual(worst, 0, 'engine and python lattice numbers must be bit-identical (max |delta| = 0)');
});
