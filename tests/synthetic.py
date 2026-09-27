"""Synthetic IFC-style geometry for tests: a wall face at an angle with a window
opening, a balcony slab crossing the face, and a pipe penetration. Coordinates
are IFC mm, Z-up, like the payload the browser sends."""

import math

ANGLE = math.radians(30.0)       # wall runs at 30 degrees to the X axis
U = (math.cos(ANGLE), math.sin(ANGLE), 0.0)      # along the wall
N = (math.sin(ANGLE), -math.cos(ANGLE), 0.0)     # outward normal (u = z x n)
ORIGIN = (10000.0, 5000.0, 100.0)               # bottom-left corner of the face


def world(u, v, d=0.0):
    return (ORIGIN[0] + U[0] * u + N[0] * d, ORIGIN[1] + U[1] * u + N[1] * d, ORIGIN[2] + v)


def rect_tris(u0, v0, u1, v1, d=0.0):
    a, b, c, e = world(u0, v0, d), world(u1, v0, d), world(u1, v1, d), world(u0, v1, d)
    return [[a, b, c], [a, c, e]]


def box_tris(u0, u1, v0, v1, d0, d1):
    """Closed box in wall-local coords (u along, v up, d outward)."""
    tris = []
    tris += rect_tris(u0, v0, u1, v1, d0) + rect_tris(u0, v0, u1, v1, d1)
    for u in (u0, u1):
        p = [world(u, v0, d0), world(u, v1, d0), world(u, v1, d1), world(u, v0, d1)]
        tris += [[p[0], p[1], p[2]], [p[0], p[2], p[3]]]
    for v in (v0, v1):
        p = [world(u0, v, d0), world(u1, v, d0), world(u1, v, d1), world(u0, v, d1)]
        tris += [[p[0], p[1], p[2]], [p[0], p[2], p[3]]]
    return tris


def prism_tris(profile_uv, d0, d1):
    """Closed prism: a (u, v) polygon extruded along the wall normal from d0 to d1."""
    n = len(profile_uv)
    front = [world(u, v, d0) for u, v in profile_uv]
    back = [world(u, v, d1) for u, v in profile_uv]
    tris = []
    for i in range(1, n - 1):
        tris.append([front[0], front[i], front[i + 1]])
        tris.append([back[0], back[i + 1], back[i]])
    for i in range(n):
        j = (i + 1) % n
        tris.append([front[i], front[j], back[j]])
        tris.append([front[i], back[j], back[i]])
    return [[list(p) for p in t] for t in tris]


def pitched_roof(u0=5000.0, ridge=6500.0, u1=8000.0, eaves=1500.0, apex=2400.0, t=200.0):
    """Two sloped roof slabs meeting at a ridge, straddling the wall face (a gable)."""
    south = prism_tris([(u0, eaves), (ridge, apex), (ridge, apex + t), (u0, eaves + t)], -100.0, 3000.0)
    north = prism_tris([(ridge, apex), (u1 + 300, eaves), (u1 + 300, eaves + t), (ridge, apex + t)], -100.0, 3000.0)
    return south, north


def wall_face(width=8000.0, height=3000.0, window=(2000.0, 900.0, 3200.0, 2100.0)):
    """Outer face triangles with a rectangular window hole (4 rects around it)."""
    wu0, wv0, wu1, wv1 = window
    tris = []
    tris += rect_tris(0, 0, width, wv0)
    tris += rect_tris(0, wv1, width, height)
    tris += rect_tris(0, wv0, wu0, wv1)
    tris += rect_tris(wu1, wv0, width, wv1)
    # duplicate inverted twin of one triangle (double-sided mesh noise)
    a, b, c = tris[0]
    tris.append([a, c, b])
    return [[list(p) for p in t] for t in tris]


def wall_face_with_door(width=8000.0, height=3000.0, window=(2000.0, 900.0, 3200.0, 2100.0),
                        door=(5000.0, 5900.0, 2100.0)):
    """The same face with a door added. A door runs to the foot of the wall, so it is a
    notch in the outline rather than an interior hole."""
    wu0, wv0, wu1, wv1 = window
    du0, du1, dv1 = door
    tris = []
    tris += rect_tris(0, wv1, width, height)      # over both openings
    tris += rect_tris(0, 0, wu0, wv1)             # left of the window, full height
    tris += rect_tris(wu0, 0, wu1, wv0)           # under the window
    tris += rect_tris(wu1, 0, du0, wv1)           # between the two
    tris += rect_tris(du1, 0, width, wv1)         # right of the door
    return [[list(p) for p in t] for t in tris]


def payload(name="Elevation A", pitched=False):
    slab = box_tris(4500.0, 8000.0, 2000.0, 2200.0, -300.0, 1500.0)     # balcony slab through face
    roof = box_tris(-500.0, 1500.0, 1400.0, 1600.0, -100.0, 2500.0)     # lower flat roof abutting left
    pipe = box_tris(6000.0, 6100.0, 300.0, 400.0, -200.0, 200.0)        # pipe penetration
    floor = box_tris(0.0, 8000.0, 1450.0, 1700.0, -900.0, -300.0)       # internal slab, does not reach face
    context = [
        {"type": "IfcSlab", "name": "Balcony", "tris": [[list(p) for p in t] for t in slab]},
        {"type": "IfcRoof", "name": "Lower roof", "tris": [[list(p) for p in t] for t in roof]},
        {"type": "IfcPipeSegment", "name": "SVP", "tris": [[list(p) for p in t] for t in pipe]},
        {"type": "IfcSlab", "name": "First floor", "tris": [[list(p) for p in t] for t in floor]},
    ]
    if pitched:   # replace the balcony with a gable roof against the right half of the wall
        south, north = pitched_roof()
        context = [c for c in context if c["name"] != "Balcony"]
        context += [{"type": "IfcRoof", "name": "Gable roof S", "tris": south},
                    {"type": "IfcRoof", "name": "Gable roof N", "tris": north}]
    return {"name": name, "faces": wall_face(), "outward": list(N), "context": context,
            "options": {"penetrations": True}}


def corner_payload(name="Elevation B"):
    """A second wall face turning an external corner at the right end of the first
    (its outward normal is the first wall's +u direction), 4000 long, 3000 high."""
    n2 = (U[0], U[1], 0.0)                 # faces along the first wall's u direction
    u2 = (-n2[1], n2[0], 0.0)              # = z × n2, runs away from the corner (-N direction)
    corner = world(8000.0, 0.0, 0.0)       # corner point on both faces
    def w2(u, v):
        return (corner[0] + u2[0] * u, corner[1] + u2[1] * u, corner[2] + v)
    a, b, c, d = w2(0, 0), w2(4000, 0), w2(4000, 3000), w2(0, 3000)
    return {"name": name, "faces": [[list(a), list(b), list(c)], [list(a), list(c), list(d)]],
            "outward": list(n2), "context": [], "options": {}}


# ── Fallwright: synthetic roofs ─────────────────────────────────────────────
# A flat roof 10000 x 6000 whose structure tops out at ROOF_Z, turned ROOF_ANGLE off the
# world axes so the roof frame has something to square up to. Roof-local (x, y): x along
# the long side, y across it, origin at the south-west corner of the structure's top.

ROOF_ANGLE = math.radians(20.0)
RU = (math.cos(ROOF_ANGLE), math.sin(ROOF_ANGLE), 0.0)
RV = (-math.sin(ROOF_ANGLE), math.cos(ROOF_ANGLE), 0.0)
ROOF_ORIGIN = (2000.0, -3000.0, 3000.0)
ROOF_Z = ROOF_ORIGIN[2]


def rworld(x, y, z=0.0):
    """Roof-local (x, y, height above the structure) to IFC world."""
    return [ROOF_ORIGIN[0] + RU[0] * x + RV[0] * y, ROOF_ORIGIN[1] + RU[1] * x + RV[1] * y, ROOF_ORIGIN[2] + z]


def rbox(x0, y0, z0, x1, y1, z1):
    """Closed box in roof-local coordinates, as world triangles."""
    c = [rworld(x, y, z) for z in (z0, z1) for y in (y0, y1) for x in (x0, x1)]
    # corners: 0 (x0,y0,z0) 1 (x1,y0,z0) 2 (x0,y1,z0) 3 (x1,y1,z0), 4..7 the same at z1
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = []
    for a, b, cc, d in quads:
        tris += [[c[a], c[b], c[cc]], [c[a], c[cc], c[d]]]
    return tris


def roof_top_faces(width=10000.0, depth=6000.0, hole=None):
    """Top face of the structure, optionally with a rectangular hole (x0, y0, x1, y1)."""
    if hole is None:
        return [[rworld(0, 0), rworld(width, 0), rworld(width, depth)],
                [rworld(0, 0), rworld(width, depth), rworld(0, depth)]]
    x0, y0, x1, y1 = hole
    tris = []
    for a, b, c, d in ((0, 0, width, y0), (0, y1, width, depth), (0, y0, x0, y1), (x1, y0, width, y1)):
        tris += [[rworld(a, b), rworld(c, b), rworld(c, d)], [rworld(a, b), rworld(c, d), rworld(a, d)]]
    return tris


def roof_payload(name="Roof 1", hole=(4000.0, 2500.0, 5200.0, 3700.0), pipe=True, parapet_rise=1000.0,
                 wall_rise=3000.0, door=True):
    """The sample roof: an abutment wall along the north side (y = 6000, rising wall_rise)
    with a door in it, a parapet along the east side (x = 10000, rising parapet_rise), and
    free edges south and west. A rooflight hole and a pipe through the deck."""
    context = [
        {"type": "IfcWall", "name": "North wall", "tris": rbox(-300, 6000, -3000, 10300, 6300, wall_rise)},
        {"type": "IfcWall", "name": "East parapet", "tris": rbox(10000, 0, -3000, 10300, 6000, parapet_rise)},
        {"type": "IfcSlab", "name": "Roof slab", "tris": rbox(0, 0, -250, 10000, 6000, 0)},
    ]
    if door:
        context.append({"type": "IfcDoor", "name": "Roof door", "tris": rbox(2000, 6000, 360, 2900, 6300, 2460)})
    if pipe:
        context.append({"type": "IfcPipeSegment", "name": "SVP", "tris": rbox(8000, 1000, -500, 8110, 1110, 900)})
    return {"name": name, "faces": roof_top_faces(hole=hole), "outward": [0.0, 0.0, 1.0],
            "seeds": [rworld(1000, 1000)], "context": context, "options": {"penetrations": True}}


def l_roof_payload(name="L roof"):
    """An L-shaped roof with no walls: a 10000 x 4000 wing along x, and a 4000 x 6000 wing
    going up from its west end (so the re-entrant corner is at (4000, 4000))."""
    tris = [[rworld(0, 0), rworld(10000, 0), rworld(10000, 4000)], [rworld(0, 0), rworld(10000, 4000), rworld(0, 4000)],
            [rworld(0, 4000), rworld(4000, 4000), rworld(4000, 10000)], [rworld(0, 4000), rworld(4000, 10000), rworld(0, 10000)]]
    return {"name": name, "faces": tris, "outward": [0.0, 0.0, 1.0], "seeds": [], "context": [], "options": {}}


def rect_roof(width=10000.0, depth=6000.0, holes=()):
    """A bare extracted-roof record (no walls, every edge a free edge), built directly rather
    than through extraction so the falls tests see exact numbers. Edges run anticlockwise
    from the origin: E1 south (y = 0), E2 east, E3 north, E4 west."""
    from roof_edges import classify
    ext = [[0.0, 0.0], [width, 0.0], [width, depth], [0.0, depth]]
    polygons = [{"exterior": ext, "holes": [list(h) for h in holes]}]
    frame = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}
    return {"ok": True, "name": "Roof 1", "frame": frame, "datum_z": 0.0, "polygons": polygons,
            "width": width, "height": depth, "edges": classify(polygons, []), "holes": []}


def l_roof():
    """L-shaped bare roof record: see l_roof_payload. Edges anticlockwise from the origin:
    E1 south, E2 east (x = 10000), E3 (y = 4000 back to x = 4000), E4 (x = 4000 up), E5 north
    of the upper wing, E6 west."""
    from roof_edges import classify
    ext = [[0.0, 0.0], [10000.0, 0.0], [10000.0, 4000.0], [4000.0, 4000.0], [4000.0, 10000.0], [0.0, 10000.0]]
    polygons = [{"exterior": ext, "holes": []}]
    frame = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}
    return {"ok": True, "name": "L roof", "frame": frame, "datum_z": 0.0, "polygons": polygons,
            "width": 10000.0, "height": 10000.0, "edges": classify(polygons, []), "holes": []}
