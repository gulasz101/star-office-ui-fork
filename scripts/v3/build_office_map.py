#!/usr/bin/env python3
"""Build the v3 placeholder tileset and the office-01 Tiled map (SOU-154).

Outputs (both open in Tiled):
  frontend/v3/assets/placeholder/tiles.png   8 tiles of 32x32 in one row
  frontend/v3/maps/office-01.json            orthogonal map, 16x12 tiles

Layers:
  floor   (tile)    floor everywhere inside the shell
  walls   (tile)    wall caps / faces; tiles with property collides=true block walking
  props   (object)  one rectangle per prop: name = prop key, rect = footprint in px;
                    property `collides` (default true) blocks those tiles
  zones   (object)  one point per zone: name = zone id, the tile the character stands on

Once SOU-155 owns the layout, edit office-01.json in Tiled instead of re-running this.
"""
import json
import os

from PIL import Image, ImageDraw

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
T = 32
W, H = 16, 12

# --- tileset -----------------------------------------------------------------
TILES = [
    # (name, base colour, accent, collides)
    ("floor_wood_a", (150, 104, 70), (128, 86, 56), False),
    ("floor_wood_b", (142, 98, 66), (122, 82, 54), False),
    ("garage_concrete", (118, 122, 128), (104, 108, 114), False),
    ("wall_cap", (58, 62, 82), (74, 80, 104), True),
    ("wall_face", (196, 188, 170), (176, 168, 150), True),
    ("wall_base", (150, 142, 126), (110, 102, 90), True),
    ("door_mat", (86, 120, 92), (70, 100, 76), False),
    ("garage_stripe", (118, 122, 128), (214, 178, 64), False),
]
GID = {name: i + 1 for i, (name, *_rest) in enumerate(TILES)}


def draw_tileset(path):
    img = Image.new("RGBA", (T * len(TILES), T), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, (name, base, accent, _c) in enumerate(TILES):
        x0 = i * T
        d.rectangle([x0, 0, x0 + T - 1, T - 1], fill=base)
        if name.startswith("floor_wood"):
            for y in (7, 15, 23, 31):  # plank seams
                d.line([x0, y, x0 + T - 1, y], fill=accent)
            off = 10 if name.endswith("a") else 22
            d.line([x0 + off, 0, x0 + off, 7], fill=accent)
            d.line([x0 + (off + 14) % T, 8, x0 + (off + 14) % T, 15], fill=accent)
        elif name == "garage_concrete":
            for px, py in ((5, 6), (20, 11), (12, 24), (27, 27), (3, 19)):
                d.point([x0 + px, py], fill=accent)
        elif name == "wall_cap":
            d.line([x0, T - 1, x0 + T - 1, T - 1], fill=accent)
        elif name == "wall_face":
            d.line([x0, 0, x0 + T - 1, 0], fill=accent)
            d.line([x0, 16, x0 + T - 1, 16], fill=accent)
        elif name == "wall_base":
            d.rectangle([x0, 22, x0 + T - 1, T - 1], fill=accent)
        elif name == "door_mat":
            d.rectangle([x0 + 3, 3, x0 + T - 4, T - 4], outline=accent)
        elif name == "garage_stripe":
            d.rectangle([x0, 13, x0 + T - 1, 18], fill=accent)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path)


# --- layout ------------------------------------------------------------------
GARAGE_X0 = 11          # garage nook: columns 11..14
PARTITION_X = 10        # wall between office and garage, open at rows 7..10


def build_layers():
    floor = [[0] * W for _ in range(H)]
    walls = [[0] * W for _ in range(H)]
    for y in range(H):
        for x in range(W):
            if 1 <= x <= W - 2 and 1 <= y <= H - 2:
                if x >= GARAGE_X0:
                    floor[y][x] = GID["garage_stripe"] if y == 8 and x == GARAGE_X0 else GID["garage_concrete"]
                else:
                    floor[y][x] = GID["floor_wood_a"] if (x + y) % 2 else GID["floor_wood_b"]
    # back wall: cap on row 0, face rows 1..2 (top-down 3/4 shows the wall front)
    for x in range(W):
        walls[0][x] = GID["wall_cap"]
        walls[1][x] = GID["wall_face"]
        walls[2][x] = GID["wall_base"] if 1 <= x <= W - 2 else GID["wall_cap"]
    # side walls and front wall: caps only (we look over them)
    for y in range(H):
        walls[y][0] = GID["wall_cap"]
        walls[y][W - 1] = GID["wall_cap"]
    for x in range(W):
        walls[H - 1][x] = GID["wall_cap"]
    # entry door in the front wall
    for x in (6, 7):
        walls[H - 1][x] = 0
        floor[H - 1][x] = GID["door_mat"]
    # garage partition with an opening at rows 7..10
    for y in range(3, 7):
        walls[y][PARTITION_X] = GID["wall_cap"]
    # garage roller door in the back wall is just a different face for now
    return floor, walls


# props: key -> (tile x, tile y, w, h, collides)
PROPS = {
    "desk": (2, 3, 2, 2, True),
    "chair": (2, 5, 1, 1, False),
    "rack": (5, 2, 1, 2, True),
    "bookshelf": (7, 2, 2, 2, True),
    "coffee": (1, 8, 2, 1, True),
    "rug": (4, 6, 3, 2, False),
    "couch": (3, 9, 3, 1, True),
    "bass": (8, 9, 1, 1, True),
    "amp": (9, 9, 1, 1, True),
    "cat": (1, 10, 1, 1, True),
    "van": (12, 3, 3, 4, True),
    "spool": (11, 3, 1, 1, True),
    "crate": (14, 10, 1, 1, True),
}

# zones: id -> (tile x, tile y, face, seat). (x, y) is the walkable tile the character
# stands on; `face` is where it looks while working; `seat` is an optional one-tile hop onto
# furniture (sitting on the couch) that the character controller animates.
ZONES = {
    "work": (2, 5, "up", None),       # in the chair, facing the desk
    "ops": (5, 4, "up", None),        # in front of the rack
    "read": (7, 4, "up", None),       # in front of the bookshelf
    "wait": (2, 9, "up", None),       # in front of the coffee machine
    "rest": (4, 10, "down", "up"),    # in front of the couch, then sit on it
    "build": (11, 8, "right", None),  # next to the van
    "entry": (6, 10, "down", None),   # just inside the door
}


def obj(i, name, x, y, w, h, props=None, point=False):
    o = {"id": i, "name": name, "type": "", "x": x, "y": y, "width": w, "height": h,
         "rotation": 0, "visible": True}
    if point:
        o["point"] = True
    if props:
        o["properties"] = [{"name": k, "type": "bool" if isinstance(v, bool) else "string", "value": v}
                           for k, v in props.items()]
    return o


def build_map():
    floor, walls = build_layers()
    oid = 1
    prop_objs = []
    for key, (tx, ty, tw, th, collides) in PROPS.items():
        prop_objs.append(obj(oid, key, tx * T, ty * T, tw * T, th * T, {"collides": collides}))
        oid += 1
    zone_objs = []
    for key, (tx, ty, face, seat) in ZONES.items():
        props = {"face": face}
        if seat:
            props["seat"] = seat
        # point at the centre of the tile
        zone_objs.append(obj(oid, key, tx * T + T // 2, ty * T + T // 2, 0, 0, props, point=True))
        oid += 1
    flat = lambda grid: [v for row in grid for v in row]
    return {
        "type": "map", "version": "1.10", "tiledversion": "1.11.0",
        "orientation": "orthogonal", "renderorder": "right-down",
        "width": W, "height": H, "tilewidth": T, "tileheight": T,
        "infinite": False, "nextlayerid": 5, "nextobjectid": oid,
        "properties": [{"name": "office", "type": "string", "value": "office-01"}],
        "tilesets": [{
            "firstgid": 1, "name": "placeholder", "image": "../assets/placeholder/tiles.png",
            "imagewidth": T * len(TILES), "imageheight": T, "tilewidth": T, "tileheight": T,
            "tilecount": len(TILES), "columns": len(TILES), "margin": 0, "spacing": 0,
            "tiles": [{"id": i, "type": name,
                       "properties": [{"name": "collides", "type": "bool", "value": c}]}
                      for i, (name, _b, _a, c) in enumerate(TILES)],
        }],
        "layers": [
            {"id": 1, "name": "floor", "type": "tilelayer", "width": W, "height": H,
             "x": 0, "y": 0, "opacity": 1, "visible": True, "data": flat(floor)},
            {"id": 2, "name": "walls", "type": "tilelayer", "width": W, "height": H,
             "x": 0, "y": 0, "opacity": 1, "visible": True, "data": flat(walls)},
            {"id": 3, "name": "props", "type": "objectgroup", "draworder": "topdown",
             "x": 0, "y": 0, "opacity": 1, "visible": True, "objects": prop_objs},
            {"id": 4, "name": "zones", "type": "objectgroup", "draworder": "topdown",
             "x": 0, "y": 0, "opacity": 1, "visible": True, "objects": zone_objs},
        ],
    }


if __name__ == "__main__":
    draw_tileset(os.path.join(ROOT, "frontend/v3/assets/placeholder/tiles.png"))
    out = os.path.join(ROOT, "frontend/v3/maps/office-01.json")
    with open(out, "w") as f:
        json.dump(build_map(), f, indent=1)
        f.write("\n")
    print("wrote", out)
