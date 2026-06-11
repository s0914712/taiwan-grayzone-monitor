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
const fetchLog = [];
window.fetch = (url) => {
    fetchLog.push(String(url));
    if (String(url).startsWith('vessel_routes/412345678.json')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(JSON.parse(JSON.stringify(ROUTE_FIXTURE))) });
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
    assert(fetchLog.some(u => u.startsWith('vessel_routes/412345678.json')), 'route file fetched');
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

    console.log('✅ map integration test passed —',
        `cluster=${clusterMarkers} detail=${detailMarkers} dark=${plotted} rings=2 route=OK`);
})().catch((e) => { console.error('❌', e); process.exit(1); });
