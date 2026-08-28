/**
 * 縣市色塊地圖的真 Leaflet 整合測試 — network-traffic.js × tw_counties.geojson。
 *
 * tests/network-traffic-smoke.js 把 L 整個 stub 掉，驗的是資料流；這支相反：
 * 用 jsdom + 真的 Leaflet 1.9.4 把 GeoJSON 真的畫出來，抓的是 stub 抓不到的
 * 問題——GeoJSON 幾何無效、屬性名對不上、style/onEachFeature 用錯 API。
 *
 * Usage: node tests/network-county-map-integration.js
 * Requires: npm packages jsdom + leaflet（同 map-integration.js）
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

const DOCS = path.join(__dirname, '..', 'docs');
const geo = JSON.parse(fs.readFileSync(path.join(DOCS, 'tw_counties.geojson')));
const cf = JSON.parse(fs.readFileSync(path.join(DOCS, 'cf_radar.json')));
const ioda = JSON.parse(fs.readFileSync(path.join(DOCS, 'ioda.json')));

// 縣市指標檔是選配，這裡合成：連江有進行中的異常，臺北正常，其餘無資料
const countyMetrics = {
    generated_at: new Date().toISOString(),
    counties: [
        {
            iso: 'TW-LIE', name_zh: '連江縣（馬祖）', name_en: 'Lienchiang (Matsu)',
            status: 'available', metric_id: 'iqi_bandwidth',
            metric_label_zh: '頻寬（IQI 中位數）', metric_label_en: 'Bandwidth (IQI median)',
            unit: 'Mbps', higher_is_better: true, is_speed: true,
            latest: 12.5, baseline: 60, pct_vs_baseline: -79.2, level: 'alert',
            anomalies: [{ onset: '2026-08-28T00:00:00Z', end: '2026-08-28T06:00:00Z', severity: 'critical' }],
            series: {
                timestamps: ['2026-08-27T21:00:00Z', '2026-08-28T00:00:00Z', '2026-08-28T03:00:00Z'],
                values: [60, 20, 12.5], bucket_hours: 3,
            },
        },
        {
            iso: 'TW-TPE', name_zh: '臺北市', name_en: 'Taipei',
            status: 'available', metric_id: 'iqi_bandwidth',
            metric_label_zh: '頻寬（IQI 中位數）', metric_label_en: 'Bandwidth (IQI median)',
            unit: 'Mbps', higher_is_better: true, is_speed: true,
            latest: 180, baseline: 175, pct_vs_baseline: 2.9, level: 'normal',
            anomalies: [],
            series: { timestamps: ['2026-08-28T00:00:00Z'], values: [180], bucket_hours: 3 },
        },
    ],
};

// IODA 的縣市可達性是免憑證的保底來源：這裡塞兩個縣市，驗證「可達性」模式
// 會出現在切換列上，而且切過去之後色塊照樣畫得出來。
ioda.counties = [
    {
        iso: 'TW-KIN', label_zh: '金門縣', label_en: 'Kinmen', lat: 24.45, lon: 118.39,
        region_code: '4209', status: 'available', level: 'watch', anomaly_count: 1,
        max_corroborating_sources: 2,
        signals: [
            { datasource: 'bgp', label_zh: 'BGP 可見前綴', latest: 420, anomaly_count: 1 },
            { datasource: 'ping-slash24', label_zh: '主動探測可達 /24', latest: 66, anomaly_count: 0 },
        ],
        latest_anomaly: { onset: '2026-08-26T00:00:00Z', end: '2026-08-26T04:00:00Z', severity: 'high' },
        primary: { datasource: 'ping-slash24', bucket_hours: 3,
            timestamps: ['2026-08-27T21:00:00Z', '2026-08-28T00:00:00Z'], values: [66, 64] },
    },
    {
        iso: 'TW-HUA', label_zh: '花蓮縣', label_en: 'Hualien', lat: 23.8, lon: 121.38,
        region_code: null, status: 'unavailable', error_reason: 'region_not_found',
        level: 'unknown', anomaly_count: 0, signals: [], latest_anomaly: null,
    },
];

const dom = new JSDOM(fs.readFileSync(path.join(DOCS, 'network-traffic.html'), 'utf8'),
    { runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/' });
const { window } = dom;

const mapEl = window.document.getElementById('connectivityMap');
Object.defineProperty(mapEl, 'clientWidth', { value: 900 });
Object.defineProperty(mapEl, 'clientHeight', { value: 600 });

window.i18n = { getLang: () => 'zh' };
window.Chart = function () { return { destroy() {} }; };
window.fetch = async (url) => {
    const body = url.startsWith('ioda.json') ? ioda
        : url.startsWith('tw_counties.geojson') ? geo
            : url.startsWith('cf_radar_counties.json') ? countyMetrics : cf;
    return { ok: true, json: async () => body };
};

window.eval(LEAFLET_SRC);
window.eval(fs.readFileSync(path.join(DOCS, 'js', 'network-traffic.js'), 'utf8'));
window.document.dispatchEvent(new window.Event('DOMContentLoaded'));

setTimeout(() => {
    const paths = mapEl.querySelectorAll('path');
    assert(paths.length >= 22, `expected ≥22 rendered county paths, got ${paths.length}`);

    const modes = window.document.getElementById('countyModes').innerHTML;
    assert(modes.includes('網速'), 'speed mode chip rendered');

    const legend = window.document.getElementById('countyLegend').innerHTML;
    assert(legend.includes('Mbps'), 'legend carries the unit');
    assert(legend.includes('12.5') || legend.includes('12'), 'legend low end comes from real data');
    assert(legend.includes('180'), 'legend high end comes from real data');

    // 標題列的四個觀測點標記仍在色塊之上（circleMarker 也是 path）
    assert(mapEl.querySelectorAll('.leaflet-interactive').length >= 22,
        'county polygons are interactive (clickable for popups)');

    // ── 模式切換：Cloudflare 沒資料時 IODA 的可達性要能接手 ──────────────
    const chips = Array.from(window.document.querySelectorAll('#countyModes .county-mode'));
    assert.strictEqual(chips.length, 2, 'speed (Cloudflare) + reachability (IODA) modes offered');
    const reachChip = chips.find((chip) => chip.textContent.includes('可達性'));
    assert(reachChip, 'IODA reachability mode chip rendered');
    reachChip.dispatchEvent(new window.Event('click', { bubbles: true }));

    assert(window.document.getElementById('countyLegend').innerHTML.includes('IODA'),
        'legend switches to the IODA reachability wording');
    assert(mapEl.querySelectorAll('path').length >= 22,
        'county polygons survive a mode switch');

    console.log(`✅ county choropleth integration passed — ${paths.length} paths rendered with real Leaflet`);
}, 50);
