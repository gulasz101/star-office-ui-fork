// AgentCharacter: one agent's sprite inside its office. It walks tile by tile along A* paths,
// faces where it's going, sits down on seats, plays the activity animation and shows a
// speech bubble. Targets can change mid-walk: the new path starts from the next tile, so
// it never teleports and never cuts through props.
import { TILE, CHAR_H } from './config.js';
import { FACINGS, FRAME, SHEET_COLS } from './placeholders.js';

const STEP_MS = 230; // one tile
const SEAT_DELTA = { up: [0, -1], down: [0, 1], left: [-1, 0], right: [1, 0] };
const ACTIVITY_ANIM = {
  typing: 'work', terminal: 'work', building: 'work', reading: 'idle', thinking: 'idle',
  coffee: 'idle', sleeping: 'sleep', arriving: 'idle', leaving: 'idle', celebrating: 'work',
};

function facingOf(dx, dy, fallback) {
  if (dx > 0) return 'right';
  if (dx < 0) return 'left';
  if (dy > 0) return 'down';
  if (dy < 0) return 'up';
  return fallback;
}

export function registerCharacterAnims(scene, key) {
  FACINGS.forEach((facing, row) => {
    const f = (col) => ({ key, frame: row * SHEET_COLS + col });
    const add = (name, frames, frameRate, repeat = -1) => {
      const k = `${key}-${name}-${facing}`;
      if (!scene.anims.exists(k)) scene.anims.create({ key: k, frames, frameRate, repeat });
    };
    add('idle', [f(FRAME.idle)], 1);
    add('walk', FRAME.walk.map(f), 9);
    add('work', FRAME.work.map(f), 5);
    add('sleep', [f(FRAME.sleep)], 1);
  });
}

export class AgentCharacter {
  constructor(office, { agentId, sheetKey, start }) {
    this.office = office;
    this.scene = office.scene;
    this.agentId = agentId;
    this.sheetKey = sheetKey;
    registerCharacterAnims(this.scene, sheetKey);

    this.tile = { x: start.tx, y: start.ty };
    this.facing = 'down';
    this.seated = null; // seat direction while sitting
    this.target = null; // { zone, activity, bubble }
    this.walking = false;
    this.state = { zone: null, activity: 'arriving', bubble: '' };

    const p = this._feet(this.tile.x, this.tile.y);
    this.sprite = this.scene.add.sprite(p.x, p.y, sheetKey, 0).setOrigin(0.5, 1);
    this._buildBubble();
    this._play('idle');
    this._sync();
    this.scene.events.on('update', this._sync, this);
  }

  // world position of the feet when standing on a tile of this office
  _feet(tx, ty) {
    const c = this.office.tileToWorld(tx, ty);
    return { x: c.x, y: c.y + TILE / 2 - 2 };
  }

  _play(anim) {
    this.anim = anim;
    this.sprite.anims.play(`${this.sheetKey}-${anim}-${this.facing}`, true);
  }

  _buildBubble() {
    this.bubbleText = this.scene.add.text(0, 0, '', {
      fontFamily: 'monospace', fontSize: '9px', color: '#1d1f2a', resolution: 4,
    }).setOrigin(0.5, 1);
    this.bubbleBg = this.scene.add.graphics();
    this.zzz = this.scene.add.text(0, 0, 'z', {
      fontFamily: 'monospace', fontSize: '10px', color: '#cfe3ff', stroke: '#1d1f2a', strokeThickness: 3, resolution: 4,
    }).setOrigin(0.5, 1).setVisible(false);
    this.zOff = { v: 0 };
    this.scene.tweens.add({ targets: this.zOff, v: -7, duration: 1100, repeat: -1, yoyo: true });
    this.scene.tweens.add({ targets: this.zzz, alpha: { from: 1, to: 0.3 }, duration: 1100, repeat: -1, yoyo: true });
  }

  _drawBubble() {
    const text = this.state.bubble || '';
    this.bubbleText.setText(text);
    this.bubbleBg.clear();
    const visible = Boolean(text) && this.state.activity !== 'sleeping';
    this.bubbleText.setVisible(visible);
    this.bubbleBg.setVisible(visible);
    if (!visible) return;
    const w = Math.ceil(this.bubbleText.width) + 8;
    const h = 13;
    this.bubbleBg.fillStyle(0xfff8e6, 1).lineStyle(1, 0x1d1f2a, 1);
    this.bubbleBg.fillRoundedRect(-w / 2, -h, w, h, 4).strokeRoundedRect(-w / 2, -h, w, h, 4);
    this.bubbleBg.fillTriangle(-3, 0, 3, 0, 0, 4).lineBetween(-3, 0, 0, 4).lineBetween(3, 0, 0, 4);
  }

  _sync() {
    const s = this.sprite;
    // Props sort by the bottom edge of their footprint. Standing feet are drawn 2 px above
    // the tile edge, so +3 puts the character in front of anything on its own row (the
    // chair it sits in) and behind everything further down. Seated, it sorts just in front
    // of the furniture tile it sits on.
    s.setDepth(this.seated ? this.seatDepth + 1 : s.y + 3);
    const top = s.y - CHAR_H - 4;
    this.bubbleBg.setPosition(Math.round(s.x), Math.round(top)).setDepth(100000);
    this.bubbleText.setPosition(Math.round(s.x), Math.round(top) - 2).setDepth(100001);
    this.zzz.setPosition(Math.round(s.x + 11), Math.round(top + 14 + this.zOff.v)).setDepth(100002);
  }

  /**
   * Go to a zone and do an activity there. Safe to call at any time, also mid-walk.
   * @param {{zone: string, activity?: string, bubble?: string}} t
   */
  do(t) {
    if (!this.office.zones[t.zone]) {
      console.warn(`[v3] ${this.agentId}: unknown zone ${t.zone}`);
      return;
    }
    this.target = { activity: 'thinking', bubble: '', ...t };
    // show what it's about to do right away, even while walking there
    this.state = { ...this.state, bubble: this.target.bubble };
    this._drawBubble();
    if (!this.walking) this._advance();
  }

  _arrived() {
    const z = this.office.zones[this.target.zone];
    return this.tile.x === z.tx && this.tile.y === z.ty;
  }

  // one decision per tile: stand up if needed, take one step, or settle into the activity
  _advance() {
    if (!this.target) return;
    const z = this.office.zones[this.target.zone];

    if (this.seated && !(this._arrived() && z.seat === this.seated)) {
      return this._hop(this.seated, true, () => this._advance());
    }
    if (this._arrived()) return this._settle(z);

    const path = this.office.path([this.tile.x, this.tile.y], [z.tx, z.ty]);
    if (!path || path.length < 2) {
      console.warn(`[v3] ${this.agentId}: no path to ${this.target.zone}`);
      return this._settle(null);
    }
    const [nx, ny] = path[1];
    this.facing = facingOf(nx - this.tile.x, ny - this.tile.y, this.facing);
    this._play('walk');
    this.walking = true;
    const p = this._feet(nx, ny);
    this.scene.tweens.add({
      targets: this.sprite, x: p.x, y: p.y, duration: STEP_MS,
      onComplete: () => {
        this.tile = { x: nx, y: ny };
        this.walking = false;
        this._advance(); // re-plans every tile, so a new target applies immediately
      },
    });
  }

  // sit down onto (or stand up from) the seat tile in direction `dir`
  _hop(dir, standingUp, done) {
    const [dx, dy] = SEAT_DELTA[dir];
    const from = this._feet(this.tile.x, this.tile.y);
    const to = standingUp ? from : { x: from.x + dx * TILE, y: from.y + dy * TILE - 4 };
    this.walking = true;
    this.facing = standingUp ? 'down' : this.facing;
    this.scene.tweens.add({
      targets: this.sprite, x: to.x, y: to.y, duration: 180, ease: 'Quad.easeOut',
      onComplete: () => {
        this.walking = false;
        this.seated = standingUp ? null : dir;
        // bottom edge of the seat tile, which is what the seat prop sorts by
        this.seatDepth = this.office.tileToWorld(this.tile.x + dx, this.tile.y + dy).y + TILE / 2;
        done();
      },
    });
  }

  _settle(z) {
    const t = this.target;
    if (z) this.facing = z.face || this.facing;
    const finish = () => {
      this.state = { zone: t.zone, activity: t.activity, bubble: t.bubble };
      this._play(ACTIVITY_ANIM[t.activity] || 'idle');
      this.zzz.setVisible(t.activity === 'sleeping');
      this._drawBubble();
    };
    if (z && z.seat && !this.seated) {
      this._hop(z.seat, false, finish);
    } else {
      finish();
    }
  }
}
