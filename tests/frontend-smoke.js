/**
 * Frontend smoke test — evaluates docs/js modules with minimal browser stubs
 * and asserts MapData/MapModule load correctly with all exports intact.
 *
 * Usage: node tests/frontend-smoke.js  (run from repo root)
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DOCS_JS = path.join(__dirname, '..', 'docs', 'js');

const ctx = {
    console, Math, Date, Set, Map, JSON, RegExp, Promise,
    setTimeout, clearTimeout, setInterval, clearInterval,
    window: {}, navigator: {},
    document: { getElementById: () => null, addEventListener: () => {}, createElement: () => ({ style: {} }) },
    fetch: () => new Promise(() => {}),
    L: { divIcon: (o) => o },
    i18n: { t: (k) => k, getLang: () => 'zh' },
};
ctx.globalThis = ctx;
vm.createContext(ctx);

function loadScript(file, captureVar) {
    const src = fs.readFileSync(path.join(DOCS_JS, file), 'utf8');
    vm.runInContext(src + `\n;globalThis.__cap = ${captureVar};`, ctx, { filename: file });
    const captured = ctx.__cap;
    ctx[captureVar] = captured; // expose for subsequent scripts
    return captured;
}

const MapData = loadScript('map-data.js', 'MapData');
const MapModule = loadScript('map.js', 'MapModule');

assert(MapData && MapModule, 'modules defined');

// MapData pure helpers
assert.strictEqual(MapData.getMidFlag('412345678'), '中國');
assert.strictEqual(MapData.getMidFlag('416000000'), '台灣');
assert.strictEqual(MapData.getGovType({ name: 'HAIJING 2304' }), 'coastguard');
assert.strictEqual(MapData.getGovType({ name: 'XIANGYANGHONG 06' }), 'research');
assert.strictEqual(MapData.getGovType({ name: 'AN TONG JING TANG' }), null,
    'word boundary: must not match TONG JI');
assert.strictEqual(MapData._decodeNavStatus('1'), '錨泊');
assert.strictEqual(MapData.VESSEL_COLORS.fishing, '#00ff88');
assert(MapData.FOC_MIDS.has('351'), 'Panama is FOC');

const icon = MapData.createVesselIcon('#fff', false, 90, 'TEST"SHIP');
assert(icon.html.includes('aria-label="TEST&quot;SHIP"'), 'aria-label escaped');

const ring = MapData.offsetPolygonNm(
    [{ lat: 24, lon: 121 }, { lat: 25, lon: 121 }, { lat: 24.5, lon: 122 }], 12);
assert.strictEqual(ring.length, 4, 'offset polygon closed');

// MapModule public API must stay intact (consumed by app.js + inline HTML)
const REQUIRED_EXPORTS = [
    'init', 'drawFishingHotspots', 'displayVessels', 'renderVesselsForZoom',
    'displayDarkVessels', 'displaySuspiciousVessels', 'displayGovVessels',
    'toggleLayer', 'focusVessel', 'focusPosition', 'loadSubmarineCables',
    'loadCableFaultStatus', 'getCableFaultStatus', 'loadVesselRoute',
    'clearVesselRoute', 'searchVesselRoute', 'snapshotMap', 'setFilterFoc',
    'locateVesselType', 'drawTerritorialBaseline', 'setSuspiciousData',
    'showVesselInfoCard', 'getMidFlag', 'getGovType', 'govLabel',
    'GOV_BADGE_ICON', 'GOV_TYPES', 'FISHING_HOTSPOTS', 'VESSEL_COLORS',
    'REGION_COLORS', 'REGION_NAMES',
];
for (const k of REQUIRED_EXPORTS) {
    assert(MapModule[k] !== undefined, `MapModule.${k} missing`);
}
assert.strictEqual(MapModule.getMidFlag('477000000'), '香港');

console.log('✅ frontend smoke test passed — MapData + MapModule OK');
