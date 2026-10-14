// Star Office UI - layout and layering config
// All coordinates, depths and asset paths are managed here
// Avoid magic numbers and reduce the risk of mistakes

// Core rules:
// - Transparent assets (e.g. the desk) are forced to .png; opaque ones prefer .webp
// - Layering: low -> sofa(10) -> starWorking(900) -> desk(1000) -> flower(1100)

const LAYOUT = {
  // === game canvas ===
  game: {
    width: 1280,
    height: 720
  },

  // === area coordinates ===
  areas: {
    door:        { x: 640, y: 550 },
    writing:     { x: 320, y: 360 },
    researching: { x: 320, y: 360 },
    error:       { x: 1066, y: 180 },
    breakroom:   { x: 640, y: 360 }
  },

  // === decorations & furniture: position + origin + depth ===
  furniture: {
    // sofa
    sofa: {
      x: 670,
      y: 144,
      origin: { x: 0, y: 0 },
      depth: 10
    },

    // new desk (forced transparent PNG)
    desk: {
      x: 218,
      y: 417,
      origin: { x: 0.5, y: 0.5 },
      depth: 1000
    },

    // flower pot on the desk
    flower: {
      x: 310,
      y: 390,
      origin: { x: 0.5, y: 0.5 },
      depth: 1100,
      scale: 0.8
    },

    // Star working at the desk (below desk)
    starWorking: {
      x: 217,
      y: 333,
      origin: { x: 0.5, y: 0.5 },
      depth: 900,
      scale: 1.32
    },

    // plants
    plants: [
      { x: 565, y: 178, depth: 5 },
      { x: 230, y: 185, depth: 5 },
      { x: 977, y: 496, depth: 5 }
    ],

    // poster
    poster: {
      x: 252,
      y: 66,
      depth: 4
    },

    // coffee machine
    coffeeMachine: {
      x: 659,
      y: 397,
      origin: { x: 0.5, y: 0.5 },
      depth: 99
    },

    // server area
    serverroom: {
      x: 1021,
      y: 142,
      origin: { x: 0.5, y: 0.5 },
      depth: 2
    },

    // error bug
    errorBug: {
      x: 1007,
      y: 221,
      origin: { x: 0.5, y: 0.5 },
      depth: 50,
      scale: 0.9,
      pingPong: { leftX: 1007, rightX: 1111, speed: 0.6 }
    },

    // sync animation
    syncAnim: {
      x: 1157,
      y: 592,
      origin: { x: 0.5, y: 0.5 },
      depth: 40
    },

    // cat
    cat: {
      x: 94,
      y: 557,
      origin: { x: 0.5, y: 0.5 },
      depth: 2000
    }
  },

  // === plaque ===
  plaque: {
    x: 640,
    y: 720 - 36,
    width: 420,
    height: 44
  },

  // === asset loading rules: which ones are forced to PNG (transparent assets) ===
  forcePng: {
    desk_v2: true // the new desk must be transparent, forced PNG
  },

  // === total asset count (for the loading progress bar) ===
  totalAssets: 15
};
