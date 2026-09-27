// Browser smoke test: load the app in Chromium, import the sample roof, pick the top of
// the structure, build it through the wizard (placing two outlets and a gutter edge in
// plan), drag a sump, type over a fall, and download both exports.
// Run: node tests/smoke.js  (needs the Flask server on :8080; VENDOR_DIR where CDNs are blocked)
const path = require('path');
const fs = require('fs');
let page = null, logs = [], logsAll = [];

async function main() {
    const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
    // Only https:// goes through the egress proxy (CDNs); the local Flask app is plain http.
    const proxyHost = (process.env.HTTPS_PROXY || '').replace(/^https?:\/\//, '');
    const args = ['--use-gl=swiftshader', '--enable-unsafe-swiftshader'];
    if (proxyHost) args.push('--proxy-server=https=' + proxyHost + ';http=direct://');
    const browser = await chromium.launch({ headless: true, args });
    page = await browser.newPage({ viewport: { width: 1500, height: 900 }, acceptDownloads: true });
    logs = [];
    // VENDOR_DIR: serve the CDN runtimes from local copies (sandboxes that block CDNs).
    // Expected layout: <dir>/pyodide/*, <dir>/three/{build,examples}, <dir>/web-ifc/*,
    // <dir>/wasm-wheels/* (the IfcOpenShell wheel the browser installs for IFC export).
    const vendor = process.env.VENDOR_DIR;
    if (vendor) {
        const mime = { js: 'application/javascript', wasm: 'application/wasm', json: 'application/json', zip: 'application/zip', whl: 'application/octet-stream' };
        const map = [
            [/^https:\/\/cdn\.jsdelivr\.net\/pyodide\/v0\.29\.0\/full\/(.+)$/, m => path.join(vendor, 'pyodide', m[1])],
            [/^https:\/\/cdnjs\.cloudflare\.com\/ajax\/libs\/three\.js\/r128\/three\.min\.js$/, () => path.join(vendor, 'three', 'build', 'three.min.js')],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/three@0\.128\.0\/(.+)$/, m => path.join(vendor, 'three', m[1])],
            [/^https:\/\/cdn\.jsdelivr\.net\/npm\/web-ifc@[\d.]+\/(.+)$/, m => path.join(vendor, 'web-ifc', m[1])],
            [/^https:\/\/ifcopenshell\.github\.io\/wasm-wheels\/(.+)$/, m => path.join(vendor, 'wasm-wheels', m[1])],
            [/^https:\/\/files\.pythonhosted\.org\/vendored\/(.+)$/, m => path.join(vendor, 'pypi', m[1])],
        ];
        // micropip resolves the IFC export's pure-Python deps through the PyPI simple
        // index; answer it from <dir>/pypi so the install needs no network either.
        await page.route(/^https:\/\/pypi\.org\/simple\/([^/]+)\//, route => {
            const name = route.request().url().match(/simple\/([^/]+)\//)[1];
            const files = fs.existsSync(path.join(vendor, 'pypi'))
                ? fs.readdirSync(path.join(vendor, 'pypi')).filter(f => f.toLowerCase().startsWith(name.replace(/-/g, '_').toLowerCase() + '-')) : [];
            if (!files.length) { logs.push('vendor miss: pypi ' + name); return route.fulfill({ status: 404, body: '' }); }
            return route.fulfill({ contentType: 'application/vnd.pypi.simple.v1+json', body: JSON.stringify({
                meta: { 'api-version': '1.0' }, name,
                files: files.map(f => ({ filename: f, url: 'https://files.pythonhosted.org/vendored/' + f, hashes: {} })) }) });
        });
        await page.route(/^https:\/\/(cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|ifcopenshell\.github\.io|files\.pythonhosted\.org)\//, route => {
            const url = route.request().url().split('?')[0];
            for (const [re, fn] of map) {
                const m = url.match(re);
                if (!m) continue;
                const file = fn(m);
                if (!fs.existsSync(file)) { logs.push('vendor miss: ' + url); return route.fulfill({ status: 404, body: '' }); }
                const ext = file.split('.').pop();
                return route.fulfill({ path: file, contentType: mime[ext] || 'application/octet-stream' });
            }
            logs.push('unmapped cdn: ' + url);
            return route.fulfill({ status: 404, body: '' });
        });
    }
    page.on('console', m => { if (m.type() === 'error' || m.type() === 'warning') logs.push(m.type() + ': ' + m.text().slice(0, 300)); });
    page.on('console', m => logsAll.push(m.text()));
    page.on('pageerror', e => logs.push('pageerror: ' + e.message));
    page.on('requestfailed', r => logs.push('requestfailed: ' + r.url() + ' ' + (r.failure() && r.failure().errorText)));
    await page.goto('http://127.0.0.1:8080/', { waitUntil: 'load' });

    await page.setInputFiles('#ifc-file', path.join(__dirname, 'sample_roof.ifc'));
    await page.waitForFunction(() => typeof allMeshes !== "undefined" && allMeshes.length > 0, null, { timeout: 120000 });
    console.log('model:', await page.evaluate(() => document.getElementById('model-info').textContent));
    await page.waitForFunction(() => typeof pyReady !== "undefined" && pyReady === true, null, { timeout: 300000 });
    console.log('engine ready');
    const box = await page.locator('#viewport canvas').boundingBox();
    const shot = name => page.screenshot({ path: path.join(__dirname, name) });

    // Look down on the roof and click a point on the slab's top, clear of the rooflight.
    const at = await page.evaluate(() => {
        const meta = meshMeta.find(m => m.name === 'Roof slab');
        const bb = new THREE.Box3().setFromObject(meta.mesh);
        const c = bb.getCenter(new THREE.Vector3());
        camera.position.set(c.x + 2000, c.y + 16000, c.z + 6000);
        controls.target.copy(c);
        controls.update();
        camera.updateMatrixWorld();
        const s = toScreen(new THREE.Vector3(bb.min.x + 1500, bb.max.y, bb.max.z - 1500));
        return s;
    });
    await page.waitForTimeout(300);
    const eye = () => page.evaluate(() => camera.position.toArray().map(Math.round).join(','));
    const before = await eye();
    await page.mouse.click(box.x + at.x, box.y + at.y);
    await page.waitForFunction(() => state.roofs.length && state.roofs[0].result && state.roofs[0].result.ok, null, { timeout: 60000 });
    console.log('camera untouched by the pick:', before === await eye());
    const r = await page.evaluate(() => state.roofs[0].result);
    console.log('roof:', r.name, r.width, 'x', r.height, 'at', r.datum_z, 'holes', r.holes.map(h => h.kind).join(','),
                'warnings', JSON.stringify(r.warnings));
    console.log('edges:', r.edges.filter(e => e.ring === 0).map(e => `${e.id} ${e.type}${e.doors.length ? ' +door sill ' + e.doors[0].sill : ''}`).join(' | '));
    // Debugging a stuck roof: the engine's stage lines reached the console live, the dump
    // carries the timing, and the payload saves to a file that replays offline.
    const pyLines = logsAll.filter(l => l.startsWith('[fallwright:py]'));
    console.log('live engine lines:', pyLines.length, '| stages seen:',
                ['plane fit', 'plan union', 'wall records', 'wall feet', 'penetrations', 'cleanup', 'classify', 'done in']
                    .map(s => s + ':' + pyLines.some(l => l.includes('Roof 1: ' + s))).join(' '),
                '| per element:', pyLines.some(l => /walls \d+\/\d+ /.test(l)));
    console.log('fallwright() timing:', await page.evaluate(() => { const d = fallwright(); const t = d.roofs[0].timing;
        return t && Object.keys(t.stages).join(',') + ' | slowest ' + t.slowest.map(s => s.name + ' ' + s.ms + 'ms').join(', ')
            + ' | last_payload ' + (d.last_payload && d.last_payload.roof); }));
    const [saved] = await Promise.all([page.waitForEvent('download'), page.evaluate(() => fallwright.payload())]);
    const savedPath = path.join(require('os').tmpdir(), saved.suggestedFilename());
    await saved.saveAs(savedPath);
    const replay = require('child_process').spawnSync(process.env.PYTHON || 'python', [path.join(__dirname, 'replay.py'), savedPath], { encoding: 'utf8' });
    console.log('payload:', saved.suggestedFilename(), '| replay exit', replay.status, '|',
                (replay.stdout.match(/result: .*/) || ['no result'])[0]);
    console.log('nothing built yet:', await page.evaluate(() => cladGroup.children.length === 0 && !window._lastPreview));

    // Build: Enter opens the wizard; two outlets are placed in plan.
    await page.waitForSelector('#make-roof', { state: 'visible', timeout: 10000 });
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press('Enter');
    await page.waitForSelector('#roof-wizard.open', { timeout: 10000 });
    console.log('wizard:', await page.evaluate(() => document.getElementById('wiz-title').textContent));
    await page.fill('#wiz-count', '2');
    await page.click('#wiz-next');
    await page.waitForSelector('#place-widget', { state: 'visible', timeout: 10000 });
    await page.waitForTimeout(900);          // the swing into plan view
    console.log('in plan:', await page.evaluate(() => in2D()), '| widget:', await page.evaluate(() => document.querySelector('#place-widget .edit-head span').textContent));
    async function clickCorner(i) {
        const s = await page.evaluate(i => { const r = state.placing.roof; return screenOf(r, exteriorOf(r)[i]); }, i);
        await page.mouse.click(box.x + s.x + 3, box.y + s.y - 2);
        await page.waitForTimeout(150);
    }
    async function clickEdge(id) {
        const s = await page.evaluate(id => { const r = state.placing.roof; const e = r.result.edges.find(x => x.id === id);
            return screenOf(r, [(e.a[0] * 0.3 + e.b[0] * 0.7), (e.a[1] * 0.3 + e.b[1] * 0.7)]); }, id);
        await page.mouse.click(box.x + s.x, box.y + s.y);
        await page.waitForTimeout(150);
    }
    // Outlet 1: a hopper through the east parapet (E2), 1500 from its start corner (1).
    await clickCorner(1);
    await clickEdge('E2');
    console.log('  stage after corner + edge:', await page.evaluate(() => state.placing.stage + ' on ' + state.placing.edge));
    await page.fill('#place-offset', '1500');
    await page.click('#place-type .turn-btn[data-value="hopper"]');
    await page.click('#place-widget .btn-primary');
    // Outlet 2: an internal outlet with a sump on the south edge (E1), 6000 from corner 0.
    await clickCorner(0);
    await clickEdge('E1');
    await page.fill('#place-offset', '6000');
    // A hopper on a free edge is refused.
    const refused = await page.evaluate(() => document.querySelector('#place-type .turn-btn[data-value="hopper"]').disabled);
    console.log('  hopper refused on a free edge:', refused);
    await page.check('#place-sump');
    await page.click('#place-widget .btn-primary');
    // Gutter edge: the west side.
    await clickEdge('E4');
    console.log('  gutters:', await page.evaluate(() => JSON.stringify(state.placing.roof.edgeTypes)));
    await shot('smoke_placing.png');
    await page.click('#place-done');
    await page.waitForSelector('#roof-wizard.open', { timeout: 10000 });
    for (let i = 0; i < 10; i++) {
        const step = await page.evaluate(() => [document.getElementById('wiz-title').textContent,
            Array.from(document.querySelectorAll('#wiz-body input')).map(n => n.id.replace('wiz-', '') + '=' + n.value).join(' '),
            document.getElementById('wiz-next').textContent]);
        console.log('  wizard step:', step.slice(0, 2).join(' | '));
        await page.click('#wiz-next');
        if (step[2] === 'Build') break;
        await page.waitForTimeout(100);
    }
    await page.waitForFunction(() => window._lastPreview && cladGroup.children.length > 0, null, { timeout: 120000 });
    const counts = await page.evaluate(() => { const c = {}; cladGroup.children.forEach(g => c[g.name] = g.children.length); return c; });
    console.log('preview layers:', JSON.stringify(counts));
    const falls = await page.evaluate(() => { const f = roofFalls(state.roofs[0]); return {
        facets: f.facets.map(x => x.id + ' ' + x.label).join(', '), valleys: f.creases.filter(c => c.kind === 'valley').map(c => c.label).join(','),
        sumps: f.sumps.map(s => `${s.kind} ${s.edge} r${Math.round(s.r)} drop ${s.drop === null ? '-' : Math.round(s.drop)}`).join(' | '),
        depth: [Math.round(f.min_depth), Math.round(f.max_depth)], ponding: f.ponding }; });
    console.log('falls:', JSON.stringify(falls));
    console.log('checks:', await page.evaluate(() => Array.from(document.querySelectorAll('.check-item')).map(c =>
        c.querySelector('.check-dot').className.split(' ')[1] + ' ' + c.textContent.slice(0, 90)).join('\n        ')));
    console.log('labels in plan:', await page.evaluate(() => D2.items.length), '| overlay lines:', await page.evaluate(() => planGroup.children.reduce((a, g) => a + g.children.length, 0)));
    await shot('smoke_plan.png');

    // Zoom in on the hopper's sump, as a user would, then drag it 300 along its edge: the
    // facets follow while dragging, the layers rebuild on release.
    await page.evaluate(() => { window._savedView = { pos: controls.object.position.clone(), target: controls.target.clone(), zoom: controls.object.zoom };
        const r = state.roofs[0], s = roofFalls(r).sumps.find(x => x.outlet === 0);
        const c = planWorld(r, s.rect.reduce((a, q) => a + q[0], 0) / 4, s.rect.reduce((a, q) => a + q[1], 0) / 4, s.rim);
        controls.object.position.add(c.clone().sub(controls.target)); controls.target.copy(c);
        controls.object.zoom *= 6; controls.object.updateProjectionMatrix(); controls.update(); });
    await page.waitForTimeout(400);
    const sumpHandle = await page.evaluate(() => { const it = D2.items.find(i => i.drag === 'sump' && i.k === 0);
        return it && it.el.style.display !== 'none' && { x: parseFloat(it.el.style.left), y: parseFloat(it.el.style.top) }; });
    const t0 = await page.evaluate(() => roofFalls(state.roofs[0]).sumps.find(s => s.outlet === 0).b1);
    if (sumpHandle) {
        const pxPerMm = await page.evaluate(() => { const r = state.roofs[0], a = screenOf(r, [10000, 0]), b = screenOf(r, [10000, 1000]); return Math.hypot(b.x - a.x, b.y - a.y) / 1000; });
        await page.mouse.move(box.x + sumpHandle.x, box.y + sumpHandle.y);
        console.log('  under the pointer:', await page.evaluate(([x, y]) => { const r = document.getElementById('viewport').getBoundingClientRect();
            const el = document.elementFromPoint(r.left + x, r.top + y); return el && (el.className + ' ' + el.textContent); }, [sumpHandle.x, sumpHandle.y]));
        await page.mouse.down();
        console.log('  dragging:', await page.evaluate(() => !!state.dragging && state.dragging.item && state.dragging.item.drag));
        for (let k = 1; k <= 10; k++) { await page.mouse.move(box.x + sumpHandle.x, box.y + sumpHandle.y - 30 * pxPerMm * k); await page.waitForTimeout(40); }
        await page.mouse.up();
        await page.waitForTimeout(2500);
        await shot('smoke_drag.png');
        const t1 = await page.evaluate(() => roofFalls(state.roofs[0]).sumps.find(s => s.outlet === 0).b1);
        console.log('sump drag: b1', Math.round(t0), '->', Math.round(t1), '| outlet 1 still inside:', await page.evaluate(() => {
            const f = roofFalls(state.roofs[0]), s = f.sumps.find(x => x.outlet === 0), o = f.outlets[0]; return o.t >= s.b1 - 0.5 && o.t <= s.b2 + 0.5; }));
    } else console.log('sump drag: NO HANDLE');

    // Back to the whole roof, then type over the main fall where it sits in plan.
    await page.evaluate(() => { const v = window._savedView; controls.object.position.copy(v.pos); controls.target.copy(v.target);
        controls.object.zoom = v.zoom; controls.object.updateProjectionMatrix(); controls.update(); });
    await page.waitForTimeout(300);
    const fallLabel = await page.evaluate(() => { const it = D2.items.find(i => i.kind === 'fall');
        return it && { x: parseFloat(it.el.style.left), y: parseFloat(it.el.style.top) }; });
    await page.mouse.click(box.x + fallLabel.x, box.y + fallLabel.y);
    await page.fill('#d2-input', '30');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(2500);
    console.log('main fall typed over:', await page.evaluate(() => document.getElementById('fall').value), '| facets now',
                await page.evaluate(() => roofFalls(state.roofs[0]).facets.map(f => f.label).join(',')));

    // Back to 3D, then the DXF through its cutting planes.
    await page.keyboard.press('Escape');
    await page.waitForTimeout(900);
    console.log('back in 3D:', await page.evaluate(() => !in2D()));
    await shot('smoke_3d.png');
    await page.click('#dxf-btn');
    await page.waitForSelector('#cut-widget', { state: 'visible', timeout: 10000 });
    await page.waitForTimeout(900);
    console.log('cut planes:', await page.evaluate(() => JSON.stringify(state.roofs[0].cutPlanes)));
    const cutHandle = await page.evaluate(() => { const it = D2.items.find(i => i.drag === 'cut' && i.cut === 0);
        return it && { x: parseFloat(it.el.style.left), y: parseFloat(it.el.style.top) }; });
    await page.mouse.move(box.x + cutHandle.x, box.y + cutHandle.y);
    await page.mouse.down();
    await page.mouse.move(box.x + cutHandle.x, box.y + cutHandle.y + 60, { steps: 5 });
    await page.mouse.up();
    await page.click('#cut-widget button:has-text("+ along v")');
    console.log('cut planes moved and added:', await page.evaluate(() => JSON.stringify(state.roofs[0].cutPlanes)));
    await shot('smoke_cuts.png');
    const dl = [];
    page.on('download', d => dl.push(d));
    await page.click('#cut-export');
    await page.waitForSelector('#download-reminder.open', { timeout: 60000 });
    await page.click('#download-reminder .btn-primary');
    await page.waitForTimeout(1500);
    const dxfText = dl.length ? fs.readFileSync(await dl[0].path(), 'utf8') : '';
    console.log('dxf:', dl.length ? dl[0].suggestedFilename() : 'none', dxfText.length, 'bytes',
                ['SECTION A-A', 'SECTION B-B', 'SECTION C-C', 'DETAIL: ABUTMENT', 'DETAIL: PARAPET', 'HOPPER DETAIL', 'OUTLET SCHEDULE'].map(k => k + ':' + dxfText.includes(k)).join(' '));

    // IFC4X3 in the browser, through the IfcOpenShell WASM wheel.
    await page.click('#ifc-btn');
    await page.waitForSelector('#download-reminder.open', { timeout: 300000 });
    await page.click('#download-reminder .btn-primary');
    await page.waitForTimeout(1500);
    const ifcText = dl.length > 1 ? fs.readFileSync(await dl[1].path(), 'utf8') : '';
    console.log('ifc:', dl.length > 1 ? dl[1].suggestedFilename() : 'none', ifcText.length, 'bytes',
                'IFC4X3:', /FILE_SCHEMA\(\('IFC4X3/.test(ifcText), 'assembly:', ifcText.includes('Warm roof system'),
                'breps:', (ifcText.match(/IFCFACETEDBREP\(/g) || []).length, 'Pset_Fallwright:', ifcText.includes('Pset_Fallwright'),
                '| on the host model (slab corner 0,0,3000):', /IFCCARTESIANPOINT\(\(0\.,0\.,3000\.\)\)/.test(ifcText));

    // A dead engine is rebuilt rather than bricking the session.
    console.log('engine restart:', await page.evaluate(async () => { const ok = await restartEngine(); return ok && pyReady; }));
    await page.waitForTimeout(2000);
    console.log('preview after restart:', await page.evaluate(() => !!window._lastPreview && cladGroup.children.length > 0));

    // The server reader (IfcOpenShell) arrives Z-up and is turned Y-up like web-ifc's: the
    // slab must come back level, with its top at the same height.
    await page.evaluate(() => retryOnServer());
    await page.waitForFunction(() => state.model && /server/.test(state.model.reader) && allMeshes.length > 0, null, { timeout: 120000 });
    console.log('server import:', await page.evaluate(() => { const m = meshMeta.find(x => x.name === 'Roof slab');
        const bb = new THREE.Box3().setFromObject(m.mesh), s = bb.getSize(new THREE.Vector3());
        return `slab ${Math.round(s.x)} x ${Math.round(s.z)} x ${Math.round(s.y)} high, top at ${Math.round(bb.max.y)}, offset ${JSON.stringify(modelOffset)}`; }));

    const errors = logs.filter(l => /error|Error/.test(l) && !/favicon/.test(l));
    console.log('console errors:', errors.length ? errors.join('\n  ') : 'none');
    await browser.close();
}

main().catch(async err => {
    console.error('SMOKE FAILED:', err);
    console.error(logs.join('\n'));
    try { if (page) await page.screenshot({ path: path.join(__dirname, 'smoke_fail.png') }); } catch (e) { /* closed */ }
    process.exit(1);
});
