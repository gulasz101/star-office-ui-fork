/**
 * main.js - boot the v2 engine scene from the published schema-2 pack (SOU-124).
 *
 * The page carries ZERO agent literals: which room renders comes from
 * pack/index.json (SOU-115 ruling - identity is data), overridable with
 * ?room=<id> for future side-by-side checks. Canvas size is explicit, from
 * descriptor.canvas (the issue's boot contract). The engine itself is the
 * pinned vendored build loaded by index.html; this file is a module, not a
 * giant inline <script> - that shape is what v2 replaces.
 */
import { createRoomScene, exposeReadout } from './scene.js';

function fatal(message) {
  const el = document.getElementById('fatal');
  el.hidden = false;
  el.textContent = message;
  window.__SOU2 = { ready: false, fatal: message };
  console.error('[office_v2] ' + message);
}

async function fetchJSON(url) {
  // 404 must resolve to a controlled failure, not a hung promise (SOU-115 lesson).
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} -> HTTP ${r.status}`);
  return r.json();
}

async function boot() {
  let index;
  try {
    index = await fetchJSON('pack/index.json');
  } catch (e) {
    return fatal('pack index unavailable: ' + e.message);
  }
  const rooms = (index && index.rooms) || [];
  if (!rooms.length) return fatal('pack/index.json lists no rooms');

  const want = new URLSearchParams(location.search).get('room');
  const room = want ? rooms.find((r) => r.id === want) : rooms[0];
  if (!room) return fatal(`room '${want}' not in pack index (${rooms.map((r) => r.id).join(', ')})`);

  const packBase = `pack/${room.id}/`;
  let descriptor;
  try {
    descriptor = await fetchJSON(packBase + room.descriptor);
  } catch (e) {
    return fatal('room descriptor unavailable: ' + e.message);
  }
  if (descriptor.schema !== 2) return fatal(`room '${room.id}' is not schema 2 (got ${descriptor.schema})`);

  const scene = createRoomScene(descriptor, packBase);
  exposeReadout(room.id);

  return new Phaser.Game({
    type: Phaser.AUTO,            // issue contract: AUTO, never HEADLESS in the page
    parent: 'game',
    width: descriptor.canvas.w,   // explicit size from the descriptor
    height: descriptor.canvas.h,
    pixelArt: true,
    backgroundColor: '#0b0e14',
    scene,
  });
}

boot();
