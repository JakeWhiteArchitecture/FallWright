"""Build tests/sample_roof.ifc: a flat roof for Fallwright. A two-storey block 8000 x 6000 on
plan whose lower roof, 10000 x 6000, runs out east past it; the structure (the slab top,
standing in for the top of the joists) is at +3000. Along the north side the upper storey's
wall rises past the roof and has a door onto it (an abutment); the east side has a 1000 mm
parapet; south and west are free edges. A rooflight opening is voided through the slab, and
two roof outlets sit on the roof where the smoke test places them. It needs no joists.
Run: python tests/make_sample.py"""

import os

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_roof.ifc")
ROOF_Z = 3000.0


def box(ifc, body, x0, y0, z0, x1, y1, z1):
    pts = [ifc.createIfcCartesianPoint((float(a), float(b))) for a, b in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    pts.append(pts[0])
    profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, ifc.createIfcPolyline(pts))
    place = ifc.createIfcAxis2Placement3D(ifc.createIfcCartesianPoint((0.0, 0.0, float(z0))), None, None)
    solid = ifc.createIfcExtrudedAreaSolid(profile, place, ifc.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))
    return ifc.createIfcProductDefinitionShape(None, None, [ifc.createIfcShapeRepresentation(body, "Body", "SweptSolid", [solid])])


def element(ifc, body, storey, cls, name, bounds, predefined=None):
    e = ifcopenshell.api.run("root.create_entity", ifc, ifc_class=cls, name=name)
    e.Representation = box(ifc, body, *bounds)
    e.ObjectPlacement = ifc.createIfcLocalPlacement(None, ifc.createIfcAxis2Placement3D(
        ifc.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None))
    if predefined:
        e.PredefinedType = predefined
    if storey is not None:
        ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[e])
    return e


def main():
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="Sample Roof")
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model", context_identifier="Body",
                                target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Plot 7")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Terrace")
    ground = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    first = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="First Floor")
    ground.Elevation, first.Elevation = 0.0, ROOF_Z
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[ground, first])

    z = ROOF_Z
    # Ground floor walls under the roof, stopping at the underside of the slab.
    element(ifc, body, ground, "IfcWall", "South wall", (0, 0, 0, 10000, 300, z - 250))
    element(ifc, body, ground, "IfcWall", "West wall", (0, 300, 0, 300, 5700, z - 250))
    # The roof: slab top at +3000, 10000 x 6000 on plan.
    slab = element(ifc, body, ground, "IfcSlab", "Roof slab", (0, 0, z - 250, 10000, 6000, z), "ROOF")
    opening = element(ifc, body, None, "IfcOpeningElement", "Rooflight opening", (4000, 2500, z - 300, 5200, 3700, z + 50))
    ifc.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, slab, opening)
    # North: the upper storey's wall stands on the roof's edge and rises 3000 past it,
    # with a door out onto the roof.
    north = element(ifc, body, first, "IfcWall", "Upper north wall", (0, 6000, z - 250, 10000, 6300, z + 3000))
    door_open = element(ifc, body, None, "IfcOpeningElement", "Door opening", (2000, 5950, z + 360, 2900, 6350, z + 2460))
    ifc.createIfcRelVoidsElement(ifcopenshell.guid.new(), None, None, None, north, door_open)
    element(ifc, body, first, "IfcDoor", "Roof door", (2000, 6000, z + 360, 2900, 6300, z + 2460))
    # East: a 1000 mm parapet.
    element(ifc, body, first, "IfcWall", "East parapet", (10000, 0, 0, 10300, 6300, z + 1000))
    # A rooflight on a kerb over the opening, and two roof outlets on the roof.
    element(ifc, body, first, "IfcWindow", "Rooflight", (3950, 2450, z + 250, 5250, 3750, z + 350))
    element(ifc, body, first, "IfcWasteTerminal", "Roof outlet 1", (9800, 1450, z, 9950, 1600, z + 60), "ROOFDRAIN")
    element(ifc, body, first, "IfcWasteTerminal", "Roof outlet 2", (5925, 50, z, 6075, 200, z + 60), "ROOFDRAIN")
    ifc.write(OUT)
    print("wrote", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
