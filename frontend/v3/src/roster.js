// Roster bar (SOU-236): one chip per agent along the bottom of the screen, in building
// order. Hoodie colour, name and a status dot; a click flies the camera to the room.
import { hoodieFor, ROOM_NAMES } from './rooms.js';
import { hsvToRgb } from './palette.js';

const GENERATED_ORANGE = [28, 0.82, 0.94]; // the sheet's hoodie, before any swap

export function hoodieCss(agent) {
  const h = hoodieFor(agent);
  const [bh, bs, bv] = GENERATED_ORANGE;
  const [r, g, b] = h ? hsvToRgb(h.h, Math.min(1, bs * (h.s ?? 1)), Math.min(1, bv * (h.v ?? 1))) : hsvToRgb(bh, bs, bv);
  return `rgb(${r}, ${g}, ${b})`;
}

// what the dot says: working, idle (coffee, arriving, thinking) or asleep
export function statusOf(character) {
  const st = (character.walking && character.target) || character.state || {};
  if (st.activity === 'sleeping') return 'asleep';
  if (['typing', 'terminal', 'building', 'reading', 'celebrating'].includes(st.activity)) return 'working';
  return 'idle';
}

export class Roster {
  constructor(onPick) {
    this.root = document.getElementById('roster');
    this.onPick = onPick;
    this.chips = new Map();
  }

  render(offices, focused) {
    if (!this.root) return;
    const list = [...offices].sort((a, b) => a.slot - b.slot);
    for (const o of list) {
      let chip = this.chips.get(o.agentId);
      if (!chip) {
        chip = document.createElement('button');
        chip.className = 'chip';
        chip.type = 'button';
        const swatch = document.createElement('span');
        swatch.className = 'swatch';
        swatch.style.background = hoodieCss(o.agentId);
        const name = document.createElement('span');
        name.className = 'name';
        name.textContent = o.agentId;
        const dot = document.createElement('span');
        dot.className = 'dot';
        chip.append(swatch, name, dot);
        chip.title = ROOM_NAMES[o.roomType] || o.roomType;
        chip.addEventListener('click', (e) => {
          e.stopPropagation();
          this.onPick(o);
        });
        this.chips.set(o.agentId, chip);
      }
      chip.dataset.status = statusOf(o.character);
      chip.classList.toggle('focused', focused === o);
      this.root.append(chip); // re-append keeps building order as rooms are added
    }
  }
}
