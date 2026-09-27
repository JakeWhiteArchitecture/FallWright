"""
Fallwright — edge classification.

Every segment of the roof outline, and of every hole outline, gets a type:

  abutment     a wall stands along the edge and rises PARAPET_RISE or more above the
               structure: membrane upstand under a counter-flashing chased into the wall
  parapet      a wall stands along the edge but tops out lower: upstand over the coping
  drip         no wall: drip trim, membrane dressed over
  kerb         an opening hole (both ways at least MIN_OPENING): kerb and upstand
  penetration  a small hole: collar symbol only

"gutter" and "check_kerb" are never detected; the user sets them on a free edge. A wall
counts when its section, taken WALL_PROBE above the structure, lies along the edge within
WALL_REACH for at least half the edge's length. Doors in a wall along an edge are found
and their sill level recorded, for the threshold check.

Coordinates: roof-local plan (u, v) mm, with heights relative to the structure (the datum).
"""

import math

from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

from cladding_booleans import iter_polygons, _intersection
from fabric_extract import _section
from roof_constants import (MIN_OPENING, PARAPET_RISE, WALL_PROBE, WALL_REACH, EDGE_LABELS)

WALL_TYPES = frozenset({"IFCWALL", "IFCWALLSTANDARDCASE", "IFCWALLELEMENTEDCASE", "IFCCURTAINWALL"})
DOOR_TYPES = frozenset({"IFCDOOR", "IFCDOORSTANDARDCASE"})
MIN_COVER = 0.5          # fraction of an edge a wall must run along
THICKNESS_SEARCH = 1000.0
FOOT_PROBE = 10.0        # mm above the structure: where a wall standing on the deck is cut out


def edge_frame(a, b):
    """(unit along, inward normal, length) of the segment a→b. Rings run anticlockwise
    outside and clockwise round holes, so the roof is always on the left."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return (1.0, 0.0), (0.0, 1.0), 0.0
    e = (dx / length, dy / length)
    return e, (-e[1], e[0]), length


def _strip(a, b, near, far):
    """Band beside the segment a→b on the outer (right-hand) side, from *near* to *far*
    measured outward (negative reaches back over the roof)."""
    e, m, _length = edge_frame(a, b)
    o = (-m[0], -m[1])
    return Polygon([(a[0] + o[0] * near, a[1] + o[1] * near), (b[0] + o[0] * near, b[1] + o[1] * near),
                    (b[0] + o[0] * far, b[1] + o[1] * far), (a[0] + o[0] * far, a[1] + o[1] * far)])


def _covered(part, a, e, length):
    """Length of the edge a part's footprint runs along (union of its t-intervals)."""
    spans = []
    for pg in iter_polygons(part):
        ts = [(x - a[0]) * e[0] + (y - a[1]) * e[1] for x, y in pg.exterior.coords]
        lo, hi = max(0.0, min(ts)), min(length, max(ts))
        if hi > lo:
            spans.append((lo, hi))
    spans.sort()
    total, cur = 0.0, None
    for lo, hi in spans:
        if cur and lo <= cur[1]:
            cur = (cur[0], max(cur[1], hi))
        else:
            if cur:
                total += cur[1] - cur[0]
            cur = (lo, hi)
    if cur:
        total += cur[1] - cur[0]
    return total


def wall_records(context, frame, datum, shift):
    """Each wall and door near the roof as {"kind", "name", "section" (plan polygon at the
    probe height), "top" (above the datum), "bbox" ...}. *shift* is the (u, v) the region
    was moved by to put its corner at the local origin."""
    from fabric_extract import _to_local
    n, u = (0.0, 0.0, 1.0), tuple(frame["u"])
    out = []
    for elem in context or []:
        etype = (elem.get("type") or "").upper()
        if etype not in WALL_TYPES and etype not in DOOR_TYPES:
            continue
        tris = elem.get("tris") or []
        if not tris:
            continue
        try:
            tris = [[tuple(float(c) for c in v) for v in tri] for tri in tris]
            zs = [v[2] for tri in tris for v in tri]
            rec = {"kind": "door" if etype in DOOR_TYPES else "wall", "name": elem.get("name") or etype,
                   "type": elem.get("type") or etype, "top": max(zs) - datum, "bottom": min(zs) - datum}
            if rec["kind"] == "wall":
                _s, touches, poly, _pts = _section(tris, n, datum + WALL_PROBE, u, "roof")
                if not touches or poly is None or poly.is_empty:
                    continue
                rec["section"] = translate(poly, -shift[0], -shift[1])
                # Its foot, just above the deck: a wall standing on the slab is cut out of
                # the roof here, where a door higher up leaves no gap.
                _s, touches, foot, _pts = _section(tris, n, datum + FOOT_PROBE, u, "roof")
                rec["foot"] = translate(foot, -shift[0], -shift[1]) if touches and foot is not None else None
            else:
                pts = [_to_local(v, n, u, "roof") for tri in tris for v in tri]
                xs, ys = [q[0] - shift[0] for q in pts], [q[1] - shift[1] for q in pts]
                rec["bbox"] = (min(xs), min(ys), max(xs), max(ys))
            out.append(rec)
        except Exception:   # noqa: BLE001 — one awkward element must not sink the rest
            continue
    return out


def classify(polygons, records):
    """Edge list for one roof: every outline segment, exterior first, then each hole."""
    edges = []
    walls = [r for r in records if r["kind"] == "wall"]
    doors = [r for r in records if r["kind"] == "door"]
    for pg in polygons[:1]:
        rings = [("E", pg["exterior"], None)] + [("H%d." % (k + 1), h, _hole_kind(h)) for k, h in enumerate(pg.get("holes") or [])]
        for ring_i, (prefix, ring, hole_kind) in enumerate(rings):
            n = len(ring)
            for i in range(n):
                a, b = ring[i], ring[(i + 1) % n]
                e, _m, length = edge_frame(a, b)
                if length < 1.0:
                    continue
                rec = {"id": "%s%d" % (prefix, i + 1), "ring": ring_i, "index": i,
                       "a": [round(a[0], 2), round(a[1], 2)], "b": [round(b[0], 2), round(b[1], 2)],
                       "length": round(length, 1), "wall": None, "coverage": 0.0, "doors": []}
                wall = _wall_along(a, b, e, length, walls)
                if wall:
                    rec["wall"], rec["coverage"] = wall["info"], wall["coverage"]
                    rec["detected"] = "abutment" if wall["info"]["rise"] >= PARAPET_RISE else "parapet"
                    rec["doors"] = _doors_along(a, b, e, length, wall["info"]["thickness"], doors)
                elif hole_kind:
                    rec["detected"] = hole_kind
                else:
                    rec["detected"] = "drip"
                rec["type"] = rec["detected"]
                rec["label"] = EDGE_LABELS[rec["type"]]
                edges.append(rec)
    return edges


def _hole_kind(ring):
    us = [q[0] for q in ring]
    vs = [q[1] for q in ring]
    big = max(us) - min(us) >= MIN_OPENING and max(vs) - min(vs) >= MIN_OPENING
    return "kerb" if big else "penetration"


def _wall_along(a, b, e, length, walls):
    """The wall standing along a→b, if one covers at least half its length: its rise above
    the structure and its thickness square to the edge."""
    best = None
    near = _strip(a, b, -10.0, WALL_REACH)
    for w in walls:
        try:
            hit = _intersection(w["section"], near)
        except Exception:   # noqa: BLE001
            continue
        if hit is None or hit.is_empty:
            continue
        cover = _covered(hit, a, e, length) / max(length, 1e-9)
        if cover >= MIN_COVER and (best is None or cover > best["coverage"]):
            best = {"coverage": round(cover, 3), "wall": w}
    if not best:
        return None
    w = best["wall"]
    thickness = _thickness(a, b, w["section"])
    best["info"] = {"name": w["name"], "type": w["type"], "rise": round(w["top"], 1),
                    "thickness": round(thickness, 1)}
    return best


def _thickness(a, b, section):
    """How far the wall runs out from the edge line, square to it."""
    e, m, length = edge_frame(a, b)
    band = _intersection(section, _strip(a, b, -10.0, THICKNESS_SEARCH))
    far = 0.0
    for pg in iter_polygons(band):
        for x, y in pg.exterior.coords:
            far = max(far, -((x - a[0]) * m[0] + (y - a[1]) * m[1]))
    return far if far > 20.0 else 0.0


def _doors_along(a, b, e, length, thickness, doors):
    """Doors whose footprint sits in the wall along a→b: each as its centre along the
    edge, its width, and its sill above the structure."""
    _e, m, _l = edge_frame(a, b)
    reach = max(thickness, 100.0) + 100.0
    out = []
    for d in doors:
        x0, y0, x1, y1 = d["bbox"]
        corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        ts = [(x - a[0]) * e[0] + (y - a[1]) * e[1] for x, y in corners]
        ss = [-((x - a[0]) * m[0] + (y - a[1]) * m[1]) for x, y in corners]   # outward
        if max(ss) < -100.0 or min(ss) > reach:
            continue
        lo, hi = max(0.0, min(ts)), min(length, max(ts))
        if hi - lo < 100.0:
            continue
        out.append({"name": d["name"], "t": round((lo + hi) / 2, 1), "width": round(hi - lo, 1),
                    "sill": round(d["bottom"], 1)})
    return out


def wall_feet(records):
    """Every wall's footprint just above the deck, for cutting the walls that stand on the
    slab out of the roof region."""
    polys = [r["foot"] for r in records if r["kind"] == "wall" and r.get("foot") is not None]
    return unary_union(polys) if polys else None
