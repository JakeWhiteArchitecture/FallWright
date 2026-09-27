"""Fallwright — shared constants, parameter parsing and the mesh helpers.

Every generated element is one of two shapes, both in a frame {"origin", "u", "v", "n"}:

  prism  a (u, v) profile extruded along n from *depth* by *thickness*. An optional
         "lift" [l0, l1] raises the profile by l0 at the start of the extrusion and l1 at
         the end, so a trim swept along a sloping edge still follows the roof.
  slab   plan rings in a roof frame between two planes z = a + b·u + c·v ("bot" and "top").
         Anything that lies on the falls (firrings, deck, layers) is a slab: flat or
         sloping top and bottom, vertical sides, so neighbouring facets meet on the
         vertical plane through their hip or valley.
"""

TOOL_NAME = "Fallwright"
TOOL_URL = "https://jakewhitearchitecture.com/fallwright/"
IFC_SCHEMA_LABEL = "IFC4X3"
SCOPE_NOTE = ("Fallwright does not calculate U-values or condensation risk (Parts L and C), does "
              "not check roof covering fire performance (Part B), and does not design the deck, "
              "loads or wind uplift fixing. The falls scheme is design intent; the firrings are "
              "cut on site to suit the joists from the depths given.")
QUANTITY_NOTE = ("Any schedule the tool produces is setting-out information, not a quantity "
                 "take-off for pricing.")
DISCLAIMER = "Fallwright — Preliminary design aid only. User must verify all outputs before use."

# Rules the checks and the geometry share (mm unless noted).
MIN_UPSTAND = 150.0        # membrane upstand above the finished surface
MIN_DROP = 75.0            # sump floor's highest point to the rim
MIN_SUMP_INS = 50.0        # insulation under a sump
MIN_FIRRING = 25.0         # main roof firrings never thinner
MAX_FIRRING = 150.0        # past this the firrings need restraint and the edge gets tall
STD_SUMP_L, STD_SUMP_W = 500.0, 300.0   # a standard sump is level; larger goes to falls
DESIGN_FALL = 40.0         # BS 6229: design to 1:40 ...
MIN_FALL = 80.0            # ... so the finished roof still makes 1:80
MIN_FACET_AREA = 100000.0  # mm² (0.1 m²): smaller facets merge into a neighbour
THRESHOLD_PASS, THRESHOLD_WARN = 150.0, 75.0   # door sill above the finished surface
MIN_OPENING = 300.0        # holes this big both ways are openings with kerbs
PARAPET_RISE = 1500.0      # a wall topping out lower than this above the structure is a parapet
WALL_PROBE = 300.0         # walls are sectioned this far above the structure
WALL_REACH = 50.0          # and must lie within this of the edge
CONTOUR_STEP = 25.0        # firring depth contours
TRACE_STEP = 50.0          # trapped water trace

EDGE_TYPES = ("abutment", "parapet", "drip", "gutter", "check_kerb", "kerb", "penetration")
EDGE_LABELS = {"abutment": "Abutment", "parapet": "Parapet", "drip": "Free edge: drip",
               "gutter": "Free edge: gutter", "check_kerb": "Check kerb", "kerb": "Kerb",
               "penetration": "Penetration"}

COLORS = {  # ifc_type: (hex, opacity)
    "firring": ("#c8a87c", 1.0), "deck": ("#d9b77a", 1.0), "vcl": ("#6d6d8a", 0.9),
    "insulation": ("#e8d86a", 0.55), "membrane": ("#3d4450", 0.9),
    "sump_firring": ("#b8946a", 1.0), "sump_deck": ("#d9b77a", 1.0), "sump_vcl": ("#6d6d8a", 0.9),
    "sump_insulation": ("#e0c95a", 0.7), "sump_membrane": ("#262b33", 0.95),
    "upstand": ("#3d4450", 0.9), "ins_upstand": ("#e8d86a", 0.55), "vcl_upstand": ("#6d6d8a", 0.9),
    "counter_flashing": ("#9aa7b4", 1.0), "drip_trim": ("#9aa7b4", 1.0), "kerb": ("#a0522d", 1.0),
    "outlet": ("#1d9bf0", 1.0), "penetration_cut": ("#ff5c5c", 0.5), "sleeve": ("#1d9bf0", 1.0),
    "hopper": ("#1d9bf0", 0.85)}

# Layers the legend and the DXF share: ifc_type → layer key.
LAYER_OF = {"firring": "firring", "sump_firring": "firring", "deck": "deck", "sump_deck": "deck",
            "vcl": "vcl", "sump_vcl": "vcl", "vcl_upstand": "vcl", "insulation": "insulation",
            "sump_insulation": "insulation", "ins_upstand": "insulation", "membrane": "membrane",
            "sump_membrane": "membrane", "upstand": "membrane", "counter_flashing": "trims",
            "drip_trim": "trims", "kerb": "kerbs", "outlet": "outlets", "sleeve": "outlets",
            "hopper": "outlets", "penetration_cut": "outlets"}

_NUMERIC = {  # name: (default, min, max)
    "fall": (40, 10, 200),            # main fall, 1:G
    "cricket_fall": (40, 10, 200),    # along a drain edge, 1:Gc
    "sump_fall": (40, 10, 200),       # a larger sump's floor, 1:Gs
    "d_min": (25, 0, 100),            # minimum firring on the main roof
    "deck_t": (18, 6, 40),
    "vcl_t": (4, 0.5, 20),
    "insulation_t": (120, 10, 400),
    "membrane_t": (2, 0.5, 20),
    "upstand": (150, 25, 600),
    "sump_ins": (50, 10, 200),
    "sump_drop": (75, 10, 300),
    "sump_firring": (0, 0, 200),
    "sump_l": (500, 0, 10000),
    "sump_w": (300, 0, 3000),
    "ins_upstand_t": (50, 0, 150),
    "kerb_w": (50, 25, 200),
    "check_kerb_h": (50, 10, 300),
    "trim_w": (70, 20, 200), "trim_h": (50, 20, 200),
    "flash_lap": (75, 25, 200),
    "hopper_w": (150, 50, 600), "hopper_h": (100, 50, 600),
    "outlet_d": (100, 40, 300),
    "wall_t": (215, 50, 600),         # fallback wall thickness for a hopper sleeve
}


def _parse(params):
    """Clean parameter dict: defaults, clamped ranges and the derived buildup."""
    p = {"roofs": list(params.get("roofs") or []), "context": params.get("context") or {}}
    for key, (default, lo, hi) in _NUMERIC.items():
        try:
            p[key] = min(hi, max(lo, float(params.get(key, default))))
        except (TypeError, ValueError):
            p[key] = float(default)
    p["membrane_name"] = str(params.get("membrane_name") or "Single-ply membrane")[:120]
    p["trim"] = bool(params.get("trim", True))
    p["above_firrings"] = p["deck_t"] + p["vcl_t"] + p["insulation_t"] + p["membrane_t"]
    p["sump_buildup"] = p["deck_t"] + p["vcl_t"] + p["sump_ins"] + p["membrane_t"]
    return p


def fall_label(ratio):
    """'1:57' for a fall of 1 in 57; 'level' for none."""
    if ratio is None or ratio <= 0 or ratio > 1e5:
        return "level"
    return "1:%d" % int(round(ratio))


def prism(profile, depth, thickness, frame, ifc_type, name="", roof="", holes=None, lift=None, **extra):
    """Profile in frame-local (u, v) mm; *depth* is the back face's offset along n."""
    color, opacity = COLORS.get(ifc_type, ("#cccccc", 1.0))
    m = {"type": "prism", "profile": [[float(a), float(b)] for a, b in profile],
         "holes": [[[float(a), float(b)] for a, b in h] for h in (holes or [])],
         "depth": float(depth), "thickness": float(thickness), "frame": frame,
         "color": color, "opacity": opacity, "name": name, "ifc_type": ifc_type, "roof": roof}
    if lift and (abs(lift[0]) > 1e-9 or abs(lift[1]) > 1e-9):
        m["lift"] = [float(lift[0]), float(lift[1])]
    m.update(extra)
    return m


def slab(rings, bot, top, frame, ifc_type, name="", roof="", **extra):
    """Plan rings (exterior first, then holes) in a roof frame between planes *bot* and
    *top*, each (a, b, c) with z = a + b·u + c·v above the frame origin."""
    color, opacity = COLORS.get(ifc_type, ("#cccccc", 1.0))
    m = {"type": "slab", "rings": [[[float(a), float(b)] for a, b in r] for r in rings],
         "bot": [float(c) for c in bot], "top": [float(c) for c in top], "frame": frame,
         "color": color, "opacity": opacity, "name": name, "ifc_type": ifc_type, "roof": roof}
    m.update(extra)
    return m


def plane_z(plane, x, y):
    return plane[0] + plane[1] * x + plane[2] * y


def rect(u0, v0, u1, v1):
    return [[u0, v0], [u1, v0], [u1, v1], [u0, v1]]
