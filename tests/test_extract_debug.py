"""Extraction debugging: stage logging, the time limit, timing, replay, and the single
wall pass giving the same edges as slicing every wall twice."""

import array
import base64
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import pytest  # noqa: E402

import roof_edges  # noqa: E402
import roof_extract  # noqa: E402
from roof_extract import extract_roof, STAGES  # noqa: E402
from roof_edges import wall_records, classify  # noqa: E402
from synthetic import roof_payload  # noqa: E402


def _tris(e):
    v = array.array("f")
    v.frombytes(base64.b64decode(e["verts"]))
    i = array.array("I")
    i.frombytes(base64.b64decode(e["idx"]))
    pts = [tuple(v[k:k + 3]) for k in range(0, len(v), 3)]
    return [[list(pts[i[k]]), list(pts[i[k + 1]]), list(pts[i[k + 2]])] for k in range(0, len(i), 3)]


@pytest.fixture(scope="module")
def sample_payload():
    """tests/sample_roof.ifc through the server importer, the slab's top picked as the
    browser would pick it."""
    from ifc_import import import_ifc
    res = import_ifc(os.path.join(HERE, "sample_roof.ifc"))
    slab = next(e for e in res["elements"] if e["name"] == "Roof slab")
    top = [t for t in _tris(slab) if all(abs(q[2] - 3000.0) < 1 for q in t)]
    ctx = [{"type": e["type"], "name": e["name"], "tris": _tris(e)} for e in res["elements"] if e is not slab]
    return {"name": "Roof 1", "faces": top, "outward": [0, 0, 1], "seeds": [[1000, 1000, 3000]],
            "context": ctx, "options": {"penetrations": True}}


def _quiet(payload, **options):
    p = json.loads(json.dumps(payload))
    p["options"] = dict(p.get("options") or {}, debug=False, **options)
    return p


def test_zero_budget_returns_at_once_with_what_it_has(sample_payload):
    r = extract_roof(_quiet(sample_payload, budget_s=0))
    assert r["ok"]                                  # the roof itself is still there
    ran_out = [w for w in r["warnings"] if w.startswith("Ran out of time")]
    assert ran_out and "reading walls and doors after 0 of" in ran_out[0]
    assert all(e["type"] in ("drip", "kerb", "penetration") for e in r["edges"])   # no wall was read
    assert r["timing"]["stages"]["wall_records"] < 50


def test_budget_unset_means_the_default(sample_payload):
    r = extract_roof(_quiet(sample_payload))
    assert r["ok"] and not [w for w in r["warnings"] if w.startswith("Ran out of time")]
    assert roof_extract.DEFAULT_BUDGET_S == 20.0


def test_timing_names_every_stage(sample_payload):
    r = extract_roof(_quiet(sample_payload))
    t = r["timing"]
    assert set(t["stages"]) == set(STAGES)
    assert t["stages"]["total"] >= max(v for k, v in t["stages"].items() if k != "total")
    assert 1 <= len(t["slowest"]) <= 3
    for s in t["slowest"]:
        assert {"stage", "index", "total", "type", "name", "tris", "ms"} <= set(s)
    assert [s["ms"] for s in t["slowest"]] == sorted((s["ms"] for s in t["slowest"]), reverse=True)


def test_debug_logs_each_stage_and_element(sample_payload, capsys, monkeypatch):
    monkeypatch.setattr(roof_edges, "SLOW_MS", -1.0)      # everything is "slow"
    monkeypatch.setattr(roof_extract, "SLOW_MS", -1.0)
    p = json.loads(json.dumps(sample_payload))
    p["options"]["debug"] = True
    extract_roof(p)
    out = capsys.readouterr().out
    for stage in STAGES[:-1]:
        assert "Roof 1: %s " % stage.replace("_", " ") in out, stage
    assert "Roof 1: done in" in out
    walls = [ln for ln in out.splitlines() if ln.strip().startswith("walls ")]
    assert walls and walls[0].strip().startswith("walls 1/") and " tris " in walls[0] and walls[0].rstrip().endswith("SLOW")
    assert any(ln.strip().startswith("penetrations 1/") for ln in out.splitlines())
    extract_roof(_quiet(sample_payload))
    assert capsys.readouterr().out == ""


def _edges_slicing_twice(payload, result):
    """The edges as the old code classified them: every wall sliced again in the final
    frame, at the region's local origin."""
    f = result["frame"]
    o, u, v = f["origin"], f["u"], f["v"]
    umin = o[0] * u[0] + o[1] * u[1]
    vmin = o[0] * v[0] + o[1] * v[1]
    records = wall_records(payload["context"], f, result["datum_z"], (umin, vmin))
    return classify(result["polygons"], records)


@pytest.mark.parametrize("which", ["sample", "synthetic"])
def test_single_wall_pass_gives_identical_edges(which, sample_payload, monkeypatch):
    payload = _quiet(sample_payload if which == "sample" else roof_payload())
    calls = []
    real = roof_edges._section
    monkeypatch.setattr(roof_edges, "_section", lambda *a, **k: calls.append(1) or real(*a, **k))
    r = extract_roof(payload)
    extraction = len(calls)
    del calls[:]
    wall_records(payload["context"], r["frame"], r["datum_z"])
    assert calls and extraction == len(calls)  # the whole extraction slices the walls once
    monkeypatch.setattr(roof_edges, "_section", real)
    before = _edges_slicing_twice(payload, r)
    assert r["edges"] == before
    assert {e["type"] for e in r["edges"] if e["ring"] == 0} >= {"abutment", "parapet", "drip"}


def test_replay_runs_a_saved_payload(sample_payload, tmp_path):
    path = tmp_path / "fallwright-payload-Roof-1.json"
    path.write_text(json.dumps(sample_payload))
    out = subprocess.run([sys.executable, os.path.join(HERE, "replay.py"), str(path)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert "result: Roof 1 ok=True" in out.stdout
    for stage in STAGES:
        assert stage in out.stdout, stage
    assert "walls 1/" in out.stdout and "slowest elements:" in out.stdout
    # and the time limit can be set from the command line
    out = subprocess.run([sys.executable, os.path.join(HERE, "replay.py"), str(path), "--budget", "0"],
                         capture_output=True, text=True, timeout=120)
    assert "Ran out of time" in out.stdout
