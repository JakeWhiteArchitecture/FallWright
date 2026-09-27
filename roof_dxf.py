"""
Fallwright — DXF export: falls plan, sections, edge details and the schedule.

    roof_to_dxf_string(params, built=None) -> str

One file, drawn at true size in millimetres (model space) and laid out left to right:

  1. Falls plan (for 1:100): outline and holes, facet boundaries (hips dashed, valleys
     chain-dotted), drain edges heavy, outlets numbered and dimensioned from their corners,
     hoppers through the wall, sumps with their size and levels, fall arrows, valley falls,
     firring depth contours every 25 mm, firring depths and spot levels, edge types.
  2. Sections (for 1:20): one per cutting plane, looking the way its arrow points.
  3. Edge details (for 1:5): one per edge type on the roof, and one per hopper.
  4. Notes and schedule.

DXF R12 (AC1009), readable by every CAD package. Entities carry their own linetype where
one layer needs two (hips and valleys).
"""

import math

from shapely.geometry import LineString, Point, Polygon

from roof_constants import (_parse, TOOL_NAME, IFC_SCHEMA_LABEL, SCOPE_NOTE, QUANTITY_NOTE, DISCLAIMER,
                            EDGE_LABELS, fall_label, plane_z, MIN_UPSTAND)
from roof_edges import edge_frame
from roof_falls import region_of
from roof_checks import upstand_top

LAYERS = {   # name: (colour, linetype)
    "ROOF_OUTLINE": (7, "CONTINUOUS"), "FACETS": (8, "CONTINUOUS"), "DRAINS": (5, "CONTINUOUS"),
    "OUTLETS": (5, "CONTINUOUS"), "FALL_ARROWS": (3, "CONTINUOUS"), "FIRRING_DEPTHS": (9, "CONTINUOUS"),
    "SUMPS": (4, "CONTINUOUS"), "LEVELS": (2, "CONTINUOUS"), "SECTION_LINES": (1, "DASHDOT"),
    "STRUCTURE": (8, "CONTINUOUS"), "FIRRINGS": (30, "CONTINUOUS"), "DECK": (32, "CONTINUOUS"),
    "VCL": (6, "CONTINUOUS"), "INSULATION": (2, "CONTINUOUS"), "MEMBRANE": (7, "CONTINUOUS"),
    "NO_OUTLET": (1, "CONTINUOUS"), "DETAILS": (7, "CONTINUOUS"), "DIMS": (7, "CONTINUOUS"),
    "NOTES": (7, "CONTINUOUS"),
}
BUILDUP_LAYERS = ("FIRRINGS", "DECK", "VCL", "INSULATION", "MEMBRANE")
T_PLAN, T_SECTION, T_DETAIL = 150.0, 50.0, 12.0     # text: about 1.5, 2.5 and 2.4 mm on the sheet
GAP = 3000.0


class _Dxf:
    """DXF R12 from LINE and TEXT entities; a line may override its layer's linetype."""

    def __init__(self):
        self.lines, self.texts = [], []

    def line(self, a, b, layer, ltype=None):
        if math.hypot(b[0] - a[0], b[1] - a[1]) > 0.01:
            self.lines.append((a, b, layer, ltype))

    def poly(self, pts, layer, closed=False, ltype=None):
        pts = list(pts)
        for a, b in zip(pts, pts[1:] + (pts[:1] if closed else [])):
            self.line(a, b, layer, ltype)

    def text(self, s, at, h, layer="NOTES", rot=0.0):
        self.texts.append((str(s), at, h, layer, rot))

    def circle(self, c, r, layer, n=24):
        self.poly([(c[0] + r * math.cos(2 * math.pi * i / n), c[1] + r * math.sin(2 * math.pi * i / n)) for i in range(n)],
                  layer, closed=True)

    def to_string(self):
        out = []
        a = out.append
        a("  0"); a("SECTION"); a("  2"); a("HEADER")
        a("  9"); a("$ACADVER"); a("  1"); a("AC1009")
        a("  9"); a("$MEASUREMENT"); a(" 70"); a("     1")
        a("  9"); a("$LTSCALE"); a(" 40"); a("20.0")
        a("  0"); a("ENDSEC")
        a("  0"); a("SECTION"); a("  2"); a("TABLES")
        types = {"DASHED": [12.7, 6.35, -6.35], "DASHDOT": [25.4, 12.7, -6.35, 0.0, -6.35]}
        a("  0"); a("TABLE"); a("  2"); a("LTYPE"); a(" 70"); a("     %d" % (len(types) + 1))
        a("  0"); a("LTYPE"); a("  2"); a("CONTINUOUS"); a(" 70"); a("     0"); a("  3"); a("Solid line")
        a(" 72"); a("    65"); a(" 73"); a("     0"); a(" 40"); a("0.0")
        for name, pat in types.items():
            a("  0"); a("LTYPE"); a("  2"); a(name); a(" 70"); a("     0"); a("  3"); a("")
            a(" 72"); a("    65"); a(" 73"); a("     %d" % (len(pat) - 1)); a(" 40"); a("%.4f" % pat[0])
            for v in pat[1:]:
                a(" 49"); a("%.4f" % v)
        a("  0"); a("ENDTAB")
        a("  0"); a("TABLE"); a("  2"); a("LAYER"); a(" 70"); a("     %d" % (len(LAYERS) + 1))
        a("  0"); a("LAYER"); a("  2"); a("0"); a(" 70"); a("     0"); a(" 62"); a("     7"); a("  6"); a("CONTINUOUS")
        for name, (colour, lt) in LAYERS.items():
            a("  0"); a("LAYER"); a("  2"); a(name); a(" 70"); a("     0"); a(" 62"); a("     %d" % colour); a("  6"); a(lt)
        a("  0"); a("ENDTAB"); a("  0"); a("ENDSEC")
        a("  0"); a("SECTION"); a("  2"); a("ENTITIES")
        for (x0, y0), (x1, y1), layer, lt in self.lines:
            a("  0"); a("LINE"); a("  8"); a(layer)
            if lt:
                a("  6"); a(lt)
            a(" 10"); a("%.3f" % x0); a(" 20"); a("%.3f" % y0); a(" 30"); a("0.0")
            a(" 11"); a("%.3f" % x1); a(" 21"); a("%.3f" % y1); a(" 31"); a("0.0")
        for s, (x, y), h, layer, rot in self.texts:
            a("  0"); a("TEXT"); a("  8"); a(layer)
            a(" 10"); a("%.3f" % x); a(" 20"); a("%.3f" % y); a(" 30"); a("0.0")
            a(" 40"); a("%.3f" % h); a("  1"); a(s.replace("\n", " "))
            if rot:
                a(" 50"); a("%.3f" % rot)
        a("  0"); a("ENDSEC"); a("  0"); a("EOF")
        return "\r\n".join(out) + "\r\n"


# ── drawing helpers ──────────────────────────────────────────────────────

def _dim(dxf, p1, p2, off, h, label=None, layer="DIMS"):
    """Aligned dimension p1→p2, the dimension line *off* to the left of that direction."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L < 1:
        return
    nx, ny = -dy / L, dx / L
    q1, q2 = (p1[0] + nx * off, p1[1] + ny * off), (p2[0] + nx * off, p2[1] + ny * off)
    sgn = 1.0 if off >= 0 else -1.0
    dxf.line((p1[0] + nx * sgn * h * 0.3, p1[1] + ny * sgn * h * 0.3), (q1[0] + nx * sgn * h * 0.5, q1[1] + ny * sgn * h * 0.5), layer)
    dxf.line((p2[0] + nx * sgn * h * 0.3, p2[1] + ny * sgn * h * 0.3), (q2[0] + nx * sgn * h * 0.5, q2[1] + ny * sgn * h * 0.5), layer)
    dxf.line(q1, q2, layer)
    t = h * 0.4
    for q in (q1, q2):
        dxf.line((q[0] - (dx / L + nx) * t, q[1] - (dy / L + ny) * t), (q[0] + (dx / L + nx) * t, q[1] + (dy / L + ny) * t), layer)
    rot = math.degrees(math.atan2(dy, dx))
    if rot > 90.0 or rot <= -90.0:
        rot += 180.0
    mid = ((q1[0] + q2[0]) / 2 + nx * sgn * h * 0.4, (q1[1] + q2[1]) / 2 + ny * sgn * h * 0.4)
    dxf.text(label if label is not None else "%.0f" % L, mid, h, layer, rot)


def _arrow(dxf, at, d, length, h, layer):
    x, y = at
    tip = (x + d[0] * length / 2, y + d[1] * length / 2)
    tail = (x - d[0] * length / 2, y - d[1] * length / 2)
    dxf.line(tail, tip, layer)
    ax, ay = -d[0] * h, -d[1] * h
    for s in (0.5, -0.5):
        dxf.line(tip, (tip[0] + ax - d[1] * h * s, tip[1] + ay + d[0] * h * s), layer)


def _hatch(dxf, poly, layer, step):
    """Diagonal hatch lines clipped to a polygon."""
    x0, y0, x1, y1 = poly.bounds
    k = -((x1 - x0) + (y1 - y0))
    while k < (x1 - x0) + (y1 - y0):
        ln = LineString([(x0 + k, y0), (x0 + k + (y1 - y0) + 1, y1 + 1)])
        hit = poly.intersection(ln)
        for g in getattr(hit, "geoms", [hit]):
            if g.geom_type == "LineString" and g.length > 1:
                dxf.line(g.coords[0], g.coords[-1], layer)
        k += step


# ── 1. the falls plan ────────────────────────────────────────────────────

def _plan(dxf, p, roof, falls, ox, oy):
    """Returns the plan's (width, height)."""
    region = region_of(roof)
    x0, y0, x1, y1 = region.bounds
    P = lambda x, y: (ox + x - x0, oy + y - y0)   # noqa: E731
    h = T_PLAN
    pg = roof["polygons"][0]
    dxf.poly([P(*q) for q in pg["exterior"]], "ROOF_OUTLINE", closed=True)
    for hole in pg.get("holes") or []:
        dxf.poly([P(*q) for q in hole], "ROOF_OUTLINE", closed=True)
    facets = falls["facets"]
    # Facet boundaries: every crease, typed.
    for c in falls["creases"]:
        lt = {"valley": "DASHDOT", "hip": "DASHED", "ridge": "DASHED"}.get(c["kind"])
        dxf.line(P(*c["a"]), P(*c["b"]), "FACETS", lt)
        if c["kind"] == "valley":
            mx, my = (c["a"][0] + c["b"][0]) / 2, (c["a"][1] + c["b"][1]) / 2
            rot = math.degrees(math.atan2(c["b"][1] - c["a"][1], c["b"][0] - c["a"][0]))
            if rot > 90 or rot <= -90:
                rot += 180
            dxf.text("valley %s" % c["label"], P(mx, my), h * 0.8, "FALL_ARROWS", rot)
    for f in facets:
        ratio, d = f["fall"], f["dir"]
        c = f["poly"].representative_point()
        if ratio:
            _arrow(dxf, P(c.x, c.y), d, 900.0, h, "FALL_ARROWS")
            dxf.text("%s %s" % (f["id"], fall_label(ratio)), P(c.x + h * 0.5, c.y + h * 0.8), h, "FALL_ARROWS")
    for level in falls.get("contours") or []:
        for ln in level["lines"]:
            dxf.poly([P(*q) for q in ln], "FIRRING_DEPTHS")
        ln = max(level["lines"], key=len)
        if ln:
            mid = ln[len(ln) // 2]
            dxf.text("%.0f" % level["level"], P(*mid), h * 0.6, "FIRRING_DEPTHS")
    for s in falls.get("spots") or []:
        if s["kind"] != "vertex":
            continue
        at = P(*s["p"])
        dxf.line((at[0] - 40, at[1] - 40), (at[0] + 40, at[1] + 40), "LEVELS")
        dxf.line((at[0] - 40, at[1] + 40), (at[0] + 40, at[1] - 40), "LEVELS")
        dxf.text("f%.0f" % s["depth"], (at[0] + 60, at[1] + 20), h * 0.6, "FIRRING_DEPTHS")
        dxf.text("+%.0f (%.0f)" % (s["finished"], s["abs"]), (at[0] + 60, at[1] - h * 0.7), h * 0.6, "LEVELS")
    for pgn in falls.get("unreached") or []:
        shp = Polygon([P(*q) for q in (pgn if isinstance(pgn, list) else list(pgn.exterior.coords))])
        dxf.poly(list(shp.exterior.coords)[:-1], "NO_OUTLET", closed=True)
        _hatch(dxf, shp, "NO_OUTLET", 150.0)
        dxf.text("NO OUTLET REACHES THIS AREA", (shp.centroid.x, shp.centroid.y), h, "NO_OUTLET")
    edges = {e["id"]: e for e in falls.get("edges") or []}
    for e in edges.values():
        ev, m, L = edge_frame(e["a"], e["b"])
        mid = ((e["a"][0] + e["b"][0]) / 2 - m[0] * 350, (e["a"][1] + e["b"][1]) / 2 - m[1] * 350)
        rot = math.degrees(math.atan2(ev[1], ev[0]))
        if rot > 90 or rot <= -90:
            rot += 180
        if e["ring"] == 0 or e["index"] == 0:
            dxf.text("%s %s" % (e["id"], EDGE_LABELS.get(e["type"], e["type"]).upper()), P(*mid), h * 0.7, "ROOF_OUTLINE", rot)
    drains = {s["edge"] for s in falls["sumps"]}
    for eid in drains:
        e = edges.get(eid)
        if not e:
            continue
        ev, m, L = edge_frame(e["a"], e["b"])
        for off in (15.0, 30.0):
            dxf.line(P(e["a"][0] + m[0] * off, e["a"][1] + m[1] * off), P(e["b"][0] + m[0] * off, e["b"][1] + m[1] * off), "DRAINS")
        if e["type"] == "gutter":
            dxf.poly([P(e["a"][0] - m[0] * 60, e["a"][1] - m[1] * 60), P(e["b"][0] - m[0] * 60, e["b"][1] - m[1] * 60),
                      P(e["b"][0] - m[0] * 175, e["b"][1] - m[1] * 175), P(e["a"][0] - m[0] * 175, e["a"][1] - m[1] * 175)],
                     "DRAINS", closed=True)
    for s in falls["sumps"]:
        if s["kind"] != "sump":
            continue
        dxf.poly([P(x, y) for x, y in list(s["rect"].exterior.coords)[:-1]], "SUMPS", closed=True)
        e = edges[s["edge"]]
        ev, m, _L = edge_frame(e["a"], e["b"])
        datum = float(roof.get("datum_z") or 0.0)
        rim = s["r"] + p["above_firrings"]
        floor = s["sf"] + p["sump_buildup"]
        c = s["rect"].centroid
        lines = ["SUMP %d  %.0f x %.0f" % ((s.get("outlet") or 0) + 1, s["b2"] - s["b1"], s["W"]),
                 "rim +%.0f (%.0f)" % (rim, datum + rim), "floor +%.0f (%.0f)" % (floor, datum + floor),
                 "drop %.0f" % s["drop"]]
        for i, t in enumerate(lines):
            dxf.text(t, P(c.x + m[0] * (s["W"] + 250) - ev[0] * 400, c.y + m[1] * (s["W"] + 250) - ev[1] * 400 - i * h * 0.9),
                     h * 0.7, "SUMPS")
    for o in falls["outlets"]:
        if "point" not in o:
            continue
        e = edges.get(o["edge"])
        x, y = o["point"]
        if o["type"] == "internal":
            dxf.circle(P(x, y), p["outlet_d"] / 2, "OUTLETS")
        else:
            ev, m, _L = edge_frame(e["a"], e["b"])
            thick = float((e.get("wall") or {}).get("thickness") or p["wall_t"])
            w = p["hopper_w"] / 2
            corners = [(x - ev[0] * w, y - ev[1] * w), (x + ev[0] * w, y + ev[1] * w),
                       (x + ev[0] * w - m[0] * thick, y + ev[1] * w - m[1] * thick),
                       (x - ev[0] * w - m[0] * thick, y - ev[1] * w - m[1] * thick)]
            dxf.poly([P(*q) for q in corners], "OUTLETS", closed=True)
            hx, hy = x - m[0] * (thick + 100), y - m[1] * (thick + 100)
            dxf.poly([P(hx - ev[0] * 150 - m[0] * 100, hy - ev[1] * 150 - m[1] * 100),
                      P(hx + ev[0] * 150 - m[0] * 100, hy + ev[1] * 150 - m[1] * 100),
                      P(hx + ev[0] * 150 + m[0] * 100, hy + ev[1] * 150 + m[1] * 100),
                      P(hx - ev[0] * 150 + m[0] * 100, hy - ev[1] * 150 + m[1] * 100)], "OUTLETS", closed=True)
        dxf.text("O%d %s" % (o["n"], "HOPPER" if o["type"] == "hopper" else "OUTLET"), P(x + 120, y + 120), h, "OUTLETS")
        if e:
            corner = e["a"] if o["corner"] == "a" else e["b"]
            at = _on_edge(e, o["t"])
            a1, a2 = (P(*corner), P(*at)) if o["corner"] == "a" else (P(*at), P(*corner))
            _dim(dxf, a1, a2, -600.0, h * 0.8, "O%d %.0f from %s" % (o["n"], o["offset"], "start" if o["corner"] == "a" else "end"))
    for cp in roof.get("cut_planes") or default_planes(roof):
        line = _cut_line(region, cp)
        if line is None:
            continue
        (ax, ay), (bx, by) = line
        dxf.line(P(ax, ay), P(bx, by), "SECTION_LINES")
        w = _look(cp)
        for q in ((ax, ay), (bx, by)):
            _arrow(dxf, P(q[0] + w[0] * 300, q[1] + w[1] * 300), w, 500.0, h, "SECTION_LINES")
            dxf.text(cp.get("label", "A"), P(q[0] - w[0] * 200 + 60, q[1] - w[1] * 200), h * 1.6, "SECTION_LINES")
    W, H = x1 - x0, y1 - y0
    dxf.text("FALLS PLAN  -  %s  -  intended 1:100" % (roof.get("name") or "Roof").upper(), P(x0, y1 + 1300), h * 1.6, "NOTES")
    dxf.text("Firring depths (f) and finished levels above the top of structure, (absolute). Contours every 25 mm of "
             "firring depth. Hips dashed, valleys chain-dotted, drain edges heavy.", P(x0, y1 + 1000), h * 0.7, "NOTES")
    return W, H


def _on_edge(e, t):
    ev, _m, _L = edge_frame(e["a"], e["b"])
    return (e["a"][0] + ev[0] * t, e["a"][1] + ev[1] * t)


# ── 2. sections ──────────────────────────────────────────────────────────

def _look(cp):
    """Unit direction a cutting plane looks: square to its line, flipped on request."""
    w = (0.0, 1.0) if cp.get("dir", "u") == "u" else (1.0, 0.0)
    return (-w[0], -w[1]) if cp.get("flip") else w


def _cut_line(region, cp):
    """The cutting plane's line across the roof's bounds, as ((x, y), (x, y))."""
    if region.is_empty:
        return None
    x0, y0, x1, y1 = region.bounds
    pos = float(cp.get("pos", 0.0))
    if cp.get("dir", "u") == "u":
        return ((x0 - 500, pos), (x1 + 500, pos))
    return ((pos, y0 - 500), (pos, y1 + 500))


def _section(dxf, p, roof, falls, cp, ox, oy):
    """One section along a cutting plane. Returns its (width, height) on the sheet."""
    region = region_of(roof)
    line = _cut_line(region, cp)
    if line is None:
        return 0.0, 0.0
    w = _look(cp)
    right = (w[1], -w[0])                                # the viewer's right, facing w
    along = lambda x, y: x * right[0] + y * right[1]     # noqa: E731
    hit = region.intersection(LineString(line))
    spans = [g for g in getattr(hit, "geoms", [hit]) if g.geom_type == "LineString" and g.length > 1]
    if not spans:
        return 0.0, 0.0
    all_s = [along(*q) for g in spans for q in g.coords]
    s0 = min(all_s)
    X = lambda x, y: ox + along(x, y) - s0               # noqa: E731
    h = T_SECTION
    top_seen = 0.0
    ups = []
    for g in spans:
        a, b = g.coords[0], g.coords[-1]
        if along(*a) > along(*b):
            a, b = b, a
        pieces = _pieces(falls, p, a, b)
        for (x0, y0, x1, y1, kind, pl) in pieces:
            z0, z1 = plane_z(pl, x0, y0), plane_z(pl, x1, y1)
            stack = [("FIRRINGS", None)] + [(lay, t) for lay, t in _stack(p, kind)]
            lo0 = lo1 = 0.0
            for lay, t in stack:
                hi0, hi1 = (z0, z1) if t is None else (lo0 + t, lo1 + t)
                if t is None and max(z0, z1) < 0.5:
                    continue
                dxf.poly([(X(x0, y0), oy + lo0), (X(x1, y1), oy + lo1), (X(x1, y1), oy + hi1), (X(x0, y0), oy + hi0)],
                         lay, closed=True)
                lo0, lo1 = hi0, hi1
            top_seen = max(top_seen, lo0, lo1)
        dxf.line((X(*a), oy), (X(*b), oy), "STRUCTURE")
        dxf.line((X(*a), oy - 200), (X(*b), oy - 200), "STRUCTURE", "DASHED")
        for end, first in ((a, True), (b, False)):
            fin = _finished_near(falls, p, end, b if first else a)
            e = _edge_at(falls, end)
            if fin is not None:
                dxf.text("+%.0f (%.0f)" % (fin, float(roof.get("datum_z") or 0.0) + fin),
                         (X(*end) + (-h * 9 if first else h), oy + fin + h * 5), h, "LEVELS")
            if e is not None:
                ups.append(_section_edge(dxf, p, e, (X(*end), oy), fin, 1.0 if first else -1.0, h))
    title_y = oy + max([top_seen] + [u for u in ups if u]) + 900
    dxf.text("SECTION %s-%s  -  intended 1:20" % (cp.get("label", "A"), cp.get("label", "A")), (ox, title_y), h * 2.4, "NOTES")
    dxf.text("Looking %s. Layers at true thickness; finished levels above the top of structure (absolute)."
             % _compass(w), (ox, title_y - h * 3), h, "NOTES")
    return max(all_s) - s0, title_y - oy + 400


def _compass(w):
    return {(0.0, 1.0): "along +v", (0.0, -1.0): "along -v", (1.0, 0.0): "along +u", (-1.0, 0.0): "along -u"}.get(
        (round(w[0], 1) + 0.0, round(w[1], 1) + 0.0), "square to the cut")


def _stack(p, kind):
    if kind == "sump":
        return [("DECK", p["deck_t"]), ("VCL", p["vcl_t"]), ("INSULATION", p["sump_ins"]), ("MEMBRANE", p["membrane_t"])]
    return [("DECK", p["deck_t"]), ("VCL", p["vcl_t"]), ("INSULATION", p["insulation_t"]), ("MEMBRANE", p["membrane_t"])]


def _pieces(falls, p, a, b):
    """The cut a→b split wherever it crosses a facet or sump boundary, each run tagged with
    the plane of what it crosses: [(x0, y0, x1, y1, "roof" | "sump", plane)]."""
    seg = LineString([a, b])
    L = seg.length
    ts = {0.0, L}
    shapes = [("roof", f["poly"], f["plane"]) for f in falls["facets"]]
    for s in falls["sumps"]:
        for piece in s.get("floor") or []:
            shapes.append(("sump", piece["poly"], piece["plane"]))
    for _k, poly, _pl in shapes:
        inter = seg.intersection(poly.boundary)
        for g in getattr(inter, "geoms", [inter]):
            if g.is_empty:
                continue
            for q in g.coords:
                ts.add(seg.project(Point(q)))
    ts = sorted(ts)
    out = []
    for t0, t1 in zip(ts, ts[1:]):
        if t1 - t0 < 0.5:
            continue
        mid = seg.interpolate((t0 + t1) / 2)
        found = next(((k, pl) for k, poly, pl in shapes if k == "sump" and poly.buffer(0.1).contains(mid)), None)
        if found is None:
            found = next(((k, pl) for k, poly, pl in shapes if k == "roof" and poly.buffer(0.1).contains(mid)), None)
        if found is None:
            continue
        q0, q1 = seg.interpolate(t0), seg.interpolate(t1)
        out.append((q0.x, q0.y, q1.x, q1.y, found[0], found[1]))
    return out


def _finished_near(falls, p, end, toward):
    from roof_falls import finished_at
    L = math.hypot(toward[0] - end[0], toward[1] - end[1]) or 1.0
    q = (end[0] + (toward[0] - end[0]) / L * 2.0, end[1] + (toward[1] - end[1]) / L * 2.0)
    return finished_at(falls["facets"], falls["sumps"], p, q[0], q[1])


def _edge_at(falls, pt):
    q = Point(pt)
    best, dist = None, 5.0
    for e in falls.get("edges") or []:
        d = LineString([e["a"], e["b"]]).distance(q)
        if d < dist:
            best, dist = e, d
    return best


def _section_edge(dxf, p, e, at, fin, side, h):
    """What the section shows where it crosses an edge: the wall and upstand, the trim, or
    the kerb. *side* is +1 when the roof lies to the right of *at*. Returns the height used."""
    x, y = at
    out = lambda d: x - side * d       # noqa: E731 — outward from the roof
    kind = e["type"]
    fin = fin or 0.0
    mt = p["membrane_t"]
    if kind in ("abutment", "parapet"):
        top = upstand_top(p, e) or (fin + MIN_UPSTAND)
        thick = float((e.get("wall") or {}).get("thickness") or p["wall_t"])
        wall_top = top + 600.0 if kind == "abutment" else top
        dxf.poly([(out(0), y - 300), (out(0), y + wall_top), (out(thick), y + wall_top), (out(thick), y - 300)], "DETAILS")
        dxf.line((out(-p["vcl_t"] - (p["ins_upstand_t"] if kind == "abutment" else 0) - mt), y + fin),
                 (out(-p["vcl_t"] - (p["ins_upstand_t"] if kind == "abutment" else 0) - mt), y + top), "MEMBRANE")
        dxf.text("%s %s: upstand to +%.0f" % (e["id"], kind, top), (out(thick) - side * 0 + (h if side < 0 else -h * 22), y + wall_top + h),
                 h, "DETAILS")
        return wall_top
    if kind in ("drip", "gutter"):
        dxf.poly([(out(-p["trim_w"]), y + fin - mt), (out(3), y + fin - mt), (out(3), y + fin - mt - p["trim_h"])], "DETAILS")
        if kind == "gutter":
            cx, cy = out(3 + 60), y + fin - mt - p["trim_h"] - 20
            dxf.poly([(cx + 60 * math.cos(math.pi + math.pi * i / 12), cy + 60 * math.sin(math.pi + math.pi * i / 12))
                      for i in range(13)], "DRAINS")
        dxf.text("%s %s" % (e["id"], EDGE_LABELS[kind]), (out(0) + (h if side < 0 else -h * 12), y + fin + h * 4), h, "DETAILS")
        return fin
    if kind == "kerb":
        top = fin + p["upstand"]
        dxf.poly([(out(0), y), (out(0), y + top), (out(p["kerb_w"]), y + top), (out(p["kerb_w"]), y)], "DETAILS", closed=True)
        dxf.line((out(-p["ins_upstand_t"] - mt), y + fin), (out(-p["ins_upstand_t"] - mt), y + top), "MEMBRANE")
        dxf.text("%s kerb to +%.0f" % (e["id"], top), (out(0), y + top + h), h, "DETAILS")
        return top
    if kind == "check_kerb":
        dxf.poly([(out(-p["kerb_w"]), y + fin), (out(-p["kerb_w"]), y + fin + p["check_kerb_h"]),
                  (out(0), y + fin + p["check_kerb_h"]), (out(0), y + fin)], "DETAILS", closed=True)
        return fin + p["check_kerb_h"]
    return fin


# ── 3. edge details ──────────────────────────────────────────────────────

def _detail(dxf, p, kind, e, ox, oy):
    """A 1:5 detail of one edge type, drawn from the parameters, with the roof to the right
    of x = 0 (the edge line) and heights above the top of structure. Returns (w, h)."""
    h = T_DETAIL
    mt, vt, T, dk = p["membrane_t"], p["vcl_t"], p["insulation_t"], p["deck_t"]
    prof = e.get("profile") or []
    fin = max([z for _t, z, s in prof if not s] or [p["above_firrings"] + p["d_min"]])
    fd = fin - p["above_firrings"]            # firring depth at the edge's highest point
    run = 450.0
    X = lambda x: ox + x + 400                # noqa: E731 — leave room for the wall
    Y = lambda y: oy + y + 300                # noqa: E731
    box = lambda x0, y0, x1, y1, layer: dxf.poly([(X(x0), Y(y0)), (X(x1), Y(y0)), (X(x1), Y(y1)), (X(x0), Y(y1))],  # noqa: E731
                                                 layer, closed=True)
    # The structure: a joist below the datum, firrings on it, then the buildup.
    box(0, -200, run, 0, "STRUCTURE")
    box(0, 0, run, fd, "FIRRINGS")
    box(0, fd, run, fd + dk, "DECK")
    box(0, fd + dk, run, fd + dk + vt, "VCL")
    box(0, fd + dk + vt, run, fd + dk + vt + T, "INSULATION")
    box(0, fin - mt, run, fin, "MEMBRANE")
    notes = ["%s membrane %.0f mm" % (p["membrane_name"], mt), "%.0f mm insulation" % T, "VCL %.0f mm" % vt,
             "%.0f mm WBP plywood deck" % dk, "firrings to falls, %.0f mm here" % fd, "top of structure (joists)"]
    ys = [fin, fd + dk + vt + T / 2, fd + dk + vt, fd + dk / 2, fd / 2, -100]
    for t, y in zip(notes, ys):
        dxf.line((X(run), Y(y)), (X(run + 80), Y(y)), "DETAILS")
        dxf.text(t, (X(run + 100), Y(y) - h / 2), h, "DETAILS")
    top = fin
    title = EDGE_LABELS.get(kind, kind).upper()
    if kind in ("abutment", "parapet"):
        wall = e.get("wall") or {}
        thick = float(wall.get("thickness") or p["wall_t"])
        ins = p["ins_upstand_t"] if kind == "abutment" else 0.0
        top = upstand_top(p, e) or fin + p["upstand"]
        wall_top = top + 250 if kind == "abutment" else top
        dxf.poly([(X(0), Y(-250)), (X(0), Y(wall_top)), (X(-thick), Y(wall_top)), (X(-thick), Y(-250))], "DETAILS")
        box(0, fd + dk, vt, fin - mt, "VCL")                                      # VCL turned up
        if ins:
            box(vt, fin - mt, vt + ins, top - mt, "INSULATION")                   # insulation upstand
        box(vt + ins, fin - mt, vt + ins + mt, top, "MEMBRANE")                   # membrane upstand
        if kind == "abutment":
            off = vt + ins + mt
            dxf.poly([(X(off), Y(top - p["flash_lap"])), (X(off + 3), Y(top - p["flash_lap"])), (X(off + 3), Y(top + 28)),
                      (X(-25), Y(top + 28)), (X(-25), Y(top + 25)), (X(off), Y(top + 25))], "DETAILS", closed=True)
            dxf.text("counter-flashing chased into the wall, laps the upstand %.0f" % p["flash_lap"], (X(off + 40), Y(top + 40)), h, "DETAILS")
        else:
            box(-thick - 25, top, vt + mt, top + mt, "MEMBRANE")
            dxf.poly([(X(-thick - 50), Y(top + mt)), (X(vt + mt + 50), Y(top + mt)), (X(vt + mt + 50), Y(top + mt + 50)),
                      (X(-thick - 50), Y(top + mt + 50))], "DETAILS", closed=True)
            dxf.text("coping over the membrane carried up and over the parapet", (X(vt + 80), Y(top + 60)), h, "DETAILS")
        _dim(dxf, (X(vt + ins + mt + 120), Y(fin)), (X(vt + ins + mt + 120), Y(top)), -40, h,
             "upstand %.0f above finished surface" % (top - fin))
        dxf.text("%s  (%s, %.0f thick)" % (wall.get("name") or "wall", "rises %.0f" % wall["rise"] if wall.get("rise") else "", thick),
                 (X(-thick), Y(-250) - h * 2), h, "DETAILS")
        top = wall_top + 60
    elif kind in ("drip", "gutter"):
        w, tr = p["trim_w"], p["trim_h"]
        dxf.poly([(X(w), Y(fin - mt)), (X(-3), Y(fin - mt)), (X(-3), Y(fin - mt - tr)), (X(-12), Y(fin - mt - tr - 8))], "DETAILS")
        dxf.poly([(X(run), Y(fin)), (X(-3), Y(fin)), (X(-3 - mt), Y(fin - mt - tr + 10))], "MEMBRANE")
        dxf.text("drip trim %.0f x %.0f, membrane dressed over" % (w, tr), (X(20), Y(fin + 30)), h, "DETAILS")
        if kind == "gutter":
            cx, cy, r = -3 - 70, fin - mt - tr - 40, 60
            dxf.poly([(X(cx + r * math.cos(math.pi + math.pi * i / 12)), Y(cy + r * math.sin(math.pi + math.pi * i / 12)))
                      for i in range(13)], "DRAINS")
            dxf.text("eaves gutter (symbol)", (X(cx - r), Y(cy - r - 30)), h, "DETAILS")
    elif kind == "check_kerb":
        kh, kw = p["check_kerb_h"], p["kerb_w"]
        box(0, fin - mt, kw, fin + kh, "DETAILS")
        dxf.poly([(X(run), Y(fin)), (X(kw), Y(fin)), (X(kw), Y(fin + kh + mt)), (X(-3), Y(fin + kh + mt)), (X(-3), Y(fin - 40))], "MEMBRANE")
        _dim(dxf, (X(kw + 60), Y(fin)), (X(kw + 60), Y(fin + kh)), -40, h, "check kerb %.0f" % kh)
        top = fin + kh
    elif kind == "kerb":
        top = fin + p["upstand"]
        kw, ins = p["kerb_w"], p["ins_upstand_t"]
        box(-kw, 0, 0, top, "DETAILS")
        box(0, fin - mt, ins, top - mt, "INSULATION")
        dxf.poly([(X(run), Y(fin)), (X(ins + mt), Y(fin)), (X(ins + mt), Y(top)), (X(-kw - 3), Y(top)), (X(-kw - 3), Y(top - 60))], "MEMBRANE")
        dxf.text("timber kerb %.0f wide, rooflight or hatch on top" % kw, (X(-kw), Y(top + 30)), h, "DETAILS")
        _dim(dxf, (X(ins + mt + 120), Y(fin)), (X(ins + mt + 120), Y(top)), -40, h, "upstand %.0f above finished surface" % (top - fin))
    dxf.text("DETAIL: %s  -  intended 1:5" % title, (ox, Y(max(top, fin) + 180)), h * 2, "NOTES")
    dxf.text("Drawn from the parameters at %s's highest finished level, +%.0f." % (e["id"], fin), (ox, Y(max(top, fin) + 140)), h, "NOTES")
    return 2000.0, max(top, fin) + 700


def _hopper_detail(dxf, p, s, o, e, ox, oy):
    """Section through a sump square to the wall: the roof buildup to the rim, the drop,
    the sump's reduced insulation, the membrane lining, and the outlet through the wall
    into the hopper head. The wall is at x = 0, the roof to the right."""
    h = T_DETAIL
    mt, vt, T, dk, si = p["membrane_t"], p["vcl_t"], p["insulation_t"], p["deck_t"], p["sump_ins"]
    X = lambda x: ox + x + 700                # noqa: E731
    Y = lambda y: oy + y + 500                # noqa: E731
    box = lambda x0, y0, x1, y1, layer: dxf.poly([(X(x0), Y(y0)), (X(x1), Y(y0)), (X(x1), Y(y1)), (X(x0), Y(y1))],  # noqa: E731
                                                 layer, closed=True)
    W, r, sf, rho = s["W"], s["r"], s["sf"], s["rho"]
    run = W + 400.0
    floor_lo = sf + dk + vt + si + mt
    rim = r + dk + vt + T + mt
    box(0, -200, run, 0, "STRUCTURE")
    # the sump: its firrings (if any), deck, VCL, insulation, membrane, 0..W from the wall
    lay = [("FIRRINGS", sf), ("DECK", dk), ("VCL", vt), ("INSULATION", si), ("MEMBRANE", mt)]
    z = 0.0
    for name, t in lay:
        if t > 0:
            box(0, z, W, z + t, name)
        z += t
    # the roof beyond the rim
    lay = [("FIRRINGS", r), ("DECK", dk), ("VCL", vt), ("INSULATION", T), ("MEMBRANE", mt)]
    z = 0.0
    for name, t in lay:
        box(W, z, run, z + t, name)
        z += t
    dxf.poly([(X(run), Y(rim)), (X(W), Y(rim)), (X(W), Y(floor_lo)), (X(0), Y(floor_lo))], "MEMBRANE")
    _dim(dxf, (X(W + 60), Y(floor_lo + rho)), (X(W + 60), Y(rim)), -40, h, "drop %.0f" % s["drop"])
    _dim(dxf, (X(0), Y(-260)), (X(W), Y(-260)), -30, h, "sump %.0f out from the wall" % W)
    thick = float((e.get("wall") or {}).get("thickness") or p["wall_t"])
    top = upstand_top(p, e) or rim + p["upstand"]
    dxf.poly([(X(0), Y(-250)), (X(0), Y(top + 250)), (X(-thick), Y(top + 250)), (X(-thick), Y(-250))], "DETAILS")
    box(-thick, floor_lo, 0, floor_lo + p["hopper_h"], "OUTLETS")
    box(-thick, floor_lo + 3, 0, floor_lo + p["hopper_h"] - 3, "OUTLETS")
    dxf.line((X(0), Y(rim)), (X(0), Y(top)), "MEMBRANE")
    hx = -thick
    dxf.poly([(X(hx), Y(floor_lo + p["hopper_h"] + 50)), (X(hx - 200), Y(floor_lo + p["hopper_h"] + 50)),
              (X(hx - 150), Y(floor_lo - 300)), (X(hx - 50), Y(floor_lo - 300)), (X(hx), Y(floor_lo - 200))], "OUTLETS")
    dxf.line((X(hx - 100), Y(floor_lo - 300)), (X(hx - 100), Y(floor_lo - 600)), "OUTLETS")
    notes = ["rim +%.0f: roof firrings %.0f, %.0f insulation" % (rim, r, T),
             "sump floor +%.0f: firrings %.0f, %.0f insulation, rises %.0f" % (floor_lo, sf, si, rho),
             "membrane lines the sump and runs through the sleeve",
             "through-wall penetration %.0f x %.0f with sleeve" % (p["hopper_w"], p["hopper_h"]),
             "hopper head on the outside face"]
    for i, t in enumerate(notes):
        dxf.text(t, (X(run + 60), Y(rim - i * h * 2)), h, "DETAILS")
    dxf.text("HOPPER DETAIL: OUTLET %d on %s  -  intended 1:5" % (o["n"], e["id"]), (ox, Y(top + 380)), h * 2, "NOTES")
    return run + 1500.0, top + 1000


# ── 4. notes and schedule ────────────────────────────────────────────────

def _wrap(text, width=90):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines


def _notes(p, roof, falls, checks):
    datum = float(roof.get("datum_z") or 0.0)
    L = ["%s  -  NOTES AND SCHEDULE  -  %s" % (TOOL_NAME.upper(), (roof.get("name") or "Roof").upper())]
    L.append("Buildup, bottom up: firrings to falls (min %.0f), %.0f mm WBP plywood deck, VCL %.0f mm, "
             "insulation %.0f mm, %s %.0f mm." % (p["d_min"], p["deck_t"], p["vcl_t"], p["insulation_t"],
                                                p["membrane_name"], p["membrane_t"]))
    L.append("Falls: main %s, crickets %s, larger sumps %s. Top of structure %.0f." % (
        fall_label(p["fall"]), fall_label(p["cricket_fall"]), fall_label(p["sump_fall"]), datum))
    L.append("Firring depth: min %.0f, max %.0f. Upstands %.0f above the finished surface." % (
        falls.get("min_depth") or 0, falls.get("max_depth") or 0, p["upstand"]))
    L.append("Sump buildup: sump firrings (default %.0f), deck, VCL, %.0f mm insulation, membrane; "
             "minimum drop %.0f." % (p["sump_firring"], p["sump_ins"], p["sump_drop"]))
    L.append("OUTLET SCHEDULE: no. / type / edge / offset / sump L x W / rim / floor / drop / rim firrings")
    for o in falls["outlets"]:
        if "k" not in o:
            L.append("O%d  %s  %s  not placed: %s" % (o["n"], o["type"], o.get("edge"), "; ".join(o["errors"])))
            continue
        s = falls["sumps"][o["k"]]
        if s["kind"] == "sump":
            rim, floor = s["r"] + p["above_firrings"], s["sf"] + p["sump_buildup"]
            L.append("O%d  %s  %s  %.0f from %s  %.0f x %.0f  +%.0f (%.0f)  +%.0f (%.0f)  %.0f  %.0f" % (
                o["n"], o["type"], o["edge"], o["offset"], "start" if o["corner"] == "a" else "end",
                s["b2"] - s["b1"], s["W"], rim, datum + rim, floor, datum + floor, s["drop"], s["r"]))
        else:
            L.append("O%d  %s  %s  %.0f from %s  no sump  rim firrings %.0f" % (
                o["n"], o["type"], o["edge"], o["offset"], "start" if o["corner"] == "a" else "end", s["r"]))
    gut = [s["edge"] for s in falls["sumps"] if s["kind"] == "gutter"]
    if gut:
        L.append("Gutter edges: %s (symbol only)." % ", ".join(gut))
    L.append("CHECKS:")
    for c in checks:
        L += _wrap("[%s] %s: %s" % (c["status"].upper(), c["name"], c["message"]))
    L.append("")
    L += _wrap(SCOPE_NOTE) + _wrap(QUANTITY_NOTE) + [DISCLAIMER]
    L.append("Layers: " + " ".join(LAYERS))
    return L


# ── entry point ──────────────────────────────────────────────────────────

def roof_to_dxf_string(params, built=None, checks=None):
    p = _parse(params)
    if built is None:
        from roof_preview import build_all
        _m, built, checks = build_all(p)
    checks = checks or []
    dxf = _Dxf()
    ox = 0.0
    for roof, falls, _info in built:
        mine = [c for c in checks if c.get("roof") == roof.get("name")]
        W, H = _plan(dxf, p, roof, falls, ox, 0.0)
        y_notes = -1500.0
        for i, line in enumerate(_notes(p, roof, falls, mine)):
            dxf.text(line, (ox, y_notes - i * T_PLAN * 1.6), T_PLAN * (1.4 if i == 0 else 0.8), "NOTES")
        x = ox + W + GAP
        for cp in roof.get("cut_planes") or default_planes(roof):
            w, _h = _section(dxf, p, roof, falls, cp, x, 0.0)
            x += w + GAP
        kinds = []
        for e in falls.get("edges") or []:
            if e["type"] not in kinds and e["type"] != "penetration":
                kinds.append(e["type"])
        for kind in kinds:
            e = max((x_ for x_ in falls["edges"] if x_["type"] == kind),
                    key=lambda x_: max([z for _t, z, _s in x_.get("profile") or []] or [0.0]))
            w, _hh = _detail(dxf, p, kind, e, x, 0.0)
            x += w + 500.0
        by_id = {e["id"]: e for e in falls.get("edges") or []}
        for o in falls["outlets"]:
            if o["type"] == "hopper" and "k" in o and falls["sumps"][o["k"]]["kind"] == "sump":
                w, _hh = _hopper_detail(dxf, p, falls["sumps"][o["k"]], o, by_id[o["edge"]], x, 0.0)
                x += w + 500.0
        ox = x + 4000.0
    dxf.text("%s  -  %s companion export  -  units mm, drawn at true size" % (TOOL_NAME, IFC_SCHEMA_LABEL),
             (0.0, 3000.0 + max([0.0] + [float(r.get("height") or 0) for r, _f, _i in built])), T_PLAN * 1.2, "NOTES")
    return dxf.to_string()


def default_planes(roof):
    """Two cutting planes through the middle of the roof, one each way."""
    region = region_of(roof)
    if region.is_empty:
        return []
    c = region.centroid
    return [{"label": "A", "dir": "u", "pos": round(c.y, 1), "flip": False},
            {"label": "B", "dir": "v", "pos": round(c.x, 1), "flip": False}]
