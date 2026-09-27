"""
CladForge — IFC4X3 export via IfcOpenShell.

    meshes_to_ifc(meshes, params, infos=None) -> path to .ifc

One IfcElementAssembly per elevation, placed in the host wall's storey and
aggregating IfcMember battens, IfcPlate sheathing and IfcCovering insulation
and boards. Every prism becomes an IfcExtrudedAreaSolid whose placement axis
is the elevation normal, so the profile drawn in (u, v) extrudes outward.
A mitred end slopes with depth, so those elements are written as an explicit
brep instead. Raw entity creation is used for property sets: the WASM wheel ships
without pset template files.
"""

import math
import tempfile
import uuid

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.api.owner.settings
import ifcopenshell.guid

from cladding_constants import (_parse, TOOL_NAME, TOOL_URL, IFC_SCHEMA_LABEL, SCOPE_NOTE,
                                QUANTITY_NOTE, DISCLAIMER, frame_to_world)
from cladding_primitives import corner_ring

IFC_SCHEMA_VERSIONS = ("IFC4X3", "IFC4X3_ADD2", "IFC4")
_GUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, TOOL_URL)

# ifc_type: (IFC class, PredefinedType, ObjectType / material label)
_IFC_TYPE_MAP = {
    "batten":         ("IfcMember",   "USERDEFINED", "Batten"),
    "counter_batten": ("IfcMember",   "USERDEFINED", "Counter-batten"),
    "cross_batten":   ("IfcMember",   "USERDEFINED", "Cross batten"),
    "sheathing":      ("IfcPlate",    "SHEET",       "Sheathing board"),
    "insulation":     ("IfcCovering", "INSULATION",  "External insulation"),
    "panel":          ("IfcCovering", "CLADDING",    "Cladding panel"),
    "plank":          ("IfcCovering", "CLADDING",    "Cladding plank"),
    "closer":         ("IfcMember",   "USERDEFINED", "Cavity closer"),
    "reveal":         ("IfcCovering", "CLADDING",    "Reveal lining"),
}
_MATERIALS = {
    "batten": ("Timber (softwood) batten", "wood"), "counter_batten": ("Timber (softwood) batten", "wood"),
    "cross_batten": ("Timber (softwood) batten", "wood"), "sheathing": ("Sheathing board", "board"),
    "insulation": ("External insulation", "insulation"), "panel": ("Cladding panel", "cladding"),
    "plank": ("Cladding plank", "cladding"), "closer": ("Timber (solid) cavity closer", "wood"),
    "reveal": ("Reveal lining", "cladding"),
}


# ── GUIDs and header ─────────────────────────────────────────────────────

def _stable_guid(key):
    """Deterministic GlobalId: re-exporting the same design yields the same GUIDs."""
    return ifcopenshell.guid.compress(uuid.uuid5(_GUID_NAMESPACE, key).hex)


def _mesh_fingerprint(mesh):
    flat = ["%s|%s|%.1f|%.1f" % (mesh.get("ifc_type", ""), mesh.get("elevation", ""),
                                 mesh.get("depth", 0), mesh.get("thickness", 0))]
    for ring in [mesh.get("profile", [])] + list(mesh.get("holes") or []):
        flat.append(",".join("%.1f,%.1f" % (a, b) for a, b in ring))
    f = mesh.get("frame") or {}
    flat.append(",".join("%.2f" % c for c in list(f.get("origin", [])) + list(f.get("u", [])) + list(f.get("n", []))))
    return "|".join(flat)


def _header(ifc):
    """FILE_NAME / FILE_DESCRIPTION across ifcopenshell versions (WASM 0.8.2 vs 0.8.5+)."""
    hdr = getattr(ifc, "header", None)
    if hdr is None or not hasattr(hdr, "file_name"):
        hdr = ifc.wrapped_data.header
        if callable(hdr):
            hdr = hdr()
    return hdr


def _create_file():
    """Newest schema this build carries. Ask before trying: the browser's WASM wheel
    ships IFC2X3 and IFC4 only, and requesting a schema it does not have raises a C++
    exception that kills the Pyodide runtime outright rather than something Python can
    catch — so a try/except ladder cannot be the fallback."""
    try:
        import ifcopenshell.ifcopenshell_wrapper as _wrapper
        available = set(_wrapper.schema_names())
    except Exception:   # noqa: BLE001 — an old build without the introspection
        available = set()
    for version in IFC_SCHEMA_VERSIONS:
        if not available or version in available:
            return ifcopenshell.api.run("project.create_file", version=version)
    return ifcopenshell.api.run("project.create_file", version=IFC_SCHEMA_VERSIONS[-1])


def _schema_label(ifc):
    """What was actually written, which is not always the headline schema."""
    return getattr(ifc, "schema", None) or getattr(ifc, "schema_identifier", None) or IFC_SCHEMA_LABEL


# ── geometry ─────────────────────────────────────────────────────────────

def _polyline(ifc, ring):
    pts = [ifc.createIfcCartesianPoint((float(a), float(b))) for a, b in ring]
    pts.append(pts[0])
    return ifc.createIfcPolyline(pts)


def _prism_solid(ifc, mesh):
    """Solid for a prism. A square-ended one is an IfcExtrudedAreaSolid: the profile in
    the elevation frame, extruded along the wall normal. A mitred one is an explicit
    brep, because its end faces slope with depth; booleans against an infinite half
    space are not dependable enough to cut a joint people will build from."""
    if mesh.get("type") == "slab":
        return _slab_brep(ifc, mesh), "Brep"        # lies on the falls
    if mesh.get("lift"):
        return _lift_brep(ifc, mesh), "Brep"        # swept along a sloping edge
    corner = mesh.get("corner")
    if corner and (corner.get("k_l") or corner.get("k_r")):
        return _corner_brep(ifc, mesh), "Brep"      # end faces slope with depth
    outer = _polyline(ifc, corner_ring(mesh["profile"], corner, 0.0))
    holes = [_polyline(ifc, h) for h in (mesh.get("holes") or []) if len(h) >= 3]
    if holes:
        profile = ifc.createIfcArbitraryProfileDefWithVoids("AREA", None, outer, holes)
    else:
        profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, outer)
    frame = mesh["frame"]
    loc = frame_to_world(frame, 0.0, 0.0, float(mesh["depth"]))
    placement = ifc.createIfcAxis2Placement3D(
        ifc.createIfcCartesianPoint(tuple(float(c) for c in loc)),
        ifc.createIfcDirection(tuple(float(c) for c in frame["n"])),
        ifc.createIfcDirection(tuple(float(c) for c in frame["u"])))
    return ifc.createIfcExtrudedAreaSolid(profile, placement, ifc.createIfcDirection((0.0, 0.0, 1.0)),
                                          float(mesh["thickness"])), "SweptSolid"


def _point(ifc, cache, xyz):
    key = tuple(round(float(c), 4) for c in xyz)
    if key not in cache:
        cache[key] = ifc.createIfcCartesianPoint(key)
    return cache[key]


def _face(ifc, outer, inner=()):
    bounds = [ifc.createIfcFaceOuterBound(ifc.createIfcPolyLoop(outer), True)]
    bounds += [ifc.createIfcFaceBound(ifc.createIfcPolyLoop(ring), True) for ring in inner]
    return ifc.createIfcFace(bounds)


def _corner_brep(ifc, mesh):
    """Closed shell of a prism whose ends slope with depth. Profile rings run
    anticlockwise in (u, v) and u x v = the outward normal, so the outer ring as given
    faces outwards on the far cap and is reversed on the near one."""
    frame, corner = mesh["frame"], mesh["corner"]
    near = float(mesh["depth"])
    far = near + float(mesh["thickness"])
    cache, faces, layers = {}, [], []
    for ring in [list(mesh["profile"])] + [list(h) for h in (mesh.get("holes") or [])]:
        if len(ring) < 3:
            continue
        lo = [_point(ifc, cache, frame_to_world(frame, u, v, near)) for u, v in corner_ring(ring, corner, near)]
        hi = [_point(ifc, cache, frame_to_world(frame, u, v, far)) for u, v in corner_ring(ring, corner, far)]
        layers.append((lo, hi))
        for i in range(len(lo)):                       # side faces, one quad per edge
            j = (i + 1) % len(lo)
            if lo[i] is not lo[j] or hi[i] is not hi[j]:
                faces.append(_face(ifc, [lo[i], lo[j], hi[j], hi[i]]))
    outer_lo, outer_hi = layers[0]
    faces.append(_face(ifc, list(reversed(outer_lo)), [list(reversed(r)) for r, _h in layers[1:]]))
    faces.append(_face(ifc, outer_hi, [h for _l, h in layers[1:]]))
    return ifc.createIfcFacetedBrep(ifc.createIfcClosedShell(faces))


def _closed_shell(ifc, layers):
    """Faceted brep from [(near ring, far ring)] point lists, outer ring first: side quads
    for every edge (a quad whose two points coincide becomes a triangle, a degenerate one
    is left out), the near cap reversed, the far cap as given, holes as inner bounds."""
    faces = []
    for lo, hi in layers:
        for i in range(len(lo)):
            j = (i + 1) % len(lo)
            quad = []
            for q in (lo[i], lo[j], hi[j], hi[i]):
                if not quad or q is not quad[-1]:
                    quad.append(q)
            if len(quad) > 1 and quad[0] is quad[-1]:
                quad.pop()
            if len(quad) >= 3:
                faces.append(_face(ifc, quad))
    outer_lo, outer_hi = layers[0]
    faces.append(_face(ifc, list(reversed(outer_lo)), [list(reversed(r)) for r, _h in layers[1:]]))
    faces.append(_face(ifc, outer_hi, [h for _l, h in layers[1:]]))
    return ifc.createIfcFacetedBrep(ifc.createIfcClosedShell(faces))


def _slab_brep(ifc, mesh):
    """A slab between two planes over plan rings in a roof frame: flat or sloping top and
    bottom, vertical sides. Rings run anticlockwise outside and clockwise round holes in
    (u, v), and the roof frame's n is up, so the top cap as given faces up."""
    frame, bot, top = mesh["frame"], mesh["bot"], mesh["top"]
    z = lambda pl, u, v: pl[0] + pl[1] * u + pl[2] * v   # noqa: E731
    cache, layers = {}, []
    for ring in mesh["rings"]:
        if len(ring) < 3:
            continue
        lo = [_point(ifc, cache, frame_to_world(frame, u, v, z(bot, u, v))) for u, v in ring]
        hi = [_point(ifc, cache, frame_to_world(frame, u, v, z(top, u, v))) for u, v in ring]
        layers.append((lo, hi))
    return _closed_shell(ifc, layers)


def _lift_brep(ifc, mesh):
    """A prism whose profile rises by lift[0] at its start and lift[1] at its end: a
    section swept along a sloping edge."""
    frame = mesh["frame"]
    near = float(mesh["depth"])
    far = near + float(mesh["thickness"])
    l0, l1 = mesh["lift"]
    cache, layers = {}, []
    for ring in [list(mesh["profile"])] + [list(h) for h in (mesh.get("holes") or [])]:
        if len(ring) < 3:
            continue
        lo = [_point(ifc, cache, frame_to_world(frame, u, v + l0, near)) for u, v in ring]
        hi = [_point(ifc, cache, frame_to_world(frame, u, v + l1, far)) for u, v in ring]
        layers.append((lo, hi))
    return _closed_shell(ifc, layers)


def _make_element(ifc, body, ifc_class, name, solid, predefined=None, object_type=None):
    element = ifcopenshell.api.run("root.create_entity", ifc, ifc_class=ifc_class, name=name)
    solid, kind = solid
    rep = ifc.createIfcShapeRepresentation(body, "Body", kind, [solid])
    element.Representation = ifc.createIfcProductDefinitionShape(None, None, [rep])
    origin = ifc.createIfcCartesianPoint((0.0, 0.0, 0.0))
    element.ObjectPlacement = ifc.createIfcLocalPlacement(None, ifc.createIfcAxis2Placement3D(origin, None, None))
    for attr, value in (("PredefinedType", predefined), ("ObjectType", object_type)):
        if value:
            try:
                setattr(element, attr, value)
            except Exception:
                pass
    return element


# ── property sets ────────────────────────────────────────────────────────

def _owner(ifc):
    hist = ifc.by_type("IfcOwnerHistory")
    return hist[0] if hist else None


def _pset(ifc, products, name, props):
    """Attach a property set. props: {name: (IfcTypeName, value)}; None values skipped."""
    values = []
    for key, (typ, val) in props.items():
        if val is None:
            continue
        try:
            values.append(ifc.createIfcPropertySingleValue(key, None, ifc.create_entity(typ, val), None))
        except Exception:
            continue
    if not values:
        return
    pset = ifc.createIfcPropertySet(ifcopenshell.guid.new(), _owner(ifc), name, None, values)
    ifc.createIfcRelDefinesByProperties(ifcopenshell.guid.new(), _owner(ifc), None, None, products, pset)


def _setting_out_props(p, info, elev, schema=IFC_SCHEMA_LABEL):
    return {
        "SchemaLabel": ("IfcLabel", schema),
        "CladdingType": ("IfcLabel", p["cladding_type"]),
        "BoardOrientation": ("IfcLabel", p["boards_run"]),
        "BattenOrientation": ("IfcLabel", p["battens"]),
        "BattenSection": ("IfcLabel", "%.0f x %.0f" % (p["batten_w"], p["batten_d"])),
        "BattenCentres": ("IfcPositiveLengthMeasure", float(p["batten_centres"])),
        "CounterBattens": ("IfcBoolean", bool(p["has_cb"])),
        "CounterBattenSection": ("IfcLabel", "%.0f x %.0f" % (p["cb_w"], p["cb_d"])) if p["has_cb"] else ("IfcLabel", None),
        "CounterBattenCentres": ("IfcPositiveLengthMeasure", float(p["cb_centres"]) if p["has_cb"] else None),
        "SheathingThickness": ("IfcPositiveLengthMeasure", float(p["sheathing_t"]) if p["sheathing"] else None),
        "InsulationThickness": ("IfcPositiveLengthMeasure", float(p["insulation_t"]) if p["insulation"] else None),
        "SplashZone": ("IfcLengthMeasure", float(p["splash"])),
        "BaseLevel": ("IfcLengthMeasure", float(info.get("base_level", 0))),
        "CoverWidth": ("IfcPositiveLengthMeasure", float(info.get("cover", 0)) or None),
        "HorizontalOffset": ("IfcLengthMeasure", float(elev.get("offset", 0))),
        "Courses": ("IfcInteger", int(info.get("n_courses", 0))),
        "ClosingCutLeft": ("IfcLengthMeasure", float(info.get("closing_cut_left", 0))),
        "ClosingCutRight": ("IfcLengthMeasure", float(info.get("closing_cut_right", 0))),
        "ClosingCutTop": ("IfcLengthMeasure", float(info.get("closing_cut_top", 0))),
        "ElevationWidth": ("IfcPositiveLengthMeasure", float(elev.get("width", 0)) or None),
        "ElevationHeight": ("IfcPositiveLengthMeasure", float(elev.get("height", 0)) or None),
        "ScopeNote": ("IfcText", SCOPE_NOTE),
        "QuantityNote": ("IfcText", QUANTITY_NOTE),
    }


# ── spatial hierarchy ────────────────────────────────────────────────────

def _spatial(ifc, context, elevations):
    """Project → Site → Building → Storeys, named after the host model."""
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject",
                                   name=context.get("project") or "%s Cladding" % TOOL_NAME)
    try:
        project.LongName = "%s cladding setting-out (%s)" % (TOOL_NAME, _schema_label(ifc))
        project.Phase = "Preliminary design"
    except Exception:
        pass
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "MILLIMETERS"})
    ctx = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body = ifcopenshell.api.run("context.add_context", ifc, context_type="Model",
                                context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite",
                                name=context.get("site") or "Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding",
                                    name=context.get("building") or "Building")
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=project, products=[site])
    ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=site, products=[building])
    storeys = {}
    for elev in elevations:
        st = elev.get("storey") or {}
        key = st.get("name") or "Ground Floor"
        if key not in storeys:
            storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name=key)
            try:
                storey.Elevation = float(st.get("elevation", 0.0) or 0.0)
            except Exception:
                pass
            ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=building, products=[storey])
            storeys[key] = storey
    return project, body, site, building, storeys


def _unshift(meshes, offset):
    """Put the elements back on the host model. The viewer works near the origin so
    float32 keeps its millimetres, and reports the shift it applied as context.offset."""
    if not offset or not any(offset):
        return meshes
    frames, out = {}, []
    for mesh in meshes:
        frame = mesh.get("frame")
        key = id(frame)
        if key not in frames:
            frames[key] = dict(frame, origin=[frame["origin"][i] + float(offset[i]) for i in range(3)])
        out.append(dict(mesh, frame=frames[key]))
    return out


def meshes_to_ifc(meshes, params, infos=None):
    """Convert prism meshes into an IFC4X3 file. Returns the temp file path."""
    p = _parse(params)
    if infos is None:
        from cladding_preview import _build_all
        infos = _build_all(p)[2]
    info_by_name = {i["elevation"]: i for i in infos}
    context = p.get("context") or {}
    meshes = _unshift(meshes, context.get("offset"))
    ifc = _create_file()

    person = ifcopenshell.api.run("owner.add_person", ifc, family_name="User")
    org = ifcopenshell.api.run("owner.add_organisation", ifc, identification="JWA",
                               name="Jake White Architecture")
    ifcopenshell.api.run("owner.add_person_and_organisation", ifc, person=person, organisation=org)
    ifcopenshell.api.run("owner.add_application", ifc, application_developer=org, version="1.0",
                         application_full_name=TOOL_NAME, application_identifier=TOOL_NAME.lower())
    ifcopenshell.api.owner.settings.get_user = lambda f: f.by_type("IfcPersonAndOrganization")[0]
    ifcopenshell.api.owner.settings.get_application = lambda f: f.by_type("IfcApplication")[0]

    elevations = [e for e in p["elevations"] if e.get("ok", True) and e.get("polygons")]
    project, body, site, building, storeys = _spatial(ifc, context, elevations)

    by_elev = {}
    for m in meshes:
        by_elev.setdefault(m.get("elevation", ""), []).append(m)

    elements, fingerprints, assemblies, by_material = [], {}, [], {}
    for elev in elevations:
        name = elev.get("name", "Elevation")
        storey = storeys[(elev.get("storey") or {}).get("name") or "Ground Floor"]
        assembly = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcElementAssembly",
                                        name="%s cladding" % name)
        for attr, value in (("PredefinedType", "USERDEFINED"), ("ObjectType", "Cladding system"),
                            ("Description", "%s %s buildup on %s" % (TOOL_NAME, p["cladding_type"], name))):
            try:
                setattr(assembly, attr, value)
            except Exception:
                pass
        ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[assembly])
        parts = []
        for mesh in by_elev.get(name, []):
            ifc_class, predefined, label = _IFC_TYPE_MAP.get(mesh.get("ifc_type"), ("IfcBuildingElementProxy", None, None))
            try:
                elem = _make_element(ifc, body, ifc_class, mesh.get("name") or label, _prism_solid(ifc, mesh),
                                     predefined, label)
            except Exception:
                continue
            parts.append(elem)
            fingerprints[elem.id()] = "%s|%s|%s" % (ifc_class, mesh.get("name", ""), _mesh_fingerprint(mesh))
            by_material.setdefault(mesh.get("ifc_type"), []).append(elem)
            common = {"Reference": ("IfcIdentifier", label), "IsExternal": ("IfcBoolean", True)}
            if ifc_class == "IfcCovering":
                _pset(ifc, [elem], "Pset_CoveringCommon", common)
            elif ifc_class == "IfcMember":
                _pset(ifc, [elem], "Pset_MemberCommon", common)
            elif ifc_class == "IfcPlate":
                _pset(ifc, [elem], "Pset_PlateCommon", common)
        if parts:
            ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=assembly, products=parts)
        _pset(ifc, [assembly], "%s_SettingOut" % TOOL_NAME,
              _setting_out_props(p, info_by_name.get(name, {}), elev, _schema_label(ifc)))
        elements += parts
        assemblies.append((name, assembly))

    for ifc_type, elems in by_material.items():
        mat_name, category = _MATERIALS.get(ifc_type, (ifc_type, None))
        try:
            material = ifc.create_entity("IfcMaterial", Name=mat_name, Category=category)
            ifc.create_entity("IfcRelAssociatesMaterial", GlobalId=ifcopenshell.guid.new(), OwnerHistory=_owner(ifc),
                              RelatedObjects=elems, RelatingMaterial=material)
        except Exception:
            pass

    _pset(ifc, [project], "%s_Disclaimer" % TOOL_NAME, {
        "Notice": ("IfcText", DISCLAIMER), "ScopeNote": ("IfcText", SCOPE_NOTE),
        "QuantityNote": ("IfcText", QUANTITY_NOTE), "Schema": ("IfcLabel", _schema_label(ifc)),
        "ToolURL": ("IfcText", TOOL_URL)})
    try:
        hdr = _header(ifc)
        hdr.file_name.authorization = "User must verify all outputs before use."
        hdr.file_description.description = ("ViewDefinition [ReferenceView]",
                                            "%s %s cladding setting-out. %s" % (TOOL_NAME, _schema_label(ifc), SCOPE_NOTE))
    except Exception:
        pass

    # Stable GlobalIds: elements from their own geometry, containers from the design.
    design_fp = _stable_guid("design|" + "|".join(sorted(fingerprints.values())))
    assigned = set()
    for elem in elements:
        elem.GlobalId = _stable_guid("element|" + fingerprints[elem.id()])
        assigned.add(elem.id())
    spatial = [("project", project), ("site", site), ("building", building)]
    spatial += [("storey|" + k, v) for k, v in storeys.items()]
    spatial += [("assembly|" + n, a) for n, a in assemblies]
    for label, ent in spatial:
        ent.GlobalId = _stable_guid("spatial|%s|%s" % (design_fp, label))
        assigned.add(ent.id())
    counters = {}
    for ent in ifc.by_type("IfcRoot"):
        if ent.id() in assigned:
            continue
        cls = ent.is_a()
        counters[cls] = counters.get(cls, 0) + 1
        ent.GlobalId = _stable_guid("aux|%s|%s|%d" % (design_fp, cls, counters[cls]))

    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    tmp.close()
    ifc.write(tmp.name)
    return tmp.name


# ── Fallwright: the roof ─────────────────────────────────────────────────

# ifc_type: (IFC class, PredefinedType, ObjectType / material label)
_ROOF_TYPE_MAP = {
    "firring":          ("IfcBuildingElementProxy", "USERDEFINED", "Firring zone"),
    "deck":             ("IfcPlate",    "SHEET",       "Deck"),
    "vcl":              ("IfcCovering", "MEMBRANE",    "Vapour control layer"),
    "insulation":       ("IfcCovering", "INSULATION",  "Roof insulation"),
    "membrane":         ("IfcCovering", "ROOFING",     "Roof membrane"),
    "sump_firring":     ("IfcBuildingElementProxy", "USERDEFINED", "Firring zone"),
    "sump_deck":        ("IfcPlate",    "SHEET",       "Sump deck"),
    "sump_vcl":         ("IfcCovering", "MEMBRANE",    "Sump vapour control layer"),
    "sump_insulation":  ("IfcCovering", "INSULATION",  "Sump insulation"),
    "sump_membrane":    ("IfcCovering", "ROOFING",     "Sump membrane"),
    "upstand":          ("IfcCovering", "ROOFING",     "Membrane upstand"),
    "ins_upstand":      ("IfcCovering", "INSULATION",  "Insulation upstand"),
    "vcl_upstand":      ("IfcCovering", "MEMBRANE",    "VCL turn-up"),
    "counter_flashing": ("IfcBuildingElementProxy", "USERDEFINED", "Counter-flashing"),
    "drip_trim":        ("IfcBuildingElementProxy", "USERDEFINED", "Drip trim"),
    "kerb":             ("IfcMember",   "USERDEFINED", "Kerb"),
    "outlet":           ("IfcWasteTerminal", "ROOFDRAIN", "Roof outlet"),
    "penetration_cut":  ("IfcBuildingElementProxy", "USERDEFINED", "Through-wall penetration (cut)"),
    "sleeve":           ("IfcBuildingElementProxy", "USERDEFINED", "Outlet sleeve"),
    "hopper":           ("IfcBuildingElementProxy", "USERDEFINED", "Hopper head"),
}
_ROOF_MATERIALS = {
    "firring": ("Timber (softwood) firrings", "wood"), "sump_firring": ("Timber (softwood) firrings", "wood"),
    "deck": ("WBP plywood deck", "wood"), "sump_deck": ("WBP plywood deck", "wood"),
    "vcl": ("Vapour control layer", "membrane"), "sump_vcl": ("Vapour control layer", "membrane"),
    "vcl_upstand": ("Vapour control layer", "membrane"), "insulation": ("Roof insulation", "insulation"),
    "sump_insulation": ("Roof insulation", "insulation"), "ins_upstand": ("Roof insulation", "insulation"),
    "kerb": ("Timber (softwood) kerb", "wood"), "counter_flashing": ("Metal flashing", "metal"),
    "drip_trim": ("Edge trim", "metal"),
}


def _roof_fingerprint(mesh):
    """What the element is and where: its type, name, shape and frame, so an unchanged
    element keeps its GlobalId across exports."""
    flat = ["%s|%s" % (mesh.get("ifc_type", ""), mesh.get("roof", ""))]
    if mesh.get("type") == "slab":
        for ring in mesh.get("rings") or []:
            flat.append(",".join("%.1f,%.1f" % (a, b) for a, b in ring))
        flat.append(",".join("%.4f" % c for c in list(mesh["bot"]) + list(mesh["top"])))
    else:
        flat.append(_mesh_fingerprint(mesh))
        flat.append(",".join("%.1f" % c for c in mesh.get("lift") or []))
    f = mesh.get("frame") or {}
    flat.append(",".join("%.2f" % c for c in list(f.get("origin", [])) + list(f.get("u", [])) + list(f.get("v") or [])))
    return "|".join(flat)


def _roof_props(p, roof, falls, schema):
    from roof_constants import fall_label
    sumps = [s for s in falls["sumps"] if s["kind"] == "sump"]
    datum = float(roof.get("datum_z") or 0.0)
    rims = "; ".join("sump %d rim firrings %.0f, rim %.0f, floor %.0f, drop %.0f" % (
        (s.get("outlet") or 0) + 1, s["r"], datum + s["r"] + p["above_firrings"],
        datum + s["sf"] + p["sump_buildup"], s["drop"]) for s in sumps)
    return {
        "SchemaLabel": ("IfcLabel", schema),
        "MainFall": ("IfcLabel", fall_label(p["fall"])),
        "CricketFall": ("IfcLabel", fall_label(p["cricket_fall"])),
        "SumpFall": ("IfcLabel", fall_label(p["sump_fall"])),
        "MinimumFirringDepth": ("IfcLengthMeasure", float(falls.get("min_depth") or 0.0)),
        "MaximumFirringDepth": ("IfcLengthMeasure", float(falls.get("max_depth") or 0.0)),
        "SumpLevels": ("IfcText", rims or None),
        "DeckThickness": ("IfcPositiveLengthMeasure", float(p["deck_t"])),
        "VCLThickness": ("IfcPositiveLengthMeasure", float(p["vcl_t"])),
        "InsulationThickness": ("IfcPositiveLengthMeasure", float(p["insulation_t"])),
        "MembraneThickness": ("IfcPositiveLengthMeasure", float(p["membrane_t"])),
        "MembraneName": ("IfcLabel", p["membrane_name"]),
        "UpstandHeight": ("IfcPositiveLengthMeasure", float(p["upstand"])),
        "StructureLevel": ("IfcLengthMeasure", datum),
        "Facets": ("IfcInteger", len(falls["facets"])),
        "Outlets": ("IfcInteger", len([o for o in falls["outlets"] if "point" in o])),
        "ScopeNote": ("IfcText", SCOPE_NOTE_ROOF()),
    }


def SCOPE_NOTE_ROOF():   # noqa: N802 — a constant, read late so this module keeps its imports
    from roof_constants import SCOPE_NOTE as note
    return note


def roof_to_ifc(meshes, params, built):
    """Fallwright's export: one IfcElementAssembly per roof ("Warm roof system") in the
    storey of the picked slab. Anything on the falls is an IfcFacetedBrep, everything else
    an IfcExtrudedAreaSolid. *built* is [(roof, falls, info)] from roof_preview.build_all.
    Returns the temp file path."""
    from roof_constants import (_parse as parse_roof, TOOL_NAME as NAME, TOOL_URL as URL, SCOPE_NOTE as SCOPE,
                                QUANTITY_NOTE as QTY, DISCLAIMER as DISC)
    p = parse_roof(params)
    context = p.get("context") or {}
    meshes = _unshift(meshes, context.get("offset"))
    ifc = _create_file()
    person = ifcopenshell.api.run("owner.add_person", ifc, family_name="User")
    org = ifcopenshell.api.run("owner.add_organisation", ifc, identification="JWA", name="Jake White Architecture")
    ifcopenshell.api.run("owner.add_person_and_organisation", ifc, person=person, organisation=org)
    ifcopenshell.api.run("owner.add_application", ifc, application_developer=org, version="1.0",
                         application_full_name=NAME, application_identifier=NAME.lower())
    ifcopenshell.api.owner.settings.get_user = lambda f: f.by_type("IfcPersonAndOrganization")[0]
    ifcopenshell.api.owner.settings.get_application = lambda f: f.by_type("IfcApplication")[0]
    roofs = [roof for roof, _f, _i in built]
    project, body, site, building, storeys = _spatial(ifc, dict(context, project=context.get("project") or
                                                               "%s Roof" % NAME), roofs)
    try:
        project.LongName = "%s warm roof falls and buildup (%s)" % (NAME, _schema_label(ifc))
    except Exception:   # noqa: BLE001
        pass
    by_roof = {}
    for m in meshes:
        by_roof.setdefault(m.get("roof", ""), []).append(m)
    elements, fingerprints, assemblies, by_material = [], {}, [], {}
    for roof, falls, _info in built:
        name = roof.get("name") or "Roof"
        storey = storeys[(roof.get("storey") or {}).get("name") or "Ground Floor"]
        assembly = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcElementAssembly",
                                        name="%s warm roof" % name)
        for attr, value in (("PredefinedType", "USERDEFINED"), ("ObjectType", "Warm roof system"),
                            ("Description", "%s warm flat roof, falls in firrings, on %s" % (NAME, name))):
            try:
                setattr(assembly, attr, value)
            except Exception:   # noqa: BLE001
                pass
        ifcopenshell.api.run("spatial.assign_container", ifc, relating_structure=storey, products=[assembly])
        parts = []
        for mesh in by_roof.get(name, []):
            ifc_class, predefined, label = _ROOF_TYPE_MAP.get(mesh.get("ifc_type"), ("IfcBuildingElementProxy", None, None))
            try:
                elem = _make_element(ifc, body, ifc_class, mesh.get("name") or label, _prism_solid(ifc, mesh),
                                     predefined, label)
            except Exception:   # noqa: BLE001 — e.g. a class this schema lacks
                try:
                    elem = _make_element(ifc, body, "IfcBuildingElementProxy", mesh.get("name") or label,
                                         _prism_solid(ifc, mesh), "USERDEFINED", label)
                except Exception:   # noqa: BLE001
                    continue
            parts.append(elem)
            fingerprints[elem.id()] = "%s|%s|%s" % (ifc_class, mesh.get("name", ""), _roof_fingerprint(mesh))
            by_material.setdefault(mesh.get("ifc_type"), []).append(elem)
            common = {"Reference": ("IfcIdentifier", label), "IsExternal": ("IfcBoolean", True)}
            pset = {"IfcCovering": "Pset_CoveringCommon", "IfcMember": "Pset_MemberCommon",
                    "IfcPlate": "Pset_PlateCommon"}.get(elem.is_a())
            if pset:
                _pset(ifc, [elem], pset, common)
            own = {"Layer": ("IfcLabel", label), "Facet": ("IfcLabel", mesh.get("facet")),
                   "MembraneName": ("IfcLabel", p["membrane_name"] if "membrane" in (mesh.get("ifc_type") or "")
                                    or mesh.get("ifc_type") == "upstand" else None)}
            _pset(ifc, [elem], "Pset_Fallwright", own)
        if parts:
            ifcopenshell.api.run("aggregate.assign_object", ifc, relating_object=assembly, products=parts)
        _pset(ifc, [assembly], "Pset_Fallwright", _roof_props(p, roof, falls, _schema_label(ifc)))
        elements += parts
        assemblies.append((name, assembly))
    for ifc_type, elems in by_material.items():
        mat_name, category = _ROOF_MATERIALS.get(ifc_type, (_ROOF_TYPE_MAP.get(ifc_type, (None, None, ifc_type))[2], None))
        try:
            material = ifc.create_entity("IfcMaterial", Name=mat_name, Category=category)
            ifc.create_entity("IfcRelAssociatesMaterial", GlobalId=ifcopenshell.guid.new(), OwnerHistory=_owner(ifc),
                              RelatedObjects=elems, RelatingMaterial=material)
        except Exception:   # noqa: BLE001
            pass
    _pset(ifc, [project], "%s_Disclaimer" % NAME, {
        "Notice": ("IfcText", DISC), "ScopeNote": ("IfcText", SCOPE), "QuantityNote": ("IfcText", QTY),
        "Schema": ("IfcLabel", _schema_label(ifc)), "ToolURL": ("IfcText", URL)})
    try:
        hdr = _header(ifc)
        hdr.file_name.authorization = "User must verify all outputs before use."
        hdr.file_description.description = ("ViewDefinition [ReferenceView]",
                                            "%s %s warm roof falls and buildup. %s" % (NAME, _schema_label(ifc), SCOPE))
    except Exception:   # noqa: BLE001
        pass
    design_fp = _stable_guid("design|" + "|".join(sorted(fingerprints.values())))
    assigned = set()
    for elem in elements:
        elem.GlobalId = _stable_guid("element|" + fingerprints[elem.id()])
        assigned.add(elem.id())
    spatial = [("project", project), ("site", site), ("building", building)]
    spatial += [("storey|" + k, v) for k, v in storeys.items()]
    spatial += [("assembly|" + n, a) for n, a in assemblies]
    for label, ent in spatial:
        ent.GlobalId = _stable_guid("spatial|%s|%s" % (design_fp, label))
        assigned.add(ent.id())
    counters = {}
    for ent in ifc.by_type("IfcRoot"):
        if ent.id() in assigned:
            continue
        cls = ent.is_a()
        counters[cls] = counters.get(cls, 0) + 1
        ent.GlobalId = _stable_guid("aux|%s|%s|%d" % (design_fp, cls, counters[cls]))
    tmp = tempfile.NamedTemporaryFile(suffix=".ifc", delete=False)
    tmp.close()
    ifc.write(tmp.name)
    return tmp.name
