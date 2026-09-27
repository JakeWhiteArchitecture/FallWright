/* Fallwright viewer — Three.js scene, web-ifc import, face picking, prism and slab rendering.
 *
 * Scene units are millimetres, Y-up (Three.js). IFC coordinates are Z-up, so
 * Three (x, y, z) <-> IFC (x, -z, y). web-ifc returns metres, already Y-up, scaled here;
 * the server reader returns IFC Z-up and is turned Y-up on arrival.
 */

let scene, camera, renderer, controls, ifcApi = null;
let modelGroup = null, cladGroup = null, outlineGroup = null, dimGroup = null, highlightGroup = null, cornerGroup = null;
let planGroup = null;         // falls overlay: facet lines, fall arrows, contours, sumps, outlets, cutting planes
let allMeshes = [], meshMeta = [], modelContext = {};
// The scene works near the origin on plan; modelOffset (IFC mm, Z-up) puts exports back on
// the host model. Heights are never shifted, so a level in the scene is a level in the model.
let modelOffset = [0, 0, 0];
const layerVisible = { model: true, firring: true, deck: true, vcl: true, insulation: true, membrane: true,
                       trims: true, kerbs: true, outlets: true, facets: true, arrows: true, contours: true,
                       levels: true, dims: true, faces: true, outline: true };
// One colour for every picked face and one for every outline: which roof a face belongs
// to is the list's job, not the viewport's.
const PICK_COLOR = 0x2a9d8f, OUTLINE_COLOR = 0x8ea3b8;
const VERT_TOL = Math.sin(Math.PI / 180);   // 1 degree: what counts as a vertical (or level) face

// ─── SCENE ───
function initThree() {
    const container = document.getElementById('viewport');
    scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0a0a1a);
    camera = new THREE.PerspectiveCamera(45, container.clientWidth / container.clientHeight, 10, 2000000);
    camera.position.set(15000, 12000, 15000);
    renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(window.devicePixelRatio);
    renderer.setSize(container.clientWidth, container.clientHeight);
    container.appendChild(renderer.domElement);
    controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.target.set(0, 1500, 0);
    scene.add(new THREE.AmbientLight(0x404040, 0.9));
    const dir1 = new THREE.DirectionalLight(0xffffff, 0.8);
    dir1.position.set(20000, 30000, 10000);
    scene.add(dir1);
    const dir2 = new THREE.DirectionalLight(0x8888ff, 0.3);
    dir2.position.set(-10000, 10000, -20000);
    scene.add(dir2);
    scene.add(new THREE.GridHelper(40000, 40, 0x222244, 0x111133));
    modelGroup = new THREE.Group(); cladGroup = new THREE.Group(); outlineGroup = new THREE.Group();
    dimGroup = new THREE.Group(); highlightGroup = new THREE.Group(); cornerGroup = new THREE.Group();
    planGroup = new THREE.Group();
    scene.add(modelGroup, cladGroup, outlineGroup, dimGroup, highlightGroup, cornerGroup, planGroup);
    window.addEventListener('resize', () => {
        camera.aspect = container.clientWidth / container.clientHeight;
        camera.updateProjectionMatrix();
        if (orthoCamera) {         // keep the flat view's height, widen or narrow it
            const h = orthoCamera.top - orthoCamera.bottom;
            orthoCamera.left = -h * camera.aspect / 2; orthoCamera.right = h * camera.aspect / 2;
            orthoCamera.updateProjectionMatrix();
        }
        renderer.setSize(container.clientWidth, container.clientHeight);
    });
    (function animate() {
        requestAnimationFrame(animate);
        controls.update();
        if (view2d.on && view2d.onFrame) view2d.onFrame();     // the 2D labels follow the view
        renderer.render(scene, activeCamera());
    })();
}

function clearGroup(group) {
    while (group.children.length) {
        const c = group.children.pop();
        c.traverse(o => { if (o.geometry) o.geometry.dispose(); if (o.material && o.material.dispose) o.material.dispose(); });
    }
}

// ─── WEB-IFC IMPORT ───
async function initWebIfc() {
    try { ifcApi = new WebIFC.IfcAPI(); await ifcApi.Init(); }
    catch (err) { console.error('web-ifc init failed', err); ifcApi = null; }
}

let _typeNames = null;
function ifcTypeName(code) {
    if (!_typeNames) {
        _typeNames = {};
        for (const k of Object.keys(WebIFC)) if (typeof WebIFC[k] === 'number' && k.startsWith('IFC')) _typeNames[WebIFC[k]] = k;
    }
    return _typeNames[code] || '';
}

function attr(line, name) {
    const v = line && line[name];
    return (v && typeof v === 'object' && 'value' in v) ? v.value : (v === undefined ? null : v);
}

function readSpatial(modelID, unitScale) {
    const elemStorey = {}, storeys = {}, ctx = {};
    try {
        const names = { IFCPROJECT: 'project', IFCSITE: 'site', IFCBUILDING: 'building' };
        for (const key of Object.keys(names)) {
            const ids = ifcApi.GetLineIDsWithType(modelID, WebIFC[key]);
            if (ids.size() > 0) ctx[names[key]] = attr(ifcApi.GetLine(modelID, ids.get(0)), 'Name') || '';
        }
        const rels = ifcApi.GetLineIDsWithType(modelID, WebIFC.IFCRELCONTAINEDINSPATIALSTRUCTURE);
        for (let i = 0; i < rels.size(); i++) {
            const rel = ifcApi.GetLine(modelID, rels.get(i));
            const sid = attr(rel, 'RelatingStructure');
            if (!storeys[sid]) {
                const st = ifcApi.GetLine(modelID, sid);
                storeys[sid] = { name: attr(st, 'Name') || ('Storey ' + sid), elevation: (parseFloat(attr(st, 'Elevation')) || 0) * unitScale };
            }
            for (const r of (rel.RelatedElements || [])) elemStorey[r.value] = storeys[sid];
        }
    } catch (e) { console.warn('spatial read failed', e); }
    return { elemStorey, ctx };
}

function readLengthUnitScale(modelID) {
    // mm per model length unit, from IfcSIUnit (default: metres).
    try {
        const ids = ifcApi.GetLineIDsWithType(modelID, WebIFC.IFCSIUNIT);
        for (let i = 0; i < ids.size(); i++) {
            const u = ifcApi.GetLine(modelID, ids.get(i));
            if (attr(u, 'UnitType') === 'LENGTHUNIT') {
                const prefix = attr(u, 'Prefix');
                return prefix === 'MILLI' ? 1 : (prefix === 'CENTI' ? 10 : (prefix === 'DECI' ? 100 : 1000));
            }
        }
    } catch (e) { /* fall through */ }
    return 1000;
}

// ─── IMPORT ───
// Two readers produce the same raw form: {verts (mm, Y-up like the scene), idx,
// expressID, type, name, storey}. web-ifc runs in the browser and keeps the file private;
// the server reader uses IfcOpenShell and builds geometry web-ifc cannot.

async function readWithWebIfc(file, onStatus) {
    if (!ifcApi) throw new Error('web-ifc is still loading');
    onStatus('Parsing IFC in the browser…');
    const data = new Uint8Array(await file.arrayBuffer());
    let modelID = null;
    try {
        modelID = ifcApi.OpenModel(data, { COORDINATE_TO_ORIGIN: false });
    } catch (err) {
        throw new Error('web-ifc could not open the file: ' + err.message);
    }
    const spatial = readSpatial(modelID, 1000);
    const raw = [];
    let failed = 0;
    try {
        ifcApi.StreamAllMeshes(modelID, (mesh) => {
            // One unbuildable element must not abandon the file.
            try {
                const verts = [], idx = [];
                let off = 0;
                for (let i = 0; i < mesh.geometries.size(); i++) {
                    const pg = mesh.geometries.get(i);
                    const g = ifcApi.GetGeometry(modelID, pg.geometryExpressID);
                    const v = ifcApi.GetVertexArray(g.GetVertexData(), g.GetVertexDataSize());
                    const ix = ifcApi.GetIndexArray(g.GetIndexData(), g.GetIndexDataSize());
                    g.delete();
                    if (!v.length || !ix.length) continue;
                    const m = new THREE.Matrix4().fromArray(pg.flatTransformation);
                    const n = v.length / 6;
                    const p = new THREE.Vector3();
                    for (let k = 0; k < n; k++) {
                        p.set(v[k * 6], v[k * 6 + 1], v[k * 6 + 2]).applyMatrix4(m);
                        verts.push(p.x * 1000, p.y * 1000, p.z * 1000);   // web-ifc emits metres
                    }
                    for (let k = 0; k < ix.length; k++) idx.push(ix[k] + off);
                    off += n;
                }
                if (!verts.length) return;
                let type = '', name = '';
                try { type = ifcTypeName(ifcApi.GetLineType(modelID, mesh.expressID)); } catch (e) { /* unknown */ }
                try { name = attr(ifcApi.GetLine(modelID, mesh.expressID), 'Name') || ''; } catch (e) { /* unnamed */ }
                raw.push({ verts, idx, expressID: mesh.expressID, type, name,
                           storey: spatial.elemStorey[mesh.expressID] || null });
            } catch (err) { failed++; }
        });
    } catch (err) {
        // The stream itself died: keep whatever was collected and let the caller decide.
        if (!raw.length) { try { ifcApi.CloseModel(modelID); } catch (e) { /* gone */ } ifcApi = null; initWebIfc();
                           throw new Error('web-ifc failed while reading geometry: ' + err.message); }
        failed++;
    }
    try { ifcApi.CloseModel(modelID); } catch (e) { /* already closed */ }
    if (!raw.length) throw new Error('web-ifc found no geometry in this file');
    return { raw, context: spatial.ctx, failed, reader: 'web-ifc' };
}

function b64ToArray(b64, Type) {
    const bin = atob(b64), bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Type(bytes.buffer);
}

async function readWithServer(file, onStatus) {
    onStatus('Building geometry on the server…');
    const body = new FormData();
    body.append('file', file, file.name);
    const resp = await fetch('api/import', { method: 'POST', body });
    if (!resp.ok) throw new Error('server importer returned ' + resp.status);
    const data = await resp.json();
    if (!data.success) throw new Error(data.error || 'server importer failed');
    const raw = data.elements.map(e => {
        // IfcOpenShell gives IFC Z-up; the scene (and web-ifc) are Y-up: (x, y, z) → (x, z, -y).
        const v = b64ToArray(e.verts, Float32Array), up = new Float32Array(v.length);
        for (let k = 0; k < v.length; k += 3) { up[k] = v[k]; up[k + 1] = v[k + 2]; up[k + 2] = -v[k + 1]; }
        return { verts: up, idx: b64ToArray(e.idx, Uint32Array), expressID: e.expressID, type: e.type, name: e.name, storey: e.storey };
    });
    if (!raw.length) throw new Error((data.warnings || []).join(' ') || 'no geometry in this file');
    return { raw, context: data.context || {}, failed: (data.failed || []).length,
             warnings: data.warnings || [], reader: 'IfcOpenShell (server)' };
}

function modelScale(raw) {
    // Judge the unit by the model's SIZE, never by how far it sits from the origin:
    // a georeferenced model has huge coordinates but ordinary dimensions.
    let lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (const r of raw) for (let k = 0; k < r.verts.length; k += 3)
        for (let a = 0; a < 3; a++) { const v = r.verts[k + a]; if (v < lo[a]) lo[a] = v; if (v > hi[a]) hi[a] = v; }
    const diag = Math.hypot(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]);
    for (const s of [1, 1000, 0.001]) if (diag * s > 1000 && diag * s < 5e6) return { scale: s, lo, hi, diag };
    return { scale: 1, lo, hi, diag };    // implausible either way: leave it alone and say so
}

function buildScene(raw, context, warnings) {
    clearGroup(modelGroup); clearGroup(highlightGroup);
    allMeshes = []; meshMeta = [];
    modelContext = context || {};
    const { scale, lo, hi, diag } = modelScale(raw);
    if (scale !== 1) warnings.push('Model read as ' + (scale === 1000 ? 'metres' : 'kilometres') + ' and scaled to mm.');
    if (diag * scale <= 1000 || diag * scale >= 5e6)
        warnings.push('Model is ' + Math.round(diag * scale) + ' mm across, which looks wrong for a building.');
    // Work near the origin on plan: float32 loses millimetres on site coordinates in the
    // hundreds of thousands, which wrecks picking and extraction. Heights stay as they are,
    // so the levels Fallwright reports are the model's own. The raw form is Y-up, so the
    // shift is (x, 0, z) in the scene and (x, -z, 0) in IFC terms for the exports.
    const shift = [Math.round((lo[0] + hi[0]) / 2 * scale), 0, Math.round((lo[2] + hi[2]) / 2 * scale)];
    modelOffset = [shift[0], -shift[2], 0];
    for (const r of raw) {
        const pos = new Float32Array(r.verts.length);
        for (let k = 0; k < r.verts.length; k += 3) {
            pos[k] = r.verts[k] * scale - shift[0];
            pos[k + 1] = r.verts[k + 1] * scale - shift[1];
            pos[k + 2] = r.verts[k + 2] * scale - shift[2];
        }
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
        geo.setIndex(new THREE.BufferAttribute(new Uint32Array(r.idx), 1));
        geo.computeVertexNormals();
        geo.computeBoundingBox();
        const isWall = /WALL/i.test(r.type);
        const mat = new THREE.MeshPhongMaterial({ color: isWall ? 0xd8d8d8 : 0xa8a8b8, flatShading: true,
                                                  side: THREE.DoubleSide, transparent: true, opacity: 0.95 });
        const th = new THREE.Mesh(geo, mat);
        th.userData.index = allMeshes.length;
        modelGroup.add(th);
        allMeshes.push(th);
        meshMeta.push({ mesh: th, expressID: r.expressID, type: r.type, name: r.name, storey: r.storey });
    }
    fitCameraTo(modelGroup);
    return new Set(meshMeta.filter(m => m.storey).map(m => m.storey.name)).size;
}

async function loadIFC(file, onStatus, force) {
    // Browser first, server second. Either reader alone is enough to work with.
    const warnings = [];
    let read = null;
    if (force !== 'server') {
        try { read = await readWithWebIfc(file, onStatus); }
        catch (err) { warnings.push('Browser importer: ' + err.message); }
    }
    if (!read) {
        try { read = await readWithServer(file, onStatus); }
        catch (err) {
            warnings.push('Server importer: ' + err.message);
            throw new Error(warnings.join(' | '));
        }
    }
    warnings.push(...(read.warnings || []));
    if (read.failed) warnings.push(read.failed + ' element(s) could not be built and were skipped.');
    const storeys = buildScene(read.raw, read.context, warnings);
    return { meshes: allMeshes.length, storeys, context: modelContext, reader: read.reader,
             warnings, offset: modelOffset };
}

function fitCameraTo(obj) {
    reset2D();
    const bb = new THREE.Box3().setFromObject(obj);
    if (bb.isEmpty()) return;
    const c = bb.getCenter(new THREE.Vector3());
    const size = bb.getSize(new THREE.Vector3()).length();
    // Roofs are picked from above, so the view starts looking down on the model.
    camera.position.set(c.x + size * 0.35, c.y + size * 1.1, c.z + size * 0.45);
    controls.target.copy(c);
    controls.update();
}

function setModelVisible(on) { layerVisible.model = on; modelGroup.visible = on; }

// ─── PICKING ───
function pickAt(event) {
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                                    -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(mouse, activeCamera());
    const hits = rc.intersectObjects(allMeshes, false);
    if (!hits.length) return null;
    const hit = hits[0];
    const normal = hit.face.normal.clone().applyMatrix3(new THREE.Matrix3().getNormalMatrix(hit.object.matrixWorld)).normalize();
    if (normal.dot(activeCamera().position.clone().sub(hit.point)) < 0) normal.negate();   // face the viewer
    return { mesh: hit.object, faceIndex: hit.faceIndex, face: hit.face, point: hit.point, normal };
}

function faceNormals(geo) {
    const pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const count = idx ? idx.length / 3 : pos.length / 9, out = new Array(count);
    const a = new THREE.Vector3(), b = new THREE.Vector3(), c = new THREE.Vector3();
    for (let f = 0; f < count; f++) {
        const i0 = (idx ? idx[f * 3] : f * 3) * 3, i1 = (idx ? idx[f * 3 + 1] : f * 3 + 1) * 3, i2 = (idx ? idx[f * 3 + 2] : f * 3 + 2) * 3;
        a.set(pos[i0], pos[i0 + 1], pos[i0 + 2]); b.set(pos[i1], pos[i1 + 1], pos[i1 + 2]); c.set(pos[i2], pos[i2 + 1], pos[i2 + 2]);
        out[f] = b.sub(a).cross(c.sub(a)).normalize().clone();
    }
    return out;
}

function coplanarFaces(mesh, hitFaceIndex, mode = 'wall', facing = null) {
    // Flood fill from the hit face over faces that share a vertex *position* (web-ifc
    // duplicates vertices per triangle, so index adjacency would stop at every fan),
    // within 1 degree of the hit normal and on the same plane. "wall": vertical faces
    // only. "roof": level faces only (|n.y| within VERT_TOL of 1 in Three.js Y-up), and
    // the face the viewer sees (*facing*, the hit normal) has to face up.
    const geo = mesh.geometry, pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const normals = faceNormals(geo), hitN = normals[hitFaceIndex];
    const roof = mode === 'roof';
    if (roof ? (Math.abs(hitN.y) < 1 - VERT_TOL || (facing && facing.y <= 0)) : Math.abs(hitN.y) >= VERT_TOL) return null;
    const oriented = n => roof ? Math.abs(n.y) >= 1 - VERT_TOL : Math.abs(n.y) < VERT_TOL;
    const count = normals.length, vertToFaces = new Map();
    const keyOf = vi => { const p = vi * 3; return Math.round(pos[p] * 10) + ',' + Math.round(pos[p + 1] * 10) + ',' + Math.round(pos[p + 2] * 10); };
    for (let f = 0; f < count; f++) for (let v = 0; v < 3; v++) {
        const k = keyOf(idx ? idx[f * 3 + v] : f * 3 + v);
        if (!vertToFaces.has(k)) vertToFaces.set(k, []);
        vertToFaces.get(k).push(f);
    }
    const vi0 = (idx ? idx[hitFaceIndex * 3] : hitFaceIndex * 3) * 3;
    const d0 = hitN.x * pos[vi0] + hitN.y * pos[vi0 + 1] + hitN.z * pos[vi0 + 2];
    const collected = new Set([hitFaceIndex]), queue = [hitFaceIndex];
    while (queue.length) {
        const f = queue.shift();
        for (let v = 0; v < 3; v++) {
            const k = keyOf(idx ? idx[f * 3 + v] : f * 3 + v);
            for (const nb of vertToFaces.get(k) || []) {
                if (collected.has(nb)) continue;
                const n = normals[nb];
                const p = (idx ? idx[nb * 3] : nb * 3) * 3;
                const d = hitN.x * pos[p] + hitN.y * pos[p + 1] + hitN.z * pos[p + 2];
                if (Math.abs(n.dot(hitN)) >= 1 - VERT_TOL && oriented(n) && Math.abs(d - d0) < 5) {
                    collected.add(nb); queue.push(nb);
                }
            }
        }
    }
    return Array.from(collected);
}

function toIfc(v) { return [v.x, -v.z, v.y]; }

function faceTriangles(mesh, faceIndices) {
    mesh.updateWorldMatrix(true, false);
    const geo = mesh.geometry, pos = geo.attributes.position.array, idx = geo.index ? geo.index.array : null;
    const wm = mesh.matrixWorld, out = [], p = new THREE.Vector3();
    for (const f of faceIndices) {
        const tri = [];
        for (let v = 0; v < 3; v++) {
            const vi = (idx ? idx[f * 3 + v] : f * 3 + v) * 3;
            p.set(pos[vi], pos[vi + 1], pos[vi + 2]).applyMatrix4(wm);
            tri.push(toIfc(p));
        }
        out.push(tri);
    }
    return out;
}

function meshTriangles(mesh) {
    const geo = mesh.geometry, idx = geo.index ? geo.index.array : null;
    const count = idx ? idx.length / 3 : geo.attributes.position.count / 3;
    return faceTriangles(mesh, Array.from({ length: count }, (_, i) => i));
}

const CONTEXT_TRI_BUDGET = 60000;   // the engine runs in a fixed WASM heap: past this it dies

function contextFor(pickedMeshes, tris, margin, outward) {
    // Every other element that could touch the picked face: its bounding box overlaps the
    // faces' box (grown by *margin*) AND some corner of it lies within reach of the face
    // plane, so elements wholly behind or wholly in front of the wall are never sent.
    const bb = new THREE.Box3();
    for (const t of tris) for (const q of t) bb.expandByPoint(new THREE.Vector3(q[0], q[2], -q[1]));
    bb.expandByScalar(margin);
    const n = new THREE.Vector3(outward[0], outward[2], -outward[1]).normalize();
    const d = n.dot(new THREE.Vector3(tris[0][0][0], tris[0][0][2], -tris[0][0][1]));
    const near = [];
    for (const meta of meshMeta) {
        if (pickedMeshes.has(meta.mesh)) continue;
        const mb = meta.mesh.geometry.boundingBox.clone().applyMatrix4(meta.mesh.matrixWorld);
        if (!mb.intersectsBox(bb)) continue;
        let lo = Infinity, hi = -Infinity;
        for (const x of [mb.min.x, mb.max.x]) for (const y of [mb.min.y, mb.max.y]) for (const z of [mb.min.z, mb.max.z]) {
            const s = n.x * x + n.y * y + n.z * z - d; lo = Math.min(lo, s); hi = Math.max(hi, s);
        }
        if (hi < -margin || lo > margin) continue;
        near.push({ meta, reach: Math.min(Math.abs(lo), Math.abs(hi)) });   // how close it comes to the face
    }
    // A big model can offer far more geometry than the engine's heap will hold, and
    // overrunning it kills the runtime outright rather than raising. Send the elements
    // that come closest to the face and report the rest rather than risking that.
    near.sort((a, b) => a.reach - b.reach);
    const elements = [];
    let used = 0, dropped = 0;
    for (const { meta } of near) {
        const t = meshTriangles(meta.mesh);
        if (used && used + t.length > CONTEXT_TRI_BUDGET) { dropped++; continue; }
        used += t.length;
        elements.push({ type: meta.type, name: meta.name, tris: t });
    }
    return { elements, dropped, triangles: used };
}

const ROOF_REACH_UP = 3500;    // mm above the structure: walls this tall classify an abutment
const ROOF_REACH_DOWN = 600;   // and below it: penetrations crossing the deck plane
const ROOF_MARGIN = 800;       // mm round the picked face on plan

function contextForRoof(pickedMeshes, tris) {
    // Every other element that could bear on a roof: on plan it overlaps the picked face
    // grown by ROOF_MARGIN, and it reaches the band from just under the structure to well
    // above it, so the walls round the edge, their doors and anything through the deck come
    // along, and the floors below and the storeys above do not.
    const bb = new THREE.Box3();
    for (const t of tris) for (const q of t) bb.expandByPoint(new THREE.Vector3(q[0], q[2], -q[1]));
    const datum = tris.reduce((a, t) => a + t[0][2], 0) / Math.max(1, tris.length);
    const plan = bb.clone();
    plan.min.x -= ROOF_MARGIN; plan.max.x += ROOF_MARGIN; plan.min.z -= ROOF_MARGIN; plan.max.z += ROOF_MARGIN;
    plan.min.y = datum - ROOF_REACH_DOWN; plan.max.y = datum + ROOF_REACH_UP;
    const near = [];
    for (const meta of meshMeta) {
        if (pickedMeshes.has(meta.mesh)) continue;
        const mb = meta.mesh.geometry.boundingBox.clone().applyMatrix4(meta.mesh.matrixWorld);
        if (!mb.intersectsBox(plan)) continue;
        const reach = Math.max(0, mb.min.y - datum, datum - mb.max.y);
        near.push({ meta, reach });
    }
    // Closest to the deck first, within the engine's heap (see CONTEXT_TRI_BUDGET).
    near.sort((a, b) => a.reach - b.reach);
    const elements = [];
    let used = 0, dropped = 0;
    for (const { meta } of near) {
        const t = meshTriangles(meta.mesh);
        if (used && used + t.length > CONTEXT_TRI_BUDGET) { dropped++; continue; }
        used += t.length;
        elements.push({ type: meta.type, name: meta.name, tris: t });
    }
    return { elements, dropped, triangles: used };
}

// Where a mouse event meets the level plane at IFC height *z*, as IFC [x, y, z].
function pointOnLevel(event, z) {
    const rect = renderer.domElement.getBoundingClientRect();
    const mouse = new THREE.Vector2(((event.clientX - rect.left) / rect.width) * 2 - 1,
                                    -((event.clientY - rect.top) / rect.height) * 2 + 1);
    const rc = new THREE.Raycaster();
    rc.setFromCamera(mouse, activeCamera());
    const hit = new THREE.Vector3();
    if (!rc.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), -z), hit)) return null;
    return toIfc(hit);
}

// Screen position (px within the canvas) of a Three.js point.
function toScreen(p) {
    const q = p.clone().project(activeCamera());
    const w = renderer.domElement.clientWidth, h = renderer.domElement.clientHeight;
    return { x: (q.x + 1) / 2 * w, y: (1 - q.y) / 2 * h, off: Math.abs(q.x) > 1.05 || Math.abs(q.y) > 1.05 || q.z > 1 };
}

function highlightFaces(mesh, faceIndices, color = PICK_COLOR) {
    const tris = faceTriangles(mesh, faceIndices), positions = [];
    for (const t of tris) for (const q of t) positions.push(q[0], q[2], -q[1]);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
    const hl = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.45, side: THREE.DoubleSide, depthTest: false }));
    highlightGroup.add(hl);
    return hl;
}

// ─── PRISM RENDERING ───
// IFC (x, y, z) Z-up → Three.js (x, z, -y) Y-up.
function v3(a) { return new THREE.Vector3(a[0], a[2] || 0, -a[1]); }

function frameMatrix(frame, depth) {
    // Basis from the frame's own u, v, n: a wall stands (v is world Z, the default for old
    // data), a roof lies flat (n is world Z), a trim runs along an edge (n along it).
    const u = v3(frame.u), n = v3(frame.n), v = v3(frame.v || [0, 0, 1]);
    const o = v3(frame.origin).addScaledVector(n, depth || 0);
    return new THREE.Matrix4().makeBasis(u, v, n).setPosition(o);
}

// Frame-local point (u, v, depth) → Three.js world.
function frameWorld(frame, u, v, d) {
    return new THREE.Vector3(u, v, d || 0).applyMatrix4(frameMatrix(frame, 0));
}

function shapeFromRings(profile, holes) {
    const shape = new THREE.Shape(profile.map(p => new THREE.Vector2(p[0], p[1])));
    for (const h of holes || []) shape.holes.push(new THREE.Path(h.map(p => new THREE.Vector2(p[0], p[1]))));
    return shape;
}

const _layerOf = { firring: 'firring', sump_firring: 'firring', deck: 'deck', sump_deck: 'deck',
                   vcl: 'vcl', sump_vcl: 'vcl', vcl_upstand: 'vcl', insulation: 'insulation',
                   sump_insulation: 'insulation', ins_upstand: 'insulation', membrane: 'membrane',
                   sump_membrane: 'membrane', upstand: 'membrane', counter_flashing: 'trims', drip_trim: 'trims',
                   kerb: 'kerbs', outlet: 'outlets', sleeve: 'outlets', hopper: 'outlets', penetration_cut: 'outlets' };

// A slab: plan rings in a roof frame between two planes z = a + b·u + c·v. Built in the
// frame's local coordinates, caps triangulated in plan, vertical sides.
function slabGeometry(m) {
    const z = (pl, x, y) => pl[0] + pl[1] * x + pl[2] * y;
    const contour = m.rings[0].map(p => new THREE.Vector2(p[0], p[1]));
    const holes = m.rings.slice(1).map(r => r.map(p => new THREE.Vector2(p[0], p[1])));
    const tris = THREE.ShapeUtils.triangulateShape(contour, holes);
    const all = contour.concat(...holes);
    const pos = [];
    const push = (p, pl) => pos.push(p.x, p.y, z(pl, p.x, p.y));
    for (const [a, b, c] of tris) {
        push(all[a], m.top); push(all[b], m.top); push(all[c], m.top);
        push(all[a], m.bot); push(all[c], m.bot); push(all[b], m.bot);
    }
    for (const ring of [contour].concat(holes)) {
        for (let i = 0; i < ring.length; i++) {
            const p = ring[i], q = ring[(i + 1) % ring.length];
            push(p, m.bot); push(q, m.bot); push(q, m.top);
            push(p, m.bot); push(q, m.top); push(p, m.top);
        }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    geo.computeVertexNormals();
    return geo;
}

function applyLift(geo, m) {
    // A section swept along a sloping edge rises from lift[0] to lift[1] along the extrusion.
    if (!m.lift) return;
    const pos = geo.attributes.position, t = m.thickness || 1;
    for (let i = 0; i < pos.count; i++) pos.setY(i, pos.getY(i) + m.lift[0] + (m.lift[1] - m.lift[0]) * (pos.getZ(i) / t));
    pos.needsUpdate = true;
    geo.computeVertexNormals();
}

function applyCorner(geo, m, tol = 0.6) {
    // Move the vertices on a corner end to u_end -/+ (ext + k x depth): k shears the
    // end onto the corner's bisector (a mitre), ext runs it past square (a lap).
    // Local z runs 0..thickness along the wall normal, so a vertex is at m.depth + z.
    const c = m.corner;
    if (!c) return;
    const pos = geo.attributes.position;
    for (let i = 0; i < pos.count; i++) {
        const x = pos.getX(i), s = m.depth + pos.getZ(i);
        if ((c.k_l || c.ext_l) && Math.abs(x - c.u_l) < tol) pos.setX(i, x - ((c.ext_l || 0) + (c.k_l || 0) * s));
        else if ((c.k_r || c.ext_r) && Math.abs(x - c.u_r) < tol) pos.setX(i, x + ((c.ext_r || 0) + (c.k_r || 0) * s));
    }
    pos.needsUpdate = true;
    geo.computeVertexNormals();
}

function renderGeometry(meshes) {
    clearGroup(cladGroup);
    const byLayer = {};
    for (const m of meshes) {
        const layer = _layerOf[m.ifc_type] || 'trims';
        if (!byLayer[layer]) { byLayer[layer] = new THREE.Group(); byLayer[layer].name = layer; cladGroup.add(byLayer[layer]); }
        let geo;
        try {
            if (m.type === 'slab') geo = slabGeometry(m);
            else geo = new THREE.ExtrudeGeometry(shapeFromRings(m.profile, m.holes), { depth: m.thickness, bevelEnabled: false });
        } catch (e) { continue; }
        if (m.type !== 'slab') { applyCorner(geo, m); applyLift(geo, m); }
        const mat = new THREE.MeshPhongMaterial({ color: new THREE.Color(m.color), flatShading: true, transparent: m.opacity < 1,
                                                  opacity: m.opacity, side: THREE.DoubleSide, depthWrite: m.opacity >= 1 });
        const mesh = new THREE.Mesh(geo, mat);
        mesh.matrixAutoUpdate = false;
        mesh.matrix.copy(frameMatrix(m.frame, m.type === 'slab' ? 0 : m.depth));
        mesh.userData.name = m.name;
        mesh.userData.elevation = m.roof;
        byLayer[layer].add(mesh);
        if (m.opacity >= 1 || m.ifc_type === 'membrane') {
            const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 20), new THREE.LineBasicMaterial({ color: 0x1b2a3a, transparent: true, opacity: 0.6 }));
            edges.matrixAutoUpdate = false; edges.matrix.copy(mesh.matrix);
            edges.userData.elevation = m.roof;
            byLayer[layer].add(edges);
        }
    }
    for (const key of Object.keys(byLayer)) byLayer[key].visible = layerVisible[key] !== false;
    filter2D();          // flat, only the elevation being looked at is drawn
}

function ringLine(ring, frame, depth, color, dashed) {
    const pts = ring.map(p => new THREE.Vector3(p[0], p[1], 0));
    pts.push(pts[0].clone());
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const mat = dashed ? new THREE.LineDashedMaterial({ color, dashSize: 60, gapSize: 40 }) : new THREE.LineBasicMaterial({ color });
    const line = new THREE.Line(geo, mat);
    if (dashed) line.computeLineDistances();
    line.matrixAutoUpdate = false; line.matrix.copy(frameMatrix(frame, depth));
    return line;
}

function renderOutlines(roofs) {
    // Each roof's outline and holes, on the top of the structure.
    clearGroup(outlineGroup);
    for (const r of roofs) {
        for (const poly of r.polygons) {
            outlineGroup.add(ringLine(poly.exterior, r.frame, 2, OUTLINE_COLOR, false));
            for (const h of poly.holes) outlineGroup.add(ringLine(h, r.frame, 2, 0xff5c5c, false));
        }
    }
}

function setLayerVisible(key, on) {
    layerVisible[key] = on;
    if (key === 'model') { modelGroup.visible = on; return; }
    if (key === 'faces') { highlightGroup.visible = on; return; }     // the picked roof face
    if (key === 'outline') { outlineGroup.visible = on; return; }     // outline and holes
    for (const g of cladGroup.children) if (g.name === key) g.visible = on;
    for (const g of planGroup.children) if (g.name === key) g.visible = on;
    if (typeof renderPlanLabels === 'function') renderPlanLabels();
}
