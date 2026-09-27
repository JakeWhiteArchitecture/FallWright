"""The falls engine on synthetic roofs (requirements section 11)."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
from shapely.geometry import Point, Polygon  # noqa: E402

from synthetic import rect_roof, l_roof  # noqa: E402
from roof_constants import _parse, plane_z  # noqa: E402
from roof_falls import analyse, resolve  # noqa: E402
from roof_edges import edge_frame  # noqa: E402
from roof_preview import build_all  # noqa: E402

W, D = 10000.0, 6000.0


def run(roof, **params):
    p = _parse(params)
    return analyse(roof, p), p


def with_outlets(*outlets, **extra):
    r = rect_roof()
    r["outlets"] = list(outlets)
    r.update(extra)
    return r


def outlet(edge="E1", offset=5000.0, kind="internal", sump=False, corner="a", **kw):
    o = {"edge": edge, "corner": corner, "offset": offset, "type": kind, "sump": sump}
    o.update(kw)
    return o


def formula(roof, p, x, y):
    """The requirements' surface, written out independently of the engine:
    min over facing sumps of r + max((s - W)/G, e/Gc)."""
    sumps, _o = resolve(roof, p)
    edges = {e["id"]: e for e in roof["edges"]}
    best = None
    for s in sumps:
        e = edges[s["edge"]]
        ev, m, _L = edge_frame(e["a"], e["b"])
        sv = (x - e["a"][0]) * m[0] + (y - e["a"][1]) * m[1]
        t = (x - e["a"][0]) * ev[0] + (y - e["a"][1]) * ev[1]
        if sv < -1e-6:
            continue
        ek = max(s["b1"] - t, 0.0, t - s["b2"])
        z = s["r"] + max((sv - s["W"]) / p["fall"], ek / p["cricket_fall"])
        best = z if best is None else min(best, z)
    return best


def assert_planar(a, roof, p, tol=0.1):
    """Every facet vertex lies on its facet's plane, and on the requirements' surface."""
    for f in a["facets"]:
        for x, y in f["poly"].exterior.coords:
            z = plane_z(f["plane"], x, y)
            want = formula(roof, p, x, y)
            assert want is not None and abs(z - want) < tol, (f["id"], x, y, z, want)


def valleys(a):
    return [c for c in a["creases"] if c["kind"] == "valley"]


def test_one_gutter_edge_is_one_facet():
    r = rect_roof()
    r["edge_types"] = {"E1": "gutter"}
    a, p = run(r)
    assert len(a["facets"]) == 1
    pl = a["facets"][0]["plane"]
    assert abs(plane_z(pl, 3000, 0) - 25.0) < 1e-6              # the drain edge is at d_min
    assert abs(plane_z(pl, 3000, D) - (25.0 + D / 40.0)) < 1e-6  # far edge: d_min + width / G
    assert abs(a["facets"][0]["fall"] - 40.0) < 1e-6
    assert a["ponding"] == [] and not a["unreached"]
    assert_planar(a, r, p)


def test_one_outlet_no_sump():
    """The requirements' own formula gives one main-fall plane, not two: the main fall
    r + s/G does not depend on t, so the wedge between the valleys is a single facet."""
    r = with_outlets(outlet())
    a, p = run(r)
    kinds = sorted(f["kind"] for f in a["facets"])
    assert kinds == ["cricket", "cricket", "main"]
    vs = valleys(a)
    assert len(vs) == 2
    for v in vs:
        dx, dy = v["b"][0] - v["a"][0], v["b"][1] - v["a"][1]
        assert abs(abs(math.degrees(math.atan2(dy, dx))) - 45.0) < 0.01 or abs(abs(math.degrees(math.atan2(dy, dx))) - 135.0) < 0.01
        assert abs(v["fall"] - math.hypot(40, 40)) < 0.01 and v["label"] == "1:57"
        assert [round(c) for c in v["a"]] == [5000, 0]         # both start at the outlet
    assert_planar(a, r, p)


def test_sump_moves_the_valleys_to_its_corners():
    r = with_outlets(outlet(sump=True))
    a, p = run(r)
    s = a["sumps"][0]
    assert (s["b1"], s["b2"], s["W"]) == (4750.0, 5250.0, 300.0)
    starts = sorted(tuple(round(c, 1) for c in v["a"]) for v in valleys(a))
    assert starts == [(4750.0, 300.0), (5250.0, 300.0)]
    # the whole rim is at one level, round all three open sides
    for x, y in ((4749, 10), (4749, 299), (4800, 301), (5000, 301), (5200, 301), (5251, 150), (5251, 5)):
        assert abs(formula(r, p, x, y) - s["r"]) < 0.05
    # and the surface is continuous round it: no step between neighbouring facets
    assert not [c for c in a["creases"] if c["kind"] == "step"]
    assert_planar(a, r, p)


@pytest.mark.parametrize("T, rim, drop", [(120, 25.0, 95.0), (100, 25.0, 75.0), (80, 45.0, 75.0)])
def test_standard_sump_datum(T, rim, drop):
    a, _p = run(with_outlets(outlet(sump=True)), insulation_t=T)
    s = a["sumps"][0]
    assert abs(s["r"] - rim) < 1e-9 and abs(s["drop"] - drop) < 1e-9


def _valley_starts(a):
    return sorted((tuple(round(c, 1) for c in v["a"]) for v in valleys(a)))


def test_lengthening_or_widening_a_sump_moves_its_valleys():
    base, _p = run(with_outlets(outlet(sump=True, sump_t0=4750.0)))
    longer, _p = run(with_outlets(outlet(sump=True, sump_t0=4750.0, sump_l=700.0)))
    b0, b1 = _valley_starts(base)
    l0, l1 = _valley_starts(longer)
    assert l0 == b0                                   # the other end's valley stays
    assert abs(l1[0] - b1[0] - 200.0) < 0.01 and l1[1] == b1[1]
    wider, _p = run(with_outlets(outlet(sump=True, sump_w=400.0)))
    w0, w1 = _valley_starts(wider)
    assert w0[1] == 400.0 and w1[1] == 400.0         # both out into the roof
    assert w0[0] == b0[0] and w1[0] == b1[0]


def test_long_sump_with_the_hopper_off_to_one_side():
    """2000 mm sump, hopper 200 mm from one end: the rim is the drain, so the roof falls do
    not move with the hopper; the floor rises 45 mm to the far end at 1:40; with T = 120
    the rim firrings thicken to 50 and the rim is 75 above the far end."""
    def roof(offset):
        return with_outlets(outlet(edge="E2", offset=offset, kind="hopper", sump=True, sump_t0=1000.0, sump_l=2000.0))
    a, p = run(roof(1200.0))
    s = a["sumps"][0]
    assert abs(s["rho"] - 45.0) < 1e-9
    assert abs(s["r"] - 50.0) < 1e-9 and abs(s["drop"] - 75.0) < 1e-9
    assert len(s["floor"]) == 2
    e = next(x for x in roof(1200.0)["edges"] if x["id"] == "E2")
    ev, m, _L = edge_frame(e["a"], e["b"])

    def floor_at(t):
        x, y = e["a"][0] + ev[0] * t + m[0] * 1.0, e["a"][1] + ev[1] * t + m[1] * 1.0
        pc = next(pc for pc in s["floor"] if pc["poly"].buffer(0.01).contains(Point(x, y)))
        return plane_z(pc["plane"], x, y)
    assert abs(floor_at(1200.0)) < 0.05                       # lowest at the hopper
    assert abs(floor_at(2999.0) - 44.975) < 0.05              # rises 45 over 1800 to the far end
    assert abs(s["r"] + p["insulation_t"] - (s["sf"] + p["sump_ins"] + s["rho"]) - 75.0) < 1e-9
    other, _p = run(roof(2800.0))
    assert [f["poly"].wkt for f in a["facets"]] == [f["poly"].wkt for f in other["facets"]]
    assert [f["plane"] for f in a["facets"]] == [f["plane"] for f in other["facets"]]


@pytest.mark.parametrize("L, Wd, along, across", [(500, 300, False, False), (510, 300, True, False),
                                                    (500, 310, False, True)])
def test_standard_sump_is_level(L, Wd, along, across):
    a, _p = run(with_outlets(outlet(sump=True, sump_l=L, sump_w=Wd)))
    s = a["sumps"][0]
    assert (s["along"], s["across"]) == (along, across)
    if not along and not across:
        assert s["rho"] == 0 and all(abs(pc["plane"][1]) < 1e-12 and abs(pc["plane"][2]) < 1e-12 for pc in s["floor"])
    else:
        assert s["rho"] > 0


def test_two_sumps_of_different_widths_are_continuous():
    r = with_outlets(outlet(offset=2500.0, sump=True), outlet(offset=7500.0, sump=True, sump_w=600.0))
    a, p = run(r)
    assert not [c for c in a["creases"] if c["kind"] == "step"]
    # sample across the ridge between them: the engine's facets agree with the formula
    for x in range(4000, 6001, 125):
        for y in (50.0, 400.0, 900.0):
            f = next((f for f in a["facets"] if f["poly"].buffer(0.01).contains(Polygon([(x, y), (x + 0.01, y), (x, y + 0.01)]))), None)
            if f is not None:
                assert abs(plane_z(f["plane"], x, y) - formula(r, p, x, y)) < 0.05
    assert_planar(a, r, p)


def test_two_outlets_on_one_edge_ridge_midway():
    r = with_outlets(outlet(offset=2500.0, sump=True), outlet(offset=7500.0, sump=True))
    a, p = run(r)
    ridges = [c for c in a["creases"] if c["kind"] == "ridge"]
    assert ridges
    mid = (2750.0 + 7250.0) / 2
    assert all(abs(c["a"][0] - mid) < 0.01 and abs(c["b"][0] - mid) < 0.01 for c in ridges)
    # symmetrical: the surface mirrors about the ridge
    for x, y in ((1000, 500), (3000, 2000), (4500, 4000), (200, 5900)):
        assert abs(formula(r, p, x, y) - formula(r, p, W - x, y)) < 1e-6
    assert_planar(a, r, p)


def test_outlets_on_adjacent_edges_hip_on_the_bisector():
    r = with_outlets(outlet(edge="E1", offset=3000.0, sump=True), outlet(edge="E2", offset=3000.0, sump=True))
    a, p = run(r)
    hips = [c for c in a["creases"] if c["kind"] == "hip" and abs(c["a"][0] - c["b"][0]) > 1 and abs(c["a"][1] - c["b"][1]) > 1]
    assert hips
    for h in hips:        # on the bisector of the corner at (W, 0): x + y = W, the sumps equal
        for q in (h["a"], h["b"]):
            assert abs(q[0] + q[1] - W) < 0.05, h
    assert_planar(a, r, p)


def test_other_corner_complementary_offset():
    a1, _p = run(with_outlets(outlet(offset=3000.0, sump=True)))
    a2, _p = run(with_outlets(outlet(offset=7000.0, sump=True, corner="b")))
    assert sorted(f["poly"].wkt for f in a1["facets"]) == sorted(f["poly"].wkt for f in a2["facets"])
    assert sorted(f["plane"] for f in a1["facets"]) == sorted(f["plane"] for f in a2["facets"])


def test_l_shaped_roof_traps_water_in_the_other_wing():
    r = l_roof()
    r["outlets"] = [outlet(edge="E2", offset=2000.0)]
    a, p = run(r)
    assert a["ponding"]
    upper = Polygon([(0, 4000), (4000, 4000), (4000, 10000), (0, 10000)])
    flagged = [f for f in a["facets"] if f["id"] in a["ponding"]]
    assert any(f["poly"].intersection(upper).area > 0.5 * f["poly"].area for f in flagged)
    lower_only = [f for f in a["facets"] if f["poly"].intersection(upper).area < 1.0]
    assert not [f for f in lower_only if f["id"] in a["ponding"]]
    assert_planar(a, r, p)
    # every facet on a plain rectangle drains
    b, _p = run(with_outlets(outlet(sump=True)))
    assert b["ponding"] == []


def test_rooflight_hole_is_cut_from_every_layer():
    hole = [[4000.0, 2500.0], [4000.0, 3700.0], [5200.0, 3700.0], [5200.0, 2500.0]]
    r = rect_roof(holes=[hole])
    r["outlets"] = [outlet(sump=True)]
    p = _parse({"roofs": [r]})
    meshes, built, _c = build_all(p)
    kerb_band = Polygon(hole).buffer(p["kerb_w"], join_style=2)
    layers = [m for m in meshes if m["type"] == "slab" and not m["ifc_type"].startswith("sump")]
    assert {m["ifc_type"] for m in layers} == {"firring", "deck", "vcl", "insulation", "membrane"}
    for m in layers:
        shape = Polygon(m["rings"][0], m["rings"][1:])
        assert shape.intersection(kerb_band).area < 1.0, m["name"]
    kerbs = [m for m in meshes if m["ifc_type"] == "kerb"]
    assert len(kerbs) == 1
    fin = [m for m in meshes if m["ifc_type"] == "upstand" and "Kerb" in m["name"]]
    assert fin and all(abs(m["upstand_top"] - kerbs[0]["upstand_top"]) < 1e-9 for m in fin)


def test_valley_facets_share_the_mitre_exactly():
    r = with_outlets(outlet(sump=True))
    p = _parse({"roofs": [r]})
    meshes, built, _c = build_all(p)
    falls = built[0][1]
    firrings = {m["facet"]: m for m in meshes if m["ifc_type"] == "firring"}
    for v in valleys(falls):
        fa, fb = (firrings[i] for i in v["facets"])
        pa, pb = Polygon(fa["rings"][0]), Polygon(fb["rings"][0])
        assert pa.intersection(pb).area < 1e-6                  # no overlap
        shared = pa.boundary.intersection(pb.boundary)
        assert abs(shared.length - v["length"]) < 0.01          # no gap: they meet along the whole valley
        for x, y in (v["a"], v["b"]):                            # same top along it
            assert [x, y] in [list(q) for q in fa["rings"][0]] and [x, y] in [list(q) for q in fb["rings"][0]]
            assert abs(plane_z(fa["top"], x, y) - plane_z(fb["top"], x, y)) < 1e-6
