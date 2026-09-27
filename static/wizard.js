/* Fallwright build wizard.
   Picking a roof generates nothing. The roof is built only when asked: the button top
   right of the viewport, or Enter, opens this wizard. The number of outlets comes first;
   placing them and marking gutter edges happen by clicks in plan, with the dialog out of
   the way; then the sumps, falls and buildup. Every answer is written into the side
   panel's own control, so the panel edits the roof live afterwards. */

const WIZ = { step: 0, draft: null, roof: null };

// Number fields per step, mirroring the side panel: [id, label, unit].
const WIZ_GROUPS = {
    sumps: [['sump_ins', 'Sump insulation', 'mm, 50 minimum'], ['sump_drop', 'Minimum drop to the rim', 'mm, 75 minimum'],
            ['sump_firring', 'Sump firrings', 'mm, 0 = none']],
    falls: [['fall', 'Main fall 1:', 'default 40'], ['cricket_fall', 'Cricket fall 1:', 'default 40'],
            ['d_min', 'Minimum firring', 'mm, 25']],
    deck: [['deck_t', 'Deck', 'mm WBP plywood']],
    vcl: [['vcl_t', 'VCL', 'mm']],
    insulation: [['insulation_t', 'Insulation', 'mm, from the U-value calculation']],
    membrane: [['membrane_t', 'Membrane', 'mm']],
    upstand: [['upstand', 'Upstand height', 'mm above the finished surface']],
};
const WIZ_STEPS = ['count', 'place', 'gutters', 'sumps', 'falls', 'deck', 'vcl', 'insulation', 'membrane', 'upstand'];
const WIZ_TITLES = { count: 'How many outlets?', place: 'Place the outlets', gutters: 'Gutter edges', sumps: 'Sumps',
                     falls: 'Falls', deck: 'Deck', vcl: 'Vapour control layer', insulation: 'Insulation',
                     membrane: 'Membrane', upstand: 'Upstands' };
const WIZ_NOTES = {
    count: 'Outlets sit on the roof outline. Next you click a corner, the edge to run along, and type the offset from that corner. Eaves gutters are not outlets: you mark those edges after.',
    sumps: 'A sump is a recess against the edge, 500 along by 300 out by default. It is the datum: its floor is the lowest level on the roof, and the rim, the drop above it, is the line the falls run up from. A sump larger than standard goes to falls.',
    falls: 'Design to 1:40 so the finished roof still makes 1:80 after deflection (BS 6229). A valley falls at 1:sqrt(G² + Gc²), 1:57 with both at 1:40, and the checks warn about it.',
    deck: 'Firrings to falls go under the deck; everything above is constant thickness.',
    vcl: 'Turned up at abutments and parapets to the top of the insulation.',
    insulation: 'Constant thickness, from a U-value calculation done elsewhere: Fallwright does not calculate U-values.',
    membrane: 'One layer, printed in the DXF notes and the IFC property set. No manufacturer data is built in.',
    upstand: 'Upstands go to this height above the finished surface at the highest point of each edge; the top is level.',
};

function roofReady(r) { return !!(r && r.result && r.result.ok); }

function updateBuildButton() {
    const btn = document.getElementById('make-roof');
    if (!btn) return;
    const r = activeRoof();
    btn.style.display = roofReady(r) && !r.built && !state.placing ? '' : 'none';
    btn.textContent = `Build ${r ? r.name : 'roof'}  ⏎`;
}

function openWizard() {
    const r = activeRoof();
    if (!roofReady(r) || state.placing || state.cutting) return;
    WIZ.roof = r;
    WIZ.draft = { count: r.nOutlets || Math.max(1, r.outlets.length || 2), dims: {}, membrane_name: val('membrane_name') };
    for (const g of Object.keys(WIZ_GROUPS)) for (const [id] of WIZ_GROUPS[g]) WIZ.draft.dims[id] = val(id);
    WIZ.step = 0;
    document.getElementById('roof-wizard').classList.add('open');
    renderWizard();
}

function closeWizard() {
    document.getElementById('roof-wizard').classList.remove('open');
    WIZ.draft = null;
}

function wizFields(group) {
    return '<div class="wiz-dims">' + WIZ_GROUPS[group].map(([fid, label, unit]) => {
        const src = document.getElementById(fid);
        return `<div class="field"><label>${label} <span class="unit">${unit}</span></label>
            <input type="number" id="wiz-${fid}" value="${WIZ.draft.dims[fid]}" min="${src.min}" max="${src.max}"
                   step="${src.step || 1}" oninput="WIZ.draft.dims['${fid}'] = this.value"></div>`;
    }).join('') + '</div>';
}

function wizBody(id) {
    if (id === 'count') {
        return `<div class="field"><label>Number of outlets <span class="unit">internal outlets and hoppers</span></label>
            <input type="number" id="wiz-count" min="0" max="50" value="${WIZ.draft.count}" oninput="WIZ.draft.count = this.value"></div>
            <p class="hint">${WIZ_NOTES.count}</p>`;
    }
    if (id === 'membrane') {
        return `<div class="field"><label>Membrane name <span class="unit">free text</span></label>
            <input type="text" id="wiz-membrane_name" value="${WIZ.draft.membrane_name}" oninput="WIZ.draft.membrane_name = this.value"></div>`
            + wizFields('membrane') + `<p class="hint">${WIZ_NOTES.membrane}</p>`;
    }
    return wizFields(id) + `<p class="hint">${WIZ_NOTES[id] || ''}</p>`;
}

function renderWizard() {
    const id = WIZ_STEPS[WIZ.step];
    document.getElementById('wiz-title').textContent = WIZ_TITLES[id];
    document.getElementById('wiz-step').textContent = `Step ${WIZ.step + 1} of ${WIZ_STEPS.length}`;
    document.getElementById('wiz-chain').textContent = 'Building ' + WIZ.roof.name;
    document.getElementById('wiz-body').innerHTML = wizBody(id);
    document.getElementById('wiz-back').style.visibility = WIZ.step ? '' : 'hidden';
    document.getElementById('wiz-next').textContent = WIZ.step === WIZ_STEPS.length - 1 ? 'Build' : 'Next';
    const first = document.querySelector('#wiz-body input');
    if (first) { first.focus(); first.select && first.select(); }
}

function wizBack() {
    if (!WIZ.step) return;
    if (WIZ_STEPS[WIZ.step] === 'sumps') {         // back into plan, to the gutter edges
        document.getElementById('roof-wizard').classList.remove('open');
        startPlacing(WIZ.roof, wizResume);
        return;
    }
    WIZ.step--;
    renderWizard();
}

// Placing is done: back to the dialog for the sumps, falls and buildup.
function wizResume() {
    WIZ.step = WIZ_STEPS.indexOf('sumps');
    document.getElementById('roof-wizard').classList.add('open');
    renderWizard();
}

function wizNext() {
    const id = WIZ_STEPS[WIZ.step];
    if (id === 'count') {
        const r = WIZ.roof;
        r.nOutlets = Math.max(0, Math.min(50, parseInt(WIZ.draft.count, 10) || 0));
        if (r.outlets.length > r.nOutlets) r.outlets.length = r.nOutlets;
        // Placing and gutters happen in plan, with the dialog out of the way.
        document.getElementById('roof-wizard').classList.remove('open');
        startPlacing(r, wizResume);
        return;
    }
    if (WIZ.step >= WIZ_STEPS.length - 1) return wizBuild();
    WIZ.step++;
    renderWizard();
}

function wizBuild() {
    const d = WIZ.draft, r = WIZ.roof;
    for (const g of Object.keys(WIZ_GROUPS)) for (const [fid] of WIZ_GROUPS[g]) document.getElementById(fid).value = d.dims[fid];
    document.getElementById('membrane_name').value = d.membrane_name;
    r.built = true;
    closeWizard();
    renderRoofList();
    setStatus('Built ' + r.name + ' — everything in the panel previews live from here', 'ready');
    updatePreview();
}

// ─── PLACING OUTLETS AND GUTTER EDGES, IN PLAN ───
// For each outlet: click a corner (it snaps to the outline's vertices), click the edge to
// run along (one of the two meeting there), type the offset from that corner and choose
// the type. Then click free edges to make them gutter edges. The dialog is closed
// throughout; the widget top left of the view says what the next click does.
const SNAP_PX = 22;

function startPlacing(r, done) {
    if (!roofReady(r)) return;
    state.placing = { roof: r, stage: r.outlets.length < r.nOutlets ? 'corner' : 'gutters', corner: null, edge: null, done };
    togglePlan(true);
    renderPlacing();
    updateBuildButton();
}

function stopPlacing(finished) {
    const P = state.placing;
    state.placing = null;
    document.getElementById('place-widget').style.display = 'none';
    renderPlanLabels();
    renderRoofList();
    if (finished && P && P.done) P.done();
    else if (!finished) closeWizard();
    updatePreview();
}

function exteriorOf(r) { return r.result.polygons[0].exterior; }
function exteriorEdges(r) { return r.result.edges.filter(e => e.ring === 0); }

function renderPlacing() {
    const P = state.placing, box = document.getElementById('place-widget');
    if (!P) { box.style.display = 'none'; return; }
    const r = P.roof, k = r.outlets.length;
    box.style.display = '';
    let title = '', note = '', body = '', actions = '';
    if (P.stage === 'corner') {
        title = `Outlet ${k + 1} of ${r.nOutlets}: click a corner of the roof`;
        note = 'The corners are marked; a click snaps to the nearest. The offset is measured from the corner you pick.';
        actions = `<button class="btn btn-secondary btn-sm" onclick="skipToGutters()">Skip to gutter edges</button>`;
    } else if (P.stage === 'edge') {
        const [e1, e2] = cornerEdges(r, P.corner);
        title = `Outlet ${k + 1} of ${r.nOutlets}: click the edge to run along`;
        note = 'Or choose one of the two edges that meet at that corner:';
        body = [e1, e2].map(e => `<button class="btn btn-secondary btn-sm" onclick="pickEdge('${e.id}')">${e.id} · ${EDGE_LABEL[edgeType(r, e)] || edgeType(r, e)} · ${Math.round(e.length)} mm</button>`).join('');
        actions = `<button class="btn btn-secondary btn-sm" onclick="state.placing.stage='corner'; renderPlacing(); renderPlanLabels()">Back</button>`;
    } else if (P.stage === 'offset') {
        const e = r.result.edges.find(x => x.id === P.edge);
        const t = edgeType(r, e), walled = t === 'abutment' || t === 'parapet';
        title = `Outlet ${k + 1} of ${r.nOutlets} on ${e.id} (${EDGE_LABEL[t] || t}, ${Math.round(e.length)} mm)`;
        note = 'Offset from the corner you clicked, along the edge.';
        body = `<div class="row pair"><div class="field"><label>Offset <span class="unit">mm from the corner</span></label>
                <input type="number" id="place-offset" value="${Math.round(P.offset !== undefined ? P.offset : e.length / 2)}" min="0" max="${Math.round(e.length)}" step="10"
                       onkeydown="if (event.key === 'Enter') { event.stopPropagation(); placeOutlet(); }"></div>
            <div class="field"><label>Type</label><div id="place-type" class="turn-toggle">
                <button class="turn-btn ${P.type !== 'hopper' ? 'active' : ''}" data-value="internal" onclick="state.placing.type='internal'; selectToggle('place-type','internal'); document.getElementById('place-sump').disabled=false">Internal</button>
                <button class="turn-btn ${P.type === 'hopper' ? 'active' : ''}" data-value="hopper" ${walled ? '' : 'disabled title="Abutment or parapet edges only"'}
                        onclick="state.placing.type='hopper'; selectToggle('place-type','hopper'); const s=document.getElementById('place-sump'); s.checked=true; s.disabled=true">Hopper</button></div></div></div>
            <label class="chk"><input type="checkbox" id="place-sump" ${P.type === 'hopper' ? 'checked disabled' : ''}> Sump (${val('sump_l')} × ${val('sump_w')}, centred on the outlet)</label>
            ${walled ? '' : '<p class="hint">A hopper goes through a wall, so it needs an abutment or parapet edge. This is a free edge: internal outlet only.</p>'}`;
        actions = `<button class="btn btn-secondary btn-sm" onclick="state.placing.stage='edge'; renderPlacing(); renderPlanLabels()">Back</button>
                   <button class="btn btn-primary btn-sm" onclick="placeOutlet()">Place outlet ${k + 1}</button>`;
    } else {
        title = 'Gutter edges: click a free edge to make it an eaves gutter';
        note = 'The whole edge then takes water. Click it again to undo. Walled edges cannot be gutter edges.';
        const gutters = exteriorEdges(r).filter(e => edgeType(r, e) === 'gutter').map(e => e.id);
        body = `<p class="hint">Gutter edges: ${gutters.length ? gutters.join(', ') : 'none'}. Outlets placed: ${r.outlets.length} of ${r.nOutlets}.</p>`;
        actions = (r.outlets.length < r.nOutlets ? `<button class="btn btn-secondary btn-sm" onclick="state.placing.stage='corner'; renderPlacing(); renderPlanLabels()">Place outlets</button>` : '')
            + `<button class="btn btn-primary btn-sm" id="place-done" onclick="stopPlacing(true)">Done</button>`;
    }
    box.innerHTML = `<div class="edit-head"><span>${title}</span><button class="mini" onclick="stopPlacing()" title="Stop (Esc)">✕</button></div>
        <p class="hint" style="margin:2px 0 8px">${note}</p>${body}<div class="wiz-actions" style="justify-content:flex-end">${actions}</div>`;
    const off = document.getElementById('place-offset');
    if (off) { off.focus(); off.select(); }
    renderPlanLabels();
}

const EDGE_LABEL = { abutment: 'Abutment', parapet: 'Parapet', drip: 'Free edge: drip', gutter: 'Free edge: gutter',
                     check_kerb: 'Check kerb', kerb: 'Kerb', penetration: 'Penetration' };

function skipToGutters() { state.placing.stage = 'gutters'; renderPlacing(); }

function cornerEdges(r, i) {
    const edges = exteriorEdges(r), n = edges.length;
    // Corner i is edge i's start and edge i-1's end.
    return [edges[i % n], edges[(i - 1 + n) % n]];
}

// Screen distance from a click to the roof's corners and edges.
function screenOf(r, q) { return toScreen(frameWorld(r.result.frame, q[0], q[1], 0)); }

function nearestCorner(r, ev) {
    const rect = renderer.domElement.getBoundingClientRect(), mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    let best = null, bestD = SNAP_PX;
    exteriorOf(r).forEach((q, i) => {
        const s = screenOf(r, q), d = Math.hypot(s.x - mx, s.y - my);
        if (d < bestD) { bestD = d; best = i; }
    });
    return best;
}

function nearestEdge(r, ev, among) {
    const rect = renderer.domElement.getBoundingClientRect(), mx = ev.clientX - rect.left, my = ev.clientY - rect.top;
    let best = null, bestD = SNAP_PX;
    for (const e of among || exteriorEdges(r)) {
        const a = screenOf(r, e.a), b = screenOf(r, e.b);
        const dx = b.x - a.x, dy = b.y - a.y, L2 = dx * dx + dy * dy || 1;
        const t = Math.max(0, Math.min(1, ((mx - a.x) * dx + (my - a.y) * dy) / L2));
        const d = Math.hypot(a.x + dx * t - mx, a.y + dy * t - my);
        if (d < bestD) { bestD = d; best = e; }
    }
    return best;
}

function placingClick(ev) {
    const P = state.placing, r = P.roof;
    if (P.stage === 'corner') {
        const i = nearestCorner(r, ev);
        if (i === null) { setStatus('Click nearer a corner of the roof — they are marked', 'busy'); return; }
        return pickCorner(i);
    } else if (P.stage === 'edge') {
        const e = nearestEdge(r, ev, cornerEdges(r, P.corner));
        if (!e) { setStatus('Click one of the two edges that meet at that corner', 'busy'); return; }
        return pickEdge(e.id);
    } else if (P.stage === 'gutters') {
        const e = nearestEdge(r, ev);
        if (!e) return;
        const t = edgeType(r, e);
        if (t === 'gutter') delete r.edgeTypes[e.id];
        else if (t === 'drip' || t === 'check_kerb') r.edgeTypes[e.id] = 'gutter';
        else { setStatus(`${e.id} is ${EDGE_LABEL[t] || t}: only a free edge can be a gutter edge`, 'busy'); return; }
        setStatus(`${e.id} ${edgeType(r, e) === 'gutter' ? 'is now a gutter edge' : 'is a drip edge again'}`, 'ready');
        renderRoofList();
    }
    renderPlacing();
}

function placingHover(ev) {
    const P = state.placing;
    if (!P || ev.target !== renderer.domElement) return;
    const r = P.roof;
    const hot = P.stage === 'corner' ? nearestCorner(r, ev) : null;
    const edge = P.stage === 'edge' ? nearestEdge(r, ev, cornerEdges(r, P.corner)) : (P.stage === 'gutters' ? nearestEdge(r, ev) : null);
    if (hot !== P.hot || (edge && edge.id) !== P.hotEdge) { P.hot = hot; P.hotEdge = edge && edge.id; renderPlanLabels(); }
}

function pickCorner(i) {
    const P = state.placing;
    if (!P || P.stage !== 'corner') return;
    P.corner = i;
    P.stage = 'edge';
    renderPlacing();
}

function pickEdge(id) {
    const P = state.placing;
    const [e1, e2] = cornerEdges(P.roof, P.corner);
    if (id !== e1.id && id !== e2.id) return;
    P.edge = id;
    // The corner is edge e1's start ("a") or edge e2's end ("b").
    P.end = id === e1.id ? 'a' : 'b';
    P.stage = 'offset';
    P.type = 'internal';
    renderPlacing();
}

function placeOutlet() {
    const P = state.placing, r = P.roof;
    const e = r.result.edges.find(x => x.id === P.edge);
    const offset = parseFloat(document.getElementById('place-offset').value);
    if (!isFinite(offset) || offset < 0 || offset > e.length) { setStatus(`The offset has to be between 0 and ${Math.round(e.length)} mm along ${e.id}`, 'busy'); return; }
    const type = P.type === 'hopper' ? 'hopper' : 'internal';
    const t = edgeType(r, e);
    if (type === 'hopper' && t !== 'abutment' && t !== 'parapet') {
        setStatus('Refused: a hopper goes through a wall — abutment or parapet edges only', 'busy');
        return;
    }
    const sump = type === 'hopper' || document.getElementById('place-sump').checked;
    r.outlets.push(newOutlet(e.id, P.end, offset, type, sump));
    setStatus(`Outlet ${r.outlets.length} placed on ${e.id}, ${Math.round(offset)} mm from its ${P.end === 'a' ? 'start' : 'end'}`, 'ready');
    Object.assign(P, { stage: r.outlets.length < r.nOutlets ? 'corner' : 'gutters', corner: null, edge: null, offset: undefined });
    renderRoofList();
    renderPlacing();
}

function initWizard() {
    document.addEventListener('keydown', e => {
        const open = document.getElementById('roof-wizard').classList.contains('open');
        if (e.key === 'Escape' && open) return closeWizard();
        if (e.key !== 'Enter') return;
        const el = document.activeElement, typing = el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName);
        if (open) { if (!typing || el.id.indexOf('wiz-') === 0) { e.preventDefault(); wizNext(); } return; }
        if (typing || state.placing || state.cutting) return;   // Enter in a field belongs to the field
        if (document.getElementById('download-reminder').classList.contains('open')) return;
        e.preventDefault();
        openWizard();
    });
    document.getElementById('roof-wizard').addEventListener('click', e => { if (e.target.id === 'roof-wizard') closeWizard(); });
    updateBuildButton();
}
