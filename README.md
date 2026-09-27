# Fallwright

**Warm flat roof buildup, falls and edge details from an IFC model, in the browser.**

Import an IFC, click the top of the roof structure, place the outlets, and Fallwright works
out the falls from the outlets, forms them in firrings, builds the warm roof on top,
classifies every edge (abutment, parapet, drip trim, gutter, kerb), and checks the upstand
heights against the finished roof surface. Output is IFC4X3 geometry placed in the host
model's storey, and a DXF with a falls plan, sections and a detail for each edge type.

Forked from [CladForge](https://github.com/JakeWhiteArchitecture/CladForge) (IFC import,
viewer, face picking, prism engine, IFC and DXF writers, wizard, 2D view), itself forked from
StairSmith and SunForm. The full specification is [`requirements.md`](requirements.md); this
file says how it was built and where the build chose or departed from it.

## Pipeline

| Stage | Action | Where it runs |
|---|---|---|
| 1 | Import IFC, render in viewer | browser (`static/viewer.js`, unchanged readers) |
| 2 | Click the top face of the roof structure | browser (`viewer.coplanarFaces(..., 'roof')`) |
| 3 | Grow picks into one level roof region; holes, walls on the deck and penetrations cut out | Pyodide (`roof_extract.py`) |
| 4 | Classify every outline edge | Pyodide (`roof_edges.py`) |
| 5 | Outlets: the number, then corner, edge, offset and type for each, by clicks in plan; gutter edges | UI (`static/wizard.js`) |
| 6 | Wizard: sumps, falls, deck, VCL, insulation, membrane, upstands | UI (`static/wizard.js`) |
| 7 | Falls, firring zone, deck, layers, upstands, kerbs, trims, outlets | Pyodide, live (`roof_falls.py`, `roof_geometry.py`) |
| 8 | Export DXF: position the cutting planes on plan | UI (`static/plan2d.js`) |
| 9 | Checks | Pyodide (`roof_checks.py`) |
| 10 | Export IFC4X3 and DXF | Pyodide (`ifc_generator.roof_to_ifc`, `roof_dxf.py`) |

Picking and generating stay separate. Nothing is generated until the wizard builds the roof;
after that every panel field previews live, typed numbers debounced at 300 ms. Dragging a
sump or outlet recomputes only the facets, once per animation frame; the layers rebuild on
release.

## Running it

```bash
pip install -r requirements.txt
python app.py            # http://localhost:8080
```

Flask serves the page and the Python sources for Pyodide to import; the only route that does
work is `/api/import`, the IfcOpenShell fallback for models web-ifc cannot build. The runtime
pins are a matched set: **Pyodide 0.29.0** (CPython 3.13, `pyodide_2025_0`), **Shapely 2.0.7**
from that distribution, and the **IfcOpenShell 0.8.5** WASM wheel, which carries IFC4X3_ADD2.
The demo model is `tests/sample_roof.ifc` (rebuild with `python tests/make_sample.py`): a
10 × 6 m flat roof with an abutment wall and a door on the north side, a 1000 mm parapet on
the east, free edges south and west, and a voided rooflight.

### Using the tool

1. Drop an IFC on the panel. It is parsed in your browser and never uploaded.
2. With **Pick roof** on, click the top of the roof structure (the top of the joists). More
   clicks on the same level merge in; a click on another level starts a second roof.
3. Check the **Edges** list: each edge's detected type, the wall it found and any doors.
   Change any of them there.
4. Press **Enter** (or **Build**, top right). Type the number of outlets. The view swings to
   plan: for each outlet click a corner, click the edge to run along, type the offset from
   that corner and choose **Internal** or **Hopper** (a hopper is refused on a free edge).
   Then click free edges to make them gutter edges, and **Done**. The wizard carries on with
   the sumps, falls, deck, VCL, insulation, membrane and upstand height, and **Build**.
5. Press **E** for plan view. Fall labels, valley falls and spot levels (firring depth and
   finished level) sit on the plan. Click an outlet offset, a sump length, width or drop,
   either fall or an upstand height to type over it. Drag a sump whole, by its ends or by
   its front edge, or an outlet within its sump. Zoom in if a handle is hidden: handles give
   way to one another when they would overlap on screen.
6. Read the checks. **Download IFC4X3** writes the model. **Export DXF** swings to plan and
   shows two cutting planes, A-A and B-B: drag them, click an arrow to flip the way a section
   looks, **+** to add one, then **Export**. The planes stay with the roof for next time.

## How the falls are built

Every outlet with a sump, every internal outlet without one (a sump of no size), and every
gutter edge (one long sump with no width) is a *sump* on a drain edge. For a point *p* at
distance *s* in from that edge and *t* along it:

```
e(p)            = max(b1 - t, 0, t - b2)
f(p)            = max((s - W) / G, e / Gc)
firring depth   = min over the sumps of  r + f(p)
```

f is a max of four planes, so the surface is a min of maxes of planes: continuous, every
facet exactly planar, every hip and valley straight, and every pair of pitches meeting on
the line drawn between them. `roof_falls.build_facets` builds each sump's main
and cricket pieces from half-planes, cuts each where another sump is lower, merges coplanar
neighbours, and snaps every facet to a 0.01 mm grid so neighbours share their vertices. The
firring zone is one slab per facet, flat on the joists with its top on the falls, so two
facets meet on the vertical plane through their hip or valley (the mitre).

The sump sets the datum. Its rim firring depth is `r = max(d_min, sf + si + ρ + drop − T)`
and its actual drop `r + T − (sf + si + ρ)`: with a standard sump, no sump firrings and
120 mm insulation, the firrings stay at 25 and the step is 95 mm.

## Decisions and departures

Where the requirements left room, or where building them showed something, this is what was
done.

| Item | Decision | Where |
|---|---|---|
| One outlet, no sump | **Three facets, not four.** The requirements' own formula gives one main-fall plane, r + s/G, which does not depend on t, so the wedge between the two valleys is a single facet. The test follows the formula. | `tests/test_falls.py` |
| Trapped water | From each facet's centroid and from just inside each of its corners, steepest descent in 50 mm steps. Each step tries the fall of every facet in reach and every pair of them, so water on a valley runs down the valley. Against a wall on the outline, water that has no fall along it stops (the re-entrant corner case). Against a hole, a kerb or a pipe, it divides and runs round. | `roof_falls.trapped` |
| Pitches meet | Every facet is its plane's volume, split vertically where its top surface runs through a neighbour's: the seam is the line where the two planes cross, drawn on plan as the hip, valley or ridge. Nothing is allowed to cut a facet anywhere else, so along every seam both pitches are at the same height. A test builds 80 random roofs (rectangles, L-shapes, rooflights, mixed falls and sumps) and checks every seam, gaps, overlaps and every corner against the formula. | `roof_falls.build_facets`, `tests/test_falls.py` |
| Drains that do not face a point | The requirements take the min only over sumps whose edge faces the point. As a hard cut that makes the surface jump where an edge's line crosses the roof (200 mm at the inner corner of an L), so the min is taken over every sump; behind its edge a sump's crickets carry on continuously. Facing is kept for reach: roof its governing sump does not face is hatched as no outlet reaching it. | `roof_falls.build_facets` |
| Small facets | **Kept exact, not merged.** Folding a facet under 0.1 m² into a neighbour gives it the neighbour's plane, and the two pitches then no longer meet. The check warns about each one instead. | `roof_falls._mark_small`, `roof_checks` |
| Joining facet pieces | With snap-rounding on the 0.01 mm facet grid. GEOS's plain union of near-coincident pieces can silently drop a piece, depending on their order: it left a 3.7 m² hole in one test roof. | `roof_falls._merge_coplanar` |
| Sump at placement | Centred on its outlet, then shifted along the edge if it would run past an end. Once dragged or typed it is independent of the outlet. | `roof_falls.resolve` |
| Walls on the deck | A wall that stands on the roof slab is cut out of the region at its foot, 10 mm above the structure, so its face becomes the edge. | `roof_extract`, `roof_edges.wall_feet` |
| Edge coverage | A wall counts along an edge when its section covers at least half the edge. Partly walled edges are not split in v1. | `roof_edges.MIN_COVER` |
| Parapet upstand | Carried to the parapet's top and over it; the parapet top, not the upstand height, sets it, so a low parapet fails the upstand check. Insulation upstands go at abutments and kerbs, as specified. | `roof_checks.upstand_top`, `roof_geometry` |
| Hopper in the host wall | The export is a separate file and cannot modify the host, so the through-wall penetration is always a proxy solid marking the cut, with a sleeve and a hopper head. | `roof_geometry._outlet` |
| Outlet bodies | `IfcWasteTerminal` with `PredefinedType=ROOFDRAIN`, the IFC4X3 subtype of `IfcFlowTerminal` for a roof drain. | `ifc_generator._ROOF_TYPE_MAP` |
| DXF scale | Everything is drawn at true size in model space, as CAD expects; each view is labelled with its intended plot scale (1:100 plan, 1:20 sections, 1:5 details) and its text sized for it. | `roof_dxf.py` |
| Plan labels | HTML over the plan view only, as CladForge's 2D labels were. The 3D view shows the overlay lines (facets, arrows, contours, sumps) without text. | `static/plan2d.js` |
| CladForge engine | Kept. `cladding_*.py`, `dxf_generator.py` and their 57 tests stay as the regression guard on the general frame, which is shared code. The UI is Fallwright's only. | |

### Fixed on the way

- **Model offset axes.** web-ifc returns Y-up vertices, but the viewer treated them as IFC
  Z-up, so the recentring offset handed to the exports was in the wrong axis order, and a
  georeferenced model's export would have landed in the wrong place. It also shifted heights,
  so levels were relative to the model's bottom. Now only plan is recentred, heights stay the
  model's own, and the offset reaches the exports in IFC axes. The smoke test checks that the
  exported firrings sit exactly on the slab's corner in model coordinates.
- **Server importer orientation.** IfcOpenShell returns Z-up; its vertices are now turned
  Y-up on arrival, so a model read on the server stands upright like one read by web-ifc.

## Geometry model

Two shapes, both in a frame `{"origin", "u", "v", "n"}` (IFC mm, Z-up):

```
{"type": "prism", "profile": [[u, v], ...], "holes": [...], "depth": d, "thickness": t,
 "frame": {...}, "lift": [l0, l1], ...}     # extruded along n; lift slopes it along an edge
{"type": "slab", "rings": [[[u, v], ...], ...], "bot": [a, b, c], "top": [a, b, c],
 "frame": {...}, ...}                        # between planes z = a + b·u + c·v in a roof frame
```

Frames lie flat (a roof: n is world Z, u along the longest outline edge), stand (upstands,
kerb faces, hoppers: v is world Z) or run along an edge (trims and counter-flashings: n along
the edge). `frame_to_world` is `origin + u·U + v·V + d·N`; a frame without `v` is old
CladForge data and reads as world Z, which is what n × u gives for a wall.

## Exports

**IFC4X3.** One `IfcElementAssembly` per roof (`USERDEFINED`, "Warm roof system") in the
picked slab's storey. Membrane, insulation and VCL as `IfcCovering` (ROOFING, INSULATION,
MEMBRANE), the deck as `IfcPlate`, the firring zone as `IfcBuildingElementProxy` "Firring
zone" (one per facet), kerbs as `IfcMember`, trims, flashings, sleeves, hoppers and the
penetration cut as proxies. Anything on the falls is an `IfcFacetedBrep`; the rest is
`IfcExtrudedAreaSolid`. `Pset_Fallwright` on the assembly (falls, sump levels and rim
firrings, firring depths, buildup, membrane name) and on each piece (layer, facet). Schema
probing and stable GUIDs are CladForge's.

**DXF.** R12, left to right: falls plan, one section per cutting plane, one detail per edge
type and per hopper, and the notes and outlet schedule under the plan. Layers `ROOF_OUTLINE`,
`FACETS`, `DRAINS`, `OUTLETS`, `FALL_ARROWS`, `FIRRING_DEPTHS`, `SUMPS`, `LEVELS`,
`SECTION_LINES`, `STRUCTURE`, `FIRRINGS`, `DECK`, `VCL`, `INSULATION`, `MEMBRANE`,
`NO_OUTLET`, `DETAILS`, `DIMS`, `NOTES`.

**Scope limits, stated in the UI and both export headers.** Fallwright does not calculate
U-values or condensation risk (Parts L and C), does not check roof covering fire performance
(Part B), and does not design the deck, loads or wind uplift fixing. The falls scheme is
design intent; the firrings are cut on site to suit the joists from the depths given. Any
schedule is setting-out information, not a quantity take-off for pricing.

## Files

| File | What |
|---|---|
| `roof_constants.py` | Names, rules, defaults, parameter parsing, the prism and slab helpers |
| `roof_extract.py` | Picked faces to one roof region in its plan frame |
| `roof_edges.py` | Edge classification, walls, doors |
| `roof_falls.py` | Sumps and rims, the facets, creases, contours, spot levels, trapped water, edge levels |
| `roof_geometry.py` | The warm roof: layers, sumps, upstands, kerbs, trims, outlets |
| `roof_checks.py` | The checks table |
| `roof_preview.py` | One call for the page: geometry, falls, checks |
| `roof_dxf.py` | The DXF |
| `ifc_generator.py` | CladForge's IFC writer plus `roof_to_ifc` |
| `fabric_extract.py`, `cladding_*.py`, `dxf_generator.py` | CladForge's engine: the frame, the plane cut, the shared booleans |
| `static/viewer.js` | Three.js scene, import, picking, prism and slab rendering |
| `static/view2d.js` | The swing into plan view |
| `static/app.js` | State, extraction, parameters, preview, the plan overlay, downloads |
| `static/wizard.js` | The build wizard and placing outlets in plan |
| `static/plan2d.js` | Plan labels, typing over, dragging, cutting planes |

## Tests

```bash
python -m pytest -q                                              # engine and exports
python app.py &                                                  # then, in another shell:
VENDOR_DIR=... PLAYWRIGHT_MODULE=... node tests/smoke.js         # the browser, end to end
```

`pytest` runs the CladForge suite unchanged (the frame guard) and Fallwright's: the frame
round trip, every falls case in the requirements on synthetic roofs, edge classification on
the synthetic roof and on the sample IFC through the server importer, the checks, and both
exports (including the firring breps' volumes against the falls). The smoke test drives
Chromium through Playwright: loads the sample, picks the roof, builds it through the wizard
placing a hopper and an internal outlet in plan and a gutter edge, drags a sump, types over a
fall, exports the DXF through its cutting planes and the IFC through the WASM wheel, restarts
the engine, and re-imports on the server. `VENDOR_DIR` serves the CDN runtimes from local
copies (`<dir>/{pyodide,three,web-ifc,wasm-wheels,pypi}`) where the CDNs are unreachable.

## Debugging a slow or stuck roof

Extraction runs in Pyodide on the page's main thread, so while Python works the page is
frozen: nothing repaints and `fallwright()` cannot run. These are the ways in:

- **The console, live.** Python's output goes to the browser console a line at a time as it
  is written, prefixed `[fallwright:py]`. Each stage prints its time as it ends (`plane fit`,
  `plan union`, `wall records`, `wall feet`, `penetrations`, `cleanup`, `classify`, then
  `done in`). Every nearby wall, door and possible penetration prints as it is read, with
  its place in the loop, type, name, triangle count and milliseconds; one over 500 ms says
  `SLOW`. Open the console before picking: the last line printed names what did not finish.
- **The time limit.** Both loops over nearby elements stop after 20 s (`DEFAULT_BUDGET_S`
  in `roof_extract.py`; `options.budget_s` in the payload overrides it, and 0 is a real
  value). The roof comes back from what was read, with a warning such as "Ran out of time
  reading walls and doors after 80 of 127 nearby elements — 47 skipped". Check the edges
  next to anything skipped.
- **`fallwright()`**, in the console once the page is back, dumps the engine state, each
  roof with its warnings and `timing` (the stage times and the three slowest elements),
  the last payload, and the log, which includes the Python lines.
- **`fallwright.payload()`** downloads the last extraction payload as
  `fallwright-payload-<roof>-<time>.json`. It is kept before Python runs, so it is there
  even when Python never came back (reload the page, load the model, pick the roof again
  with the console open, and save it as soon as the page responds).
- **`python tests/replay.py <file>`** runs the saved payload through `extract_roof` in
  ordinary Python with the logging on, and prints the result, the stage times and the
  slowest elements. `--budget SECONDS` sets the time limit. This is where to profile a
  slow model, e.g. `python -m cProfile -s cumtime tests/replay.py <file>`.

Each wall is sliced once, at the probe height and at its foot, and moved with the region to
its local origin afterwards. It used to be sliced a second time in the final frame, which on
a roof with 127 walls round it was three quarters of the extraction.

## Limitations

- One roof region per build; a second level is a second roof.
- Joists are not read or modelled. The firring zone is cut on site to suit them, from the
  depths and contours given.
- Tapered insulation is not built yet; the falls surface is ready for it.
- Angle fillets, gutters (symbol only), overflows (flagged, not placed) and outlet capacity
  are not modelled or checked.
- Cutting planes run square to the roof frame only.
- Pyodide runs on the page's main thread, so a slow extraction freezes the page; the engine
  is bounded (`CONTEXT_TRI_BUDGET`, the 20 s time limit) and rebuilt if it dies
  (`restartEngine`). See **Debugging a slow or stuck roof**.

## Licence

MIT. Copyright 2026 Jake White Architecture. See `NOTICE` for the CladForge, StairSmith and
SunForm attribution.
