"""Fallwright — thin facade for Pyodide: one call builds everything the page shows.

    generate(params)          -> {"geometry", "roofs": [{"name", "falls", "info"}], "checks"}
    falls_only(params, name)  -> {"facets", "creases", "sumps", "outlets", "unreached"}
                                 the drag path: recomputed on every animation frame while a
                                 sump or outlet moves; layers and breps rebuild on release.
"""

from roof_constants import _parse
from roof_falls import analyse, facet_json, sump_json
from roof_geometry import build_roof
from roof_checks import check_roof


def _live(p):
    return [r for r in p["roofs"] if r.get("ok", True) and r.get("polygons")]


def _falls_json(roof, falls, p, full=True):
    datum = float(roof.get("datum_z") or 0.0)
    out = {"facets": [facet_json(f) for f in falls["facets"]], "creases": falls["creases"],
           "sumps": [sump_json(s, p, datum) for s in falls["sumps"]],
           "outlets": [{k: v for k, v in o.items()} for o in falls["outlets"]],
           "unreached": [[[round(x, 1), round(y, 1)] for x, y in list(pg.exterior.coords)[:-1]]
                         for pg in falls["unreached"]]}
    if full:
        out.update({"contours": falls["contours"], "spots": falls["spots"], "ponding": falls["ponding"],
                    "min_depth": falls["min_depth"], "max_depth": falls["max_depth"], "merged": falls["merged"],
                    "edges": [{k: v for k, v in e.items()} for e in falls["edges"]]})
    return out


def build_all(p):
    """(meshes, [(roof, falls, info)], checks) for every built roof."""
    meshes, built, checks = [], [], []
    for roof in _live(p):
        falls = analyse(roof, p)
        m, info = build_roof(p, roof, falls)
        meshes += m
        built.append((roof, falls, info))
        checks += check_roof(p, roof, falls)
    return meshes, built, checks


def generate(params):
    p = _parse(params)
    meshes, built, checks = build_all(p)
    roofs = [{"name": roof.get("name"), "falls": _falls_json(roof, falls, p), "info": info}
             for roof, falls, info in built]
    return {"geometry": _unique_names(meshes), "roofs": roofs, "checks": checks}


def falls_only(params, name=None):
    p = _parse(params)
    for roof in _live(p):
        if name is None or roof.get("name") == name:
            return _falls_json(roof, analyse(roof, p, full=False), p, full=False)
    return None


def _unique_names(meshes):
    counts, seen = {}, {}
    for m in meshes:
        m["name"] = m.get("name") or m.get("ifc_type", "Element").replace("_", " ").title()
        counts[m["name"]] = counts.get(m["name"], 0) + 1
    for m in meshes:
        if counts[m["name"]] > 1:
            seen[m["name"]] = seen.get(m["name"], 0) + 1
            m["name"] = "%s %d" % (m["name"], seen[m["name"]])
    return meshes
