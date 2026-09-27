"""Edge classification, on the synthetic roof and on the sample IFC through the importer."""

import array
import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402

from synthetic import roof_payload  # noqa: E402
from roof_extract import extract_roof  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def by_side(r):
    """Exterior edges keyed by compass side of the 10000 x 6000 roof."""
    out = {}
    for e in r["edges"]:
        if e["ring"] != 0:
            continue
        (ax, ay), (bx, by) = e["a"], e["b"]
        side = ("south" if ay < 1 and by < 1 else "north" if ay > 5999 and by > 5999
                else "west" if ax < 1 and bx < 1 else "east")
        out[side] = e
    return out


def test_wall_parapet_and_free_edges():
    r = extract_roof(roof_payload())
    assert r["ok"], r["warnings"]
    sides = by_side(r)
    assert sides["north"]["type"] == "abutment" and sides["north"]["wall"]["rise"] == 3000.0
    assert sides["east"]["type"] == "parapet" and sides["east"]["wall"]["rise"] == 1000.0
    assert sides["south"]["type"] == "drip" and sides["west"]["type"] == "drip"
    assert sides["east"]["wall"]["thickness"] == pytest.approx(300.0, abs=1.0)
    # the door in the abutment wall, with its sill above the structure
    doors = sides["north"]["doors"]
    assert len(doors) == 1 and doors[0]["sill"] == pytest.approx(360.0) and doors[0]["width"] == pytest.approx(900.0)
    # the rooflight opening gets a kerb; the pipe is a penetration
    kinds = {h["id"]: h["kind"] for h in r["holes"]}
    assert sorted(kinds.values()) == ["opening", "penetration"]
    assert {e["type"] for e in r["edges"] if e["ring"] > 0} == {"kerb", "penetration"}


def test_a_1600_wall_is_an_abutment():
    sides = by_side(extract_roof(roof_payload(parapet_rise=1600.0)))
    assert sides["east"]["type"] == "abutment"
    sides = by_side(extract_roof(roof_payload(parapet_rise=1400.0)))
    assert sides["east"]["type"] == "parapet"


def test_edges_start_south_west_and_run_anticlockwise():
    r = extract_roof(roof_payload())
    ext = [e for e in r["edges"] if e["ring"] == 0]
    assert ext[0]["id"] == "E1" and ext[0]["a"] == [0.0, 0.0] and ext[0]["b"] == [10000.0, 0.0]


def _tris(e):
    v = array.array("f")
    v.frombytes(base64.b64decode(e["verts"]))
    i = array.array("I")
    i.frombytes(base64.b64decode(e["idx"]))
    pts = [tuple(v[k:k + 3]) for k in range(0, len(v), 3)]
    return [[list(pts[i[k]]), list(pts[i[k + 1]]), list(pts[i[k + 2]])] for k in range(0, len(i), 3)]


def test_sample_ifc_through_the_importer():
    """The flat-roofed sample, tessellated by the server importer and picked as the browser
    would: every edge classified, the door found, the rooflight voided out."""
    from ifc_import import import_ifc
    res = import_ifc(os.path.join(HERE, "sample_roof.ifc"))
    slab = next(e for e in res["elements"] if e["name"] == "Roof slab")
    top = [t for t in _tris(slab) if all(abs(q[2] - 3000.0) < 1 for q in t)]
    ctx = [{"type": e["type"], "name": e["name"], "tris": _tris(e)} for e in res["elements"] if e is not slab]
    r = extract_roof({"name": "Roof 1", "faces": top, "outward": [0, 0, 1], "seeds": [[1000, 1000, 3000]],
                      "context": ctx, "options": {"penetrations": True}})
    assert r["ok"], r["warnings"]
    assert (r["width"], r["height"]) == (10000.0, 6000.0)
    types = {e["id"]: e["type"] for e in r["edges"] if e["ring"] == 0}
    assert types == {"E1": "drip", "E2": "parapet", "E3": "abutment", "E4": "drip"}
    assert r["holes"] and r["holes"][0]["kind"] == "opening"
    e3 = next(e for e in r["edges"] if e["id"] == "E3")
    assert len(e3["doors"]) == 1 and e3["doors"][0]["sill"] == pytest.approx(360.0)
