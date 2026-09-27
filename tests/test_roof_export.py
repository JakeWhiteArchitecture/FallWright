"""IFC4X3 and DXF exports of a roof."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pytest  # noqa: E402
import ifcopenshell  # noqa: E402
import ifcopenshell.geom  # noqa: E402

from synthetic import roof_payload  # noqa: E402
from roof_extract import extract_roof  # noqa: E402
from roof_constants import _parse  # noqa: E402
from roof_preview import build_all, generate  # noqa: E402
from ifc_generator import roof_to_ifc  # noqa: E402
from roof_dxf import roof_to_dxf_string, LAYERS  # noqa: E402


@pytest.fixture(scope="module")
def design():
    r = extract_roof(roof_payload())
    r["storey"] = {"name": "First Floor", "elevation": 3000.0}
    r["outlets"] = [{"edge": "E2", "corner": "a", "offset": 1500.0, "type": "hopper"},
                    {"edge": "E1", "corner": "a", "offset": 3000.0, "type": "internal", "sump": True}]
    r["edge_types"] = {"E4": "gutter"}
    params = {"roofs": [r], "membrane_name": "Test single-ply", "context": {"project": "Job 42"}}
    meshes, built, checks = build_all(_parse(params))
    return params, meshes, built, checks


def test_ifc_export(design):
    params, meshes, built, _checks = design
    path = roof_to_ifc(meshes, params, built)
    ifc = ifcopenshell.open(path)
    assert ifc.schema.startswith("IFC4X3")
    assert ifc.by_type("IfcProject")[0].Name == "Job 42"
    assemblies = ifc.by_type("IfcElementAssembly")
    assert len(assemblies) == 1 and assemblies[0].ObjectType == "Warm roof system"
    assert assemblies[0].PredefinedType == "USERDEFINED"
    # placed in the picked slab's storey
    rel = next(r for r in ifc.by_type("IfcRelContainedInSpatialStructure") if assemblies[0] in r.RelatedElements)
    assert rel.RelatingStructure.Name == "First Floor"
    classes = {e.is_a() for e in ifc.by_type("IfcElement")}
    assert {"IfcCovering", "IfcPlate", "IfcBuildingElementProxy", "IfcMember", "IfcWasteTerminal"} <= classes
    coverings = {c.PredefinedType for c in ifc.by_type("IfcCovering")}
    assert {"ROOFING", "INSULATION", "MEMBRANE"} <= coverings
    # a brep per facet: the firring zone, one proxy per facet, every one a faceted brep
    zones = [e for e in ifc.by_type("IfcBuildingElementProxy") if e.ObjectType == "Firring zone"
             and "Sump" not in (e.Name or "")]
    assert len(zones) == len(built[0][1]["facets"])
    for z in zones:
        rep = z.Representation.Representations[0]
        assert rep.RepresentationType == "Brep" and rep.Items[0].is_a("IfcFacetedBrep")
    psets = {ps.Name for ps in ifc.by_type("IfcPropertySet")}
    assert {"Pset_Fallwright", "Pset_CoveringCommon", "Fallwright_Disclaimer"} <= psets
    roof_pset = next(ps for ps in ifc.by_type("IfcPropertySet") if ps.Name == "Pset_Fallwright"
                     and any(p.Name == "MainFall" for p in ps.HasProperties))
    props = {p.Name: p.NominalValue.wrappedValue for p in roof_pset.HasProperties}
    assert props["MainFall"] == "1:40" and props["MembraneName"] == "Test single-ply"
    assert "rim firrings" in props["SumpLevels"]
    # every element builds, and the firring breps are closed solids of the right volume
    settings = ifcopenshell.geom.settings()
    for e in ifc.by_type("IfcElement"):
        if e.Representation is None:
            continue
        shape = ifcopenshell.geom.create_shape(settings, e)
        assert len(shape.geometry.verts) >= 9, e.Name
    guids = [e.GlobalId for e in ifc.by_type("IfcRoot")]
    assert len(guids) == len(set(guids))
    again = ifcopenshell.open(roof_to_ifc(meshes, params, built))
    assert sorted(e.GlobalId for e in again.by_type("IfcElement")) == sorted(e.GlobalId for e in ifc.by_type("IfcElement"))
    os.unlink(path)


def test_firring_volume_matches_the_falls(design):
    """A firring brep's volume is the facet's mean depth times its area, so the brep is
    closed and the right way out."""
    params, meshes, built, _c = design
    from shapely.geometry import Polygon
    import ifcopenshell.util.shape as ushape
    path = roof_to_ifc(meshes, params, built)
    ifc = ifcopenshell.open(path)
    settings = ifcopenshell.geom.settings()
    zones = {e.Name: e for e in ifc.by_type("IfcBuildingElementProxy") if e.ObjectType == "Firring zone"}
    for m in meshes:
        if m["ifc_type"] != "firring":
            continue
        shape = ifcopenshell.geom.create_shape(settings, zones[m["name"]])
        vol = ushape.get_volume(shape.geometry) * 1e9      # IfcOpenShell works in metres
        poly = Polygon(m["rings"][0], m["rings"][1:])
        # the mean of a plane over a polygon is its value at the centroid
        c = poly.centroid
        a, b, cc = m["top"]
        want = (a + b * c.x + cc * c.y) * poly.area
        assert vol == pytest.approx(want, rel=1e-3), m["name"]
    os.unlink(path)


def test_dxf_export(design):
    params, _m, built, checks = design
    dxf = roof_to_dxf_string(params, built, checks)
    assert dxf.startswith("  0\r\nSECTION") and dxf.rstrip().endswith("EOF")
    for layer in ("ROOF_OUTLINE", "FACETS", "DRAINS", "OUTLETS", "FALL_ARROWS", "FIRRING_DEPTHS", "SUMPS",
                  "LEVELS", "SECTION_LINES", "FIRRINGS", "DECK", "VCL", "INSULATION", "MEMBRANE",
                  "DETAILS", "DIMS", "NOTES"):
        assert layer in LAYERS
        assert "  8\r\n%s\r\n" % layer in dxf, layer          # used, not just listed
    assert "SECTION A-A" in dxf and "SECTION B-B" in dxf
    for kind in ("ABUTMENT", "PARAPET", "FREE EDGE: DRIP", "FREE EDGE: GUTTER", "KERB"):
        assert "DETAIL: %s" % kind in dxf, kind
    assert "HOPPER DETAIL" in dxf and "OUTLET SCHEDULE" in dxf
    assert "Test single-ply" in dxf
    assert "Parts L and C" in dxf and "not a quantity take-off" in dxf


def test_cut_planes_follow_the_roof(design):
    params, _m, built, checks = design
    roof = dict(params["roofs"][0], cut_planes=[{"label": "C", "dir": "v", "pos": 2000.0, "flip": True}])
    p2 = dict(params, roofs=[roof])
    dxf = roof_to_dxf_string(p2)
    assert "SECTION C-C" in dxf and "SECTION A-A" not in dxf


def test_generate_is_json_ready(design):
    import json
    params, _m, _b, _c = design
    out = generate(params)
    text = json.dumps(out)
    assert out["roofs"][0]["falls"]["facets"] and out["checks"] and len(text) < 2_000_000
