"""
Fallwright — roof extraction.

Turns the picked top-of-structure triangles into one roof region in its own plan frame:
level, u square to the longest outline edge, holes for the voided openings, walls that
stand on the deck and anything else crossing the deck plane (pipes, flues) cut out, and
every outline edge classified (roof_edges.py). Runs once per selection.

Payload (IFC mm, Z-up), as fabric_extract's:
  {"name": "Roof 1", "faces": [[[x,y,z],...],...], "outward": [nx,ny,nz],
   "seeds": [[x,y,z],...], "context": [{"type", "name", "tris"}, ...],
   "options": {"penetrations": true, "debug": true, "budget_s": 20.0}}

Result: {"ok", "name", "warnings", "frame", "datum_z", "polygons", "width", "height",
         "area", "edges", "holes", "n_faces", "timing"}

Pyodide runs on the page's main thread, so while this runs the page is frozen and nothing
in the browser can be asked what it is doing. Two things make it answerable. With
options.debug (on by default) every stage and every nearby element is printed as it goes,
with its time; the page routes Python's stdout to the console, so the last line printed
names what did not finish. And every loop over nearby elements runs to a deadline
(options.budget_s, DEFAULT_BUDGET_S by default): past it the loop stops and the roof comes
back from what was read, with a warning naming the stage and what was skipped.
result["timing"] has the stage times and the slowest elements.
"""

import math
import time

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

from cladding_booleans import iter_polygons, polygon_to_rings, _difference
from fabric_extract import _dedupe, fit_plane, make_frame, roof_axis, _section, MIN_HOLE_AREA
from roof_constants import MIN_OPENING
from roof_edges import WALL_TYPES, DOOR_TYPES, SLOW_MS, wall_records, wall_feet, shift_records, classify

IGNORE_TYPES = frozenset({"IFCSPACE", "IFCSITE", "IFCOPENINGELEMENT", "IFCOPENINGSTANDARDCASE",
                          "IFCVIRTUALELEMENT", "IFCGRID", "IFCANNOTATION", "IFCFURNISHINGELEMENT",
                          "IFCCOVERING", "IFCSLAB", "IFCSLABSTANDARDCASE", "IFCROOF",
                          "IFCBUILDINGSTOREY", "IFCBEAM", "IFCMEMBER"}) | WALL_TYPES | DOOR_TYPES
SIMPLIFY = 2.0     # mm – collinear points and triangulation wiggles off the outline
DEFAULT_BUDGET_S = 20.0   # s – the loops over nearby elements give up past this rather than hang
STAGES = ("plane_fit", "plan_union", "wall_records", "wall_feet", "penetrations", "cleanup", "classify", "total")


def extract_roof(payload):
    """Main entry point. Never raises: a failure comes back ok=False with the reason."""
    try:
        return _extract(payload)
    except Exception as exc:   # noqa: BLE001 — surfaced to the UI, never silent
        return {"ok": False, "name": payload.get("name") or "Roof", "n_faces": 0,
                "warnings": ["Extraction failed: %s: %s" % (type(exc).__name__, exc)]}


def _plan_union(faces):
    polys = []
    for tri in faces:
        try:
            pg = Polygon([(v[0], v[1]) for v in tri])
            if pg.area > 1.0:
                polys.append(pg if pg.is_valid else pg.buffer(0))
        except Exception:   # noqa: BLE001
            continue
    if not polys:
        return None
    region = unary_union(polys)
    # Weld hairline gaps between triangles; mitre joins keep corners sharp.
    return region.buffer(0.5, join_style=2).buffer(-0.5, join_style=2)


def _say(on, msg):
    """Print a stage as it starts or ends. The page routes this to the console as it is
    written, so a slow or stuck extraction shows where it is while it runs."""
    if on:
        print(msg, flush=True)


class _Clock:
    """Stage times for result["timing"], logged as each stage ends."""

    def __init__(self, name, on):
        self.name, self.on = name, on
        self.start = self.last = time.perf_counter()
        self.stages, self.times = {}, []

    def lap(self, stage):
        now = time.perf_counter()
        self.stages[stage] = round((now - self.last) * 1000.0, 1)
        self.last = now
        _say(self.on, "%s: %s %.0f ms" % (self.name, stage.replace("_", " "), self.stages[stage]))

    def timing(self):
        stages = {k: self.stages.get(k, 0.0) for k in STAGES}
        stages["total"] = round((time.perf_counter() - self.start) * 1000.0, 1)
        slowest = sorted(self.times, key=lambda t: -t["ms"])[:3]
        return {"stages": stages, "slowest": slowest}


def _timed_out(stage, done, total):
    return ("Ran out of time %s after %d of %d nearby elements — %d skipped. The roof is built from "
            "what was read; check the edges next to anything skipped, or pick a smaller patch or switch "
            "off penetrations." % (stage, done, total, total - done))


def _extract(payload):
    name = payload.get("name") or "Roof"
    options = payload.get("options") or {}
    dbg = bool(options.get("debug", True))
    budget = options.get("budget_s")      # 0 is a real value, so do not fall back on falsiness
    clock = _Clock(name, dbg)
    deadline = clock.start + (DEFAULT_BUDGET_S if budget is None else float(budget))
    faces = _dedupe([[tuple(float(c) for c in v) for v in tri] for tri in payload.get("faces", [])])
    context = payload.get("context") or []
    result = {"ok": False, "name": name, "warnings": [], "n_faces": len(faces)}
    _say(dbg, "%s: extracting — %d faces, %d nearby elements (%d triangles), budget %.0f s" % (
        name, len(faces), len(context), sum(len(c.get("tris") or []) for c in context),
        DEFAULT_BUDGET_S if budget is None else float(budget)))

    def done():
        result["timing"] = clock.timing()
        _say(dbg, "%s: %s in %.0f ms — slowest: %s" % (
            name, "done" if result["ok"] else "FAILED", result["timing"]["stages"]["total"],
            ", ".join("%s %r %.0f ms" % (t["type"], t["name"], t["ms"]) for t in result["timing"]["slowest"]) or "nothing"))
        return result

    if not faces:
        result["warnings"].append("No faces picked")
        return done()
    n, datum, warnings = fit_plane(faces, payload.get("outward"), mode="roof")
    result["warnings"] += warnings
    clock.lap("plane_fit")
    if n is None:
        return done()
    world = _plan_union(faces)
    if world is None or world.is_empty:
        result["warnings"].append("Picked faces have no area")
        return done()
    biggest = max(iter_polygons(world), key=lambda pg: pg.area)
    # Rounded as make_frame rounds it, so the walls sliced now are exactly the ones the
    # frame would give: they are sliced once and only moved for the local origin later.
    u = tuple(round(c, 9) for c in roof_axis(list(biggest.exterior.coords)[:-1]))
    angle = math.atan2(u[1], u[0])
    to_local = lambda g: rotate(g, -angle, origin=(0.0, 0.0), use_radians=True)   # noqa: E731
    region = to_local(world)
    clock.lap("plan_union")

    # Everything else near the roof: walls standing on the deck are cut out at their foot,
    # anything else crossing the deck plane is a penetration, cut as its convex hull.
    report = {}
    records = wall_records(context, {"u": list(u)}, datum, (0.0, 0.0),
                           say=(lambda m: _say(dbg, m)), deadline=deadline, report=report)
    clock.times += report["times"]
    if report["timed_out"]:
        result["warnings"].append(_timed_out("reading walls and doors", report["done"], report["total"]))
    clock.lap("wall_records")
    feet = wall_feet(records)
    if feet is not None and feet.intersection(region).area > MIN_HOLE_AREA:
        cut = _difference(region, feet)
        if cut is not None and not cut.is_empty:
            region = cut
    clock.lap("wall_feet")
    skipped, cuts = 0, []
    others = [elem for elem in context if (elem.get("type") or "").upper() not in IGNORE_TYPES and elem.get("tris")] \
        if options.get("penetrations", True) else []
    for k, elem in enumerate(others):
        if time.perf_counter() > deadline:
            result["warnings"].append(_timed_out("cutting penetrations", k, len(others)))
            _say(dbg, "  DEADLINE reached — skipping %d remaining element(s)" % (len(others) - k))
            break
        t0 = time.perf_counter()
        try:
            tris = [[tuple(float(c) for c in v) for v in tri] for tri in elem["tris"]]
            straddles, touches, poly, _pts = _section(tris, n, datum, u, "roof")
            if straddles and touches and poly is not None:
                cuts.append(poly.convex_hull)
        except Exception:   # noqa: BLE001
            skipped += 1
        ms = (time.perf_counter() - t0) * 1000.0
        clock.times.append({"stage": "penetrations", "index": k + 1, "total": len(others),
                            "type": elem.get("type") or "", "name": elem.get("name") or "",
                            "tris": len(elem["tris"]), "ms": round(ms, 1)})
        _say(dbg, "  penetrations %d/%d %s %r %d tris %.0f ms%s" % (
            k + 1, len(others), elem.get("type") or "", elem.get("name") or "", len(elem["tris"]), ms,
            "  SLOW" if ms > SLOW_MS else ""))
    for cut_poly in cuts:
        try:
            if cut_poly.intersection(region).area > MIN_HOLE_AREA:
                cut = _difference(region, cut_poly)
                if cut is not None and not cut.is_empty:
                    region = cut
        except Exception:   # noqa: BLE001
            continue
    if skipped:
        result["warnings"].append("%d nearby element(s) could not be sectioned and were ignored" % skipped)
    clock.lap("penetrations")

    # One roof per build: the patch the clicks landed on, or else the biggest.
    parts = [pg for pg in iter_polygons(region) if pg.area > MIN_HOLE_AREA]
    if not parts:
        result["warnings"].append("Nothing left of the picked face once walls and penetrations were cut")
        clock.lap("cleanup")
        return done()
    seeds = [to_local(Point(s[0], s[1])) for s in payload.get("seeds") or [] if s and len(s) >= 2]
    keep = next((pg for pg in parts for q in seeds if pg.buffer(100.0).contains(q)), None)
    if keep is None:
        keep = max(parts, key=lambda pg: pg.area)
    if len(parts) > 1:
        result["warnings"].append("%d separate patches: only the one you clicked is the roof; pick again "
                                  "on another level to build a second roof" % len(parts))
    keep = keep.simplify(SIMPLIFY, preserve_topology=True)

    # Local origin at the region's bottom-left.
    umin, vmin, umax, vmax = keep.bounds
    keep = translate(keep, -umin, -vmin)
    ext, holes = polygon_to_rings(keep)
    ext = _from_south_west(ext)
    holes = [_from_south_west(h) for h in holes if abs(Polygon(h).area) >= MIN_HOLE_AREA]
    polygons = [{"exterior": ext, "holes": holes}]
    frame = make_frame(n, datum, umin, vmin, mode="roof", u=u)
    clock.lap("cleanup")
    records = shift_records(records, umin, vmin)       # the walls, moved with the region
    edges = classify(polygons, records)
    hole_info = []
    for k, h in enumerate(holes):
        us, vs = [q[0] for q in h], [q[1] for q in h]
        w, d = max(us) - min(us), max(vs) - min(vs)
        hole_info.append({"id": "H%d" % (k + 1), "w": round(w, 1), "d": round(d, 1),
                          "kind": "opening" if w >= MIN_OPENING and d >= MIN_OPENING else "penetration"})
    result.update({"ok": True, "frame": frame, "datum_z": round(datum, 3), "polygons": polygons,
                   "width": round(umax - umin, 2), "height": round(vmax - vmin, 2),
                   "area": round(keep.area, 1), "edges": edges, "holes": hole_info,
                   "n_walls": sum(1 for r in records if r["kind"] == "wall"),
                   "n_doors": sum(1 for r in records if r["kind"] == "door")})
    clock.lap("classify")
    return done()


def _from_south_west(ring):
    """The ring started at its lowest (then leftmost) vertex, so edge E1 is always the one
    running east from the south-west corner and edge names are stable between picks."""
    k = min(range(len(ring)), key=lambda i: (round(ring[i][1], 1), round(ring[i][0], 1)))
    return ring[k:] + ring[:k]
