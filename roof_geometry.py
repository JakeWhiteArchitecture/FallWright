"""
Fallwright — the warm roof built on the falls.

    build_roof(p, roof, falls) -> (meshes, info)

Bottom to top: the firring zone (one slab per facet, flat on the joists, its top on the
falls; neighbours meet on the vertical plane through their hip or valley, which is the
mitre), then deck, VCL, insulation and membrane, each offset vertically from the one
below. Every layer is clipped to the region minus its holes, minus a kerb's width round
each opening. Sumps are built separately from their own datum. Along the edges: VCL
turn-ups, insulation and membrane upstands, counter-flashings at abutments, the membrane
over a parapet, drip trims at free edges, check kerbs, and a timber kerb with its upstand
round every opening. Outlets are simple cylinders; a hopper is a proxy solid marking the
cut through the wall, a sleeve and a hopper head on the outside face.
"""

import math

from shapely.geometry import Polygon, LineString
from shapely.ops import unary_union

from cladding_booleans import iter_polygons, polygon_to_rings, _difference
from roof_constants import prism, slab, plane_z, rect, EDGE_LABELS
from roof_edges import edge_frame
from roof_falls import region_of, finished_at
from roof_checks import upstand_top

SEGMENTS = 24          # sides on an outlet cylinder
KERB_SAMPLE = 100.0    # mm between samples of the finished level round a kerb


# ── frames ──────────────────────────────────────────────────────────────

def _world_dir(rf, x, y):
    """A roof-local plan direction as a world vector."""
    u, v = rf["u"], rf["v"]
    return [u[0] * x + v[0] * y, u[1] * x + v[1] * y, 0.0]


def _world_pt(rf, x, y, z=0.0):
    o, u, v = rf["origin"], rf["u"], rf["v"]
    return [o[0] + u[0] * x + v[0] * y, o[1] + u[1] * x + v[1] * y, o[2] + z]


def wall_frame(rf, a, b):
    """Standing frame along the plan segment a→b: u along it, v up, n to its right, which
    is out of the roof for an outline edge (rings run with the roof on their left)."""
    e, _m, _l = edge_frame(a, b)
    U = _world_dir(rf, e[0], e[1])
    return {"origin": _world_pt(rf, a[0], a[1]), "u": U, "v": [0.0, 0.0, 1.0], "n": [U[1], -U[0], 0.0]}


def edge_sweep_frame(rf, a, b, t0=0.0):
    """Frame for a section swept along a→b: n along the edge, u into the roof, v up, the
    origin at t0 along the edge."""
    e, m, _l = edge_frame(a, b)
    return {"origin": _world_pt(rf, a[0] + e[0] * t0, a[1] + e[1] * t0),
            "u": _world_dir(rf, m[0], m[1]), "v": [0.0, 0.0, 1.0], "n": _world_dir(rf, e[0], e[1])}


def _strip(profile_bot, top, t0=None, t1=None):
    """A wall-frame profile polygon: along the bottom levels [(t, z)] and back along a level
    *top*, or along *top* given as levels too."""
    bot = [(t, z) for t, z in profile_bot]
    if t0 is not None:
        bot = [(max(t0, min(t1, t)), z) for t, z in bot]
    if isinstance(top, (int, float)):
        ring = bot + [(bot[-1][0], top), (bot[0][0], top)]
    else:
        ring = bot + list(reversed(top))
    out = []
    for q in ring:
        if not out or abs(out[-1][0] - q[0]) > 1e-6 or abs(out[-1][1] - q[1]) > 1e-6:
            out.append(q)
    return out if len(out) >= 3 else None


# ── the build ────────────────────────────────────────────────────────────

def build_roof(p, roof, falls):
    """Meshes for one roof, and the numbers the panel and the exports report."""
    name = roof.get("name") or "Roof"
    rf = roof["frame"]
    meshes = []
    region = region_of(roof)
    edges = falls.get("edges") or []
    sumps, facets = falls["sumps"], falls["facets"]
    kerb_holes = _kerb_holes(roof, edges)
    kerb_bands = [Polygon(h).buffer(p["kerb_w"], join_style=2) for h in kerb_holes]
    cut = unary_union(kerb_bands) if kerb_bands else None
    layers = (("firring", None), ("deck", p["deck_t"]), ("vcl", p["vcl_t"]),
              ("insulation", p["insulation_t"]), ("membrane", p["membrane_t"]))
    for f in facets:
        poly = f["poly"] if cut is None else _difference(f["poly"], cut)
        for part in iter_polygons(poly):
            if part.area < 1.0:
                continue
            ext, holes = polygon_to_rings(part)
            rings = [ext] + holes
            a, b, c = f["plane"]
            base = (0.0, 0.0, 0.0)
            level = 0.0
            for key, t in layers:
                if t is None:
                    meshes.append(slab(rings, base, (a, b, c), rf, "firring", "%s Firring zone %s" % (name, f["id"]),
                                       name, facet=f["id"]))
                    continue
                bot = (a + level, b, c)
                top = (a + level + t, b, c)
                meshes.append(slab(rings, bot, top, rf, key, "%s %s %s" % (name, _LAYER_NAMES[key], f["id"]),
                                   name, facet=f["id"]))
                level += t
    for s in sumps:
        if s["kind"] == "sump":
            meshes += _sump(p, rf, name, s, falls, edges)
    by_id = {e["id"]: e for e in edges}
    for e in edges:
        meshes += _edge_elements(p, rf, name, e, falls)
    for i, h in enumerate(kerb_holes):
        meshes += _kerb(p, rf, name, i, h, falls)
    for o in falls["outlets"]:
        if "point" in o:
            meshes += _outlet(p, rf, name, o, falls, by_id.get(o["edge"]))
    info = {"roof": name, "facets": len(facets), "kerbs": len(kerb_holes),
            "min_depth": falls.get("min_depth"), "max_depth": falls.get("max_depth"),
            "area": round(region.area / 1e6, 2) if not region.is_empty else 0.0}
    return meshes, info


_LAYER_NAMES = {"deck": "Deck", "vcl": "VCL", "insulation": "Insulation", "membrane": "Membrane"}


def _kerb_holes(roof, edges):
    """Hole rings that get a kerb: the openings, unless every edge of one was retyped."""
    holes = ((roof.get("polygons") or [{}])[0].get("holes")) or []
    out = []
    for k, h in enumerate(holes):
        types = [e["type"] for e in edges if e["ring"] == k + 1]
        if "kerb" in types:
            out.append(h)
    return out


# ── sumps ────────────────────────────────────────────────────────────────

def _sump(p, rf, name, s, falls, edges):
    """A sump's own buildup on its floor pieces, and the VCL and membrane dressed down its
    three open sides from the rim to the floor."""
    out = []
    label = "%s Sump %d" % (name, (s.get("outlet") or 0) + 1)
    for j, piece in enumerate(s["floor"]):
        ext, holes = polygon_to_rings(piece["poly"])
        a, b, c = piece["plane"]
        tag = "%s%s" % (label, "" if len(s["floor"]) == 1 else " " + "ab"[j])
        if max(abs(plane_z(piece["plane"], x, y)) for x, y in ext) > 0.5:
            out.append(slab([ext], (0.0, 0.0, 0.0), (a, b, c), rf, "sump_firring", tag + " firrings", name))
        level = 0.0
        for key, t in (("sump_deck", p["deck_t"]), ("sump_vcl", p["vcl_t"]), ("sump_insulation", p["sump_ins"]),
                       ("sump_membrane", p["membrane_t"])):
            out.append(slab([ext], (a + level, b, c), (a + level + t, b, c), rf, key,
                            "%s %s" % (tag, key.replace("sump_", "").replace("vcl", "VCL").title().replace("Vcl", "VCL")), name))
            level += t
    # The open sides, walked with the sump on their right, so the lining's depth runs into it.
    e = next(x for x in edges if x["id"] == s["edge"])
    ev, m, _L = edge_frame(e["a"], e["b"])
    ax, ay = e["a"]
    pt = lambda t, sv: (ax + ev[0] * t + m[0] * sv, ay + ev[1] * t + m[1] * sv)   # noqa: E731
    sides = [(pt(s["b1"], 0.0), pt(s["b1"], s["W"])), (pt(s["b1"], s["W"]), pt(s["b2"], s["W"])),
             (pt(s["b2"], s["W"]), pt(s["b2"], 0.0))]
    for i, (a, b) in enumerate(sides):
        side = LineString([a, b])
        if side.length < 1.0:
            continue
        rim, floor = [], []
        for t in _sample(side.length, 50.0):
            q = side.interpolate(t)
            # The side's normal to the right points into the sump, the roof is to its left.
            _e, n_left, _l = edge_frame(a, b)
            z_rim = finished_at(falls["facets"], [], p, q.x + n_left[0] * 2.0, q.y + n_left[1] * 2.0)
            z_floor = finished_at(falls["facets"], [s], p, q.x - n_left[0] * 2.0, q.y - n_left[1] * 2.0)
            if z_rim is not None and z_floor is not None:
                rim.append((t, z_rim))
                floor.append((t, z_floor))
        if len(rim) < 2:
            continue
        fr = wall_frame(rf, a, b)
        mt, vt = p["membrane_t"], p["vcl_t"]
        memb = _strip(floor, [(t, z) for t, z in rim])
        if memb:
            out.append(prism(memb, 0.0, mt, fr, "sump_membrane", "%s membrane lining %d" % (label, i + 1), name))
        vcl = _strip([(t, z - mt - p["sump_ins"]) for t, z in floor], [(t, z - mt) for t, z in rim])
        if vcl:
            out.append(prism(vcl, mt, vt, fr, "sump_vcl", "%s VCL lining %d" % (label, i + 1), name))
    return out


def _sample(length, step):
    n = max(1, int(math.ceil(length / step)))
    return [length * i / n for i in range(n + 1)]


# ── edges ────────────────────────────────────────────────────────────────

def _edge_elements(p, rf, name, e, falls):
    kind = e["type"]
    prof = e.get("profile") or []
    if len(prof) < 2 or kind in ("kerb", "penetration"):
        return []
    tag = "%s %s %s" % (name, EDGE_LABELS.get(kind, kind), e["id"])
    fr = wall_frame(rf, e["a"], e["b"])
    mt, vt = p["membrane_t"], p["vcl_t"]
    out = []
    pts = [(t, z) for t, z, _s in prof]
    if kind in ("abutment", "parapet"):
        wall = e.get("wall") or {}
        ins_t = p["ins_upstand_t"] if kind == "abutment" else 0.0
        top = upstand_top(p, e)
        # VCL turned up the wall from the deck to the insulation top.
        vcl = _strip([(t, z - mt - (p["sump_ins"] if s else p["insulation_t"]) - vt) for t, z, s in prof],
                     [(t, z - mt) for t, z, _s in prof])
        if vcl:
            out.append(prism(vcl, -vt, vt, fr, "vcl_upstand", tag + " VCL turn-up", name))
        if ins_t > 0:
            ins = _strip([(t, z - mt) for t, z in pts], top - mt)
            if ins:
                out.append(prism(ins, -vt - ins_t, ins_t, fr, "ins_upstand", tag + " insulation upstand", name))
        memb = _strip([(t, z - mt) for t, z in pts], top)
        if memb:
            out.append(prism(memb, -vt - ins_t - mt, mt, fr, "upstand", tag + " membrane upstand", name,
                             upstand_top=top))
        L = float(e["length"])
        off = vt + ins_t + mt
        sweep = edge_sweep_frame(rf, e["a"], e["b"])
        if kind == "abutment":
            lap = p["flash_lap"]
            profile = [(off, top - lap), (off + 3.0, top - lap), (off + 3.0, top + 28.0), (-25.0, top + 28.0),
                       (-25.0, top + 25.0), (off, top + 25.0)]
            out.append(prism(profile, 0.0, L, sweep, "counter_flashing", tag + " counter-flashing", name))
        else:
            thick = float(wall.get("thickness") or p["wall_t"])
            profile = [(-thick - 25.0, top), (off, top), (off, top + mt), (-thick - 25.0, top + mt)]
            out.append(prism(profile, 0.0, L, sweep, "upstand", tag + " membrane over the parapet", name))
        return out
    if kind in ("drip", "gutter"):
        w, h = p["trim_w"], p["trim_h"]
        profile = [(-3.0, -h), (0.0, -h), (0.0, -3.0), (w, -3.0), (w, 0.0), (-3.0, 0.0)]
        return _swept(profile, rf, e, [(t, z - mt) for t, z in pts], "drip_trim", tag + " drip trim", name)
    if kind == "check_kerb":
        w, h = p["kerb_w"], p["check_kerb_h"]
        profile = [(0.0, -mt), (w, -mt), (w, h), (0.0, h)]
        return _swept(profile, rf, e, pts, "kerb", tag + " check kerb", name)
    return out


def _swept(profile, rf, e, levels, ifc_type, label, name):
    """A section swept along an edge, lifted to follow the levels [(t, z)]: one prism per
    straight run between kinks."""
    out = []
    runs = [(t0, z0, t1, z1) for (t0, z0), (t1, z1) in zip(levels, levels[1:]) if t1 - t0 > 1.0]
    for i, (t0, z0, t1, z1) in enumerate(runs):
        fr = edge_sweep_frame(rf, e["a"], e["b"], t0)
        out.append(prism(profile, 0.0, t1 - t0, fr, ifc_type, "%s %d" % (label, i + 1) if len(runs) > 1 else label,
                         name, lift=[z0, z1]))
    return out


# ── kerbs ────────────────────────────────────────────────────────────────

def _kerb(p, rf, name, i, hole, falls):
    """Timber kerb round an opening, its top set so the upstand is the upstand height above
    the finished surface at the kerb's highest point; the insulation and membrane upstand
    round its outer face."""
    inner = Polygon(hole)
    outer = inner.buffer(p["kerb_w"], join_style=2)
    ext, _h = polygon_to_rings(outer)
    hole_ring, _x = polygon_to_rings(inner)
    ring = LineString(list(outer.exterior.coords))
    levels = []
    for t in _sample(ring.length, KERB_SAMPLE):
        q = ring.interpolate(t)
        z = _finished_outside(falls, p, outer, q)
        if z is not None:
            levels.append(z)
    top_fin = max(levels) if levels else p["above_firrings"]
    top = top_fin + p["upstand"]
    label = "%s Kerb %d" % (name, i + 1)
    out = [prism(ext, 0.0, top, rf, "kerb", label, name, holes=[list(reversed(hole_ring))], upstand_top=top)]
    mt, ins_t = p["membrane_t"], p["ins_upstand_t"]
    coords = ext + ext[:1]
    for j, (a, b) in enumerate(zip(coords, coords[1:])):
        side = LineString([a, b])
        if side.length < 1.0:
            continue
        bot = []
        for t in _sample(side.length, 100.0):
            q = side.interpolate(t)
            z = _finished_outside(falls, p, outer, q)
            if z is not None:
                bot.append((t, z - mt))
        if len(bot) < 2:
            continue
        fr = wall_frame(rf, a, b)
        if ins_t > 0:
            ins = _strip(bot, top - mt)
            if ins:
                out.append(prism(ins, 0.0, ins_t, fr, "ins_upstand", "%s insulation upstand %d" % (label, j + 1), name))
        memb = _strip(bot, top)
        if memb:
            out.append(prism(memb, ins_t, mt, fr, "upstand", "%s membrane upstand %d" % (label, j + 1), name,
                             upstand_top=top))
    return out


def _finished_outside(falls, p, outer, q):
    """Finished level just outside a kerb at a point on its outer ring."""
    c = outer.centroid
    dx, dy = q.x - c.x, q.y - c.y
    L = math.hypot(dx, dy) or 1.0
    for step in (2.0, 20.0, 60.0):
        z = finished_at(falls["facets"], falls["sumps"], p, q.x + dx / L * step, q.y + dy / L * step)
        if z is not None:
            return z
    return None


# ── outlets ──────────────────────────────────────────────────────────────

def _outlet(p, rf, name, o, falls, edge):
    s = falls["sumps"][o["k"]]
    x, y = o["point"]
    tag = "%s Outlet %d" % (name, o["n"])
    floor = finished_at(falls["facets"], falls["sumps"], p, x + 0.0, y + 0.0)
    if s["kind"] == "sump":
        floor = s["sf"] + p["sump_buildup"]
    if floor is None:
        floor = s["r"] + p["above_firrings"]
    if o["type"] == "internal":
        r = p["outlet_d"] / 2.0
        circle = [[x + r * math.cos(2 * math.pi * i / SEGMENTS), y + r * math.sin(2 * math.pi * i / SEGMENTS)]
                  for i in range(SEGMENTS)]
        return [prism(circle, -150.0, floor + 150.0, rf, "outlet", tag + " (internal)", name, outlet=o["n"])]
    if edge is None:
        return []
    # A hopper: a rectangular cut through the wall at the sump floor, a sleeve lining it,
    # and a hopper head on the outside face.
    fr = wall_frame(rf, edge["a"], edge["b"])
    t = s["t_out"]
    w, h = p["hopper_w"], p["hopper_h"]
    thick = float((edge.get("wall") or {}).get("thickness") or p["wall_t"])
    cut = rect(t - w / 2, floor, t + w / 2, floor + h)
    sleeve_in = rect(t - w / 2 + 3, floor + 3, t + w / 2 - 3, floor + h - 3)
    return [prism(cut, 0.0, thick, fr, "penetration_cut", tag + " through-wall penetration", name, outlet=o["n"]),
            prism(cut, 0.0, thick, fr, "sleeve", tag + " sleeve", name, holes=[list(reversed(sleeve_in))], outlet=o["n"]),
            prism(rect(t - 150, floor - 300, t + 150, floor + h + 50), thick, 200.0, fr, "hopper",
                  tag + " hopper head", name, outlet=o["n"])]

