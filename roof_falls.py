"""
Fallwright — the falls engine.

The outlets drive everything. Each outlet with a sump, each internal outlet without one,
and each gutter edge is a *sump* k on a drain edge j, running t = b1..b2 along the edge and
W in from it, with its rim at firring depth r. For a point p with s = distance in from the
edge and t = distance along it:

    e_k(p) = max(b1 - t, 0, t - b2)
    f_k(p) = max((s - W) / G, e_k / Gc)
    firring depth(p) = min over the sumps whose edge faces p (s >= 0) of r_k + f_k(p)

f_k is a max of four planes, so the surface is a min of maxes of planes and every facet is
exactly planar. The facets are built with Shapely: each sump splits into a main-fall piece
and two cricket pieces by half-planes, and each piece is cut where another sump is lower.
The sump rectangles come out of the region first; they are built separately.

Also here: the sump datum (rims and drops), the creases (valleys, hips, ridges) with their
falls, 25 mm depth contours, spot levels, the trapped water trace, and the finished level
along every edge. Coordinates are roof-local plan (u, v) mm; depths are above the
structure (the top of the joists).
"""

import math

from shapely import set_precision
from shapely.geometry import Polygon, LineString, Point, box
from shapely.ops import unary_union, linemerge
from shapely.prepared import prep

from cladding_booleans import iter_polygons, _difference, _intersection
from roof_constants import (_parse, MIN_FACET_AREA, STD_SUMP_L, STD_SUMP_W, CONTOUR_STEP, TRACE_STEP,
                            EDGE_LABELS, fall_label, plane_z)
from roof_edges import edge_frame

GRID = 0.01            # mm: every facet is snapped to this grid so neighbours share vertices
EPS = 1e-9
TIE = 1e-6
NOISE_AREA = 1000.0    # mm²: slivers smaller than this merge quietly


# ── the roof's edges, outlets and sumps ──────────────────────────────────

def roof_edges(roof):
    """The roof's edges with the user's overrides applied (roof["edge_types"])."""
    over = roof.get("edge_types") or {}
    out = []
    for e in roof.get("edges") or []:
        e = dict(e)
        t = over.get(e["id"])
        if t in EDGE_LABELS:
            e["type"] = t
        e["label"] = EDGE_LABELS.get(e["type"], e["type"])
        out.append(e)
    return out


def region_of(roof):
    pg = (roof.get("polygons") or [None])[0]
    if not pg:
        return Polygon()
    poly = Polygon(pg["exterior"], pg.get("holes") or [])
    return poly if poly.is_valid else poly.buffer(0)


def _edge_point(e, t, s=0.0):
    ev, m, _l = edge_frame(e["a"], e["b"])
    return (e["a"][0] + ev[0] * t + m[0] * s, e["a"][1] + ev[1] * t + m[1] * s)


def _edge_plane(e, const, cs, ct):
    """Plane z = const + cs·s + ct·t in the edge's (s, t), as (a, b, c) in plan (x, y)."""
    ev, m, _l = edge_frame(e["a"], e["b"])
    ax, ay = e["a"]
    am, ae = ax * m[0] + ay * m[1], ax * ev[0] + ay * ev[1]
    return (const - cs * am - ct * ae, cs * m[0] + ct * ev[0], cs * m[1] + ct * ev[1])


def resolve(roof, p):
    """(sumps, outlets): every drain on the roof with its datum worked out.

    A sump sets the datum. Its floor is the structure, sump firrings sf, deck, VCL, the
    sump insulation and the membrane; a sump larger than standard falls at 1:Gs, so its
    floor rises rho from the outlet to its highest point. The rim is at least the minimum
    drop above that, and the main roof firrings are never under d_min, so

        r    = max(d_min, sf + si + rho + drop - T)
        drop = r + T - (sf + si + rho)      (deck, VCL and membrane cancel)
    """
    edges = {e["id"]: e for e in roof_edges(roof)}
    T, si, d_min, Gs = p["insulation_t"], p["sump_ins"], p["d_min"], p["sump_fall"]
    sumps, outlets = [], []
    for i, o in enumerate(roof.get("outlets") or []):
        e = edges.get(o.get("edge"))
        rec = {"n": i + 1, "type": "hopper" if o.get("type") == "hopper" else "internal",
               "edge": o.get("edge"), "corner": "b" if o.get("corner") == "b" else "a",
               "offset": float(o.get("offset") or 0.0), "errors": []}
        outlets.append(rec)
        if e is None or e["ring"] != 0:
            rec["errors"].append("not on the roof outline")
            continue
        L = float(e["length"])
        t = rec["offset"] if rec["corner"] == "a" else L - rec["offset"]
        rec["t"], rec["edge_length"], rec["edge_type"] = t, L, e["type"]
        if t < -0.5 or t > L + 0.5:
            rec["errors"].append("offset %.0f is past the end of %s (%.0f long)" % (rec["offset"], e["id"], L))
        t_c = min(L, max(0.0, t))
        has_sump = bool(o.get("sump", rec["type"] == "hopper"))
        rec["sump"] = has_sump
        k = len(sumps)
        if has_sump:
            Ls = float(o["sump_l"]) if o.get("sump_l") is not None else p["sump_l"]
            Ws = float(o["sump_w"]) if o.get("sump_w") is not None else p["sump_w"]
            Ls, Ws = max(0.0, Ls), max(0.0, Ws)
            if o.get("sump_t0") is None:           # centred on the outlet, shifted to fit the edge
                b1 = t_c - Ls / 2.0
                if Ls <= L:
                    b1 = min(max(b1, 0.0), L - Ls)
            else:
                b1 = float(o["sump_t0"])
            b2 = b1 + Ls
            sf = float(o["sump_firring"]) if o.get("sump_firring") is not None else p["sump_firring"]
            along, across = Ls > STD_SUMP_L + 0.5, Ws > STD_SUMP_W + 0.5
            t_in = min(b2, max(b1, t_c))
            rho = (max(t_in - b1, b2 - t_in) / Gs if along else 0.0) + (Ws / Gs if across else 0.0)
            want = float(o["drop"]) if o.get("drop") is not None else p["sump_drop"]
            r = max(d_min, sf + si + rho + want - T)
            sump = {"k": k, "kind": "sump", "edge": e["id"], "b1": b1, "b2": b2, "W": Ws, "r": r,
                    "sf": sf, "rho": rho, "along": along, "across": across, "t_out": t_c, "t_in": t_in,
                    "drop": r + T - (sf + si + rho), "drop_asked": want, "outlet": i}
            if not (b1 - 0.5 <= t <= b2 + 0.5):
                rec["errors"].append("outlet is outside its sump")
        else:
            sump = {"k": k, "kind": "point", "edge": e["id"], "b1": t_c, "b2": t_c, "W": 0.0, "r": d_min,
                    "sf": 0.0, "rho": 0.0, "along": False, "across": False, "t_out": t_c, "t_in": t_c,
                    "drop": None, "outlet": i}
        rec["k"] = k
        sumps.append(sump)
    for e in edges.values():
        if e["ring"] == 0 and e["type"] == "gutter":
            sumps.append({"k": len(sumps), "kind": "gutter", "edge": e["id"], "b1": 0.0, "b2": float(e["length"]),
                          "W": 0.0, "r": d_min, "sf": 0.0, "rho": 0.0, "along": False, "across": False,
                          "t_out": None, "t_in": None, "drop": None, "outlet": None})
    for s in sumps:
        e = edges[s["edge"]]
        s["planes"] = _sump_planes(e, s, p)
        s["facing"] = _edge_plane(e, 0.0, 1.0, 0.0)             # s >= 0
        s["rect"] = _sump_rect(e, s) if s["kind"] == "sump" else None
        s["floor"] = _floor_pieces(e, s, p) if s["kind"] == "sump" else []
    for rec in outlets:
        if "k" in rec:
            s = sumps[rec["k"]]
            e = edges[rec["edge"]]
            inset = 0.0 if s["kind"] == "sump" else p["outlet_d"] / 2.0 + 10.0   # flange against the edge
            rec["point"] = list(_edge_point(e, s["t_out"], inset if rec["type"] == "internal" else 0.0))
    return sumps, outlets


def _sump_planes(e, s, p):
    """The four planes f_k is the max of: main, cricket from b1, cricket from b2, flat."""
    G, Gc, r, W = p["fall"], p["cricket_fall"], s["r"], s["W"]
    return {"main": _edge_plane(e, r - W / G, 1.0 / G, 0.0),
            "left": _edge_plane(e, r + s["b1"] / Gc, 0.0, -1.0 / Gc),
            "right": _edge_plane(e, r - s["b2"] / Gc, 0.0, 1.0 / Gc),
            "flat": (r, 0.0, 0.0)}


def _sump_rect(e, s):
    return Polygon([_edge_point(e, s["b1"]), _edge_point(e, s["b2"]),
                    _edge_point(e, s["b2"], s["W"]), _edge_point(e, s["b1"], s["W"])])


def _floor_pieces(e, s, p):
    """The sump floor's firring top as planar pieces: level for a standard sump; a larger
    one falls along to the outlet from both ends, and across to the wall, at 1:Gs."""
    Gs, sf = p["sump_fall"], s["sf"]
    across = 1.0 / Gs if s["across"] else 0.0
    spans = [(s["b1"], s["t_in"], -1.0), (s["t_in"], s["b2"], 1.0)] if s["along"] else [(s["b1"], s["b2"], 0.0)]
    out = []
    for t0, t1, sign in spans:
        if t1 - t0 < 0.5:
            continue
        ct = sign / Gs if s["along"] else 0.0
        plane = _edge_plane(e, sf - ct * s["t_in"], across, ct)
        poly = Polygon([_edge_point(e, t0), _edge_point(e, t1), _edge_point(e, t1, s["W"]), _edge_point(e, t0, s["W"])])
        out.append({"poly": poly, "plane": plane})
    return out


# ── half-planes ──────────────────────────────────────────────────────────

def _clip(poly, f):
    """Convex polygon (list of (x, y)) clipped to f(x, y) = a + b·x + c·y >= 0."""
    a, b, c = f
    out = []
    n = len(poly)
    for i in range(n):
        p0, p1 = poly[i], poly[(i + 1) % n]
        v0, v1 = a + b * p0[0] + c * p0[1], a + b * p1[0] + c * p1[1]
        if v0 >= 0:
            out.append(p0)
        if (v0 >= 0) != (v1 >= 0):
            t = v0 / (v0 - v1)
            out.append((p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t))
    return out


def _sub(p1, p2):
    return (p1[0] - p2[0], p1[1] - p2[1], p1[2] - p2[2])


def _convex(bbox, fs, ties=()):
    """bbox ∩ every half-plane f >= 0. A constant f (two parallel planes) is all or nothing;
    *ties* lists the ones that keep an exact tie."""
    poly = bbox
    for i, f in enumerate(fs):
        if abs(f[1]) < EPS and abs(f[2]) < EPS:
            keep = f[0] > TIE or (abs(f[0]) <= TIE and i in ties)
            if not keep:
                return []
            continue
        poly = _clip(poly, f)
        if len(poly) < 3:
            return []
    return poly


# ── the surface ──────────────────────────────────────────────────────────

def build_facets(roof, p, sumps):
    """(facets, unreached, merged) for the roof. Each facet: {"poly", "plane", "sump",
    "kind"}; unreached is the geometry no drain edge faces; merged counts the small
    facets folded into a neighbour."""
    region = region_of(roof)
    if region.is_empty:
        return [], None, 0
    rects = [s["rect"] for s in sumps if s.get("rect") is not None]
    R = region
    if rects:
        R = _difference(region, unary_union(rects)) or region
    x0, y0, x1, y1 = region.bounds
    grow = (x1 - x0) + (y1 - y0) + 1000.0
    bbox = [(x0 - grow, y0 - grow), (x1 + grow, y0 - grow), (x1 + grow, y1 + grow), (x0 - grow, y1 + grow)]
    kinds = ("main", "left", "right")
    pieces = []
    for s in sumps:
        pl = s["planes"]
        for kind in kinds:
            me = pl[kind]
            others = [pl[o] for o in ("main", "left", "right", "flat") if o != kind]
            convex = _convex(bbox, [s["facing"]] + [_sub(me, o) for o in others], ties=range(1, 4))
            if not convex:
                continue
            shape = _intersection(Polygon(convex), R)
            if shape is None or shape.is_empty:
                continue
            for t in sumps:
                if t is s:
                    continue
                # Where sump t is lower: facing it, and below this plane on all four of its
                # planes. An exact tie goes to the sump listed first.
                lower = _convex(bbox, [t["facing"]] + [_sub(me, q) for q in t["planes"].values()],
                                ties=range(1, 5) if t["k"] < s["k"] else ())
                if lower:
                    shape = _difference(shape, Polygon(lower))
                    if shape is None or shape.is_empty:
                        break
            if shape is None or shape.is_empty:
                continue
            pieces.append({"shape": shape, "plane": me, "sump": s["k"],
                           "kind": "main" if kind == "main" else "cricket"})
    facets = _merge_coplanar(pieces)
    facets, merged = _merge_small(facets)
    covered = unary_union([f["poly"] for f in facets]) if facets else Polygon()
    unreached = _difference(R, covered) if facets else R
    unreached = [pg for pg in iter_polygons(unreached) if pg.area > NOISE_AREA] if unreached is not None else []
    return facets, unreached, merged


def _plane_key(pl):
    return (round(pl[0], 4), round(pl[1], 9), round(pl[2], 9))


def _merge_coplanar(pieces):
    """One facet per connected run of pieces on the same plane (two sumps on one edge with
    the same rim share their main fall), snapped to a common grid."""
    groups = {}
    for pc in pieces:
        groups.setdefault(_plane_key(pc["plane"]), []).append(pc)
    facets = []
    for key, group in groups.items():
        shape = unary_union([g["shape"] for g in group])
        shape = set_precision(shape, GRID)
        for pg in iter_polygons(shape):
            if pg.area <= 1.0:
                continue
            facets.append({"poly": pg, "plane": group[0]["plane"], "sump": min(g["sump"] for g in group),
                           "kind": group[0]["kind"]})
    facets.sort(key=lambda f: (f["sump"], f["kind"] != "main", round(f["poly"].centroid.y), round(f["poly"].centroid.x)))
    return facets


def _merge_small(facets):
    """Fold every facet under MIN_FACET_AREA into the neighbour it shares most boundary
    with. It takes that neighbour's plane, which lies above the true surface there (the
    surface is a lower envelope), so the firrings only get deeper. Returns (facets, the
    number merged that were big enough to report). A small facet with no neighbour stays
    and is marked, so the check can report it."""
    merged, alone = 0, set()
    while True:
        small = [f for f in facets if f["poly"].area < MIN_FACET_AREA and id(f) not in alone]
        if not small:
            return facets, merged
        f = min(small, key=lambda x: x["poly"].area)
        best, share = None, 0.0
        for g in facets:
            if g is f:
                continue
            try:
                length = f["poly"].boundary.intersection(g["poly"].buffer(0.05)).length
            except Exception:   # noqa: BLE001
                length = 0.0
            if length > share + 1e-6:
                best, share = g, length
        if best is None:
            if f["poly"].area < NOISE_AREA:
                facets.remove(f)            # an isolated sliver: nothing to fold it into
            else:
                f["small"] = True
                alone.add(id(f))
            continue
        joined = set_precision(unary_union([best["poly"], f["poly"]]), GRID)
        parts = sorted(iter_polygons(joined), key=lambda pg: -pg.area)
        best["poly"] = parts[0] if parts else best["poly"]
        facets.remove(f)
        if f["poly"].area >= NOISE_AREA:
            merged += 1
            best["merged"] = best.get("merged", 0) + 1


# ── what the surface gives ───────────────────────────────────────────────

def gradient(plane):
    return plane[1], plane[2]


def fall_of(plane):
    """(ratio 1:X, downhill unit (dx, dy)) of a plane; ratio None when level."""
    gx, gy = gradient(plane)
    g = math.hypot(gx, gy)
    if g < 1e-9:
        return None, (0.0, 0.0)
    return 1.0 / g, (-gx / g, -gy / g)


def creases(facets):
    """Every line two facets share: a valley where water collects (the surface folds up
    either side), a hip where the falls to two different drain edges meet, a ridge where
    two falls to the same edge part (the cricket ridge between sumps). Each with its fall
    along the line."""
    out = []
    for i, a in enumerate(facets):
        for j in range(i + 1, len(facets)):
            b = facets[j]
            if not a["poly"].buffer(0.1).intersects(b["poly"]):
                continue
            try:
                shared = a["poly"].boundary.intersection(b["poly"].buffer(0.05))
            except Exception:   # noqa: BLE001
                continue
            lines = [g for g in _lines(shared) if g.length > 5.0]
            if not lines:
                continue
            merged = linemerge(lines) if len(lines) > 1 else lines[0]
            for ln in _lines(merged):
                if ln.length <= 5.0:
                    continue
                (x0, y0), (x1, y1) = _snap(ln.coords[0], a, b), _snap(ln.coords[-1], a, b)
                kind = _crease_kind(a, b, ln)
                z0, z1 = plane_z(a["plane"], x0, y0), plane_z(a["plane"], x1, y1)
                dz = abs(z1 - z0)
                length = math.hypot(x1 - x0, y1 - y0)
                ratio = length / dz if dz > 1e-6 else None
                lo, hi = ((x0, y0), (x1, y1)) if z0 <= z1 else ((x1, y1), (x0, y0))
                out.append({"kind": kind, "a": [round(lo[0], 2), round(lo[1], 2)],
                            "b": [round(hi[0], 2), round(hi[1], 2)], "length": round(length, 2),
                            "fall": round(ratio, 2) if ratio else None, "label": fall_label(ratio),
                            "facets": [a["id"], b["id"]] if "id" in a else [i, j]})
    return out


def _snap(pt, a, b, tol=0.5):
    """A crease end moved onto the facet corner it came from (the shared boundary is found
    through a hair's-width buffer, which leaves its ends a hair off)."""
    best, dist = pt, tol
    for f in (a, b):
        for q in f["poly"].exterior.coords:
            d = math.hypot(q[0] - pt[0], q[1] - pt[1])
            if d < dist:
                best, dist = q, d
    return best


def _lines(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "LineString":
        return [geom]
    if hasattr(geom, "geoms"):
        return [g for sub in geom.geoms for g in _lines(sub)]
    return []


def _crease_kind(a, b, ln):
    m = ln.interpolate(0.5, normalized=True)
    (x0, y0), (x1, y1) = ln.coords[0], ln.coords[-1]
    L = math.hypot(x1 - x0, y1 - y0) or 1.0
    nx, ny = -(y1 - y0) / L, (x1 - x0) / L
    side = 20.0
    q = Point(m.x + nx * side, m.y + ny * side)
    if not b["poly"].buffer(0.5).contains(q):
        q = Point(m.x - nx * side, m.y - ny * side)
    # a's plane carried across into b: above b's surface is a fold down (a ridge or hip),
    # below it a fold up (a valley).
    above = plane_z(a["plane"], q.x, q.y) - plane_z(b["plane"], q.x, q.y)
    if abs(above) < 1e-4:
        return "step" if abs(plane_z(a["plane"], m.x, m.y) - plane_z(b["plane"], m.x, m.y)) > 0.5 else "joint"
    if above < 0:
        return "valley"
    return "ridge" if a.get("edge") == b.get("edge") else "hip"


def contours(facets, step=CONTOUR_STEP):
    """Depth contours every *step* of firring depth, as {"level", "lines": [[[x, y], ...]]}."""
    if not facets:
        return []
    hi = max(max(plane_z(f["plane"], x, y) for x, y in f["poly"].exterior.coords) for f in facets)
    out = []
    level = step
    while level < hi - 1e-6:
        lines = []
        for f in facets:
            a, b, c = f["plane"]
            g = math.hypot(b, c)
            if g < 1e-9:
                continue
            # A point on the line a + b·x + c·y = level, then run it both ways across the facet.
            k = (level - a) / (g * g)
            px, py = b * k, c * k
            dx, dy = -c / g, b / g
            x0, y0, x1, y1 = f["poly"].bounds
            reach = (x1 - x0) + (y1 - y0) + abs(px - x0) + abs(py - y0) + 10.0
            line = LineString([(px - dx * reach, py - dy * reach), (px + dx * reach, py + dy * reach)])
            try:
                hit = f["poly"].intersection(line)
            except Exception:   # noqa: BLE001
                continue
            lines += [[[round(x, 1), round(y, 1)] for x, y in ln.coords] for ln in _lines(hit) if ln.length > 1.0]
        if lines:
            out.append({"level": level, "lines": lines})
        level += step
    return out


def depth_at(facets, x, y, tol=0.5):
    """Firring depth at a plan point from the facet under it (None off the facets)."""
    q = Point(x, y)
    for f in facets:
        if f.get("_prep") is not None:
            if not f["_prep"].intersects(q.buffer(tol)):
                continue
        elif f["poly"].distance(q) > tol:
            continue
        return plane_z(f["plane"], x, y)
    return None


def spot_levels(facets, outlets, sumps, p, datum):
    """Firring depth and finished level at every facet vertex and every outlet."""
    seen, out = set(), []
    above = p["above_firrings"]
    for f in facets:
        for x, y in list(f["poly"].exterior.coords)[:-1]:
            key = (round(x), round(y))
            if key in seen:
                continue
            seen.add(key)
            d = depth_at(facets, x, y)
            if d is None:
                continue
            out.append({"p": [round(x, 1), round(y, 1)], "depth": round(d, 1), "finished": round(d + above, 1),
                        "abs": round(datum + d + above, 1), "kind": "vertex"})
    for o in outlets:
        if "point" not in o:
            continue
        s = sumps[o["k"]]
        if s["kind"] == "sump":
            low = s["sf"] + p["sump_buildup"]
            out.append({"p": [round(c, 1) for c in o["point"]], "depth": round(s["sf"], 1), "finished": round(low, 1),
                        "abs": round(datum + low, 1), "kind": "outlet", "outlet": o["n"]})
        else:
            d = s["r"]
            out.append({"p": [round(c, 1) for c in o["point"]], "depth": round(d, 1), "finished": round(d + above, 1),
                        "abs": round(datum + d + above, 1), "kind": "outlet", "outlet": o["n"]})
    return out


# ── trapped water ────────────────────────────────────────────────────────

def trapped(roof, facets, sumps, step=TRACE_STEP):
    """Facets that cannot drain. From each facet's centroid, and from just inside each of
    its corners, trace steepest descent across the facets in *step* stages. It has to reach
    a sump, an outlet or a gutter edge. Where it meets a boundary that is not a drain it
    slides along it while that still goes down; where it stops going down first, the
    facet ponds. This is what catches the re-entrant corner of an L-shaped roof, where a
    drain edge faces a point that water cannot actually reach from it."""
    region = region_of(roof)
    if region.is_empty or not facets:
        return []
    inside = prep(region.buffer(0.01))
    edges = {e["id"]: e for e in roof_edges(roof)}
    footprints = [prep(s["rect"].buffer(1.0)) for s in sumps if s.get("rect") is not None]
    points = [Point(_edge_point(edges[s["edge"]], s["t_out"])) for s in sumps if s["kind"] == "point"]
    gutters = [LineString([edges[s["edge"]]["a"], edges[s["edge"]]["b"]]) for s in sumps if s["kind"] == "gutter"]
    rings = [LineString(list(region.exterior.coords))] + [LineString(list(r.coords)) for r in region.interiors]
    for f in facets:
        f["_prep"] = prep(f["poly"].buffer(0.5))
        f["_box"] = f["poly"].bounds
    x0, y0, x1, y1 = region.bounds
    max_steps = int(4 * ((x1 - x0) + (y1 - y0)) / step) + 50

    def drained(q):
        if any(fp.contains(q) for fp in footprints):
            return True
        if any(q.distance(pt) <= step for pt in points):
            return True
        return not inside.contains(q) and any(q.distance(g) <= step + 1.0 for g in gutters)

    def near(pnt):
        out = []
        for g in facets:
            bx0, by0, bx1, by1 = g["_box"]
            if bx0 - step <= pnt.x <= bx1 + step and by0 - step <= pnt.y <= by1 + step \
                    and g["poly"].distance(pnt) <= step:
                out.append(g)
        return out

    def trace(start):
        # Each step tries the fall of every facet within reach and each pair of them
        # together, and takes whichever goes lowest: on a valley the pair is the valley.
        pnt = start
        z = depth_at(facets, pnt.x, pnt.y)
        level_moves = [max_steps // 4]
        if z is None:
            return False
        for _i in range(max_steps):
            dirs = [d for r, d in (fall_of(g["plane"]) for g in near(pnt)) if r is not None]
            if not dirs:
                return drained(pnt)
            cands = list(dirs)
            for i in range(len(dirs)):
                for j in range(i + 1, len(dirs)):
                    sx, sy = dirs[i][0] + dirs[j][0], dirs[i][1] + dirs[j][1]
                    L = math.hypot(sx, sy)
                    if L > 1e-6:
                        cands.append((sx / L, sy / L))
            best = None
            for dx, dy in cands:
                q = Point(pnt.x + dx * step, pnt.y + dy * step)
                if drained(q):
                    return True
                flat = False
                if not inside.contains(q):
                    slid = _slide(pnt, (dx, dy), step, rings, inside)
                    if slid is None:
                        continue
                    q, flat = slid
                    if drained(q):
                        return True
                zq = depth_at(facets, q.x, q.y)
                if zq is None:
                    continue
                # A level move round a hole is allowed, a limited number of times.
                score = zq - (1e-3 if flat else 0.0)
                if best is None or score < best[2]:
                    best = (q, zq, score, flat)
            if best is None:
                return False
            q, zq, _score, flat = best
            if zq > z - 1e-4:
                if not (flat and zq <= z + 1e-3 and level_moves[0] > 0):
                    return False
                level_moves[0] -= 1
            pnt, z = q, zq
        return False

    ponding = []
    for f in facets:
        c = f["poly"].representative_point()
        starts = [c]
        cx, cy = c.x, c.y
        for x, y in list(f["poly"].exterior.coords)[:-1][:8]:
            starts.append(Point(x + (cx - x) * 0.08, y + (cy - y) * 0.08))
        if not all(trace(s) for s in starts):
            ponding.append(f["id"])
    for f in facets:
        f.pop("_prep", None)
        f.pop("_box", None)
    return ponding


def _slide(pnt, d, step, rings, inside):
    """Water meeting a boundary runs along it: (the step turned onto the nearest boundary
    segment, True if that move is level), or None when it cannot go on. Against the
    outline, a move with no downhill component is the end: water pooled against a wall.
    Against a hole (a kerb or a penetration) the water divides and runs round it, so a
    square-on hit goes to the nearer corner even though the move is level."""
    best, dist, hole = None, float("inf"), False
    for k, ring in enumerate(rings):
        coords = list(ring.coords)
        for a, b in zip(coords, coords[1:]):
            dd = LineString([a, b]).distance(pnt)
            if dd < dist:
                best, dist, hole = (a, b), dd, k > 0
    if best is None:
        return None
    (ax, ay), (bx, by) = best
    L = math.hypot(bx - ax, by - ay) or 1.0
    tx, ty = (bx - ax) / L, (by - ay) / L
    along = d[0] * tx + d[1] * ty
    flat = False
    if abs(along) < 0.05:
        if not hole:
            return None
        to_a = math.hypot(pnt.x - ax, pnt.y - ay)
        to_b = math.hypot(pnt.x - bx, pnt.y - by)
        along, flat = (1.0 if to_b <= to_a else -1.0), True
    sgn = 1.0 if along > 0 else -1.0
    for scale in (1.0, 0.5, 0.25):
        q = Point(pnt.x + sgn * tx * step * scale, pnt.y + sgn * ty * step * scale)
        if inside.contains(q):
            return q, flat
    return None


# ── levels along the edges ───────────────────────────────────────────────

def finished_at(facets, sumps, p, x, y, where=False):
    """Finished (membrane) level above the structure at a plan point: in a sump, its
    floor; on the roof, the facet's firrings plus the buildup. With *where*, returns
    (level, True if in a sump)."""
    q = Point(x, y)
    for s in sumps:
        if s.get("rect") is not None and s["rect"].buffer(0.01).contains(q):
            z = s["sf"] + p["sump_buildup"]
            for piece in s["floor"]:
                if piece["poly"].buffer(0.5).contains(q):
                    z = plane_z(piece["plane"], x, y) + p["sump_buildup"]
                    break
            return (z, True) if where else z
    d = depth_at(facets, x, y, tol=1.0)
    z = None if d is None else d + p["above_firrings"]
    return (z, False) if where else z


def edge_profile(edge, facets, sumps, p):
    """Finished level along an edge as [(t, level, in a sump)], with a step where it drops
    into a sump. Breaks at every facet vertex and sump corner on the edge."""
    ev, m, L = edge_frame(edge["a"], edge["b"])
    ax, ay = edge["a"]
    ts = {0.0, L}
    line = LineString([edge["a"], edge["b"]]).buffer(0.5)
    for f in facets:
        for x, y in f["poly"].exterior.coords:
            if line.contains(Point(x, y)):
                ts.add(min(L, max(0.0, (x - ax) * ev[0] + (y - ay) * ev[1])))
    for s in sumps:
        if s["edge"] == edge["id"] and s.get("rect") is not None:
            ts.update(min(L, max(0.0, t)) for t in (s["b1"], s["b2"], s["t_in"]))
    out = []
    inset = 1.0
    for t in sorted(ts):
        for dt in (-0.5, 0.5):
            tt = min(L, max(0.0, t + dt))
            z, sunk = finished_at(facets, sumps, p, ax + ev[0] * tt + m[0] * inset, ay + ev[1] * tt + m[1] * inset, True)
            if z is None:
                continue
            z = round(z, 1)
            if out and abs(out[-1][0] - round(t, 1)) < 1e-6 and abs(out[-1][1] - z) < 0.5:
                continue
            if len(out) >= 2 and abs(out[-1][1] - z) < 0.05 and abs(out[-2][1] - z) < 0.05 and out[-1][2] == sunk:
                out[-1] = (round(t, 1), z, sunk)      # level run: keep its ends only
                continue
            out.append((round(t, 1), z, sunk))
    return out


# ── the whole analysis ───────────────────────────────────────────────────

def analyse(roof, params_or_p, full=True):
    """Everything about one roof's falls. *full* False is the drag path: facets and
    creases only, recomputed on every animation frame while a sump or outlet moves."""
    p = params_or_p if "above_firrings" in params_or_p else _parse(params_or_p)
    sumps, outlets = resolve(roof, p)
    facets, unreached, merged = build_facets(roof, p, sumps)
    edge_of = {s["k"]: s["edge"] for s in sumps}
    for i, f in enumerate(facets):
        f["id"] = "F%d" % (i + 1)
        f["edge"] = edge_of.get(f["sump"])
        ratio, direction = fall_of(f["plane"])
        f["fall"], f["dir"] = ratio, direction
    out = {"sumps": sumps, "outlets": outlets, "facets": facets, "unreached": unreached, "merged": merged,
           "creases": creases(facets)}
    if not full:
        return out
    datum = float(roof.get("datum_z") or 0.0)
    depths = [plane_z(f["plane"], x, y) for f in facets for x, y in f["poly"].exterior.coords]
    out.update({"contours": contours(facets), "spots": spot_levels(facets, outlets, sumps, p, datum),
                "min_depth": min(depths) if depths else None, "max_depth": max(depths) if depths else None,
                "ponding": trapped(roof, facets, sumps)})
    edges = roof_edges(roof)
    for e in edges:
        prof = edge_profile(e, facets, sumps, p)
        e["profile"] = prof
        roof_part = [z for _t, z, sunk in prof if not sunk] or [z for _t, z, _s in prof]
        e["level_min"] = min(z for _t, z, _s in prof) if prof else None
        e["level_max"] = max(roof_part) if prof else None
    out["edges"] = edges
    return out


def facet_json(f):
    """A facet as JSON: rings, plane, fall and where its fall arrow sits."""
    ext = [[round(x, 2), round(y, 2)] for x, y in list(f["poly"].exterior.coords)[:-1]]
    holes = [[[round(x, 2), round(y, 2)] for x, y in list(r.coords)[:-1]] for r in f["poly"].interiors]
    c = f["poly"].representative_point()
    return {"id": f["id"], "ring": ext, "holes": holes, "plane": [round(v, 9) for v in f["plane"]],
            "kind": f["kind"], "sump": f["sump"], "edge": f.get("edge"), "area": round(f["poly"].area, 1),
            "fall": round(f["fall"], 2) if f["fall"] else None, "label": fall_label(f["fall"]),
            "dir": [round(v, 6) for v in f["dir"]], "at": [round(c.x, 1), round(c.y, 1)],
            "merged": f.get("merged", 0), "small": bool(f.get("small"))}


def sump_json(s, p, datum):
    rec = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in s.items()
           if k in ("k", "kind", "edge", "b1", "b2", "W", "r", "sf", "rho", "along", "across", "t_out", "t_in",
                    "drop", "drop_asked", "outlet")}
    if s.get("rect") is not None:
        rec["rect"] = [[round(x, 2), round(y, 2)] for x, y in list(s["rect"].exterior.coords)[:-1]]
        rim = s["r"] + p["above_firrings"]
        floor_lo = s["sf"] + p["sump_buildup"]
        rec.update({"rim": round(rim, 1), "floor_low": round(floor_lo, 1), "floor_high": round(floor_lo + s["rho"], 1),
                    "rim_abs": round(datum + rim, 1), "floor_abs": round(datum + floor_lo, 1),
                    "L": round(s["b2"] - s["b1"], 1)})
    return rec
