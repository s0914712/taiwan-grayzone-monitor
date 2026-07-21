/**
 * Map integration test — runs docs/js map modules inside jsdom with REAL
 * Leaflet 1.9.4, then exercises the MapModule public API end-to-end:
 * init, vessel rendering (cluster + detail), dark/suspicious/gov layers,
 * layer toggles, route loading via stubbed fetch, focus helpers.
 *
 * Acts as the regression gate for map.js refactors: the assertions must
 * pass identically before and after any split.
 *
 * Usage: node tests/map-integration.js
 * Requires: npm packages jsdom + leaflet (path via JSDOM_NM env or default
 * node_modules next to repo root / /tmp/jsdom-test/node_modules).
 */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const NM_CANDIDATES = [
    process.env.JSDOM_NM,
    path.join(__dirname, '..', 'node_modules'),
    '/tmp/jsdom-test/node_modules',
].filter(Boolean);
const NM = NM_CANDIDATES.find(p => fs.existsSync(path.join(p, 'jsdom')));
if (!NM) {
    console.error('jsdom not found — run: npm install jsdom leaflet@1.9.4');
    process.exit(2);
}
const { JSDOM } = require(path.join(NM, 'jsdom'));
const LEAFLET_SRC = fs.readFileSync(path.join(NM, 'leaflet', 'dist', 'leaflet.js'), 'utf8');

const DOCS_JS = path.join(__dirname, '..', 'docs', 'js');

// ── DOM + globals ────────────────────────────────────────────────────────
const dom = new JSDOM(`<!DOCTYPE html><html><body>
    <div id="map"></div>
    <input id="mmsiSearchInput">
</body></html>`, { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/' });
const { window } = dom;

// jsdom has no layout — give the map container a real size for Leaflet bounds
const mapEl = window.document.getElementById('map');
Object.defineProperty(mapEl, 'clientWidth', { value: 800 });
Object.defineProperty(mapEl, 'clientHeight', { value: 600 });

window.i18n = { t: (k) => k, getLang: () => 'zh' };

// fetch stub: serve a route file for one MMSI, 404 elsewhere
const ROUTE_FIXTURE = {
    mmsi: '412345678', name: 'MIN SHI YU 07771', type: 'fishing',
    track: [
        { t: '2026-06-09T00:00:00+00:00', lat: 24.0, lon: 120.5, speed: 5, heading: 90 },
        { t: '2026-06-09T02:00:00+00:00', lat: 24.1, lon: 120.6, speed: 6, heading: 95 },
        { t: '2026-06-09T04:00:00+00:00', lat: 24.2, lon: 120.7, speed: 4, heading: 80 },
    ],
};
// Seascape raster TileJSON fixture (bathymetry layer resolves it at runtime)
const TILEJSON_FIXTURE = {
    tilejson: '3.0.0',
    tiles: ['https://tiles.example.test/seascape/raster/{z}/{x}/{y}.png'],
    minzoom: 0, maxzoom: 14,
    attribution: '© Open Water Software, LLC',
};
const fetchLog = [];
window.fetch = (url) => {
    fetchLog.push(String(url));
    if (String(url).includes('data/vessel_routes/412345678.json')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(ROUTE_FIXTURE))) });
    }
    if (String(url).includes('seascape/raster.json')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(TILEJSON_FIXTURE))) });
    }
    return Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('404')) });
};

// ── Load Leaflet + map modules in page context ───────────────────────────
window.eval(LEAFLET_SRC);
assert(window.L && window.L.version === '1.9.4', 'real Leaflet loaded');

function loadScript(file, captureVar) {
    const src = fs.readFileSync(path.join(DOCS_JS, file), 'utf8');
    window.eval(src + `\n;window.__cap = typeof ${captureVar} !== 'undefined' ? ${captureVar} : undefined;`);
    if (window.__cap !== undefined) window[captureVar] = window.__cap;
}

// Load every map-related module in the same order as the HTML pages.
// (Extra files appear automatically once the HTML references them.)
const indexHtml = fs.readFileSync(path.join(__dirname, '..', 'docs', 'index.html'), 'utf8');
const scriptOrder = [...indexHtml.matchAll(/<script[^>]+src="js\/(map[^"]*\.js)"/g)].map(m => m[1]);
assert(scriptOrder.includes('map.js'), 'index.html loads map.js');
for (const f of scriptOrder) {
    loadScript(f, f === 'map.js' ? 'MapModule' : path.basename(f, '.js').replace(/-(\w)/g, (_, c) => c.toUpperCase()).replace(/^map/, 'Map'));
}
const MM = window.MapModule;
assert(MM, 'MapModule defined');

const VESSELS = [
    { mmsi: '412345678', name: 'MIN SHI YU 07771', lat: 24.0, lon: 120.5, speed: 5.2, heading: 90, type_name: 'fishing' },
    { mmsi: '413456789', name: 'HAIJING 2304', lat: 24.5, lon: 120.0, speed: 12.0, heading: 180, type_name: 'coastguard', gov_type: 'coastguard' },
    { mmsi: '477123456', name: 'EVER GIVEN', lat: 23.5, lon: 121.0, speed: 14.1, heading: 45, type_name: 'cargo', suspicious: true },
];

function countLayers(pred) {
    let n = 0;
    map.eachLayer(l => { if (pred(l)) n++; });
    return n;
}

// ── 1. init ──────────────────────────────────────────────────────────────
const map = MM.init('map');
assert(map && typeof map.getZoom === 'function', 'init returns Leaflet map');
assert.strictEqual(map.getZoom(), 7, 'default zoom 7');

// ── 2. cluster-mode rendering (zoom 7 <= threshold 8) ────────────────────
let result = MM.renderVesselsForZoom(VESSELS, new window.Map());
assert.strictEqual(result.stats.total, 3, 'stats.total');
assert.strictEqual(result.stats.fishing, 1, 'stats.fishing');
assert.strictEqual(result.stats.suspicious, 1, 'stats.suspicious (cargo flagged)');
const clusterMarkers = countLayers(l => l instanceof window.L.Marker);
assert(clusterMarkers >= 1, 'cluster mode renders at least one cluster marker, got ' + clusterMarkers);

// ── 3. detail-mode rendering (zoom 10 > threshold) ───────────────────────
map.setView([24.0, 120.5], 10, { animate: false });
result = MM.renderVesselsForZoom();
const detailMarkers = countLayers(l => l instanceof window.L.Marker);
assert(detailMarkers >= 1, 'detail mode renders in-bounds vessel markers, got ' + detailMarkers);

// ── 4. dark vessels ──────────────────────────────────────────────────────
const plotted = MM.displayDarkVessels({
    regions: {
        taiwan_region: {
            dark_details: [
                { lat: 24.2, lon: 119.9, date: '2026-06-08', detections: 3 },
                { lat: 23.8, lon: 120.2, date: '2026-06-09', detections: 1 },
            ],
        },
    },
});
assert.strictEqual(plotted, 2, 'displayDarkVessels plots 2');

// ── 4b. dark popup enrichment (SAR×AIS re-match + chip forensics) ────────
// Inject match info AFTER render — popup content is a function evaluated on
// open, so late-loading data must still show up.
MM.setDarkMatchInfo({
    rematched: [
        { lat: 24.2, lon: 119.9, date: '2026-06-08', mmsi: '412000111', name: 'TEST REMATCH',
          type_name: 'fishing', distance_km: 1.7, gate_km: 3.6, dt_minutes: 22,
          method: 'interpolated', pass: 'descending·S1A' },
    ],
    residual_dark: [
        { lat: 23.8, lon: 120.2, date: '2026-06-09', zone: 'eez', in_ais_coverage: true },
    ],
}, [
    { lat: 23.8, lon: 120.2, date: '2026-06-09', found: true, peak_ratio: 14.2, length_m: 85.0,
      n_pixels: 37, time: '21:52:39', saturated: false, error: null,
      product: 'S1A_IW_GRDH_1SDV_20260609T215239_20260609T215304_TEST.SAFE',
      png: 'chips/2026-06-09_23.8_120.2.png' },
]);

function darkPopupHtml(lat, lon) {
    let html = null;
    map.eachLayer(l => {
        if (l instanceof window.L.CircleMarker && l.getPopup()) {
            const ll = l.getLatLng();
            if (Math.abs(ll.lat - lat) < 1e-6 && Math.abs(ll.lng - lon) < 1e-6) {
                const c = l.getPopup().getContent();
                html = typeof c === 'function' ? c(l) : c;
            }
        }
    });
    return html;
}

const rematchHtml = darkPopupHtml(24.2, 119.9);
assert(rematchHtml && rematchHtml.includes('dv.match_rematched'), 'rematched popup carries match line');
assert(rematchHtml.includes('TEST REMATCH') && rematchHtml.includes('412000111'), 'rematched popup names the AIS vessel');
assert(rematchHtml.includes('dv.pos'), 'popup carries position line');
assert(rematchHtml.includes('descending·S1A'), 'rematched popup carries satellite pass');
assert(rematchHtml.includes('dv.method_interpolated') && rematchHtml.includes('Δt 22 min'),
    'rematched popup carries match method + Δt');
assert(rematchHtml.includes('gate 3.6 km'), 'rematched popup carries gating radius');

const residualHtml = darkPopupHtml(23.8, 120.2);
assert(residualHtml && residualHtml.includes('dv.match_residual'), 'residual popup carries residual-dark line');
assert(residualHtml.includes('dv.zone_eez'), 'residual popup carries maritime zone');
assert(residualHtml.includes('dv.chip_confirmed'), 'residual popup carries chip verdict (peak 14.2 ≥ 10)');
assert(residualHtml.includes('2026-06-09_23.8_120.2.png'), 'residual popup links the chip PNG');
assert(residualHtml.includes('21:52 UTC'), 'residual popup carries acquisition time');
assert(residualHtml.includes('37 px'), 'residual popup carries pixel count');
assert(residualHtml.includes('S1A_IW_GRDH_1SDV_20260609T215239_') && residualHtml.includes('…'),
    'residual popup carries truncated product name');

// ── 5. suspicious + gov vessel layers ────────────────────────────────────
const circlesBefore = countLayers(l => l instanceof window.L.CircleMarker);
MM.displaySuspiciousVessels({
    suspicious_vessels: [
        { mmsi: '477123456', names: ['EVER GIVEN'], risk_level: 'high', risk_score: 9, last_lat: 23.5, last_lon: 121.0, flags: ['測試'] },
    ],
});
MM.displayGovVessels(VESSELS);
const circlesAfter = countLayers(l => l instanceof window.L.CircleMarker);
assert.strictEqual(circlesAfter - circlesBefore, 2, 'one suspicious ring + one gov ring added');

// ── 6. layer toggle round-trip ───────────────────────────────────────────
MM.toggleLayer('vessels', false);
MM.toggleLayer('vessels', true);
MM.toggleLayer('darkVessels', false);
MM.toggleLayer('darkVessels', true);

// ── 7. focus helpers ─────────────────────────────────────────────────────
MM.focusPosition(24.5, 120.0, 11);
assert.strictEqual(map.getZoom(), 11, 'focusPosition sets zoom');
MM.focusVessel('412345678', result.vessels);

// ── 8. route loading via stubbed fetch ───────────────────────────────────
(async () => {
    await MM.loadVesselRoute('412345678');
    assert(fetchLog.some(u => u.includes('data/vessel_routes/412345678.json')), 'route file fetched');
    const polylines = countLayers(l => l instanceof window.L.Polyline && !(l instanceof window.L.Polygon));
    assert(polylines >= 1, 'route polyline drawn, got ' + polylines);

    MM.clearVesselRoute();
    const polylinesAfterClear = countLayers(l => l instanceof window.L.Polyline && !(l instanceof window.L.Polygon));
    assert(polylinesAfterClear < polylines, 'clearVesselRoute removes route');

    // searchVesselRoute reads the input box
    window.document.getElementById('mmsiSearchInput').value = '412345678';
    MM.searchVesselRoute();

    // misc accessors must not throw
    MM.setSuspiciousData({ all_classifications: [] });
    MM.setFilterFoc(true);
    MM.setFilterFoc(false);
    assert.strictEqual(typeof MM.getCableFaultStatus, 'function');

    // ── 9. Seascape bathymetry: TileJSON load + toggle + attribution ─────
    const bathyOk = await MM.loadBathymetry();
    assert.strictEqual(bathyOk, true, 'loadBathymetry resolves true from TileJSON fixture');
    assert(fetchLog.some(u => u.includes('seascape/raster.json')), 'bathymetry TileJSON fetched');

    const depthGrids = () => countLayers(l =>
        l instanceof window.L.GridLayer && !(l instanceof window.L.TileLayer));
    assert.strictEqual(depthGrids(), 0, 'bathymetry hidden before toggle');
    MM.toggleLayer('bathymetry', true);
    assert.strictEqual(depthGrids(), 1, 'bathymetry grid layer shown after toggle on');
    const attribEl = window.document.querySelector('.leaflet-control-attribution');
    assert(attribEl && attribEl.textContent.includes('Open Water Software'),
        'CC BY attribution shown while bathymetry is on');
    MM.toggleLayer('bathymetry', false);
    assert.strictEqual(depthGrids(), 0, 'bathymetry removed after toggle off');
    assert(!window.document.querySelector('.leaflet-control-attribution'),
        'attribution control removed with the layer');

    // Terrarium decode + depth colormap pure functions
    const bathy = window.MapBathymetryFactory(map, { bathymetry: window.L.layerGroup() });
    assert.strictEqual(bathy.decodeTerrarium(128, 0, 0), 0, 'Terrarium sea level decodes to 0 m');
    assert.strictEqual(bathy.decodeTerrarium(127, 156, 0), -100, 'Terrarium -100 m decodes');
    assert.strictEqual(bathy.depthColor(0)[3], 0, 'land/dry pixels transparent');
    assert(bathy.depthColor(100)[3] > 0, 'water pixels opaque');
    const shallow = bathy.depthColor(199), deep = bathy.depthColor(201);
    const jump = Math.abs(shallow[0] - deep[0]) + Math.abs(shallow[1] - deep[1]) + Math.abs(shallow[2] - deep[2]);
    assert(jump > 60, 'visible color break at the 200 m shelf edge, got ' + jump);

    console.log('✅ map integration test passed —',
        `cluster=${clusterMarkers} detail=${detailMarkers} dark=${plotted} rings=2 route=OK`);
})().catch((e) => { console.error('❌', e); process.exit(1); });
