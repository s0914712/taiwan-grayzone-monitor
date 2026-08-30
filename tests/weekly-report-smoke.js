/**
 * Smoke test — 高風險船舶週報頁（docs/weekly-report.html + js/weekly-report.js）
 * 驗證：manifest → 期別 chips → 載入報表 → 熱區/船位圖層、表格、下載連結、
 *       冷啟動（days_covered < 7）警語，以及 manifest 為空時顯示待產生通知。
 * 用 stub Leaflet（不需 jsdom），與 network-traffic-smoke.js 同一種做法。
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const DOCS = path.join(__dirname, '..', 'docs');
const html = fs.readFileSync(path.join(DOCS, 'weekly-report.html'), 'utf8');
const source = fs.readFileSync(path.join(DOCS, 'js', 'weekly-report.js'), 'utf8');

// ── 合成報表（管線尚未跑出真檔時 CI 也要能驗證）─────────────────────────
const MANIFEST = {
    updated_at: '2026-08-31T00:10:00+00:00',
    weekly: [
        { week: '2026-W35', start: '2026-08-24', end: '2026-08-30', days_covered: 3, unique_highrisk: 400, critical: 113 },
        { week: '2026-W34', start: '2026-08-17', end: '2026-08-23', days_covered: 7, unique_highrisk: 380, critical: 90 },
    ],
    monthly: [
        { month: '2026-08', start: '2026-08-01', end: '2026-08-31', days_covered: 28, unique_highrisk: 500, critical: 150 },
    ],
};
const REPORT = {
    period: 'weekly', week: '2026-W35',
    start: '2026-08-24', end: '2026-08-30', days_covered: 3,
    generated_at: '2026-08-31T00:05:00+00:00',
    summary: {
        unique_highrisk: 2, critical: 1, high: 1,
        cable_loiter_vessels: 2, cable_loiter_hours_total: 9.5,
        offshore_loiter_vessels: 0,
        by_type: { fishing: 321, cargo: 11, high_speed: 1 },
        // 刻意讓「數值小但船數少」的 MID 排在前面：JS 物件對整數樣式鍵一律
        // 按數值升冪列舉，若前端不自己排序，MID 100 會蓋掉 MID 412
        by_flag: {
            '412': { en: 'China', zh: '中國', count: 310 },
            '100': { en: 'Unknown', zh: '未知', count: 3 },
            '351': { en: 'Panama', zh: '巴拿馬', count: 14 },
        },
        daily_counts: { '2026-08-28': 2 },
    },
    hotspots: [
        { lat: 26.2, lon: 120.1, events: 4, vessels: 3, loiter_hours: 20.5, avg_speed_kn: 1.2 },
        { lat: 25.3, lon: 121.4, events: 1, vessels: 1, loiter_hours: 5.8, avg_speed_kn: null },
    ],
    vessels: [
        {
            mmsi: '413000001', name: 'TEST CARGO', vessel_type: 'cargo', gov_category: '',
            flag_mid: '412', flag_en: 'China', flag_zh: '中國',
            max_risk_score: 25, risk_level: 'critical', days_seen: 3,
            cable_loiter_hours: 5.8, cable_loiter_avg_speed_kn: 1.7, cable_loiter_events: 2,
            cables_nearby: ['apcn-2'], offshore_loiter_days: 0,
            non_top10_flag: false, sanctioned: false,
            departure_port: '中柱漁港 Zhongzhu', departure_time_utc: '2026-08-25T03:24:00+00:00',
            arrival_port: null, arrival_time_utc: null,
            time_at_sea_hours: 116.8, time_at_sea_note: '',
            last_lat: 26.2, last_lon: 120.1, last_zone: 'eez',
            last_seen_utc: '2026-08-30T00:00:00+00:00',
        },
        {
            mmsi: '351000002', name: '', vessel_type: 'fishing', gov_category: '',
            flag_mid: '351', flag_en: 'Panama', flag_zh: '巴拿馬',
            max_risk_score: 9, risk_level: 'high', days_seen: 1,
            cable_loiter_hours: 3.7, cable_loiter_avg_speed_kn: null, cable_loiter_events: 1,
            cables_nearby: [], offshore_loiter_days: 0,
            non_top10_flag: true, sanctioned: false,
            departure_port: null, departure_time_utc: null,
            arrival_port: null, arrival_time_utc: null,
            time_at_sea_hours: 60.0, time_at_sea_note: '未觀測到靠港',
            last_lat: null, last_lon: null, last_zone: null,
            last_seen_utc: '2026-08-29T00:00:00+00:00',
        },
    ],
};
const CABLES = {
    type: 'FeatureCollection',
    features: [{
        type: 'Feature', properties: { slug: 'apcn-2', name: 'APCN-2', color: '00f5ff' },
        geometry: { type: 'MultiLineString', coordinates: [[[120, 25], [121, 25]]] },
    }],
};

// ── DOM / Leaflet stubs ────────────────────────────────────────────────
function makeEnv(manifest, opts) {
    opts = opts || {};
    const elements = {};
    const listeners = {};
    const domReady = [];
    const state = { rects: [], markers: [], geo: 0, view: null, fitted: null };

    function element(id) {
        if (!elements[id]) {
            elements[id] = {
                id, textContent: '', innerHTML: '', style: {}, dataset: {},
                href: '', checked: true,
                addEventListener: (n, fn) => { (listeners[id] = listeners[id] || {})[n] = fn; },
                scrollIntoView: () => {},
            };
        }
        return elements[id];
    }

    // querySelectorAll：從渲染出的 innerHTML 粗略解出 chip / 資料列，
    // 讓 click handler 綁定路徑也被走過（真頁面靠這條互動）。
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
        } else if (sel === '#hotspotTable tr[data-lat]') {
            const re = /data-lat="([^"]+)" data-lon="([^"]+)"/g;
            let m;
            while ((m = re.exec(element('hotspotTable').innerHTML))) {
                out.push({ dataset: { lat: m[1], lon: m[2] }, addEventListener: () => {} });
            }
        } else if (sel === '#vesselTable tr[data-mmsi]') {
            const re = /data-mmsi="([^"]+)"/g;
            let m;
            while ((m = re.exec(element('vesselTable').innerHTML))) {
                out.push({ dataset: { mmsi: m[1] }, addEventListener: () => {} });
            }
        }
        return out;
    }

    const layerGroup = () => ({
        _items: [],
        addTo() { return this; },
        clearLayers() { this._items.length = 0; },
        eachLayer(fn) { this._items.forEach(fn); },
    });
    const groups = {};
    const map = {
        setView(c, z) { state.view = { c, z }; return this; },
        fitBounds(b) { state.fitted = b; return this; },
        getZoom: () => 6,
        addLayer() { return this; },
        removeLayer() { return this; },
    };
    const L = {
        map: () => map,
        tileLayer: () => ({ addTo: () => ({}) }),
        layerGroup: () => { const g = layerGroup(); groups[Object.keys(groups).length] = g; return g; },
        latLngBounds: (pts) => ({ pts, pad: () => ({ pts }) }),
        rectangle: (bounds, style) => {
            const o = {
                bounds, style, popup: '',
                bindPopup(c) { this.popup = c; return this; },
                addTo(g) { g._items.push(this); state.rects.push(this); return this; },
            };
            return o;
        },
        circleMarker: (coords, style) => {
            const o = {
                coords, style, popup: '', getLatLng: () => coords,
                bindPopup(c) { this.popup = c; return this; },
                openPopup() { this.opened = true; return this; },
                addTo(g) { g._items.push(this); state.markers.push(this); return this; },
            };
            return o;
        },
        geoJSON: (data, options) => {
            (data.features || []).forEach((f) => {
                options.style(f);
                options.onEachFeature(f, { bindTooltip: () => ({}) });
                state.geo++;
            });
            return { addTo: () => ({}) };
        },
    };

    const ctx = {
        console, Math, Date, Set, JSON, Promise, setTimeout, clearTimeout, Array, Object, String,
        L,
        window: { addEventListener: (n, fn) => { (listeners.__win = listeners.__win || {})[n] = fn; } },
        i18n: { getLang: () => opts.lang || 'zh' },
        document: {
            getElementById: element,
            querySelectorAll,
            addEventListener: (n, fn) => { if (n === 'DOMContentLoaded') domReady.push(fn); },
        },
        fetch: async (url) => {
            if (url.startsWith('reports/weekly/index.json')) {
                return { ok: true, json: async () => manifest };
            }
            if (url.startsWith('taiwan_cables.json')) {
                return { ok: true, json: async () => CABLES };
            }
            if (/^reports\/(weekly|monthly)\/[^?]+\.json/.test(url)) {
                return { ok: true, json: async () => REPORT };
            }
            return { ok: false, status: 404, json: async () => ({}) };
        },
    };
    vm.createContext(ctx);
    vm.runInContext(source, ctx);
    return { ctx, elements, listeners, domReady, state };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

(async () => {
    // ── 頁面契約 ────────────────────────────────────────────────────────
    ['id="map"', 'id="periodListWeekly"', 'id="periodListMonthly"',
     'id="pendingNotice"', 'id="reportBody"', 'id="hotspotTable"',
     'id="vesselTable"', 'id="dlCsv"', 'id="dlJson"', 'id="coverageNote"',
     'id="lyrHotspots"', 'id="lyrVessels"', 'id="lyrCables"'].forEach((id) => {
        assert(html.includes(id), `page must carry ${id}`);
    });
    assert(html.includes('leaflet@1.9.4'), 'Leaflet runtime loaded');
    assert(html.includes('js/weekly-report.js'), 'page script wired');
    assert(html.includes('js/mobile-nav.js'), 'mobile nav shell included');
    assert(!html.includes('cartocdn'),
        'CARTO tiles now require an API key — page must not use them');
    assert(/\.main-content\s*\{[^}]*display:\s*block/.test(html),
        'page overrides the shared two-column .main-content grid (vertical flow)');

    // ── 正常路徑 ────────────────────────────────────────────────────────
    const env = makeEnv(MANIFEST);
    env.domReady.forEach((fn) => fn());
    await tick(); await tick(); await tick();

    const el = env.elements;
    assert(el.periodListWeekly.innerHTML.includes('2026-W35'), 'weekly chips rendered');
    assert(el.periodListWeekly.innerHTML.includes('2026-W34'), 'older week listed');
    assert(el.periodListMonthly.innerHTML.includes('2026-08'), 'monthly chips rendered');
    assert(el.periodListWeekly.innerHTML.includes('active'), 'latest week auto-selected');

    assert.strictEqual(el.statVessels.textContent, 2, 'unique high-risk stat filled');
    assert.strictEqual(el.statCritical.textContent, 1, 'critical stat filled');
    assert.strictEqual(el.statLoiterVessels.textContent, 2, 'cable-loiter vessels stat filled');
    assert.strictEqual(el.statLoiterHours.textContent, '10', 'loiter hours rounded (9.5 → 10)');

    // 冷啟動警語：days_covered 3 < 7 必須明講，不能讓人以為是完整一週
    assert(el.coverageNote.textContent.includes('3'),
        'partial-coverage warning names the number of days');
    assert.notStrictEqual(el.coverageNote.style.display, 'none',
        'partial-coverage warning is visible');

    assert(el.byTypeChips.innerHTML.includes('漁船') && el.byTypeChips.innerHTML.includes('貨輪'),
        'vessel-type chips rendered in Chinese');
    // 未對映的型別（high_speed）不能也叫「不明」——圖例上會出現兩個同名 chip
    assert(el.byTypeChips.innerHTML.includes('high_speed'),
        'unmapped vessel type keeps its raw name instead of a duplicate Unknown');
    assert(el.byTypeChips.innerHTML.indexOf('漁船') < el.byTypeChips.innerHTML.indexOf('貨輪'),
        'vessel-type chips sorted by count desc');

    assert(el.byFlagList.innerHTML.includes('中國') && el.byFlagList.innerHTML.includes('MID 351'),
        'flag table shows names and MID codes');
    // JS 物件的整數樣式鍵會被強制數值升冪列舉，前端必須自己按船數排序，
    // 否則 MID 100（3 艘）會排到 MID 412（310 艘）前面
    assert(el.byFlagList.innerHTML.indexOf('MID 412') < el.byFlagList.innerHTML.indexOf('MID 100'),
        'flag table sorted by vessel count, not by numeric MID key order');

    // 地圖圖層
    assert.strictEqual(env.state.rects.length, 2, 'both hotspot cells drawn');
    assert(env.state.rects[0].popup.includes('20.5'), 'hotspot popup carries loiter hours');
    assert(env.state.rects[0].popup.includes('1.2 kn'), 'hotspot popup carries avg speed');
    assert(env.state.rects[1].popup.includes('—'),
        'hotspot with no speed shows a dash, not a fake 0');
    assert(env.state.rects[0].style.fillColor !== env.state.rects[1].style.fillColor,
        'hotspot color scale separates high and low loiter hours');
    assert.strictEqual(env.state.markers.length, 1,
        'only the vessel with a last position is plotted');
    assert(env.state.markers[0].popup.includes('TEST CARGO'), 'vessel popup names the ship');
    assert(env.state.markers[0].popup.includes('中柱漁港'), 'vessel popup carries departure port');
    assert(env.state.markers[0].popup.includes('1.7 kn'), 'vessel popup carries loiter avg speed');
    assert.strictEqual(env.state.geo, 1, 'submarine cable layer rendered');

    // 表格
    assert(el.hotspotTable.innerHTML.includes('26.2') && el.hotspotTable.innerHTML.includes('20.5'),
        'hotspot table filled');
    assert(el.vesselTable.innerHTML.includes('TEST CARGO') && el.vesselTable.innerHTML.includes('413000001'),
        'vessel table filled');
    assert(el.vesselTable.innerHTML.includes('351000002'),
        'vessel without a position still listed in the table');

    // 下載連結指向本期
    assert.strictEqual(el.dlCsv.href, 'reports/weekly/2026-W35.csv', 'CSV link points at the period');
    assert.strictEqual(el.dlJson.href, 'reports/weekly/2026-W35.json', 'JSON link points at the period');
    assert.strictEqual(el.pendingNotice.style.display, 'none', 'pending notice hidden when data exists');

    // 語言切換重繪（期別清單與報表都是動態字串，切語言不能整頁清空）
    env.listeners.__win.langchange();
    assert(el.periodListMonthly.innerHTML.includes('2026-08'),
        'monthly chips survive a language switch');
    assert(el.vesselTable.innerHTML.includes('TEST CARGO'),
        'vessel table survives a language switch');

    // ── 空 manifest → 待產生通知 ─────────────────────────────────────────
    const empty = makeEnv({ updated_at: 'x', weekly: [], monthly: [] });
    empty.domReady.forEach((fn) => fn());
    await tick(); await tick();
    assert.strictEqual(empty.elements.reportBody.style.display, 'none',
        'report body hidden when nothing generated yet');
    assert.strictEqual(empty.elements.pendingNotice.style.display, '',
        'pending notice shown when nothing generated yet');
    assert(empty.elements.periodListWeekly.innerHTML.includes('尚無'),
        'empty weekly list says so rather than rendering blank');

    // ── 英文模式 ────────────────────────────────────────────────────────
    const en = makeEnv(MANIFEST, { lang: 'en' });
    en.domReady.forEach((fn) => fn());
    await tick(); await tick(); await tick();
    assert(en.elements.byTypeChips.innerHTML.includes('Cargo'),
        'vessel types render in English when lang=en');
    assert(en.elements.byFlagList.innerHTML.includes('China'),
        'flag names render in English when lang=en');

    console.log('✅ weekly report smoke test passed — manifest, hotspots, vessels, downloads, pending state');
})().catch((err) => { console.error(err); process.exitCode = 1; });
