"""The checks table (requirements section 8)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import rect_roof, roof_payload  # noqa: E402
from roof_constants import _parse  # noqa: E402
from roof_extract import extract_roof  # noqa: E402
from roof_falls import analyse  # noqa: E402
from roof_checks import check_roof, level_along  # noqa: E402


def checks(roof, **params):
    p = _parse(params)
    out = {}
    for c in check_roof(p, roof, analyse(roof, p)):
        out.setdefault(c["name"], []).append(c)
    return out


def status(cs, name):
    """The worst status of a check that may appear once per element."""
    rank = {"pass": 0, "warn": 1, "fail": 2}
    return max((c["status"] for c in cs[name]), key=rank.get)


def outlet(edge="E1", offset=5000.0, kind="internal", sump=False, **kw):
    o = {"edge": edge, "corner": "a", "offset": offset, "type": kind, "sump": sump}
    o.update(kw)
    return o


def door_roof(margin):
    """The sample roof with its door sill set *margin* above the finished surface there."""
    r = extract_roof(roof_payload())
    r["outlets"] = [outlet("E2", 1500.0, "hopper"), outlet("E1", 3000.0, sump=True)]
    p = _parse({})
    falls = analyse(r, p)
    e3 = next(e for e in falls["edges"] if e["id"] == "E3")
    door = e3["doors"][0]
    half = door["width"] / 2
    fin = max(level_along(e3["profile"], t) for t in (door["t"] - half, door["t"], door["t"] + half))
    for e in r["edges"]:
        if e["id"] == "E3":
            e["doors"] = [dict(door, sill=fin + margin)]
    return r


def test_door_threshold():
    assert status(checks(door_roof(160.0)), "Door threshold") == "pass"
    assert status(checks(door_roof(100.0)), "Door threshold") == "warn"
    assert status(checks(door_roof(60.0)), "Door threshold") == "fail"


def test_single_outlet_needs_an_overflow():
    r = rect_roof()
    r["outlets"] = [outlet()]
    assert status(checks(r), "Overflow") == "warn"
    r["outlets"] = [outlet(offset=2500.0), outlet(offset=7500.0)]
    assert status(checks(r), "Overflow") == "pass"
    r["outlets"] = [outlet()]
    r["edge_types"] = {"E3": "gutter"}
    assert status(checks(r), "Overflow") == "pass"


def test_hopper_on_a_free_edge_fails():
    r = rect_roof()
    r["outlets"] = [outlet(kind="hopper", sump=True)]
    assert status(checks(r), "Hopper position") == "fail"
    r = extract_roof(roof_payload())
    r["outlets"] = [outlet("E2", 1500.0, "hopper")]
    assert status(checks(r), "Hopper position") == "pass"


def test_hopper_with_its_sump_off_fails():
    r = extract_roof(roof_payload())
    r["outlets"] = [outlet("E2", 1500.0, "hopper", sump=False)]
    assert status(checks(r), "Hopper sump") == "fail"


def test_minimum_firring_on_a_gutter_edge():
    r = rect_roof()
    r["edge_types"] = {"E1": "gutter"}
    assert status(checks(r), "Minimum firring depth") == "pass"
    assert status(checks(r, d_min=20), "Minimum firring depth") == "fail"


def test_sump_drop_and_insulation():
    r = rect_roof()
    r["outlets"] = [outlet(sump=True)]
    cs = checks(r)
    assert status(cs, "Sump drop") == "pass" and cs["Sump drop"][0]["value"] == 95.0
    # a typed 60 mm drop where the drop, not the insulation, sets the step
    assert status(checks(r, insulation_t=80, sump_drop=60), "Sump drop") == "fail"
    assert status(checks(r, sump_ins=40), "Sump insulation") == "fail"


def test_falls_valleys_and_the_rest():
    r = rect_roof()
    r["outlets"] = [outlet(sump=True), outlet(offset=1000.0, edge="E3")]
    cs = checks(r)
    assert status(cs, "Design fall, facets") == "pass"
    assert status(cs, "Design fall, valleys") == "warn"          # 1:57 at 1:40 and 1:40
    assert "Steepen the cricket fall" in cs["Design fall, valleys"][0]["message"]
    assert status(checks(r, cricket_fall=30), "Design fall, valleys") == "warn"   # 1:50 is still flatter
    assert status(checks(r, fall=28, cricket_fall=28), "Design fall, valleys") == "pass"   # 1:39.6
    assert status(cs, "Drainage coverage") == "pass" and status(cs, "Trapped water") == "pass"
    assert status(cs, "Outlets placed") == "pass" and status(cs, "Sump fits") == "pass"
    assert status(checks(r, fall=100), "Design fall, facets") == "fail"
    assert status(checks(r, fall=60), "Design fall, facets") == "warn"


def test_outlets_placed_and_sumps_that_do_not_fit():
    r = rect_roof()
    r["outlets"] = [outlet(offset=12000.0)]
    r["n_outlets"] = 2
    assert status(checks(r), "Outlets placed") == "fail"
    r["outlets"] = [outlet(offset=5000.0, sump=True, sump_t0=9800.0)]
    r["n_outlets"] = 1
    cs = checks(r)
    assert status(cs, "Sump fits") == "fail" and status(cs, "Outlet in sump") == "fail"


def test_deep_firrings_warn():
    r = rect_roof(depth=12000.0)
    r["edge_types"] = {"E1": "gutter"}
    assert status(checks(r), "Maximum firring depth") == "warn"
