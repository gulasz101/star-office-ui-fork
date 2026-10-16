#!/usr/bin/env python3
"""Build one Tiled map per Lodge room type (SOU-195).

Every room is 16x12 tiles with the same shell as office-01: back wall on rows 0..2, caps on
the sides, and the door in the front wall at columns 6..7, opening onto the corridor. Rooms
differ in floor, props and zone placement. Until the per-room art sets land (SOU-199/200),
rooms are composed from the office-01 props; swapping a prop for its new art is a one-line
change here.

Outputs: frontend/v3/maps/rooms/<type>.json   (types: see ROOMS below)
The garage keeps its own hand-tuned map in frontend/v3/maps/office-01.json; this script
writes it as rooms/garage.json with the rack row widened (owner: "rack more prominent").
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from build_office_map import H, T, W, obj  # noqa: E402

# The Lodge tile strip (frontend/v3/src/lodge.js LODGE_TILES, SOU-198 contract). Rooms share
# it with the corridor so the whole building has one palette.
LODGE_TILES = [
    "snow_a", "snow_b", "snow_path", "corridor_floor", "corridor_runner", "wall_cap", "wall_face",
    "wall_base", "stone_step", "room_plank_a", "room_plank_b", "stone_floor", "metal_grate",
    "door_mat", "snow_edge", "spare",
    # appended (SOU-231): garage concrete + the SOU-216 polish tiles, see lodge.js EXTRA_TILES
    "garage_floor", "garage_floor_stain", "stone_floor_soft", "wall_face_soft",
]
COLLIDES = {"wall_cap", "wall_face", "wall_base", "wall_face_soft"}
# Tall props against the back wall whose art declares a one-row footprint (SOU-232): the maps
# list them as (x, 2, w, 2) for readability; they are written as (x, 3, w, 1), which keeps the
# bottom edge (so the drawing) and the blocking (row 2 is wall) exactly the same.
ONE_ROW_BACK = {"gear_shelf_a", "gear_shelf_b", "gear_shelf_full_a", "gear_shelf_full_b", "ups_stack",
                "network_cabinet", "curiosity_shelf", "whiteboard", "token_server", "rack_v2"}
GID = {name: i + 1 for i, name in enumerate(LODGE_TILES)}

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# (tile x, tile y, w, h, collides) per prop instance; names are art keys, repeats allowed.
# zones: id -> (tile x, tile y, face, seat)
ROOMS = {
    # claude-code: the M1 garage studio, two racks side by side
    "garage": {
        "floor": "room_plank_a", "garage": True,
        "props": [
            # the studio half (columns 1-8)
            ("desk", 2, 3, 2, 2, True), ("chair", 2, 5, 1, 1, False),
            ("rack_v2", 4, 2, 2, 2, True), ("bookshelf", 7, 2, 2, 2, True),
            ("coffee", 1, 8, 2, 1, True), ("rug", 4, 6, 3, 2, False), ("couch", 3, 9, 3, 1, True),
            ("bass", 7, 9, 1, 1, True), ("amp", 8, 9, 1, 1, True),
            # the garage bay (columns 10-14): roller door, pegboard, the van parked nose-out
            ("roller_door", 10, 1, 3, 1, False), ("pegboard", 13, 1, 2, 1, False),
            ("van_parked", 10, 5, 3, 3, True),
            ("toolbox", 13, 3, 1, 1, True), ("oil_drum", 14, 3, 1, 1, True), ("tire_stack", 14, 5, 1, 1, True),
            ("cat_box", 13, 9, 2, 1, True), ("spool", 10, 9, 1, 1, True),
        ],
        "zones": {"work": (2, 5, "up", None), "ops": (4, 4, "up", None), "read": (7, 4, "up", None),
                  "wait": (2, 9, "up", None), "rest": (4, 10, "down", "up"),
                  "build": (13, 6, "left", None), "entry": (6, 10, "down", None)},
    },
    # hermes-andrzej: the homelab manager's server room
    "serverroom": {
        "floor": "stone_floor_soft",
        "props": [
            # back wall: racks, gear shelves, the patch cabinet and the UPS
            ("rack_v2", 1, 2, 2, 2, True), ("gear_shelf_full_a", 3, 2, 2, 2, True), ("network_cabinet", 5, 2, 1, 2, True),
            ("gear_shelf_full_b", 10, 2, 2, 2, True), ("rack_v2", 12, 2, 2, 2, True), ("ups_stack", 14, 2, 1, 2, True),
            # the workbench is Andrzej's desk
            ("workbench", 7, 3, 2, 2, True), ("stool", 7, 5, 1, 1, False),
            # middle row with an aisle down the centre
            ("rack_v2", 2, 6, 2, 2, True), ("rack", 4, 6, 1, 2, True),
            ("gear_shelf_full_a", 10, 6, 2, 2, True), ("rack", 12, 6, 1, 2, True),
            ("cable_coil", 5, 9, 1, 1, False), ("cable_coil", 9, 5, 1, 1, False),
            ("coffee", 1, 9, 2, 1, True), ("couch", 11, 9, 3, 1, True),
            ("crate", 14, 6, 1, 1, True), ("crate", 14, 7, 1, 1, True), ("spool", 13, 6, 1, 1, True),
        ],
        "zones": {"work": (7, 5, "up", None), "ops": (3, 8, "up", None), "read": (11, 4, "up", None),
                  "wait": (1, 10, "up", None), "rest": (12, 10, "down", "up"),
                  "build": (13, 7, "right", None), "entry": (6, 10, "down", None)},
    },
    # openchamber / opencode / unknown workers: the universal factory (belts drawn by the renderer)
    "factory": {
        "floor": "metal_grate", "belt_row": 7,  # crossing at the door columns
        "props": [
            ("control_desk", 2, 4, 2, 1, True),
            ("assembler", 5, 2, 2, 2, True), ("assembler", 8, 2, 2, 2, True),
            ("bookshelf", 11, 2, 2, 2, True),
            ("inserter", 4, 6, 1, 1, True), ("inserter", 10, 6, 1, 1, True),
            ("crate", 13, 4, 1, 1, True), ("crate", 14, 4, 1, 1, True), ("crate", 14, 5, 1, 1, True),
            ("ore_pile", 13, 5, 1, 1, True),
            ("coffee", 12, 9, 2, 1, True), ("couch", 2, 9, 3, 1, True), ("ore_pile", 9, 9, 1, 1, True),
        ] + [("belt", x, 7, 1, 1, True) for x in range(1, 15) if x not in (6, 7)],
        "zones": {"work": (2, 5, "up", None), "ops": (5, 4, "up", None), "read": (11, 4, "up", None),
                  "wait": (12, 10, "up", None), "rest": (3, 10, "down", "up"),
                  "build": (12, 5, "right", None), "entry": (6, 10, "down", None)},
    },
    # hermes-default ("Bill Cipher"): the mystery cabin
    "mystery": {
        "floor": "room_plank_b",
        "props": [
            ("curiosity_shelf", 1, 2, 2, 2, True), ("bookshelf", 3, 2, 2, 2, True),
            ("eye_painting", 7, 1, 2, 1, False), ("rack", 10, 2, 1, 2, True),
            ("desk", 12, 3, 2, 2, True), ("chair", 12, 5, 1, 1, False),
            ("strange_rug", 6, 5, 3, 2, False), ("journal_stand", 4, 6, 1, 1, True),
            ("crystal_ball", 9, 7, 1, 1, True), ("lantern_green", 1, 7, 1, 1, True),
            ("couch", 3, 9, 3, 1, True), ("coffee", 12, 8, 2, 1, True),
            ("crate", 14, 10, 1, 1, True), ("crate", 13, 10, 1, 1, True),
        ],
        "zones": {"work": (12, 5, "up", None), "ops": (10, 4, "up", None), "read": (3, 4, "up", None),
                  "wait": (12, 9, "up", None), "rest": (4, 10, "down", "up"),
                  "build": (12, 10, "right", None), "entry": (6, 10, "down", None)},
    },
    # hermes-szarik: the token den
    "tokenden": {
        "floor": "room_plank_a",
        "props": [
            ("token_server", 2, 2, 2, 2, True), ("rack", 4, 2, 1, 2, True),
            ("red_lanterns", 7, 1, 2, 1, False),
            ("abacus_desk", 7, 3, 2, 2, True), ("chair", 7, 5, 1, 1, False), ("bookshelf", 11, 2, 2, 2, True),
            ("lucky_cat", 14, 4, 1, 1, True), ("tea_table", 7, 7, 2, 1, True),
            ("couch", 10, 9, 3, 1, True), ("coffee", 1, 8, 2, 1, True),
            ("token_stacks", 13, 6, 1, 1, True), ("token_stacks", 14, 6, 1, 1, True), ("token_stacks", 14, 7, 1, 1, True),
        ],
        "zones": {"work": (7, 5, "up", None), "ops": (3, 4, "up", None), "read": (11, 4, "up", None),
                  "wait": (2, 9, "up", None), "rest": (11, 10, "down", "up"),
                  "build": (12, 6, "right", None), "entry": (6, 10, "down", None)},
    },
    # hermes-careerpa (Zdzislaw): the career office
    "career": {
        "floor": "room_plank_a",
        "props": [
            ("bookshelf", 1, 2, 2, 2, True), ("framed_certs", 4, 1, 2, 1, False),
            ("advisor_desk", 6, 3, 2, 2, True), ("chair", 6, 5, 1, 1, False),
            ("whiteboard", 9, 2, 2, 2, True), ("rack", 13, 2, 1, 2, True),
            ("ring_light", 11, 5, 1, 1, True), ("coat_stand", 4, 3, 1, 1, True), ("ficus", 14, 4, 1, 1, True),
            ("rug", 5, 7, 3, 2, False), ("couch", 11, 9, 3, 1, True), ("coffee", 1, 8, 2, 1, True),
            ("crate", 14, 6, 1, 1, True), ("crate", 14, 7, 1, 1, True),
        ],
        "zones": {"work": (6, 5, "up", None), "ops": (13, 4, "up", None), "read": (1, 4, "up", None),
                  "wait": (2, 9, "up", None), "rest": (12, 10, "down", "up"),
                  "build": (13, 6, "right", None), "entry": (6, 10, "down", None)},
    },
    # hermes-florian: the writer's loft
    "writer": {
        "floor": "room_plank_b",
        "props": [
            ("typewriter_desk", 2, 3, 2, 2, True), ("chair", 2, 5, 1, 1, False),
            ("paper_stacks_lined", 4, 3, 1, 1, True), ("paper_bin", 1, 5, 1, 1, True),
            ("bookshelf", 5, 2, 2, 2, True), ("bookshelf", 7, 2, 2, 2, True), ("book_tower", 9, 3, 1, 1, True),
            ("cork_board", 10, 1, 2, 1, False), ("rack", 13, 2, 1, 2, True),
            ("reading_chair", 10, 6, 1, 1, True), ("rug", 6, 6, 3, 2, False), ("couch", 3, 9, 3, 1, True),
            ("coffee", 12, 8, 2, 1, True), ("paper_stacks_lined", 14, 10, 1, 1, True), ("paper_stacks_lined", 13, 10, 1, 1, True),
        ],
        "zones": {"work": (2, 5, "up", None), "ops": (13, 4, "up", None), "read": (7, 4, "up", None),
                  "wait": (12, 9, "up", None), "rest": (4, 10, "down", "up"),
                  "build": (12, 10, "right", None), "entry": (6, 10, "down", None)},
    },
}

PARTITION_X, GARAGE_X0 = 9, 10


def build_layers(room):
    floor = [[0] * W for _ in range(H)]
    walls = [[0] * W for _ in range(H)]
    for y in range(1, H - 1):
        for x in range(1, W - 1):
            if room.get("garage") and x >= GARAGE_X0:
                floor[y][x] = GID["garage_floor_stain"] if (x, y) in ((11, 9), (13, 7)) else GID["garage_floor"]
            else:
                floor[y][x] = GID[room["floor"]]
    for x in range(W):
        walls[0][x] = GID["wall_cap"]
        walls[1][x] = GID["wall_face_soft"]
        walls[2][x] = GID["wall_base"] if 1 <= x <= W - 2 else GID["wall_cap"]
        walls[H - 1][x] = GID["wall_cap"]
    for y in range(H):
        walls[y][0] = walls[y][W - 1] = GID["wall_cap"]
    for x in (6, 7):
        walls[H - 1][x] = 0
        floor[H - 1][x] = GID["door_mat"]
    if room.get("garage"):
        for y in range(3, 6):  # a short partition: the bay opens wide onto the studio
            walls[y][PARTITION_X] = GID["wall_cap"]
    return floor, walls


def build_map(name, room):
    floor, walls = build_layers(room)
    oid = 1
    prop_objs, zone_objs = [], []
    for key, tx, ty, tw, th, collides in room["props"]:
        if key in ONE_ROW_BACK and ty == 2 and th == 2:
            ty, th = 3, 1
        prop_objs.append(obj(oid, key, tx * T, ty * T, tw * T, th * T, {"collides": collides}))
        oid += 1
    for key, (tx, ty, face, seat) in room["zones"].items():
        props = {"face": face}
        if seat:
            props["seat"] = seat
        zone_objs.append(obj(oid, key, tx * T + T // 2, ty * T + T // 2, 0, 0, props, point=True))
        oid += 1
    flat = lambda grid: [v for row in grid for v in row]
    props = [{"name": "room", "type": "string", "value": name}]
    if "belt_row" in room:
        props.append({"name": "belt_row", "type": "int", "value": room["belt_row"]})
    return {
        "type": "map", "version": "1.10", "tiledversion": "1.11.0",
        "orientation": "orthogonal", "renderorder": "right-down",
        "width": W, "height": H, "tilewidth": T, "tileheight": T,
        "infinite": False, "nextlayerid": 5, "nextobjectid": oid,
        "properties": props,
        "tilesets": [{
            "firstgid": 1, "name": "lodge", "image": "../../assets/lodge/tiles.png",
            "imagewidth": T * len(LODGE_TILES), "imageheight": T, "tilewidth": T, "tileheight": T,
            "tilecount": len(LODGE_TILES), "columns": len(LODGE_TILES), "margin": 0, "spacing": 0,
            "tiles": [{"id": i, "type": n, "properties": [{"name": "collides", "type": "bool", "value": n in COLLIDES}]}
                      for i, n in enumerate(LODGE_TILES)],
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
    out_dir = os.path.join(ROOT, "frontend/v3/maps/rooms")
    os.makedirs(out_dir, exist_ok=True)
    for name, room in ROOMS.items():
        path = os.path.join(out_dir, f"{name}.json")
        with open(path, "w") as f:
            json.dump(build_map(name, room), f, indent=1)
            f.write("\n")
        print("wrote", os.path.relpath(path, ROOT))
