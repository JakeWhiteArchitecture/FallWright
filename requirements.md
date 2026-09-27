# Fallwright: requirements

**Warm flat roof buildup, falls and edge details from an IFC model, in the browser.**

Import an IFC, click the top of the roof structure, place the outlets, and Fallwright works
out the falls from the outlets, forms them in firrings, builds the warm roof on top, classifies
every edge (abutment, parapet, drip trim, gutter, kerb), and checks the upstand heights against the
finished roof surface. Output is IFC4X3 geometry placed in the host model's storey, and a DXF
with a falls plan, sections and a detail for each edge type.

Forked from [CladForge](https://github.com/JakeWhiteArchitecture/CladForge) (IFC import,
viewer, face picking, prism engine, IFC and DXF writers, wizard, 2D view), which is itself
forked from StairSmith and SunForm. The new code is the falls engine (`roof_falls.py`), edge
classification (`roof_edges.py`) and the section and detail drawings.

Built for a live job first: a flat roof on a project with fifteen clad elevations. The
**Done line** at the end says when it stops.

---

## 1. Pipeline

| Stage | Action | Where it runs |
|---|---|---|
| 1 | Import IFC, render in viewer | browser (unchanged from CladForge) |
| 2 | Click the top face of the roof structure (top of joists) | browser (`static/viewer.js`) |
| 3 | Grow picks into one coplanar roof region; holes become rooflights and penetrations | Pyodide (`roof_extract.py`, from `fabric_extract.py`) |
| 4 | Classify every outline edge: abutment, parapet or free edge | Pyodide (`roof_edges.py`) |
| 5 | Outlets: enter the number, then place each by corner, edge and offset; mark any eaves gutter edges | UI, clicks in plan |
| 6 | Wizard: falls, firrings, deck, VCL, insulation, membrane | UI (`static/wizard.js`) |
| 7 | Generate falls (main falls, crickets, valleys), firring zone, deck, layers, upstands, kerbs, trims, outlets | Pyodide, live (`roof_falls.py`, `roof_geometry.py`) |
| 8 | Export step: position the cutting planes on plan (two by default, drag, flip, add more) | UI, in plan |
| 9 | Checks | Pyodide (`roof_checks.py`) |
| 10 | Export IFC4X3 and DXF | Pyodide (both) |

Picking and generating stay separate, as in CladForge. Nothing is generated until the wizard
is built. After that every panel field previews live; typed numbers are debounced at 300 ms.

---

## 2. First change: a general frame

CladForge ties "up" to world Z in four places. Fallwright needs a frame that can lie flat
(the roof), stand vertical (upstands, kerbs) or run along an edge (trims). Change these first,
in a way CladForge could take back unchanged:

1. **Frame dict gains `v`.** `{"origin", "u", "v", "n"}` with `v = n × u`. For a CladForge wall
   frame this gives world Z exactly, so walls are unaffected. A frame with no `v` is read as
   `v = (0, 0, 1)` for old data.
2. **`frame_to_world`** uses `origin + u·U + v·V + d·N` instead of adding `v` to Z.
3. **`fit_plane` / `make_frame` / `_to_local`**: a `mode` argument. `"wall"` keeps today's
   behaviour (flatten to vertical, reject horizontal). `"roof"` requires `|n·z| ≥ cos 5°`,
   snaps `n` to `(0, 0, 1)`, and sets `u = (1, 0, 0)` rotated to the longest outline edge, so
   plan drawings come out square to the building.
4. **`viewer.coplanarFaces`**: accept a mode. In roof mode keep only faces whose normal is
   within the tolerance of vertical in world terms (`|n.y| ≥ 1 − VERT_TOL` in Three.js Y-up)
   and facing up. `frameMatrix` builds its basis from `u`, `v`, `n`.

`_prism_solid` in `ifc_generator.py` already places the extrusion with axis `n` and ref
direction `u`, so it needs no change. `_corner_brep` becomes the general **brep path** used
for anything whose top is not parallel to its base (the firring zone), and for layers that lie on the falls.

Tests: every existing CladForge test still passes with the new frame; a roof frame round-trips
local to world to local within 0.01 mm.

---

## 3. Picking the roof

- Click the top face of the roof structure (`IfcSlab` with `PredefinedType=ROOF`, `IfcRoof`,
  or any upward-facing slab face). This is the **top of joists**: the level the firrings sit
  on. Further clicks on the same plane merge into
  the region, as CladForge merges wall patches.
- One roof region per build in v1. A second click on a different level starts a second
  roof, built separately.
- **Holes** in the region (from `IfcRelVoidsElement` openings) are rooflights, hatches or
  penetrations. Anything else crossing the deck plane (pipes, flues, rooflight frames not
  voided) is sectioned and subtracted as a convex hull, as in CladForge. Holes at least
  300 mm both ways are **openings** and get kerbs; smaller ones are **penetrations** and get
  a collar symbol only.
- **Joists are not needed.** Fallwright does not know where the joists are or which way they
  run. The firrings are modelled as a zone (section 6) and cut on site to suit the joists,
  using the depths the tool gives.

---

## 4. Edges

Every segment of the region outline, and every hole outline, gets a type. Detection runs once
per region; the user can change any edge in the edge list or by clicking it in plan.

| Type | Detected when | Detail |
|---|---|---|
| **Abutment** | An `IfcWall*` or `IfcCurtainWall` section, taken 300 mm above the structure, lies along the edge within 50 mm, and the wall rises 1500 mm or more above the structure | Membrane upstand to 150 mm above the finished surface, under a counter-flashing chased into the wall |
| **Parapet** | As abutment, but the wall's top is less than 1500 mm above the structure (leaves room for the roof buildup on terraced roofs) | Upstand carried up the parapet and over it under the coping |
| **Free edge: drip** | No wall along it, not a drain | Drip trim, membrane dressed over |
| **Free edge: gutter** | No wall, and the user sets it as a gutter edge | Drip trim into eaves gutter; the whole edge takes water |
| **Kerb** | Opening hole (step 3) | Upstand to 150 mm above the finished surface round the kerb |
| **Check kerb** [OPEN] | User sets it on a free edge | Raised edge upstand, 50 mm default |

Edges are listed under the roof as rows (type, length, finished level range along it), like
CladForge's corner rows; clicking a row highlights the edge in the model.

**Doors in abutment walls.** Any `IfcDoor` in a wall along an abutment edge is found and its
sill level recorded, for the threshold check.

---

## 5. Outlets and falls

The outlets drive everything. The user places them; the tool works out the falls.

### Placing outlets
All outlets sit on the roof perimeter.

1. **Number of outlets**: typed first.
2. For each outlet in turn: **click a corner** of the roof outline (snaps to vertices), **click
   the edge** to run along (one of the two edges meeting at that corner), **type the offset**
   in mm from the corner along that edge, and choose the **type**:
   - **Internal outlet**: through the deck, flange against the edge. Any edge type. A sump is
     optional (off by default).
   - **Hopper**: a through-wall penetration in the parapet or upstand into an external hopper.
     Abutment or parapet edges only; on a free edge it is refused with a message. **Always has
     a sump.**
3. Repeat until the number is reached. The outlets list (number, type, edge, corner, offset,
   sump size) stays editable; changing the number adds or removes from the end.

**Eaves gutters** are not outlets: click a free edge and set it to *Gutter edge*. The whole edge
then takes water.

### Sumps
A sump is a rectangular recess in the roof against the edge, local to the outlet. It collects
the water and carries it to the outlet, so it can run **laterally along the face of the
parapet** and the outlet (the through-wall penetration) need not sit at its middle.

- **Size**: length along the edge `L` and depth in from the edge `W`. **Standard sump: 500 mm
  along the wall × 300 mm out from it**, the default. (One UK manufacturer recommends around
  500 × 500 and warns that smaller sumps make the waterproofing joints hard to form well; the
  sump is dragged bigger where the job needs it.) It starts centred on the outlet and is then independent of it: the sump
  can be dragged along the edge, and its ends and front edge dragged to change `L` and `W`, as
  long as the outlet stays within its length.
- **The sump sets the datum.** The sump is built first and the roof rises from it:
  1. **Sump floor, lowest point**: joist top, then firrings under the sump or none (sump
     firring depth `sf`, default 0), then the 18 mm ply deck, VCL, **50 mm insulation** and the
     membrane. This is the lowest level on the roof and the outlet invert.
  2. **Sump floor, highest point**: the same, plus the floor's rise `ρ` if the sump is larger
     than standard (below). A standard sump is level, so `ρ = 0`.
  3. **Rim**: **75 mm up from the highest point of the sump floor** (the drop, default 75,
     editable, never less). The rim runs round the three open sides of the sump and is the
     lowest finished level of the rest of the roof: the **drain line** the falls run up from.
- **Floor**: level for a standard sump (500 × 300 or smaller). **A sump larger than standard
  goes to falls** at the sump fall `G_s` (default 1:40), formed in firrings under the sump ply,
  so the 50 mm insulation stays constant:
  - longer than 500 mm: the floor falls along the sump to the outlet (from both ends if the
    outlet is not at one end);
  - deeper than 300 mm out from the wall: the floor also falls across the sump to the wall.
  Each floor piece is a plane, like the facets.
- **Firring depth at the rim.** The main roof firrings are **never less than 25 mm**
  (`d_min`); they only get deeper when the sump needs them to. Ply, VCL and membrane appear on
  both sides of the rim and cancel, so:

  ```
  r_k    = max( 25,  sf + 50 + ρ + 75 − T )     (T = roof insulation thickness)
  drop_k = (r_k + T) − (sf + 50 + ρ)            (always ≥ 75)
  ```

  - If the sump needs the rim higher than 25 mm firrings give, the firrings **thicken up** to
    suit and the drop is exactly 75 mm.
  - If not, the firrings stay at 25 mm and **the insulation depth sets the step**: the drop is
    more than 75 mm. With a standard sump on no firrings and `T` = 120, the step is
    25 + 120 − 50 = 95 mm.

  The sides of the sump are that step: the roof insulation edge, with the VCL and membrane
  dressed down it to the floor. The tool reports every sump's actual drop.

An internal outlet with no sump is a sump of zero length and width: the same maths, with the
drain line shrunk to a point on the edge.

**Dragging, all live.** In plan, each sump has handles on both ends and its front edge, and
can be dragged along its edge as a whole; each outlet or hopper can be dragged along its edge
within its sump (an internal outlet without a sump drags along the edge). Every drag updates
the falls, hips and valleys as it moves: while dragging, only the facets are recomputed
(once per animation frame); the layers and breps rebuild on release, as CladForge's offset
slider does.

### Drain edges
An edge with at least one outlet, or set as a gutter edge, is a **drain edge**. Each drain edge
`j` has two coordinates for any point `p` on the roof: `s_j(p)`, the perpendicular distance in
from the edge (positive on the roof side), and `t_j(p)`, the distance along the edge from its
start.

### The falls surface
The falls are formed in **firrings** on this job. The surface below is the top of the firring
zone; everything above it is built at constant thickness, so the finished roof has the same
falls. Tapered insulation is a later option that uses the same surface (section 10).

Two falls set the scheme: `G` (main fall, default 1:40) and `G_c` (cricket fall along a drain
edge, default **1:40**, the same as the main fall). The level the falls start from is each
sump's rim (`r_k`, above). A drain with no sump (a gutter edge, or an internal outlet without
one) starts from `d_min`, 25 mm.

Each sump `k` on drain edge `j` runs from `t = b_k1` to `t = b_k2` along the edge (`L_k` long)
and `W_k` in from it. For each sump:

```
e_k(p) = distance along the edge from t_j(p) to the sump:  max(b_k1 - t, 0, t - b_k2)
f_k(p) = max( (s_j(p) - W_k) / G ,  e_k(p) / G_c )
```

A gutter edge is one long sump the length of the edge, with `W = 0` and `r = d_min`. The roof
surface, over the sumps whose edge faces `p` (`s_j(p) ≥ 0`), outside the sumps themselves:

```
firring depth(p) = min over k of ( r_k + f_k(p) )
```

Sumps of different sizes have different rims, so a bigger sump lifts the roof round it; the
`min` keeps the surface continuous and moves the hips and valleys to suit.

What that builds:

- **Main fall**: the roof falls at 1:G across to its nearest drain edge.
- **Main fall into the sump**: opposite a sump the roof falls straight to its front rim.
- **Crickets**: along a drain edge, between sumps and out to the edge ends, the surface rises
  away from each sump end at 1:G_c, so water runs along the edge into the sump's side rim. The
  cricket ridge is midway between neighbouring sumps, perpendicular to the edge.
- **Valleys**: where a cricket meets the main fall. A straight valley runs diagonally out of
  each **front corner of each sump** (at 45 degrees when `G = G_c`), so a longer sump moves the
  valleys apart and a wider one moves them out into the roof. The fall along a valley is
  `1 : sqrt(G² + G_c²)`, which is **shallower than either**: 1:57 with both at 1:40. The tool
  reports every valley fall and checks it.
- **Hips**: where the falls to two different drain edges meet.

The surface is a min and max of planes, so every **facet is exactly planar** and every hip and
valley is a straight line, so every firring is cut to straight tapers between kinks. Build the facets with Shapely: each sump's
`f_k` splits into a main-fall piece and two cricket pieces by half-planes; the `min` across
sumps then cuts each piece where another sump's plane is lower. The sump rectangles are
subtracted from the roof region first; they are drawn and built separately. A facet under 0.1 m² is merged into its neighbour and reported. Any part of the roof no
drain edge faces is an error ("no outlet reaches this area") and is hatched red.

**Trapped water check.** From the centroid of every facet, trace steepest descent across the
facets in 50 mm steps. It must reach an outlet or a gutter edge without leaving the roof; if it
stops at a low point first, that facet is flagged as ponding. This catches re-entrant corners on
L-shaped roofs, where a drain edge "faces" a point that water cannot actually reach.

### Overflow
Flagged only when the roof has **exactly one outlet** and no gutter edge: the checks say an
overflow is required. Placing and detailing it is by hand in v1.

### Firring setting-out
Fallwright does not know the joists, so it gives the carpenter what they need to cut a firring
on any joist:

- **Firring depth** at every facet vertex, every outlet and every point where a hip or valley
  meets the roof outline.
- **Depth contours** across the roof plan every 25 mm of depth (25, 50, 75 ...), so the depth
  at any point along a joist can be read off.
- Firrings are **profiled**: a firring that crosses a hip or valley has a kink there, and its
  depth at the kink is on the plan. Where firrings meet along a hip or valley they are
  **mitred** under the line, so both deck edges at the break bear on firring.

### Outputs from falls
- Spot levels at every facet vertex and at every outlet: top of firrings and finished
  membrane level, absolute in the model datum and relative to the top of structure.
- Minimum and maximum firring depth.
- Fall arrows on each facet labelled with its true fall; valley falls labelled along each
  valley.

---

## 6. Buildup

Bottom to top, all editable in the wizard and panel, all live:

| # | Layer | Default | Geometry |
|---|---|---|---|
| 0 | Joists | in the model | not generated; the picked face is their top |
| 1 | Firring zone | from the sump rims (`r_k`), 1:40 | one brep per facet: flat base on the joists, sloped top; neighbouring facets meet on the vertical plane through their hip or valley (the mitre) |
| 2 | Deck | 18 mm WBP plywood | one brep per facet, following the falls; breaks at every hip and valley |
| 3 | VCL | 4 mm | follows the falls, turned up at abutments and parapets to the insulation top |
| 4 | Insulation | 120 mm, typed | constant thickness, follows the falls |
| 5 | Membrane | 2 mm | constant thickness, follows the falls |

- Every layer above the firrings is offset vertically from the one below. At 1:40 the
  difference from a true perpendicular offset is under 0.1 mm per 100 mm of thickness.
- The **membrane** is one layer, 2 mm by default, thickness editable, with a free-text name
  (printed in the DXF notes and the IFC property set). No manufacturer data is built in.
- The insulation thickness is typed in, from a U-value calculation done elsewhere.
- Every layer is clipped to the region minus holes, as CladForge trims to its region.
- **Insulation edge upstands** [ASSUMED]: at abutments and kerbs, a 50 mm insulation upstand
  (angle fillet omitted in v1), so the membrane turns over an insulated edge.

### Edge elements (IFC and 3D)
- **Counter-flashing** at abutments: an L profile swept along the wall, its top chased into the
  wall and its skirt lapping the upstand by 75 mm; drawn in the details, modelled as a simple
  prism.
- **Upstand membrane**: vertical prism on a wall frame along the edge. Its bottom follows the
  finished surface along the edge; its top is level at the highest finished level on that edge
  plus the upstand height. The profile is a polygon, so a sloping bottom is just its outline.
- **Kerb**: timber kerb prism round each opening, 50 × 150 mm default, top level set so the
  upstand is 150 mm above the finished surface at the kerb's highest point.
- **Sump**: its own buildup (sump firrings if any, 18 mm ply, VCL, 50 mm insulation,
  membrane), level or to falls, with the roof layers stopping at the rim and the VCL and
  membrane dressed down the three sides to the floor. Modelled as its own breps; the sump
  rectangle is cut from every roof layer.
- **Hopper and through-wall penetration**: a rectangular opening through the parapet at the
  sump floor (default 150 wide × 100 high), a sleeve through the wall, and a hopper symbol on
  the outside face. The penetration is modelled as an `IfcOpeningElement` in the host wall only
  when the export is told to modify the host; otherwise it is a proxy solid marking the cut.
- **Drip trim**: a small L profile swept along the edge (a prism on an edge frame: `n` along
  the edge, `u` and `v` in the section plane), 70 × 50 mm default.
- **Gutter** [OPEN]: a symbol line in plan and section only, not modelled.

---

## 7. UI

Reuse the CladForge shell. Differences:

- **Pick roof** replaces Pick faces. The view starts looking down.
- **Plan view** replaces the 2D elevation: press **E** to swing to an orthographic view looking
  straight down, as `view2d.enter2D` does for elevations. Outlets, gutter edges and section
  lines are placed in plan, so plan view is on for those steps.
- **Wizard steps**: number of outlets → place each (corner, edge, offset, type) → gutter
  edges → sumps (insulation 50, minimum drop 75, sump firrings 0) → falls (main fall, cricket
  fall, minimum firring 25) → deck (18) → VCL (4) → insulation (120) →
  membrane (name, 2 mm) → upstand height (150) → Build.
- **Panel sections**: Region, Edges (the edge rows), Outlets (the outlet list), Falls, Buildup,
  Checks, Export.
- **Cutting planes, at export.** Pressing Export DXF swings to plan and shows **two cutting
  planes** before writing the file: one across the roof in each direction (along the roof
  frame's u and v), through the middle of the roof. Each is drawn as a section line with
  arrowed ends and a label (A-A, B-B):
  - **drag** a plane to move it across the roof; it stays straight and parallel to its
    direction, and snaps to outlets and facet vertices within 100 mm;
  - **flip** the cut direction by clicking its arrow, which swaps the way the section looks;
  - **+** adds another plane, asking which direction it runs; each extra plane has a remove
    button;
  - **Export** writes the DXF with one section per plane; **Cancel** goes back without
    writing.
  The planes persist with the roof, so a second export starts from where they were left.
- **Dimensions in plan**: each outlet's offset from its corner, facet boundaries, firring depths,
  valley falls and spot levels as HTML labels. Click an outlet offset, a sump dimension or drop, either fall or an
  upstand height to type over it, as CladForge's 2D view does. Sump length, width, drop and
  offset from the corner are dimensioned too and can be typed over or dragged (section 5,
  **Dragging**).
- The legend toggles every layer, the facets, the fall arrows, the depth contours and the levels.

---

## 8. Checks

| Check | Pass | Warn | Fail |
|---|---|---|---|
| Design fall, every facet | 1:40 or steeper | 1:40 to 1:80 | flatter than 1:80 |
| Design fall, every valley | 1:40 or steeper | 1:40 to 1:80 | flatter than 1:80 |
| Upstand at abutments and parapets | ≥ 150 mm above finished surface at every point along the edge | | less |
| Kerb upstand | ≥ 150 mm above finished surface round the whole kerb | | less |
| Door threshold in abutment wall | sill ≥ 150 mm above finished surface | 75–150 mm (needs an accessible threshold detail with drainage) | < 75 mm |
| Drainage coverage | every point faces a drain edge | | area no outlet reaches |
| Trapped water | every facet traces down to an outlet or gutter edge | | facet ends at a low point |
| Outlets placed | as many as the number entered, each on the roof outline | | fewer placed, or offset past the end of its edge |
| Hopper position | on an abutment or parapet edge | | on a free edge |
| Hopper sump | every hopper has a sump | | hopper with no sump |
| Sump drop | ≥ 75 mm from the floor's highest point to the rim (by construction; the actual drop is reported) | | less than 75 mm (only possible from a typed value) |
| Outlet in sump | outlet within its sump's length | | outside it (dragging clamps it, so this only fires on typed values) |
| Sump fits | sump within its edge and clear of other sumps and holes | | overlaps an edge end, another sump or a hole |
| Sump insulation | ≥ 50 mm | | under 50 mm |
| Sump floor falls | standard sump level, or larger sump falling at 1:40 or steeper | larger sump at 1:40 to 1:80 | larger sump level or flatter than 1:80 |
| Overflow | two or more outlets, or a gutter edge | exactly one outlet and no gutter edge: overflow required | |
| Maximum firring depth | ≤ 150 mm | over: deep firrings need restraint, and the roof edge gets tall | |
| Minimum firring depth | ≥ 25 mm everywhere on the main roof (by construction) | | under 25 mm (only possible from a typed value) |
| Facet size | ≥ 0.1 m² | merged small facet | |

The design fall rows follow BS 6229: design to 1:40 so that the finished roof still achieves
at least 1:80 after deflection and build tolerance. With the defaults (both falls 1:40) the
valleys come out at 1:57 and warn; steepening the cricket fall or the main fall is the fix, and
the warning says so. The warning stays on by default so it is a decision, not a surprise.

The threshold check is the one that matters most on site: the finished roof surface at a door
is the deep end of the firrings more often than people expect.

**Scope limits, stated in the UI and both export headers.** Fallwright does not calculate
U-values or condensation risk (Parts L and C), does not check roof covering fire performance
(Part B), and does not design the deck, loads or wind uplift fixing. The falls scheme is
design intent; the firrings are cut on site to suit the joists from the depths given. Any
schedule the tool produces is setting-out information, not a quantity take-off for pricing.

---

## 9. Export

### IFC4X3
- One `IfcElementAssembly` per roof (`PredefinedType=USERDEFINED`, `ObjectType="Warm roof
  system"`), placed in the storey of the picked slab.
- Layers as `IfcCovering` (`ROOFING` for the membrane, `INSULATION` for insulation, `MEMBRANE`
  for VCL), deck as `IfcPlate`, the firring zone as `IfcBuildingElementProxy` with
  `ObjectType="Firring zone"` (one per facet; it is a zone, not individual firrings), kerbs as
  `IfcMember`, trims and counter-flashings as `IfcBuildingElementProxy`.
- Anything on the falls as `IfcFacetedBrep`; everything else as `IfcExtrudedAreaSolid`, as
  CladForge does.
- Property set `Pset_Fallwright`: main and cricket falls, sump levels and rim firring depths, maximum firring depth,
  membrane name, and the facet for each piece.
- Keep CladForge's `_create_file` schema probing and the stable GUIDs.

### DXF
One file, laid out left to right:

1. **Falls plan**: roof outline, holes, facet boundaries (hips dashed, valleys chain-dotted),
   drain edges (heavy), outlets numbered and dimensioned from their corners, hoppers marked
   through the parapet, sumps with their length, width, rim level, floor level and drop, fall arrows with each facet's fall, valley falls, firring depth
   contours (thin) every 25 mm, firring depths and spot levels at facet vertices and outlets,
   edge types tagged along the outline.
2. **Sections**: one per cutting plane, 1:20, looking in the plane's arrow direction. Each shows the deck, every layer at
   its true thickness along the cut, the finished surface, the levels at both ends, and the
   edge detail wherever the line crosses the outline or a hole.
3. **Edge details**: one per edge type present on the roof, 1:5, drawn from the parameters:
   firrings, deck, VCL turn-up, insulation and insulation upstand, membrane turned up to the
   upstand height, and the termination (counter-flashing, coping, drip trim or kerb). Plus a
   **hopper detail** for each hopper: a section through the sump square to the wall, showing
   the rim, the 75 mm (or more) drop, the reduced insulation, the membrane lining, the
   through-wall penetration and sleeve, and the hopper on the outside face. Dimension the
   upstand height above the finished surface.
4. **Notes and schedule**: membrane name, layer list, falls, sump buildup, rim firring depths and maximum firring depth,
   outlet schedule (number, type, edge, offset, sump length, width, rim level, floor level,
   drop), check results, the scope statement.

Layers: `ROOF_OUTLINE`, `FACETS`, `DRAINS`, `OUTLETS`, `FALL_ARROWS`, `FIRRING_DEPTHS`,
`SUMPS`, `LEVELS`, `SECTION_LINES`, one layer per buildup layer, `DETAILS`, `DIMS`, `NOTES`.

---

## 10. Decisions on open items

| Item | Decision | Where |
|---|---|---|
| Roof type | Warm flat roof, falls in firrings. | `roof_falls` |
| Tapered insulation | Later. The falls surface stays the same; the brep per facet becomes insulation instead of firrings, the insulation above goes, and tapered board rows are added. | `roof_falls` |
| Structure | Picked face is top of joists. Joists are not read or modelled; firrings are a zone. | `roof_extract` |
| Firrings | Profiled to the falls, kinked where they cross a hip or valley, mitred along hips and valleys so the deck break is supported. 25 mm minimum. | `roof_geometry.firrings` |
| Parapet or abutment | Wall top under 1500 mm above the structure is a parapet. | `roof_edges` |
| Abutment termination | Counter-flashing over the upstand. | `roof_geometry`, `dxf_generator` |
| Membrane | One layer, 2 mm default, editable, name free text. | `wizard` |
| Outlet positions | On the perimeter only, placed by corner, edge and offset. The user places them; the tool does not propose positions. | `app.placeOutlet` |
| Outlet types | Internal outlet or hopper (through-wall). Eaves gutters are gutter edges, not outlets. | `roof_falls` |
| Falls layout | Worked out by the tool from the outlets: main fall to the nearest drain edge, crickets along it between outlets, diagonal valleys into each outlet. Planar facets. | `roof_falls.facets` |
| Sumps | Rectangle against the edge, 500 along × 300 out, standard and default, centred on its outlet at placement, then draggable independently. Rim is the drain line the falls run to. Every hopper has one; internal outlets optionally. | `roof_falls.sumps` |
| Sump drop | At least 75 mm below the rim, which is the lowest point of the roof feeding it, measured at the floor's highest point. | `roof_geometry.sump`, `roof_checks` |
| Sump floor | Level up to 500 × 300. Larger goes to falls at 1:40: along the sump to the outlet when longer than 500, and to the wall when deeper than 300. | `roof_geometry.sump` |
| Sump forming | The sump is the datum: joists, firrings or none, 18 mm ply, VCL, 50 mm insulation, membrane. Larger sumps fall in firrings under the ply. | `roof_geometry.sump` |
| Roof datum | Main roof firrings are 25 mm minimum and thicken up only when a sump needs the rim higher; otherwise the insulation depth sets the step, which is then more than 75 mm. Actual drop reported per sump. | `roof_falls.rims` |
| Lateral routing | The outlet can sit anywhere along its sump, so a long sump runs water along the parapet face to a hopper off to one side. | `roof_falls.sumps` |
| Dragging | Sumps (whole, ends, front edge) and outlets drag in plan; facets recompute per animation frame, layers on release. | `app.dragSump`, `app.dragOutlet` |
| Crickets | 1:40, the same as the main fall, editable. | `wizard` |
| Overflow | Flagged when there is exactly one outlet and no gutter edge. Not placed or modelled. | `roof_checks` |
| Outlet capacity | Not checked. No rainfall or catchment calculation. | |
| Outlet bodies | Drawn as a symbol and modelled as a simple `IfcFlowTerminal` cylinder; no manufacturer data. | `roof_geometry.outlet` |
| Upstand height | 150 mm above the finished surface at the highest point of each edge; top is level. | `roof_geometry.upstand` |
| Angle fillets | Omitted. | |
| Gutters | Symbol only. | `dxf_generator` |
| Multiple roofs | One region per build; build again for another level. | `app` |
| Sections | Two cutting planes by default, one in each roof direction, shown on plan at export; drag, flip, add or remove. Orthogonal to the roof frame only. | `app.cutPlanes`, `dxf_generator` |
| New build | The deck level, upstands and door sills are designed to suit the falls, so the threshold and upstand checks report what the design needs rather than a fixed constraint. Existing roof overlays are not in v1. | `roof_checks` |
| Insulation thickness | Typed in, constant, from a U-value calculation done elsewhere. | `wizard` |
| Firring setting-out | Depths at vertices and outlets plus 25 mm depth contours; no firring schedule or counts. | `roof_falls.contours` |

---

## 11. Tests

- Frame: CladForge suite passes unchanged; roof frame round-trip within 0.01 mm.
- Falls, on synthetic roofs in `tests/synthetic.py`:
  - rectangle, one gutter edge: one facet, firring depth at the edge equals `d_min`, at the far
    edge `d_min + width / G`;
  - rectangle, one outlet mid-edge with no sump: four facets (two main, two cricket), valleys
    at 45° when `G = G_c`, valley fall reported as 1:57 at 1:40 and 1:40;
  - the same with a 500 × 300 sump: valleys start from the sump's front corners, the whole rim
    is at one level, and the surface is continuous round the sump;
  - standard sump, no sump firrings: `T` = 120 keeps the rim firrings at 25 mm and gives a
    95 mm step; `T` = 100 gives 25 mm firrings and exactly 75 mm; `T` = 80 thickens the rim
    firrings to 45 mm with a 75 mm drop;
  - lengthening the sump by 200 mm at one end moves only that end's valley by 200 mm; widening
    it moves both valleys out;
  - a 2000 mm sump with the hopper 200 mm from one end: roof falls unchanged by the hopper
    position (the rim is the drain); the sump floor rises 45 mm from the hopper to the far end
    at 1:40; with `T` = 120 the rim firrings thicken to 50 mm and the rim is 75 mm above the
    far end;
  - a 500 × 300 sump has a level floor; 510 mm long, or 310 mm out from the wall, puts it to
    falls in that direction;
  - two sumps of different widths on one edge: surface continuous (no step) between them;
  - rectangle, two outlets on one edge: cricket ridge exactly midway, symmetrical;
  - rectangle, outlets on two adjacent edges: hip between them on the bisector;
  - outlet placed from the other corner of the same edge with the complementary offset gives
    the identical surface;
  - L-shaped roof, outlets on one wing only: trapped water check flags the facet in the other
    wing that cannot drain;
  - every facet planar (all vertices within 0.1 mm of its plane) in every case above;
  - roof with a rooflight hole: hole subtracted from every layer, kerb generated;
  - two facets meeting on a valley: their firring breps share the vertical face through the
    valley exactly (the mitre), with no gap or overlap.
- Edges: a sample model with a wall on one side, a 1000 mm parapet on another and two free
  edges classifies all four correctly; a 1600 mm wall is an abutment.
- Checks: a door sill 100 mm above the finished surface warns; 60 mm fails; a single outlet
  raises the overflow warning; a hopper on a free edge fails; `d_min` of 20 mm on a gutter edge
  fails; a 60 mm sump drop fails; 40 mm sump insulation fails; a hopper with its sump turned off fails.
- Export: IFC opens in IfcOpenShell with the expected classes and a Brep per facet; DXF has
  every layer listed above.
- Update `tests/make_sample.py` to build a flat-roofed sample with a parapet, an abutment wall
  with a door, a rooflight, two free edges and two outlets. It needs no joists.

---

## Done line

Fallwright is done when, on this job's roof:

1. the roof is picked from the job model and every edge is classified correctly or corrected
   by hand;
2. the outlets are placed and the falls scheme passes the checks, or every warning is one
   you have accepted;
3. the IFC4X3 export sits correctly in the job model with the buildup and falls;
4. the DXF gives a falls plan with levels, at least two sections, and a detail for each edge
   type on this roof, good enough to go on a drawing sheet after tidying in CAD.

Anything past that waits for the next job that needs it.
