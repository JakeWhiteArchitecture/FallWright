"""
Fallwright — roof extraction.

Turns the picked top-of-structure triangles into one roof region in its own plan frame:
level, u square to the longest outline edge, holes for the voided openings, walls that
stand on the deck and anything else crossing the deck plane (pipes, flues) cut out, and
every outline edge classified (roof_edges.py). Runs once per selection.

Payload (IFC mm, Z-up), as fabric_extract's:
  {"name": "Roof 1", "faces": [[[x,y,z],...],...], "outward": [nx,ny,nz],
   "seeds": [[x,y,z],...], "context": [{"type", "name", "tris"}, ...],
   "options": {"penetrations": true}}

Result: {"ok", "name", "warnings", "frame", "datum_z", "polygons", "width", "height",
         "area", "edges", "holes", "n_faces"}
"""

import math

from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

from cladding_booleans import iter_polygons, polygon_to_rings, _difference
from fabric_extract import _dedupe, fit_plane, make_frame, roof_axis, _section, MIN_HOLE_AREA
from roof_constants import MIN_OPENING
from roof_edges import WALL_TYPES, DOOR_TYPES, wall_records, wall_feet, classify

IGNORE_TYPES = frozenset({"IFCSPACE", "IFCSITE", "IFCOPENINGELEMENT", "IFCOPENINGSTANDARDCASE",
                          "IFCVIRTUALELEMENT", "IFCGRID", "IFCANNOTATION", "IFCFURNISHINGELEMENT",
                          "IFCCOVERING", "IFCSLAB", "IFCSLABSTANDARDCASE", "IFCROOF",
                          "IFCBUILDINGSTOREY", "IFCBEAM", "IFCMEMBER"}) | WALL_TYPES | DOOR_TYPES
SIMPLIFY = 2.0     # mm – collinear points and triangulation wiggles off the outline


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


def _extract(payload):
    name = payload.get("name") or "Roof"
    faces = _dedupe([[tuple(float(c) for c in v) for v in tri] for tri in payload.get("faces", [])])
    result = {"ok": False, "name": name, "warnings": [], "n_faces": len(faces)}
    if not faces:
        result["warnings"].append("No faces picked")
        return result
    n, datum, warnings = fit_plane(faces, payload.get("outward"), mode="roof")
    result["warnings"] += warnings
    if n is None:
        return result
    world = _plan_union(faces)
    if world is None or world.is_empty:
        result["warnings"].append("Picked faces have no area")
        return result
    biggest = max(iter_polygons(world), key=lambda pg: pg.area)
    u = roof_axis(list(biggest.exterior.coords)[:-1])
    angle = math.atan2(u[1], u[0])
    to_local = lambda g: rotate(g, -angle, origin=(0.0, 0.0), use_radians=True)   # noqa: E731
    region = to_local(world)

    # Everything else near the roof: walls standing on the deck are cut out at their foot,
    # anything else crossing the deck plane is a penetration, cut as its convex hull.
    options = payload.get("options") or {}
    context = payload.get("context") or []
    rough = {"u": list(u)}
    records = wall_records(context, rough, datum, (0.0, 0.0))
    feet = wall_feet(records)
    if feet is not None and feet.intersection(region).area > MIN_HOLE_AREA:
        cut = _difference(region, feet)
        if cut is not None and not cut.is_empty:
            region = cut
    skipped, cuts = 0, []
    for elem in context:
        etype = (elem.get("type") or "").upper()
        if etype in IGNORE_TYPES or not elem.get("tris") or not options.get("penetrations", True):
            continue
        try:
            tris = [[tuple(float(c) for c in v) for v in tri] for tri in elem["tris"]]
            straddles, touches, poly, _pts = _section(tris, n, datum, u, "roof")
            if straddles and touches and poly is not None:
                cuts.append(poly.convex_hull)
        except Exception:   # noqa: BLE001
            skipped += 1
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

    # One roof per build: the patch the clicks landed on, or else the biggest.
    parts = [pg for pg in iter_polygons(region) if pg.area > MIN_HOLE_AREA]
    if not parts:
        result["warnings"].append("Nothing left of the picked face once walls and penetrations were cut")
        return result
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
    records = wall_records(context, frame, datum, (umin, vmin))
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
    return result


def _from_south_west(ring):
    """The ring started at its lowest (then leftmost) vertex, so edge E1 is always the one
    running east from the south-west corner and edge names are stable between picks."""
    k = min(range(len(ring)), key=lambda i: (round(ring[i][1], 1), round(ring[i][0], 1)))
    return ring[k:] + ring[:k]
