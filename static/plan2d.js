/* Fallwright plan view labels — dimensions you can type over, handles you can drag, and
 * the cutting planes at export. HTML over the view, placed from projected points every
 * frame, so they stay readable at any zoom.
 *
 * Editable labels (outlet offsets, sump length, width and drop, the two falls, upstand
 * height) turn into an input when clicked: Enter applies, Escape cancels, Tab moves on.
 * Sumps drag whole or by their ends and front edge; outlets drag along their edge within
 * their sump. While dragging only the facets are recomputed, once per animation frame;
 * the layers and breps rebuild on release.
 */

const D2 = { items: [], editing: null };

const DIM_NAMES = { offset: 'Outlet offset from its corner', sump_l: 'Sump length along the edge',
                    sump_w: 'Sump width out from the edge', drop: 'Minimum sump drop', fall: 'Main fall 1:',
                    cricket_fall: 'Cricket fall 1:', upstand: 'Upstand height' };

function planLayer() { return document.getElementById('dim2d-layer'); }

// Frame-local helpers: a plan point to the world, and back.
function planWorld(r, x, y, z) { return frameWorld(r.result.frame, x, y, z || 0); }
function planLocal(r, p) {
    const f = r.result.frame, o = f.origin, q = [p[0] - o[0], p[1] - o[1], p[2] - o[2]];
    return [q[0] * f.u[0] + q[1] * f.u[1] + q[2] * f.u[2], q[0] * f.v[0] + q[1] * f.v[1] + q[2] * f.v[2]];
}
function edgeAxes(e) {
    const dx = e.b[0] - e.a[0], dy = e.b[1] - e.a[1], L = Math.hypot(dx, dy) || 1;
    return { ev: [dx / L, dy / L], m: [-dy / L, dx / L], L };
}
function onEdge(e, t, s) {
    const { ev, m } = edgeAxes(e);
    return [e.a[0] + ev[0] * t + m[0] * (s || 0), e.a[1] + ev[1] * t + m[1] * (s || 0)];
}

function aboveFirrings() {
    return ['deck_t', 'vcl_t', 'insulation_t', 'membrane_t'].reduce((a, k) => a + (parseFloat(val(k)) || 0), 0);
}

function addLabel(text, at, opts = {}) {
    const el = document.createElement('button');
    el.className = 'dim2d' + (opts.edit ? ' editable' : '') + (opts.cls ? ' ' + opts.cls : '');
    el.textContent = text + (opts.edit ? '  ✎' : '');
    if (opts.title) el.title = opts.title;
    const item = Object.assign({ el, at }, opts);
    if (opts.edit) el.onclick = ev => { ev.stopPropagation(); openDimEditor(item); };
    else if (opts.onclick) el.onclick = ev => { ev.stopPropagation(); opts.onclick(item); };
    else el.onclick = ev => ev.stopPropagation();
    if (opts.drag) el.onpointerdown = ev => startDrag(ev, item);
    planLayer().appendChild(el);
    D2.items.push(item);
    return item;
}

// Rebuild every label: after a preview, on entering or leaving plan, on a legend toggle.
function renderPlanLabels() {
    const layer = planLayer();
    if (!layer) return;
    layer.innerHTML = '';
    D2.items = [];
    const r = activeRoof();
    if (!in2D() || !r || !r.result || !r.result.ok) { closeDimEditor(); return; }
    const above = aboveFirrings();
    if (state.placing) placingLabels(r);
    else if (state.cutting) cuttingLabels(r, above);
    else if (r.built) buildLabels(r, above);
    if (D2.editing) {            // the preview was rebuilt under an open editor: re-anchor it
        const again = D2.items.find(i => i.key && i.key === D2.editing.key);
        if (again) D2.editing.item = again; else closeDimEditor();
    }
    position2D();
}

function buildLabels(r, above) {
    const falls = roofFalls(r);
    if (!falls) return;
    const z = (pl, x, y) => pl[0] + pl[1] * x + pl[2] * y + above;
    if (layerVisible.arrows) {
        for (const f of falls.facets) addLabel(`${f.id} ${f.label}`, planWorld(r, f.at[0], f.at[1], z(f.plane, ...f.at)), { cls: 'fall' });
    }
    if (layerVisible.facets) {
        for (const c of falls.creases) {
            if (c.kind !== 'valley') continue;
            const f = falls.facets.find(x => x.id === c.facets[0]);
            const mx = (c.a[0] + c.b[0]) / 2, my = (c.a[1] + c.b[1]) / 2;
            addLabel(`valley ${c.label}`, planWorld(r, mx, my, z(f.plane, mx, my)), { cls: 'valley' });
        }
    }
    if (layerVisible.levels) {
        for (const s of falls.spots || []) {
            if (s.kind !== 'vertex') continue;
            addLabel(`f${Math.round(s.depth)} · +${Math.round(s.finished)}`, planWorld(r, s.p[0], s.p[1], s.finished), { cls: 'level',
                     title: `Firring ${Math.round(s.depth)} mm · finished +${Math.round(s.finished)} above the structure (${Math.round(s.abs)})` });
        }
    }
    // The falls, typed over in place.
    addLabel(`Main fall 1:${val('fall')}`, planWorld(r, 0, -700, above), { edit: true, kind: 'fall', value: parseFloat(val('fall')), key: 'fall' });
    addLabel(`Cricket fall 1:${val('cricket_fall')}`, planWorld(r, r.result.width * 0.45, -700, above),
             { edit: true, kind: 'cricket_fall', value: parseFloat(val('cricket_fall')), key: 'cricket_fall' });
    // Upstands at each walled edge.
    for (const e of falls.edges || []) {
        if (e.type !== 'abutment' && e.type !== 'parapet') continue;
        const mid = onEdge(e, e.length / 2, -350);
        addLabel(`${e.id} upstand ${val('upstand')}`, planWorld(r, mid[0], mid[1], e.level_max || above),
                 { edit: e.type === 'abutment', kind: 'upstand', value: parseFloat(val('upstand')), key: 'upstand:' + e.id,
                   title: e.type === 'parapet' ? 'On a parapet the membrane goes up and over it: the wall top sets the height' : undefined });
    }
    const edges = Object.fromEntries((falls.edges || []).map(e => [e.id, e]));
    // Outlets and sumps: dimensions to type over, handles to drag.
    falls.outlets.forEach((o, k) => {
        if (!o.point) return;
        const e = edges[o.edge], s = falls.sumps.find(x => x.outlet === k);
        const floor = s && s.floor_low !== undefined ? s.floor_low : above;
        addLabel(`O${k + 1} ${Math.round(o.offset)} from ${o.corner === 'a' ? 'start' : 'end'}`,
                 planWorld(r, ...onEdge(e, o.t, -250), floor), { edit: true, kind: 'offset', k, value: o.offset, key: 'offset:' + k });
        addLabel('●', planWorld(r, o.point[0], o.point[1], floor), { cls: 'handle outlet', drag: 'outlet', k,
                 title: `Drag outlet ${k + 1} along ${o.edge}` });
        if (!s || s.kind !== 'sump') return;
        const mid = (s.b1 + s.b2) / 2;
        addLabel(`L ${Math.round(s.L)}`, planWorld(r, ...onEdge(e, mid, s.W + 180), s.rim), { edit: true, kind: 'sump_l', k, value: s.L, key: 'sump_l:' + k });
        addLabel(`W ${Math.round(s.W)}`, planWorld(r, ...onEdge(e, s.b2 + 220, s.W / 2), s.rim), { edit: true, kind: 'sump_w', k, value: s.W, key: 'sump_w:' + k });
        addLabel(`drop ${Math.round(s.drop)}`, planWorld(r, ...onEdge(e, s.b1 - 260, s.W / 2), s.rim),
                 { edit: true, kind: 'drop', k, value: s.drop_asked || parseFloat(val('sump_drop')), key: 'drop:' + k,
                   title: `Actual drop ${Math.round(s.drop)}: rim +${Math.round(s.rim)}, floor +${Math.round(s.floor_low)}. Type the minimum drop for this sump.` });
        addLabel('≡', planWorld(r, ...onEdge(e, mid, s.W / 2), s.floor_low), { cls: 'handle', drag: 'sump', k, title: 'Drag the sump along its edge' });
        addLabel('◀', planWorld(r, ...onEdge(e, s.b1, s.W / 2), s.floor_low), { cls: 'handle', drag: 'b1', k, title: 'Drag the sump end' });
        addLabel('▶', planWorld(r, ...onEdge(e, s.b2, s.W / 2), s.floor_low), { cls: 'handle', drag: 'b2', k, title: 'Drag the sump end' });
        addLabel('▲', planWorld(r, ...onEdge(e, mid, s.W), s.floor_low), { cls: 'handle', drag: 'W', k, title: 'Drag the sump front' });
    });
    for (const u of falls.unreached || []) {
        const cx = u.reduce((a, q) => a + q[0], 0) / u.length, cy = u.reduce((a, q) => a + q[1], 0) / u.length;
        addLabel('no outlet reaches this area', planWorld(r, cx, cy, above), { cls: 'warnlabel' });
    }
    for (const id of falls.ponding || []) {
        const f = falls.facets.find(x => x.id === id);
        if (f) addLabel(`${id} ponds`, planWorld(r, f.at[0], f.at[1] - 250, above), { cls: 'warnlabel' });
    }
}

function placingLabels(r) {
    const P = state.placing;
    const ext = exteriorOf(r);
    if (P.stage === 'corner') {
        ext.forEach((q, i) => addLabel(`${i === P.hot ? '◉' : '○'}`, planWorld(r, q[0], q[1], 0), {
            cls: 'handle corner' + (i === P.hot ? ' hot' : ''), title: 'Place the outlet from this corner',
            onclick: () => pickCorner(i) }));
    }
    const edges = P.stage === 'edge' ? cornerEdges(r, P.corner) : (P.stage === 'gutters' ? exteriorEdges(r) : []);
    for (const e of edges) {
        const mid = onEdge(e, e.length / 2, 0);
        const t = edgeType(r, e);
        addLabel(`${e.id} ${EDGE_LABEL[t] || t}`, planWorld(r, mid[0], mid[1], 0), {
            cls: 'edge-pick' + (P.hotEdge === e.id ? ' hot' : '') + (t === 'gutter' ? ' gutter' : ''),
            onclick: () => P.stage === 'edge' ? pickEdge(e.id) : placingClickEdge(e) });
    }
    if (P.stage === 'edge' || P.stage === 'offset') {
        const q = ext[P.corner];
        if (q) addLabel('◉', planWorld(r, q[0], q[1], 0), { cls: 'handle corner hot' });
    }
    r.outlets.forEach((o, k) => {
        const e = r.result.edges.find(x => x.id === o.edge);
        if (!e) return;
        const t = o.corner === 'a' ? o.offset : e.length - o.offset;
        addLabel(`O${k + 1}`, planWorld(r, ...onEdge(e, t, 0), 0), { cls: 'handle outlet' });
    });
}

function placingClickEdge(e) {
    const r = state.placing.roof, t = edgeType(r, e);
    if (t === 'gutter') delete r.edgeTypes[e.id];
    else if (t === 'drip' || t === 'check_kerb') r.edgeTypes[e.id] = 'gutter';
    else { setStatus(`${e.id} is ${EDGE_LABEL[t] || t}: only a free edge can be a gutter edge`, 'busy'); return; }
    renderRoofList();
    renderPlacing();
}

// Every frame while flat: put each label where its point projects. A drag handle that
// lands on a more important one (outlet, then the sump, then its ends and front) steps
// aside until the view is zoomed in far enough to tell them apart.
const HANDLE_GAP = 24;   // px

function position2D() {
    const shown = [];
    const order = D2.items.slice().sort((a, b) => (a.prio === undefined ? -1 : a.prio) - (b.prio === undefined ? -1 : b.prio));
    for (const item of order) {
        const s = toScreen(item.at);
        let hide = s.off;
        if (!hide && item.prio !== undefined) {
            hide = shown.some(q => Math.hypot(q.x - s.x, q.y - s.y) < HANDLE_GAP);
            if (!hide) shown.push(s);
        }
        item.el.style.display = hide ? 'none' : '';
        item.el.style.left = s.x + 'px';
        item.el.style.top = s.y + 'px';
    }
    const ed = document.getElementById('dim2d-editor');
    if (D2.editing && D2.editing.item) {
        ed.style.left = D2.editing.item.el.style.left;
        ed.style.top = D2.editing.item.el.style.top;
    }
}

// ─── THE EDITOR ───
function openDimEditor(item) {
    D2.editing = { key: item.key, item };
    const ed = document.getElementById('dim2d-editor');
    ed.innerHTML = `<div class="d2-title">${DIM_NAMES[item.kind]}${item.k !== undefined ? ' · O' + (item.k + 1) : ''}</div>
        <input id="d2-input" type="number" step="1" value="${Math.round(item.value)}"> <span class="unit">${/fall/.test(item.kind) ? '' : 'mm'}</span>
        <div class="d2-hint">Enter to apply · Esc to cancel · Tab for the next</div>`;
    ed.style.display = '';
    const input = document.getElementById('d2-input');
    input.onkeydown = ev => {
        ev.stopPropagation();
        if (ev.key === 'Enter') { ev.preventDefault(); commitDimEditor(); }
        else if (ev.key === 'Escape') { ev.preventDefault(); closeDimEditor(); }
        else if (ev.key === 'Tab') {
            ev.preventDefault();
            const editable = D2.items.filter(i => i.edit);
            const k = editable.indexOf(D2.editing.item);
            commitDimEditor();
            const next = editable[(k + (ev.shiftKey ? editable.length - 1 : 1)) % editable.length];
            if (next) openDimEditor(next);
        }
    };
    position2D();
    input.focus();
    input.select();
}

function closeDimEditor() {
    D2.editing = null;
    const ed = document.getElementById('dim2d-editor');
    if (ed) { ed.style.display = 'none'; ed.innerHTML = ''; }
}

function commitDimEditor() {
    const input = document.getElementById('d2-input');
    const item = D2.editing && D2.editing.item;
    if (!input || !item) return closeDimEditor();
    const v = parseFloat(input.value);
    closeDimEditor();
    if (!isFinite(v) || v < 0 || Math.abs(v - item.value) < 0.5) return;
    applyDim(item, v);
}

function applyDim(item, v) {
    const r = activeRoof();
    const o = r && item.k !== undefined ? r.outlets[item.k] : null;
    switch (item.kind) {
        case 'fall': case 'cricket_fall': case 'upstand': document.getElementById(item.kind).value = Math.round(v); break;
        case 'offset': if (o) o.offset = v; break;
        case 'sump_l': if (o) { freezeSump(r, item.k); o.sump_l = v; } break;
        case 'sump_w': if (o) o.sump_w = v; break;
        case 'drop': if (o) o.drop = v; break;
        default: return;
    }
    setStatus(`${DIM_NAMES[item.kind]} ${Math.round(v)}${item.k !== undefined ? ' on outlet ' + (item.k + 1) : ''}`, 'ready');
    renderOutletRows();
    onNumeric();
}

// A sump centred on its outlet becomes independent of it the moment it is edited: its
// start is pinned where it is drawn now.
function freezeSump(r, k) {
    const o = r.outlets[k], falls = roofFalls(r);
    const s = falls && falls.sumps.find(x => x.outlet === k);
    if (o && s && (o.sump_t0 === null || o.sump_t0 === undefined)) o.sump_t0 = s.b1;
}

// ─── DRAGGING ───
function startDrag(ev, item) {
    ev.preventDefault();
    ev.stopPropagation();
    const r = activeRoof(), falls = roofFalls(r);
    if (!r || !falls) return;
    const o = item.k !== undefined ? r.outlets[item.k] : null;
    const s = falls.sumps.find(x => x.outlet === item.k);
    const fo = falls.outlets[item.k];
    const e = fo && (falls.edges || []).find(x => x.id === fo.edge);
    if (!o || !e) return;
    if (item.drag !== 'outlet' || (s && s.kind === 'sump')) freezeSump(r, item.k);
    const level = r.result.datum_z + aboveFirrings();
    state.dragging = { item, r, o, e, s: s && s.kind === 'sump' ? Object.assign({}, s) : null, t_out: fo.t, level,
                       grab: null, moved: false };
    controls.enabled = false;
    document.addEventListener('pointermove', onDrag);
    document.addEventListener('pointerup', endDrag, { once: true });
}

function onDrag(ev) {
    const D = state.dragging;
    if (!D) return;
    const p = pointOnLevel(ev, D.level);
    if (!p) return;
    const [x, y] = planLocal(D.r, p);
    const { ev: ed, m, L } = edgeAxes(D.e);
    const t = (x - D.e.a[0]) * ed[0] + (y - D.e.a[1]) * ed[1];
    const sv = (x - D.e.a[0]) * m[0] + (y - D.e.a[1]) * m[1];
    if (D.grab === null) D.grab = { t, t0: D.s ? D.s.b1 : 0 };
    const o = D.o, s = D.s, tOut = () => (o.corner === 'a' ? o.offset : L - o.offset);
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
    if (D.item.drag === 'outlet') {
        // Along the edge, within its sump (or anywhere on the edge without one).
        const tt = s ? clamp(t, s.b1, s.b2) : clamp(t, 0, L);
        o.offset = Math.round(o.corner === 'a' ? tt : L - tt);
    } else if (D.item.drag === 'sump') {
        // The whole sump, as long as the outlet stays within it and it stays on the edge.
        const len = s.b2 - s.b1, to = tOut();
        const b1 = clamp(D.grab.t0 + (t - D.grab.t), Math.max(0, to - len), Math.min(L - len, to));
        o.sump_t0 = Math.round(b1);
    } else if (D.item.drag === 'b1') {
        const b1 = clamp(t, 0, Math.min(tOut(), s.b2 - 50));
        o.sump_t0 = Math.round(b1);
        o.sump_l = Math.round(s.b2 - b1);
    } else if (D.item.drag === 'b2') {
        const b2 = clamp(t, Math.max(tOut(), s.b1 + 50), L);
        o.sump_l = Math.round(b2 - s.b1);
    } else if (D.item.drag === 'W') {
        o.sump_w = Math.round(clamp(sv, 50, 3000));
    }
    D.moved = true;
    setStatus(`O${D.item.k + 1}: ${Math.round(o.offset)} from ${o.corner === 'a' ? 'start' : 'end'}`
              + (o.sump ? ` · sump ${Math.round(o.sump_l)} × ${Math.round(o.sump_w)}` : ''), 'ready');
    previewFacets();
}

function endDrag() {
    const D = state.dragging;
    document.removeEventListener('pointermove', onDrag);
    controls.enabled = true;
    // The click that ends a drag must not also pick something.
    setTimeout(() => { state.dragging = null; }, 0);
    if (D && D.moved) { renderOutletRows(); updatePreview(); }
}

// ─── CUTTING PLANES, AT EXPORT ───
// Two planes by default, one each way through the middle of the roof. Drag one to move
// it (it snaps to outlets and facet vertices within 100 mm), click an arrow to flip the
// way the section looks, + to add one, × to remove an extra one. They stay with the roof.
const SNAP_CUT = 100;

function defaultCutPlanes(r) {
    return [{ label: 'A', dir: 'u', pos: Math.round(r.result.height / 2), flip: false },
            { label: 'B', dir: 'v', pos: Math.round(r.result.width / 2), flip: false }];
}

function startCutting(r) {
    if (!r) return;
    const i = state.roofs.indexOf(r);
    if (i !== state.active) setActive(i);
    if (!r.cutPlanes || !r.cutPlanes.length) r.cutPlanes = defaultCutPlanes(r);
    state.cutting = { roof: r };
    togglePlan(true);
    renderCutWidget();
    renderPlanOverlay();
    setStatus('Cutting planes: drag to move, click an arrow to flip, then Export', 'ready');
}

function stopCutting() {
    state.cutting = null;
    document.getElementById('cut-widget').style.display = 'none';
    renderPlanOverlay();
}

function renderCutWidget() {
    const r = state.cutting && state.cutting.roof, box = document.getElementById('cut-widget');
    if (!r) { box.style.display = 'none'; return; }
    box.style.display = '';
    box.innerHTML = `<div class="edit-head"><span>Sections for the DXF: ${r.cutPlanes.map(c => c.label + '-' + c.label).join(', ')}</span>
        <button class="mini" onclick="stopCutting()" title="Cancel (Esc)">✕</button></div>
        <p class="hint" style="margin:2px 0 8px">Drag a plane across the roof; it snaps to outlets and facet corners within ${SNAP_CUT} mm. Click an arrow to flip the way it looks.</p>
        <div class="wiz-actions"><button class="btn btn-secondary btn-sm" onclick="addCutPlane('u')">+ along u</button>
            <button class="btn btn-secondary btn-sm" onclick="addCutPlane('v')">+ along v</button>
            <button class="btn btn-secondary btn-sm" onclick="stopCutting()">Cancel</button>
            <button class="btn btn-primary btn-sm" id="cut-export" onclick="exportWithPlanes()">Export</button></div>`;
}

function addCutPlane(dir) {
    const r = state.cutting.roof;
    const used = new Set(r.cutPlanes.map(c => c.label));
    const label = 'ABCDEFGHJKLMNPQRSTUVWXYZ'.split('').find(c => !used.has(c)) || 'Z';
    r.cutPlanes.push({ label, dir, pos: Math.round((dir === 'u' ? r.result.height : r.result.width) / 3), flip: false });
    renderCutWidget();
    renderPlanOverlay();
}

function removeCutPlane(i) {
    const r = state.cutting.roof;
    r.cutPlanes.splice(i, 1);
    renderCutWidget();
    renderPlanOverlay();
}

function exportWithPlanes() {
    stopCutting();
    writeDXF();
}

function cuttingLabels(r, above) {
    r.cutPlanes.forEach((cp, i) => {
        const [a, b] = cutLine(r, cp);
        // Handles staggered along their lines, so two planes crossing mid-roof do not stack.
        const f = 0.25 + 0.5 * ((i % 4) / 3);
        const mid = [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f];
        const look = cp.dir === 'u' ? [0, cp.flip ? -1 : 1] : [cp.flip ? -1 : 1, 0];
        const arrow = cp.dir === 'u' ? (cp.flip ? '▼' : '▲') : (cp.flip ? '◀' : '▶');
        addLabel(`${cp.label}  ⇕`, planWorld(r, mid[0], mid[1], above), { cls: 'handle cut', drag: 'cut', cut: i,
                 title: 'Drag to move this cutting plane' });
        for (const q of [a, b]) {
            addLabel(`${cp.label} ${arrow}`, planWorld(r, q[0] + look[0] * 250, q[1] + look[1] * 250, above), {
                cls: 'cut', title: 'Click to flip the way this section looks',
                onclick: () => { cp.flip = !cp.flip; renderPlanOverlay(); } });
        }
        if (i >= 2) addLabel('×', planWorld(r, b[0], b[1], above), { cls: 'cut', title: 'Remove this plane', onclick: () => removeCutPlane(i) });
    });
    for (const item of D2.items) if (item.drag === 'cut') item.el.onpointerdown = ev => startCutDrag(ev, item);
}

function startCutDrag(ev, item) {
    ev.preventDefault();
    ev.stopPropagation();
    const r = state.cutting.roof;
    state.dragging = { cut: item.cut, r, level: r.result.datum_z + aboveFirrings() };
    controls.enabled = false;
    document.addEventListener('pointermove', onCutDrag);
    document.addEventListener('pointerup', () => {
        document.removeEventListener('pointermove', onCutDrag);
        controls.enabled = true;
        setTimeout(() => { state.dragging = null; }, 0);
    }, { once: true });
}

function onCutDrag(ev) {
    const D = state.dragging;
    const p = pointOnLevel(ev, D.level);
    if (!p) return;
    const [x, y] = planLocal(D.r, p);
    const cp = D.r.cutPlanes[D.cut];
    let pos = cp.dir === 'u' ? y : x;
    // Snap to an outlet or a facet corner within SNAP_CUT.
    const falls = roofFalls(D.r);
    const marks = [];
    if (falls) {
        for (const o of falls.outlets) if (o.point) marks.push(o.point);
        for (const f of falls.facets) marks.push(...f.ring);
    }
    let best = null;
    for (const q of marks) {
        const c = cp.dir === 'u' ? q[1] : q[0];
        if (Math.abs(c - pos) <= SNAP_CUT && (best === null || Math.abs(c - pos) < Math.abs(best - pos))) best = c;
    }
    cp.pos = Math.round(best !== null ? best : pos);
    renderPlanOverlay();
}

function initPlan2D() {
    view2d.onFrame = position2D;
    document.addEventListener('pointerdown', ev => {        // a click elsewhere closes the editor
        const ed = document.getElementById('dim2d-editor');
        if (D2.editing && ed && !ed.contains(ev.target) && !(D2.editing.item && D2.editing.item.el === ev.target)) closeDimEditor();
    });
}
