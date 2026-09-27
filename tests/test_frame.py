"""The general frame: a wall frame is unchanged, a roof frame lies flat and round-trips."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from synthetic import payload, roof_payload, RU, RV, ROOF_ORIGIN, rworld  # noqa: E402
from cladding_constants import frame_to_world, world_to_frame  # noqa: E402
from fabric_extract import extract_elevation, fit_plane, make_frame  # noqa: E402
from roof_extract import extract_roof  # noqa: E402


def test_wall_frame_v_is_world_z():
    f = extract_elevation(payload())["frame"]
    assert f["v"] == [0.0, 0.0, 1.0]
    # n × u is world Z for a wall frame, so a frame with no v reads the same
    n, u = f["n"], f["u"]
    cross = (n[1] * u[2] - n[2] * u[1], n[2] * u[0] - n[0] * u[2], n[0] * u[1] - n[1] * u[0])
    assert all(abs(a - b) < 1e-6 for a, b in zip(cross, (0, 0, 1)))
    old = {k: v for k, v in f.items() if k != "v"}
    for q in ((0, 0, 0), (1234.5, 678.9, 55.0), (8000, 3000, -20)):
        assert frame_to_world(old, *q) == frame_to_world(f, *q)


def test_roof_frame_round_trip():
    r = extract_roof(roof_payload())
    f = r["frame"]
    assert f["n"] == [0.0, 0.0, 1.0]
    # u is square to the building: along its long side
    assert all(abs(a - b) < 1e-6 for a, b in zip(f["u"], RU))
    assert all(abs(a - b) < 1e-6 for a, b in zip(f["v"], RV))
    assert abs(f["origin"][2] - ROOF_ORIGIN[2]) < 1e-6
    for q in ((0, 0, 0), (10000, 6000, 0), (1234.567, 4321.001, 187.25), (-50, 7000.5, -300)):
        w = frame_to_world(f, *q)
        back = world_to_frame(f, w)
        assert all(abs(a - b) < 0.01 for a, b in zip(q, back)), (q, back)
    # the region's local origin is the structure's south-west corner
    assert all(abs(a - b) < 0.01 for a, b in zip(frame_to_world(f, 0, 0), rworld(0, 0)))


def test_roof_plane_modes():
    flat = [[[0, 0, 100], [1000, 0, 100], [1000, 1000, 100]]]
    n, d, _w = fit_plane(flat, [0, 0, 1], mode="roof")
    assert n == (0.0, 0.0, 1.0) and abs(d - 100) < 1e-9
    # a wall face is refused in roof mode, and a roof face in wall mode
    wall = [[[0, 0, 0], [1000, 0, 0], [1000, 0, 1000]]]
    assert fit_plane(wall, [0, -1, 0], mode="roof")[0] is None
    assert fit_plane(flat, [0, 0, 1])[0] is None
    # facing down is refused
    assert fit_plane(flat, [0, 0, -1], mode="roof")[0] is None
    # 3 degrees off level is accepted and snapped level; 8 is refused
    for deg, ok in ((3.0, True), (8.0, False)):
        s = math.tan(math.radians(deg)) * 1000
        tilted = [[[0, 0, 0], [1000, 0, s], [1000, 1000, s]], [[0, 0, 0], [1000, 1000, s], [0, 1000, 0]]]
        assert (fit_plane(tilted, [0, 0, 1], mode="roof")[0] is not None) is ok


def test_make_frame_roof_is_right_handed():
    u = (math.cos(0.3), math.sin(0.3), 0.0)
    f = make_frame((0, 0, 1), 50.0, 100.0, 200.0, mode="roof", u=u)
    U, V, N = f["u"], f["v"], f["n"]
    cross = (U[1] * V[2] - U[2] * V[1], U[2] * V[0] - U[0] * V[2], U[0] * V[1] - U[1] * V[0])
    assert all(abs(a - b) < 1e-9 for a, b in zip(cross, N))
    assert abs(f["origin"][2] - 50.0) < 1e-9
