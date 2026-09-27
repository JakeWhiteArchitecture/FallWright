/* Fallwright app — roof state, Pyodide engine, live preview, the plan overlay, downloads. */

const state = { roofs: [], active: -1, pickMode: true, seq: 0, model: null, placing: null, cutting: null,
                dragging: false, selectedEdge: null };
let pyodide = null, pyReady = false, ifcReady = false, _seq = 0, _numTimer = null, _restarting = false;

// ─── ROOFS ───
// A roof is one level region picked on the top of the structure. It generates nothing
// until it is built through the wizard (static/wizard.js): picking is a selection.
function newRoof() {
    const r = { name: 'Roof ' + (++state.seq), picks: [], highlights: [], result: null, storey: null, built: false,
                edgeTypes: {}, outlets: [], nOutlets: 0, cutPlanes: null, error: null };
    state.roofs.push(r);
    setActive(state.roofs.length - 1);
    return r;
}

function deleteRoof() {
    if (state.active < 0) return;
    const r = state.roofs.splice(state.active, 1)[0];
    r.highlights.forEach(h => highlightGroup.remove(h));
    setActive(Math.min(state.active, state.roofs.length - 1));
}

function activeRoof() { return state.roofs[state.active] || null; }

function setActive(i) {
    state.active = i;
    const r = activeRoof();
    if (in2D() && r && r.result && r.result.ok) enter2D(r.name, r.result.frame, planBox(r));
    renderRoofList();
    updatePreview();
}

function renameRoof(i, value) {
    const r = state.roofs[i];
    r.name = value.trim() || r.name;
    if (r.result) r.result.name = r.name;
    renderRoofList();
    updatePreview();
}

function roofCard(r, i) {
    const res = r.result, ok = res && res.ok;
    const holes = ok ? res.holes.length : 0;
    const meta = ok ? `${Math.round(res.width)} × ${Math.round(res.height)} mm · ${(res.area / 1e6).toFixed(1)} m² · ${holes} hole${holes === 1 ? '' : 's'} · structure at ${Math.round(res.datum_z)}`
                    : `${r.picks.length} pick${r.picks.length === 1 ? '' : 's'}`;
    let warn = res && res.warnings && res.warnings.length ? `<div class="elev-warn">${res.warnings.join(' · ')}</div>` : '';
    if (r.error) warn = `<div class="elev-warn elev-error">✖ ${r.error} <button class="mini" onclick="event.stopPropagation(); runExtraction(state.roofs[${i}])">Retry</button></div>`;
    else if (!ok && r.picks.length) warn = `<div class="elev-warn">${r.pending ? 'Queued — waiting for the engine' : 'Extracting…'}</div>`;
    const status = ok ? (r.built ? ' · <b class="built">built</b>' : ' · <b class="chain-pending">not built — press Enter</b>') : '';
    return `<div class="elev-card ${i === state.active ? 'active' : ''} ${r.error ? 'error' : ''}" onclick="setActive(${i})">
        <div class="elev-head"><input class="elev-name" value="${r.name}" onclick="event.stopPropagation()" onchange="renameRoof(${i}, this.value)">
            <span class="elev-meta">${meta}${r.storey ? ' · ' + r.storey.name : ''}${status}</span></div>${warn}</div>`;
}

function renderRoofList() {
    updatePlanButton();
    const box = document.getElementById('roof-list');
    box.innerHTML = state.roofs.length ? state.roofs.map(roofCard).join('')
        : '<p class="hint">No roof yet. Load a model, then click the top of the roof structure.</p>';
    renderEdgeRows();
    renderOutletRows();
    updateBuildButton();
}

// ─── EDGES ───
// One row per outline edge: its type (detected, or set by hand), length and the range of
// finished levels along it. Clicking a row lights the edge in the model.
const EDGE_TYPE_OPTIONS = [['abutment', 'Abutment'], ['parapet', 'Parapet'], ['drip', 'Free edge: drip'],
                           ['gutter', 'Free edge: gutter'], ['check_kerb', 'Check kerb'], ['kerb', 'Kerb'],
                           ['penetration', 'Penetration']];

function edgeType(r, e) { return r.edgeTypes[e.id] || e.type; }

function liveEdges(r) {
    // The analysed edges (with their levels) when the roof is built, else the extracted ones.
    const falls = roofFalls(r);
    return (falls && falls.edges) || (r.result && r.result.edges) || [];
}

function renderEdgeRows() {
    const box = document.getElementById('edge-list');
    const r = activeRoof();
    if (!r || !r.result || !r.result.ok) { box.innerHTML = '<p class="hint">Edges appear once a roof is extracted.</p>'; return; }
    box.innerHTML = liveEdges(r).map(e => {
        const t = edgeType(r, e), hole = e.ring > 0;
        const opts = EDGE_TYPE_OPTIONS.filter(([v]) => hole ? ['kerb', 'penetration', 'abutment', 'parapet'].includes(v)
                                                            : !['kerb', 'penetration'].includes(v));
        const lv = e.level_min !== undefined && e.level_min !== null ? ` · +${Math.round(e.level_min)}–${Math.round(e.level_max)}` : '';
        const wall = e.wall ? ` · ${e.wall.name}, rises ${Math.round(e.wall.rise)}` : '';
        const doors = (e.doors || []).length ? ` · ${e.doors.length} door${e.doors.length > 1 ? 's' : ''}` : '';
        const changed = r.edgeTypes[e.id] && r.edgeTypes[e.id] !== e.detected ? ' (set)' : '';
        return `<div class="corner-row ${state.selectedEdge === e.id ? 'selected' : ''}" onclick="selectEdge('${e.id}')">
            <b>${e.id}</b> ${Math.round(e.length)} mm${lv}${wall}${doors}${changed}
            <select class="corner-detail" onclick="event.stopPropagation()" onchange="setEdgeType('${e.id}', this.value)">
            ${opts.map(([v, l]) => `<option value="${v}" ${v === t ? 'selected' : ''}>${l}</option>`).join('')}</select></div>`;
    }).join('');
}

function setEdgeType(id, type) {
    const r = activeRoof();
    if (!r) return;
    const e = r.result.edges.find(x => x.id === id);
    if (e && type === e.detected) delete r.edgeTypes[id]; else r.edgeTypes[id] = type;
    renderRoofList();
    updatePreview();
}

function selectEdge(id) {
    state.selectedEdge = state.selectedEdge === id ? null : id;
    renderEdgeRows();
    renderPlanOverlay();
}

// ─── OUTLETS ───
// The outlets list: number, type, edge, corner, offset and sump. Placed by clicks in plan
// (wizard.js), editable here; changing the number adds or removes from the end.
function renderOutletRows() {
    const box = document.getElementById('outlet-list');
    const r = activeRoof();
    const n = document.getElementById('n_outlets');
    if (!r || !r.result || !r.result.ok) { box.innerHTML = ''; n.value = 0; return; }
    n.value = r.nOutlets;
    const edges = r.result.edges.filter(e => e.ring === 0);
    const falls = roofFalls(r);
    box.innerHTML = r.outlets.map((o, k) => {
        const s = falls && falls.outlets[k] && falls.sumps.find(x => x.outlet === k);
        const sumpInfo = s && s.kind === 'sump' ? ` · rim +${Math.round(s.rim)} · floor +${Math.round(s.floor_low)} · drop ${Math.round(s.drop)}` : '';
        return `<div class="outlet-row">
            <b>O${k + 1}</b>
            <select onchange="setOutlet(${k}, 'type', this.value)"><option value="internal" ${o.type === 'internal' ? 'selected' : ''}>Internal</option>
                <option value="hopper" ${o.type === 'hopper' ? 'selected' : ''}>Hopper</option></select>
            <select onchange="setOutlet(${k}, 'edge', this.value)">${edges.map(e => `<option ${e.id === o.edge ? 'selected' : ''}>${e.id}</option>`).join('')}</select>
            <select title="Measured from" onchange="setOutlet(${k}, 'corner', this.value)"><option value="a" ${o.corner === 'a' ? 'selected' : ''}>from start</option>
                <option value="b" ${o.corner === 'b' ? 'selected' : ''}>from end</option></select>
            <input type="number" value="${Math.round(o.offset)}" step="10" title="Offset from the corner, mm" onchange="setOutlet(${k}, 'offset', parseFloat(this.value))">
            <label class="chk" title="Sump"><input type="checkbox" ${o.sump ? 'checked' : ''} onchange="setOutlet(${k}, 'sump', this.checked)"> sump</label>
            ${o.sump ? `<input type="number" value="${Math.round(o.sump_l)}" step="10" title="Sump length along the edge" onchange="setOutlet(${k}, 'sump_l', parseFloat(this.value))">
            × <input type="number" value="${Math.round(o.sump_w)}" step="10" title="Sump width out from the edge" onchange="setOutlet(${k}, 'sump_w', parseFloat(this.value))">` : ''}
            <span class="hint">${sumpInfo}</span></div>`;
    }).join('') + (r.outlets.length < r.nOutlets ? `<p class="hint">${r.nOutlets - r.outlets.length} still to place — <a href="#" onclick="startPlacing(activeRoof()); return false">place in plan</a></p>` : '');
}

function setOutlet(k, key, value) {
    const r = activeRoof();
    const o = r && r.outlets[k];
    if (!o) return;
    if (key === 'type' && value === 'hopper') {
        const e = r.result.edges.find(x => x.id === o.edge);
        const t = e && edgeType(r, e);
        if (t !== 'abutment' && t !== 'parapet') { setStatus('A hopper goes through a wall: abutment or parapet edges only', 'busy'); renderOutletRows(); return; }
        o.sump = true;
    }
    if (key === 'sump' && !value && o.type === 'hopper') { setStatus('Every hopper has a sump — the check will fail without one', 'busy'); }
    o[key] = value;
    if (key === 'edge' || key === 'corner') o.sump_t0 = null;     // the sump re-centres on the outlet
    renderOutletRows();
    updatePreview();
}

function setOutletCount(value) {
    const r = activeRoof();
    if (!r) return;
    r.nOutlets = Math.max(0, Math.min(50, parseInt(value, 10) || 0));
    if (r.outlets.length > r.nOutlets) r.outlets.length = r.nOutlets;
    renderOutletRows();
    updatePreview();
}

function newOutlet(edge, corner, offset, type, sump) {
    return { edge, corner, offset, type, sump: type === 'hopper' ? true : !!sump,
             sump_l: parseFloat(val('sump_l')) || 500, sump_w: parseFloat(val('sump_w')) || 300, sump_t0: null };
}

// ─── PICKING → EXTRACTION ───
function setPickMode(on) {
    state.pickMode = on;
    document.querySelectorAll('#pick-toggle .turn-btn').forEach(b => b.classList.toggle('active', (b.dataset.value === 'pick') === on));
}

function samePlane(hit, r) {
    if (!r.result || !r.result.ok) return r.picks.length === 0 || Math.abs(toIfc(hit.point)[2] - r.picks[0].point[2]) < 25;
    return Math.abs(toIfc(hit.point)[2] - r.result.datum_z) < 25;
}

function onViewportClick(event) {
    if (event.target !== renderer.domElement || !allMeshes.length || state._dragged || state.dragging) return;
    if (state.placing) { placingClick(event); return; }
    if (state.cutting) return;
    if (!state.pickMode) return;
    const hit = pickAt(event);
    if (!hit) return;
    const faces = coplanarFaces(hit.mesh, hit.faceIndex, 'roof', hit.normal);
    if (!faces) { setStatus('That face is not level and facing up — pick the top of the roof structure', 'busy'); return; }
    // A face on a built roof selects that roof; it is not picked again.
    const owner = state.roofs.find(r => r.result && r.result.ok && samePlane(hit, r));
    if (owner && owner.built) { setActive(state.roofs.indexOf(owner)); return; }
    let r = activeRoof();
    if (!r || r.built || !samePlane(hit, r)) r = owner || newRoof();
    const existing = r.picks.findIndex(p => p.mesh === hit.mesh && p.faces.includes(hit.faceIndex));
    if (existing >= 0) r.picks.splice(existing, 1);
    else r.picks.push({ mesh: hit.mesh, faces, normal: toIfc(hit.normal), point: toIfc(hit.point) });
    r.highlights.forEach(h => { highlightGroup.remove(h); h.geometry.dispose(); });
    r.highlights = r.picks.map(p => highlightFaces(p.mesh, p.faces));
    if (!r.storey) { const meta = meshMeta[hit.mesh.userData.index]; r.storey = meta && meta.storey ? meta.storey : null; }
    setActive(state.roofs.indexOf(r));
    runExtraction(r);
}

async function runExtraction(r) {
    if (!r.picks.length) { r.result = null; renderRoofList(); updatePreview(); return; }
    if (!pyReady) {   // engine loading or rebuilding: queue it, initPyodide picks it up
        r.pending = true;
        setStatus('Waiting for the engine — ' + r.name + ' is queued', 'busy');
        renderRoofList();
        return;
    }
    // One attempt per roof at a time; a call mid-flight runs again once this one ends.
    if (r._running) { r._again = true; return; }
    r._running = true;
    r.pending = false;
    try { await extractOnce(r); } finally { r._running = false; }
    if (r._again) { r._again = false; return runExtraction(r); }
}

async function extractOnce(r) {
    const tris = [];
    for (const p of r.picks) tris.push(...faceTriangles(p.mesh, p.faces));
    const { elements: context, dropped, triangles: nTris } = contextForRoof(new Set(r.picks.map(p => p.mesh)), tris);
    setStatus(`Extracting ${r.name}… ${tris.length} faces, ${context.length} nearby elements (${nTris} triangles)`, 'busy');
    r.error = null;
    renderRoofList();
    await new Promise(res => setTimeout(res, 30));   // let the status paint before Pyodide blocks the thread
    // debug: the engine prints every stage and nearby element as it goes (to the console,
    // see initPyodide), and gives up on nearby elements past its time limit (budget_s).
    const payload = { name: r.name, faces: tris, outward: r.picks[0].normal, context,
                      seeds: r.picks.map(p => p.point).filter(Boolean),
                      options: { penetrations: document.getElementById('penetrations').checked, debug: true } };
    const json = JSON.stringify(payload);
    // Kept before Python runs, so a roof that never comes back can still be saved with
    // fallwright.payload() and replayed offline with tests/replay.py.
    state.lastPayload = { roof: r.name, at: new Date(), json };
    log(`${r.name}: extracting — ${tris.length} faces, ${context.length} nearby elements, ${nTris} triangles, `
        + `${dropped} dropped, payload ${Math.round(json.length / 1024)} kB`);
    try {
        const t0 = performance.now();
        pyodide.globals.set('_payload_json', json);
        const out = await pyodide.runPythonAsync(`
import json as _json
from roof_extract import extract_roof as _ex
_json.dumps(_ex(_json.loads(_payload_json)))`);
        r.result = JSON.parse(out);
        r.timing = r.result.timing || null;        // kept even if the extraction failed
        const slow = ((r.timing || {}).slowest || []).map(t => `${t.type} ${t.name} ${Math.round(t.ms)} ms`);
        log(`${r.name}: ${r.result.ok ? 'ok' : 'FAILED'} in ${Math.round(performance.now() - t0)} ms`
            + (slow.length ? ` · slowest: ${slow.join(', ')}` : '')
            + (r.result.warnings || []).map(w => ' · ' + w).join(''));
        if (r.result.ok) {
            if (dropped) r.result.warnings.push(`${dropped} nearby element(s) left out to keep the engine within memory`);
            const kinds = {};
            r.result.edges.filter(e => e.ring === 0).forEach(e => kinds[e.type] = (kinds[e.type] || 0) + 1);
            setStatus(`${r.name} extracted: ` + Object.entries(kinds).map(([k, n]) => `${n} ${k}`).join(', ')
                      + (r.built ? '' : ' — press Enter to build'), 'ready');
        } else {
            r.error = r.result.warnings.join('; ') || 'Extraction failed';
            r.result = null;
            setStatus(r.name + ': ' + r.error, 'busy');
        }
    } catch (err) {
        console.error(err);
        // A C++ abort inside WASM kills the runtime for good; rebuild it once and retry.
        if (/fatally failed|Aborted/i.test(err.message || '') && !r._restarted) {
            r._restarted = true;
            r.pending = true;
            restartEngine();
            return;
        }
        r.result = null; r.error = 'Extraction error: ' + err.message;
        setStatus(r.name + ': ' + r.error, 'busy');
    }
    renderRoofList();
    updatePreview();
}

// ─── PARAMETERS ───
const NUM = ['fall', 'cricket_fall', 'sump_fall', 'd_min', 'deck_t', 'vcl_t', 'insulation_t', 'membrane_t', 'upstand',
             'sump_ins', 'sump_drop', 'sump_firring', 'sump_l', 'sump_w', 'ins_upstand_t', 'kerb_w', 'check_kerb_h'];
function val(id) { return document.getElementById(id).value; }
function toggleValue(id) { const b = document.querySelector('#' + id + ' .turn-btn.active'); return b ? b.dataset.value : null; }
function selectToggle(id, value) {
    document.querySelectorAll('#' + id + ' .turn-btn').forEach(b => b.classList.toggle('active', b.dataset.value === value));
}

function roofRecord(r) {
    return Object.assign({}, r.result, { name: r.name, storey: r.storey, edge_types: r.edgeTypes,
                                         outlets: r.outlets, n_outlets: r.nOutlets, cut_planes: r.cutPlanes });
}

function getParams(onlyBuilt = true) {
    const roofs = state.roofs.filter(r => r.result && r.result.ok && (r.built || !onlyBuilt)).map(roofRecord);
    const p = { roofs, context: Object.assign({ offset: modelOffset }, modelContext),
                membrane_name: val('membrane_name') };
    for (const k of NUM) p[k] = parseFloat(val(k));
    return p;
}

function onNumeric() {   // 300 ms debounce on typed numbers
    if (_numTimer) clearTimeout(_numTimer);
    _numTimer = setTimeout(() => updatePreview(), 300);
}

// ─── PREVIEW ───
function roofFalls(r) {
    const prev = window._lastPreview;
    const got = prev && prev.roofs.find(x => x.name === r.name);
    return got ? got.falls : null;
}

async function updatePreview() {
    const params = getParams();
    renderOutlines(state.roofs.filter(r => r.result && r.result.ok).map(r => r.result));
    if (!pyReady || !params.roofs.length) {
        window._lastPreview = null;
        renderGeometry([]); renderChecks([]); renderInfo(); renderPlanOverlay();
        return;
    }
    const seq = ++_seq;
    try {
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const out = await pyodide.runPythonAsync(`
import json as _json
from roof_preview import generate as _gen
_json.dumps(_gen(_json.loads(_params_json)))`);
        if (seq !== _seq) return;
        const result = JSON.parse(out);
        window._lastPreview = result;
        renderGeometry(result.geometry);
        renderChecks(result.checks);
        renderInfo();
        renderEdgeRows();
        renderOutletRows();
        renderPlanOverlay();
    } catch (err) {
        console.error('preview failed', err);
        if (/fatally failed|Aborted/i.test(err.message || '')) {
            if (await restartEngine()) return updatePreview();
        }
        setStatus('Preview error: ' + err.message, 'busy');
    }
}

// The drag path: only the facets, once per animation frame. Layers rebuild on release.
let _dragFrame = null;
function previewFacets() {
    if (_dragFrame) return;
    _dragFrame = requestAnimationFrame(async () => {
        _dragFrame = null;
        const r = activeRoof();
        if (!pyReady || !r) return;
        try {
            pyodide.globals.set('_params_json', JSON.stringify(getParams()));
            pyodide.globals.set('_roof_name', r.name);
            const out = await pyodide.runPythonAsync(`
import json as _json
from roof_preview import falls_only as _fo
_json.dumps(_fo(_json.loads(_params_json), _roof_name))`);
            const falls = JSON.parse(out);
            if (falls && window._lastPreview) {
                const got = window._lastPreview.roofs.find(x => x.name === r.name);
                if (got) Object.assign(got.falls, falls, { contours: [], spots: [] });
                renderPlanOverlay();
            }
        } catch (err) { console.warn('drag preview failed', err); }
    });
}

function renderChecks(checks) {
    const box = document.getElementById('checks-container');
    box.innerHTML = checks.length ? '' : '<p class="hint">Checks appear once a roof is built.</p>';
    for (const c of checks) {
        const div = document.createElement('div');
        div.className = 'check-item';
        div.innerHTML = `<div class="check-dot ${c.status}"></div><span><b>${c.name}.</b> ${c.message.replace(/^[^:]+: /, '')}</span>`;
        box.appendChild(div);
    }
}

function renderInfo() {
    const r = activeRoof(), falls = r && roofFalls(r);
    const set = (id, v) => document.getElementById(id).textContent = v;
    if (!r || !r.result || !r.result.ok) { ['dim-size', 'dim-facets', 'dim-depth', 'dim-outlets', 'dim-falls'].forEach(id => set(id, '--')); return; }
    set('dim-size', `${Math.round(r.result.width)} × ${Math.round(r.result.height)} mm · ${(r.result.area / 1e6).toFixed(1)} m²`);
    set('dim-facets', falls ? `${falls.facets.length}` : 'not built');
    set('dim-depth', falls && falls.min_depth !== null ? `${Math.round(falls.min_depth)}–${Math.round(falls.max_depth)} mm` : '--');
    set('dim-outlets', `${r.outlets.length} of ${r.nOutlets}` + (falls ? ` · ${falls.sumps.filter(s => s.kind === 'gutter').length} gutter edge(s)` : ''));
    set('dim-falls', `main 1:${val('fall')} · cricket 1:${val('cricket_fall')}`);
}

function setStatus(text, cls) {
    const chip = document.getElementById('status-chip');
    chip.textContent = text; chip.className = 'status-chip ' + (cls || '');
}

// ─── PLAN VIEW ───
// The region {u0, u1, v0, v1} of a roof in its own frame, looked at from above the finished
// surface.
function planBox(r) {
    return { u0: 0, u1: r.result.width, v0: 0, v1: r.result.height, d: 250 };
}

function canPlan() { const r = activeRoof(); return !!(r && r.result && r.result.ok); }

function togglePlan(force) {
    if (in2D() && force !== true) { exit2D(); updatePlanButton(); renderPlanLabels(); return; }
    if (!canPlan()) { setStatus('Pick and extract a roof first — the plan view looks down on the active one', 'busy'); return; }
    const r = activeRoof();
    if (!in2D()) enter2D(r.name, r.result.frame, planBox(r));
    updatePlanButton();
    renderPlanLabels();
    if (force !== true) setStatus('Plan view: click a label to type over it, drag a sump or outlet · E or Esc for 3D', 'ready');
}

function updatePlanButton() {
    const b = document.getElementById('view-2d');
    if (!b) return;
    b.textContent = in2D() ? '3D' : 'Plan view';
    b.disabled = !in2D() && !canPlan();
    b.title = in2D() ? 'Back to the 3D view (Esc)' : 'Look straight down on the active roof (E)';
}

// ─── THE PLAN OVERLAY ───
// Drawn in 3D on the finished surface: facet boundaries (valleys blue, hips and ridges
// amber), fall arrows, depth contours, sumps, outlets, drain edges, the area no outlet
// reaches (red), the facets that pond, the selected edge and the cutting planes.
const OVERLAY_LIFT = 3;    // mm above the membrane, so the lines sit on top of it

function overlayGroup(name) {
    const g = new THREE.Group();
    g.name = name;
    g.visible = layerVisible[name] !== false;
    planGroup.add(g);
    return g;
}

function planLine(frame, pts, z, color, group, dashed) {
    const P = pts.map(q => frameWorld(frame, q[0], q[1], (q.length > 2 ? q[2] : z) + OVERLAY_LIFT));
    const geo = new THREE.BufferGeometry().setFromPoints(P);
    const mat = dashed ? new THREE.LineDashedMaterial({ color, dashSize: 120, gapSize: 80, depthTest: false })
                       : new THREE.LineBasicMaterial({ color, depthTest: false });
    const line = new THREE.Line(geo, mat);
    if (dashed) line.computeLineDistances();
    line.renderOrder = 5;
    group.add(line);
    return line;
}

function renderPlanOverlay() {
    clearGroup(planGroup);
    const r = activeRoof();
    const falls = r && roofFalls(r);
    const params = getParams();
    const above = params.deck_t + params.vcl_t + params.insulation_t + params.membrane_t;
    const z = (pl, x, y) => pl[0] + pl[1] * x + pl[2] * y + above;
    if (r && r.result && r.result.ok) {
        const f = r.result.frame;
        const facetsG = overlayGroup('facets'), arrowsG = overlayGroup('arrows'), contoursG = overlayGroup('contours');
        const outletsG = overlayGroup('outlets');
        if (falls) {
            for (const c of falls.creases) {
                const fa = falls.facets.find(x => x.id === c.facets[0]);
                if (!fa) continue;
                const color = { valley: 0x4ea8ff, hip: 0xffb347, ridge: 0xffd166 }[c.kind] || 0x9aa7b4;
                planLine(f, [[c.a[0], c.a[1], z(fa.plane, ...c.a)], [c.b[0], c.b[1], z(fa.plane, ...c.b)]], 0, color, facetsG, c.kind !== 'valley');
            }
            for (const fc of falls.facets) {
                const ring = fc.ring.concat([fc.ring[0]]).map(q => [q[0], q[1], z(fc.plane, q[0], q[1])]);
                if ((falls.ponding || []).includes(fc.id)) planLine(f, ring, 0, 0xff4d4d, facetsG);
                if (!fc.fall) continue;
                const L = Math.min(900, Math.sqrt(fc.area) * 0.35), [dx, dy] = fc.dir, [cx, cy] = fc.at;
                const tail = [cx - dx * L / 2, cy - dy * L / 2], tip = [cx + dx * L / 2, cy + dy * L / 2];
                const w = L * 0.18;
                const head1 = [tip[0] - dx * w - dy * w * 0.6, tip[1] - dy * w + dx * w * 0.6];
                const head2 = [tip[0] - dx * w + dy * w * 0.6, tip[1] - dy * w - dx * w * 0.6];
                const Z = q => [q[0], q[1], z(fc.plane, q[0], q[1])];
                planLine(f, [Z(tail), Z(tip), Z(head1)], 0, 0x7bd88f, arrowsG);
                planLine(f, [Z(tip), Z(head2)], 0, 0x7bd88f, arrowsG);
            }
            for (const lv of falls.contours || []) {
                for (const ln of lv.lines) planLine(f, ln.map(q => [q[0], q[1], lv.level + above]), 0, 0x5d6b7a, contoursG);
            }
            for (const s of falls.sumps) {
                if (!s.rect) continue;
                planLine(f, s.rect.concat([s.rect[0]]), s.floor_low, 0x1d9bf0, outletsG);
            }
            for (const u of falls.unreached || []) {
                planLine(f, u.concat([u[0]]), above + params.d_min, 0xff4d4d, facetsG);
            }
        }
        // Drain edges heavy, the selected edge lit.
        const edges = liveEdges(r);
        for (const e of edges) {
            const t = edgeType(r, e);
            const zs = e.profile && e.profile.length ? Math.max(...e.profile.map(q => q[1])) : above;
            const isDrain = t === 'gutter' || r.outlets.some(o => o.edge === e.id);
            if (isDrain) planLine(f, [e.a, e.b], zs, 0x1d9bf0, outletsG);
            if (state.selectedEdge === e.id) planLine(f, [e.a, e.b], zs + 20, 0xffd166, facetsG);
        }
        for (const [k, o] of r.outlets.entries()) {
            const got = falls && falls.outlets[k];
            if (!got || !got.point) continue;
            const [x, y] = got.point, rad = 60;
            const circle = Array.from({ length: 25 }, (_, i) => [x + rad * Math.cos(i / 24 * 2 * Math.PI), y + rad * Math.sin(i / 24 * 2 * Math.PI)]);
            planLine(f, circle, above, o.type === 'hopper' ? 0xff9f1c : 0x1d9bf0, outletsG);
        }
        if (state.cutting && state.cutting.roof === r) {
            const g = overlayGroup('cuts');
            for (const cp of r.cutPlanes || []) {
                const [a, b] = cutLine(r, cp);
                planLine(f, [a, b], above + 30, 0xff4d4d, g, true);
            }
        }
    }
    filter2D();
    renderPlanLabels();
}

function cutLine(r, cp) {
    const W = r.result.width, H = r.result.height;
    return cp.dir === 'u' ? [[-500, cp.pos], [W + 500, cp.pos]] : [[cp.pos, -500], [cp.pos, H + 500]];
}

// ─── MODEL LOADING ───
async function onFileChosen(input) {
    if (!input.files.length) return;
    await loadModel(input.files[0]);
}

function retryOnServer() { if (state.file) loadModel(state.file, 'server'); }

async function loadModel(file, force) {
    const info = document.getElementById('model-info');
    state.file = file;
    try {
        state.roofs.forEach(r => r.highlights.forEach(h => highlightGroup.remove(h)));
        state.roofs = []; state.active = -1; state.seq = 0; renderRoofList();
        const summary = await loadIFC(file, t => setStatus(t, 'busy'), force);
        state.model = summary;
        const notes = (summary.warnings || []).filter(Boolean);
        info.innerHTML = `<b>${file.name}</b><br>${summary.meshes} elements · ${summary.storeys} storeys · `
            + `${summary.context.project || 'unnamed project'}<br><span class="hint">Read by ${summary.reader}.</span>`
            + (notes.length ? `<div class="elev-warn">${notes.join('<br>')}</div>` : '')
            + (summary.reader.indexOf('server') < 0
                ? `<button class="btn btn-secondary btn-sm" style="margin-top:6px" onclick="retryOnServer()">Re-import on the server</button>` : '');
        setStatus(pyReady ? 'Ready — click the top of the roof structure' : 'Model loaded, engine still loading…', pyReady ? 'ready' : 'busy');
        updatePreview();
    } catch (err) {
        console.error(err);
        info.innerHTML = `<div class="elev-warn elev-error">Load failed: ${err.message}</div>`
            + `<button class="btn btn-secondary btn-sm" style="margin-top:6px" onclick="retryOnServer()">Try the server importer</button>`;
        setStatus('Load failed', 'busy');
    }
}

// ─── DOWNLOADS ───
let _pendingBlob = null, _pendingExt = '';
function downloadName(ext) {
    const d = new Date(), stamp = [String(d.getDate()).padStart(2, '0'), String(d.getMonth() + 1).padStart(2, '0'), String(d.getFullYear()).slice(-2)].join('-');
    const key = 'fallwright_dl_' + ext + '_' + stamp;
    let n = 1;
    try { n = parseInt(localStorage.getItem(key) || '0', 10) + 1; localStorage.setItem(key, String(n)); } catch (e) { /* private mode */ }
    return `Fallwright_${stamp}_${n}.${ext}`;
}
function showReminder(blob, ext) { _pendingBlob = blob; _pendingExt = ext; document.getElementById('download-reminder').classList.add('open'); }
function confirmDownload() {
    document.getElementById('download-reminder').classList.remove('open');
    if (!_pendingBlob) return;
    const url = URL.createObjectURL(_pendingBlob), a = document.createElement('a');
    a.href = url; a.download = downloadName(_pendingExt); document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url); _pendingBlob = null;
}

function busy(btn, on, label) {
    btn.disabled = on; btn.classList.toggle('btn-generating', on);
    btn.innerHTML = on ? '<span class="btn__text">' + label + '</span>' : btn.dataset.label;
}

async function downloadIFC() {
    const params = getParams();
    if (!params.roofs.length) { alert('Pick a roof and build it first (press Enter).'); return; }
    const btn = document.getElementById('ifc-btn');
    busy(btn, true, 'Generating IFC4X3…');
    try {
        await ensureIfcOpenShell(btn);
        pyodide.globals.set('_params_json', JSON.stringify(params));
        const proxy = await pyodide.runPythonAsync(`
import json as _json, os as _os
from roof_constants import _parse as _rp
from roof_preview import build_all as _ba
from ifc_generator import roof_to_ifc as _to_ifc
_p = _json.loads(_params_json)
_m, _b, _c = _ba(_rp(_p))
_path = _to_ifc(_m, _p, _b)
with open(_path, 'rb') as _f:
    _data = _f.read()
_os.unlink(_path)
_data`);
        const blob = new Blob([proxy.toJs()], { type: 'application/x-step' });
        if (proxy.destroy) proxy.destroy();
        showReminder(blob, 'ifc');
    } catch (err) { alert('IFC export failed: ' + err.message); }
    finally { busy(btn, false); }
}

async function ensureIfcOpenShell(btn) {
    if (ifcReady) return;
    busy(btn, true, 'Installing IfcOpenShell (one-off, ~40 MB)…');
    await pyodide.loadPackage(['numpy']);
    await pyodide.runPythonAsync(`
import micropip
await micropip.install(["lark", "isodate", "python-dateutil", "typing_extensions"])
await micropip.install("https://ifcopenshell.github.io/wasm-wheels/ifcopenshell-0.8.5-cp313-cp313-pyodide_2025_0_wasm32.whl")
import ifc_generator`);
    ifcReady = true;
}

// Export DXF goes through the cutting planes first (dims2d.js): the plan view shows them,
// they can be dragged, flipped, added or removed, and Export writes the file.
function downloadDXF() {
    if (!pyReady) { alert('The engine is still loading.'); return; }
    if (!getParams().roofs.length) { alert('Pick a roof and build it first (press Enter).'); return; }
    startCutting(activeRoof() && activeRoof().built ? activeRoof() : state.roofs.find(r => r.built));
}

async function writeDXF() {
    const btn = document.getElementById('dxf-btn');
    busy(btn, true, 'Generating DXF…');
    try {
        pyodide.globals.set('_params_json', JSON.stringify(getParams()));
        const dxf = await pyodide.runPythonAsync(`
import json as _json
from roof_dxf import roof_to_dxf_string as _to_dxf
from roof_preview import build_all as _ba
from roof_constants import _parse as _rp
_p = _json.loads(_params_json)
_m, _b, _c = _ba(_rp(_p))
_to_dxf(_p, _b, _c)`);
        showReminder(new Blob([dxf], { type: 'application/dxf' }), 'dxf');
    } catch (err) { alert('DXF export failed: ' + err.message); }
    finally { busy(btn, false); }
}

// ─── DIAGNOSTICS ───
// The engine runs in a WASM runtime that can die outright, and on the page's main thread,
// so while Python works nothing here can run. What it prints reaches the console as it is
// written ([fallwright:py] lines, see initPyodide), which is the live view; the log keeps
// the record, and fallwright() dumps it with the state once the page is back.
const LOG = [];
function log(line) {
    remember(line);
    console.log('[fallwright]', line);
}

function remember(line) {
    LOG.push(new Date().toISOString().slice(11, 23) + '  ' + line);
    if (LOG.length > 500) LOG.shift();
}

// Python's stdout, a line at a time, straight to the console while Python is still running.
function pyLine(line) {
    console.log('[fallwright:py] ' + line);
    remember('py  ' + line);
}

function fallwright() {
    const dump = {
        engine: { pyReady, ifcReady, restarting: _restarting, alive: !!pyodide },
        model: state.model && { file: state.file && state.file.name, meshes: state.model.meshes,
                                storeys: state.model.storeys, reader: state.model.reader },
        roofs: state.roofs.map(r => ({
            name: r.name, built: r.built, picks: r.picks.length, outlets: r.outlets.length, n_outlets: r.nOutlets,
            state: r.result ? 'extracted' : (r.error ? 'error' : (r.pending ? 'queued' : (r._running ? 'running' : 'idle'))),
            error: r.error || null, warnings: (r.result && r.result.warnings) || [],
            timing: r.timing || null,          // stage times and the three slowest elements
        })),
        last_payload: state.lastPayload
            ? { roof: state.lastPayload.roof, at: state.lastPayload.at.toISOString(),
                kB: Math.round(state.lastPayload.json.length / 1024),
                save: 'fallwright.payload() downloads it; python tests/replay.py <file> replays it' }
            : null,
        log: LOG,
    };
    console.log(JSON.stringify(dump, null, 2));
    return dump;
}
window.fallwright = fallwright;

// The last extraction payload as a file, for replaying a slow or stuck roof offline with
// tests/replay.py. It is kept before Python runs, so it is there even if Python never
// came back.
fallwright.payload = function () {
    const last = state.lastPayload;
    if (!last) { console.warn('[fallwright] no extraction has run yet: pick a roof first'); return null; }
    const stamp = last.at.toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const name = `fallwright-payload-${last.roof.replace(/[^\w-]+/g, '-')}-${stamp}.json`;
    const url = URL.createObjectURL(new Blob([last.json], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url; a.download = name; document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
    console.log('[fallwright] saved ' + name + ' — replay it with: python tests/replay.py ' + name);
    return name;
};

// ─── PYODIDE ───
async function restartEngine() {
    if (_restarting) return false;          // one rebuild at a time
    _restarting = true;
    log('engine died — rebuilding');
    setStatus('Engine stopped — rebuilding it…', 'busy');
    pyReady = false; ifcReady = false; pyodide = null; window.pyodide = null;
    try { await initPyodide(); } catch (err) { console.error('engine restart failed', err); }
    _restarting = false;
    return pyReady;
}

const PY_MODULES = ['cladding_constants', 'cladding_primitives', 'cladding_booleans', 'cladding_geometry',
                    'cladding_checks', 'cladding_preview', 'fabric_extract', 'ifc_generator',
                    'roof_constants', 'roof_edges', 'roof_extract', 'roof_falls', 'roof_checks',
                    'roof_geometry', 'roof_preview', 'roof_dxf'];

async function initPyodide() {
    try {
        setStatus('Loading Python runtime…', 'busy');
        pyodide = await loadPyodide();
        window.pyodide = pyodide;
        // Python's prints go to the console a line at a time as they are written, not when
        // the call returns, so a long extraction shows where it is while it runs.
        pyodide.setStdout({ batched: pyLine });
        pyodide.setStderr({ batched: line => { console.warn('[fallwright:py] ' + line); remember('py! ' + line); } });
        setStatus('Loading Shapely…', 'busy');
        await pyodide.loadPackage(['shapely', 'micropip']);
        const v = Date.now();
        for (const mod of PY_MODULES) {
            const src = await (await fetch(mod + '.py?v=' + v)).text();
            pyodide.FS.writeFile('/home/pyodide/' + mod + '.py', src);
        }
        await pyodide.runPythonAsync(`
import sys
sys.path.insert(0, '/home/pyodide')
import roof_preview, roof_extract, roof_dxf`);
        pyReady = true;
        log('engine ready');
        setStatus(allMeshes.length ? 'Ready — click the top of the roof structure' : 'Ready — load an IFC', 'ready');
        const queued = state.roofs.filter(r => r.picks.length && !r.result);
        for (const r of queued) runExtraction(r);
        updatePreview();
    } catch (err) { console.error(err); setStatus('Engine failed to load: ' + err.message, 'busy'); }
}

// ─── INIT ───
function initApp() {
    initThree();
    initWebIfc();
    initPyodide();
    initWizard();
    initPlan2D();
    renderRoofList();
    const vp = document.getElementById('viewport');
    let down = null;
    vp.addEventListener('pointerdown', e => { down = [e.clientX, e.clientY]; state._dragged = false; });
    vp.addEventListener('pointermove', e => { if (down && Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) state._dragged = true; });
    vp.addEventListener('pointermove', e => { if (state.placing) placingHover(e); });
    vp.addEventListener('click', onViewportClick);
    const drop = document.getElementById('file-drop');
    drop.addEventListener('dragover', e => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', e => { e.preventDefault(); drop.classList.remove('over'); if (e.dataTransfer.files.length) loadModel(e.dataTransfer.files[0]); });
    document.addEventListener('keydown', e => {
        const el = document.activeElement, typing = el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
        const modal = ['roof-wizard', 'download-reminder'].some(id => document.getElementById(id).classList.contains('open'));
        if ((e.key === 'e' || e.key === 'E') && !typing && !modal && !e.ctrlKey && !e.metaKey && !e.altKey
            && !state.placing && !state.cutting) { togglePlan(); return; }
        if (e.key !== 'Escape' || modal) return;
        if (D2.editing) { closeDimEditor(); return; }
        if (state.placing) { stopPlacing(); return; }
        if (state.cutting) { stopCutting(); return; }
        if (in2D()) { exit2D(); updatePlanButton(); renderPlanLabels(); }
    });
    document.getElementById('download-reminder').addEventListener('click', e => { if (e.target.id === 'download-reminder') e.currentTarget.classList.remove('open'); });
}
