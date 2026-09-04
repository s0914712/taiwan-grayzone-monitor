/**
 * Smoke test — 統計分析頁（docs/statistics.html + js/statistics.js）
 * 該頁已改為「熱區地圖 + 船種／船籍分布」，舊的暗船趨勢／軍演預測／SCFI
 * 圖表全部移除。驗證：期別 chips → 熱區圖層 → 兩張長條圖 → 空狀態，
 * 並守住「舊內容不得回歸」與「熱區色階走共用模組」。
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DOCS = path.join(__dirname, '..', 'docs');
const html = fs.readFileSync(path.join(DOCS, 'statistics.html'), 'utf8');
const source = fs.readFileSync(path.join(DOCS, 'js', 'statistics.js'), 'utf8');
const hotspotLayer = fs.readFileSync(path.join(DOCS, 'js', 'hotspot-layer.js'), 'utf8');

const MANIFEST = {
    weekly: [{ week: '2026-W35', start: '2026-08-24', end: '2026-08-30',
               days_covered: 1, unique_highrisk: 400 }],
    monthly: [{ month: '2026-08', start: '2026-08-01', end: '2026-08-31',
                days_covered: 2, unique_highrisk: 510 }],
};
const REPORT = {
    week: '2026-W35', start: '2026-08-24', end: '2026-08-30',
    days_covered: 1, generated_at: '2026-08-31T00:05:00+00:00',
    summary: {
        by_type: { cargo: 179, fishing: 134, high_speed: 1 },
        // 整數樣式鍵：JS 物件按數值升冪列舉，前端必須自己按 count 排序
        by_flag: {
            '100': { en: 'Unknown', zh: '未知', count: 3 },
            '412': { en: 'China', zh: '中國', count: 145 },
            '538': { en: 'Marshall Islands', zh: '馬紹爾群島', count: 15 },
        },
    },
    hotspots: [
        { lat: 24.4, lon: 118.4, events: 17, vessels: 16, loiter_hours: 258.2, avg_speed_kn: 0.5 },
        { lat: 23.0, lon: 120.2, events: 3, vessels: 4, loiter_hours: 81.8, avg_speed_kn: null },
    ],
};
const CABLES = { type: 'FeatureCollection', features: [] };

function makeEnv(manifest, opts) {
    opts = opts || {};
    const elements = {};
    const listeners = {};
    const domReady = [];
    const state = { rects: [], charts: [] };

    function element(id) {
        if (!elements[id]) {
            elements[id] = {
                id, textContent: '', innerHTML: '', style: {}, dataset: {},
                checked: true,
                getContext: () => ({}),
                addEventListener: (n, fn) => { (listeners[id] = listeners[id] || {})[n] = fn; },
            };
        }
        return elements[id];
    }
    function querySelectorAll(sel) {
        const out = [];
        if (sel === '.period-chip') {
            ['periodListWeekly', 'periodListMonthly'].forEach((id) => {
                const re = /data-kind="([^"]+)" data-label="([^"]+)"/g;
                let m;
                while ((m = re.exec(element(id).innerHTML))) {
                    out.push({ dataset: { kind: m[1], label: m[2] }, addEventListener: () => {} });
                }
            });
        }
        return out;
    }
    const layerGroup = () => ({
        _items: [], addTo() { return this; },
        clearLayers() { this._items.length = 0; },
    });
    const map = {
        fitBounds() { return this; }, addLayer() { return this; },
        removeLayer() { return this; },
    };
    const L = {
        map: () => map,
        tileLayer: () => ({ addTo: () => ({}) }),
        layerGroup: () => layerGroup(),
        latLngBounds: (pts) => ({ pts, pad: () => ({ pts }) }),
        rectangle: (bounds, style) => ({
            bounds, style, popup: '',
            bindPopup(c) { this.popup = c; return this; },
            addTo(g) { g._items.push(this); state.rects.push(this); return this; },
        }),
        geoJSON: () => ({ addTo: () => ({}) }),
    };
    function Chart(ctx, cfg) { this.cfg = cfg; state.charts.push(cfg); }
    Chart.prototype.destroy = function () {};

    const ctx = {
        console, Math, Date, Set, JSON, Promise, setTimeout, Array, Object, String,
        L, Chart,
        window: { addEventListener: (n, fn) => { (listeners.__win = listeners.__win || {})[n] = fn; } },
        i18n: { getLang: () => opts.lang || 'zh' },
        document: {
            getElementById: element, querySelectorAll,
            addEventListener: (n, fn) => { if (n === 'DOMContentLoaded') domReady.push(fn); },
        },
        fetch: async (url) => {
            if (url.startsWith('reports/weekly/index.json')) return { ok: true, json: async () => manifest };
            if (url.startsWith('taiwan_cables.json')) return { ok: true, json: async () => CABLES };
            if (/^reports\/(weekly|monthly)\//.test(url)) return { ok: true, json: async () => REPORT };
            return { ok: false, status: 404, json: async () => ({}) };
        },
    };
    vm.createContext(ctx);
    vm.runInContext(hotspotLayer, ctx);
    vm.runInContext(source, ctx);
    return { ctx, elements, listeners, domReady, state };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
    // ── 頁面契約 ────────────────────────────────────────────────────────
    ['id="map"', 'id="periodListWeekly"', 'id="periodListMonthly"',
     'id="typeChart"', 'id="flagChart"', 'id="pendingNotice"',
     'id="reportBody"', 'id="coverageNote"', 'id="lyrHotspots"',
     'id="lyrCables"'].forEach((id) => {
        assert(html.includes(id), `page must carry ${id}`);
    });
    assert(html.includes('js/hotspot-layer.js'), 'shared hotspot module loaded');
    assert(html.indexOf('js/hotspot-layer.js') < html.indexOf('js/statistics.js'),
        'hotspot-layer.js must load before statistics.js');
    assert(html.includes('leaflet@1.9.4') && html.includes('chart.umd'),
        'Leaflet + Chart.js runtimes loaded');
    assert(!html.includes('cartocdn'), 'CARTO tiles need an API key — must not be used');

    // 舊內容不得回歸
    ['trendChart', 'aisHistoryChart', 'aisSuspiciousChart', 'aisTypeChart',
     'scfiVesselChart', 'scfiLagChart', 'dailyTableBody', 'predictionLevel',
     'js/charts.js'].forEach((gone) => {
        assert(!html.includes(gone), `old statistics content must stay removed: ${gone}`);
    });

    // ── 正常路徑 ────────────────────────────────────────────────────────
    const env = makeEnv(MANIFEST);
    env.domReady.forEach((fn) => fn());
    await tick(); await tick(); await tick();
    const el = env.elements;

    assert(el.periodListWeekly.innerHTML.includes('2026-W35'), 'weekly chip rendered');
    assert(el.periodListMonthly.innerHTML.includes('2026-08'), 'monthly chip rendered');
    assert(el.periodListWeekly.innerHTML.includes('active'), 'latest week auto-selected');

    assert.strictEqual(env.state.rects.length, 2, 'both hotspot cells drawn');
    assert(env.state.rects[0].popup.includes('258.2'), 'hotspot popup carries loiter hours');
    // 均速 null 顯示破折號，不能顯示 0（顯示 0 是謊報靜止）
    assert(env.state.rects[1].popup.includes('—'), 'null avg speed renders as a dash');
    assert(!env.state.rects[1].popup.includes('0 kn'), 'null avg speed must not render as 0');
    assert(env.state.rects[0].style.fillColor !== env.state.rects[1].style.fillColor,
        'colour scale separates high and low loiter hours');

    assert.strictEqual(env.state.charts.length, 2, 'type + flag charts built');
    const typeLabels = env.state.charts[0].data.labels;
    assert.strictEqual(typeLabels[0], '貨輪', 'vessel types sorted by count desc');
    assert(typeLabels.includes('high_speed'),
        'unmapped vessel type keeps its raw name instead of a duplicate 不明');
    const flagLabels = env.state.charts[1].data.labels;
    assert(flagLabels[0].includes('412'),
        'flag chart sorted by vessel count, not by numeric MID key order');

    assert(el.coverageNote.textContent.includes('1'), 'partial-coverage warning shown');
    assert(el.updateInfo.textContent.includes('2026-08-24'), 'period range shown');
    assert.strictEqual(el.pendingNotice.style.display, 'none', 'pending hidden when data exists');

    // 語言切換重繪
    env.listeners.__win.langchange();
    assert(el.periodListWeekly.innerHTML.includes('2026-W35'), 'chips survive langchange');

    // ── 空 manifest ─────────────────────────────────────────────────────
    const empty = makeEnv({ weekly: [], monthly: [] });
    empty.domReady.forEach((fn) => fn());
    await tick(); await tick();
    assert.strictEqual(empty.elements.reportBody.style.display, 'none',
        'report body hidden when nothing generated yet');
    assert.strictEqual(empty.elements.pendingNotice.style.display, '',
        'pending notice shown when nothing generated yet');

    // ── 英文模式 ────────────────────────────────────────────────────────
    const en = makeEnv(MANIFEST, { lang: 'en' });
    en.domReady.forEach((fn) => fn());
    await tick(); await tick(); await tick();
    assert(en.state.charts[0].data.labels[0] === 'Cargo', 'types render in English');
    assert(en.state.charts[1].data.labels[0].includes('China'), 'flags render in English');

    console.log('✅ statistics page smoke test passed — hotspots, type/flag charts, pending state');
})().catch((err) => { console.error(err); process.exitCode = 1; });
