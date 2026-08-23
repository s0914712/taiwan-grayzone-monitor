/**
 * 首頁公務船名冊 smoke test — 近 48h 回溯 + 停播標示 + 編隊橫幅。
 *
 * 迴歸重點：名冊原本只讀當前 AIS 快照，船一停播首頁就完全沒有跡象
 * （實測 2026-08-21 深夜向陽紅03 與兩艘護航海警同時停播）。本測試以
 * 「快照裡只有 1 艘、48h 名冊裡另有 3 艘」的資料驗證合併與標示。
 *
 * Usage: node tests/gov-roster-smoke.js  (run from repo root)
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DOCS_JS = path.join(__dirname, '..', 'docs', 'js');

const elements = {};
function element(id) {
    if (!elements[id]) {
        elements[id] = {
            id, textContent: '', innerHTML: '', style: {}, dataset: {},
            className: '', title: '',
            classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
            addEventListener() {}, querySelectorAll: () => [],
            querySelector: () => null, appendChild() {}, setAttribute() {},
            getAttribute: () => null, removeAttribute() {}, closest: () => null,
        };
    }
    return elements[id];
}

// ── 測試資料：快照僅 1 艘現正廣播；另 3 艘在 48h 名冊裡已停播 ──
const DATA = {
    updated_at: '2026-08-22T06:00:00Z',
    ais_snapshot: {
        updated_at: '2026-08-22T06:00:00Z',
        ais_data: {},
        vessels: [{
            mmsi: '413547290', name: 'XIANG YANG HONG 05',
            type_name: 'research', gov_type: 'research',
            lat: 21.18, lon: 121.03, speed: 9.6,
        }],
    },
    gov_vessels_recent: {
        window_hours: 48,
        as_of: '2026-08-22T06:00:00Z',
        counts: { coastguard: 2, msa: 1, research: 2 },
        total: 5,
        vessels: [
            { mmsi: '413875040', name: 'CHINACOASTGUARD2502', gov_type: 'coastguard',
              lat: 23.631, lon: 122.598, speed: 1.9, age_hours: 6.8 },
            { mmsi: '413875017', name: 'CHINACOASTGUARD1306', gov_type: 'coastguard',
              lat: 23.522, lon: 122.523, speed: 9.5, age_hours: 6.8 },
            { mmsi: '413701510', name: 'XIANG YANG HONG 03', gov_type: 'research',
              lat: 23.582, lon: 122.524, speed: 2.0, age_hours: 6.8 },
            // AIS 是未經驗證的廣播 —— 惡意船名不可原樣進 HTML
            { mmsi: '999999999', name: '<script>x</script>"\'', gov_type: 'msa',
              lat: 25.0, lon: 121.0, speed: 0, age_hours: 12.0 },
            // 快照已有 → 不可重複計入
            { mmsi: '413547290', name: 'XIANG YANG HONG 05', gov_type: 'research',
              lat: 21.18, lon: 121.03, speed: 9.6, age_hours: 0.4 },
        ],
    },
    gov_formations: {
        summary: { active_formations: 1, escorted_research: 1 },
        active_formations: [{
            id: 'GF-TEST', severity: 'high', escorted_research: true,
            vessel_count: 3, duration_hours: 12.6,
            last_lat: 23.578, last_lon: 122.549,
            members: [
                { mmsi: '413875040', name: 'CHINACOASTGUARD2502', category: 'coastguard' },
                { mmsi: '413875017', name: 'CHINACOASTGUARD1306', category: 'coastguard' },
                { mmsi: '413701510', name: 'XIANG YANG HONG 03', category: 'research' },
            ],
        }],
    },
    suspicious_analysis: { summary: {}, suspicious_vessels: [] },
    dark_vessels: {},
    vessel_monitoring: {},
};

const noop = () => {};
const MapModuleStub = {
    VESSEL_COLORS: { coastguard: '#fff', research: '#c77dff', other: '#888' },
    GOV_TYPES: ['coastguard', 'msa', 'rescue', 'research'],
    GOV_BADGE_ICON: { coastguard: '🛡️', research: '🔬' },
    getGovType: (v) => v.gov_type || null,
    govLabel: (c) => c,
    renderVesselsForZoom: () => ({ vessels: [], stats: {} }),
    displayGovVessels: noop, displayDarkVessels: noop,
    displaySuspiciousVessels: noop, setSuspiciousData: noop,
    focusVessel: noop, focusPosition: noop, init: noop,
    drawFishingHotspots: noop, displayVessels: noop,
};

const ctx = {
    console, Math, Date, Set, Map, JSON, RegExp, Promise, Array, Object,
    Number, String, isNaN, parseFloat, parseInt,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval,
    navigator: { language: 'zh-TW' },
    localStorage: { getItem: () => null, setItem() {} },
    document: {
        getElementById: element,
        addEventListener: noop,
        querySelectorAll: () => [],
        querySelector: () => null,
        createElement: () => ({ style: {}, classList: { add() {} } }),
        body: { classList: { add() {}, remove() {} } },
    },
    fetch: (url) => {
        if (String(url).includes('data.json')) {
            return Promise.resolve({ ok: true, json: () => Promise.resolve(DATA) });
        }
        return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
    },
    i18n: {
        t: (k, ...a) => a.length ? k + ':' + a.join(',') : k,
        getLang: () => 'zh',
    },
    MapModule: MapModuleStub,
    ChartsModule: { updateZoneCounts: noop, update: noop, init: noop },
    L: { divIcon: (o) => o },
};
ctx.window = ctx;
ctx.addEventListener = noop;
ctx.removeEventListener = noop;
ctx.globalThis = ctx;
vm.createContext(ctx);

// `const App = …` 不會掛上 globalThis，比照其他 smoke test 追加擷取語句
vm.runInContext(
    fs.readFileSync(path.join(DOCS_JS, 'app.js'), 'utf8') + '\n;globalThis.App = App;',
    ctx, { filename: 'app.js' });
assert(ctx.App && typeof ctx.App.loadData === 'function', 'App exported');

ctx.App.loadData().then(() => {
    const html = element('govVesselList').innerHTML;

    // 1. 停播的三艘仍在名冊上
    ['XIANG YANG HONG 03', 'CHINACOASTGUARD2502', 'CHINACOASTGUARD1306']
        .forEach((n) => assert(html.includes(n.substring(0, 16)),
            `offline vessel listed: ${n}`));

    // 2. 現正廣播的那艘不帶「最後訊號」標記，停播的有
    assert(html.includes('idx.gov_last_seen'), 'stale rows carry a last-signal label');
    assert(html.includes('is-stale'), 'stale rows get the dimmed class');

    // 3. 快照與名冊都出現的船不可重複列出（列名截斷至 16 字元，
    //    向陽紅03/05 在畫面上都是 'XIANG YANG HONG '）
    const rows = html.split('XIANG YANG HONG ').length - 1;
    assert.strictEqual(rows, 2, `deduped by mmsi (found ${rows} XYH rows)`);

    // 4. 停播的船不在地圖圖層上 → 必須以座標定位，不能呼叫 focusVessel
    assert(html.includes('App.focusSuspicious(23.582, 122.524)'),
        'offline row focuses by coordinates');
    assert(html.includes('App.focusVessel(&quot;413547290&quot;)'),
        'live row focuses the map marker');

    // 7. AIS 船名是未驗證的廣播字串 —— 進 HTML 前必須轉義
    assert(!html.includes('<script'), 'no raw markup from vessel names');
    assert(html.includes('&lt;script&gt;'), 'hostile name escaped, not dropped');

    // 5. 護航科考編隊橫幅
    assert(html.includes('gov-formation'), 'formation banner rendered');
    assert(html.includes('sev-high'), 'severity class applied');
    assert(html.includes('idx.gov_formation_escort'), 'escorted-survey label used');

    // 6. 磚上的數字算的是 48h 名冊（4 艘），不是快照（1 艘）
    assert.strictEqual(element('metricGov').textContent, '5',
        'tile counts the 48h roster, not just the live snapshot');
    assert(element('metricGovSub').textContent.includes('idx.gov_offline_n:4'),
        'tile subtitle reports how many went silent');

    console.log('✅ gov roster smoke test passed — 48h merge, stale badges, formation banner');
}).catch((e) => {
    console.error('❌', e);
    process.exit(1);
});
