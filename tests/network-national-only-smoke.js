/**
 * Radar 忽略 geoId 時的呈現 — network-traffic.js。
 *
 * 實跑管線抓到的假陽性：四個分區回傳完全相同的序列，代表 Cloudflare 對 quality
 * 端點靜默忽略 `geoId`。管線偵測到就把 granularity 標成 national_only、縣市記錄
 * 標成同名 status。這支測試守住**前端不得拿全國值當分區／縣市值上色**，
 * 但仍要把那個全國數字顯示出來。
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const elements = {};
function element(id) {
    if (!elements[id]) {
        elements[id] = {
            id, textContent: '', innerHTML: '', style: {}, dataset: {}, open: false,
            classList: { toggle: () => {} },
            addEventListener: () => {}, querySelectorAll: () => [],
        };
    }
    return elements[id];
}

const domReady = [];
const geoLayers = [];
const DOCS = path.join(__dirname, '..', 'docs');
const cf = JSON.parse(fs.readFileSync(path.join(DOCS, 'cf_radar.json')));
const ioda = JSON.parse(fs.readFileSync(path.join(DOCS, 'ioda.json')));
const counties = JSON.parse(fs.readFileSync(path.join(DOCS, 'tw_counties.geojson')));

// 管線在 granularity=national_only 時輸出的形狀
const NATIONAL_VALUE = 14.4;
const countyMetrics = {
    generated_at: new Date().toISOString(),
    granularity: 'national_only',
    adm1_groups: [{
        group_id: 'taipei', radar_name: 'Taipei', label_zh: '臺北市', label_en: 'Taipei',
        members: ['TW-TPE'], status: 'available', differentiated: false,
        metric_id: 'iqi_bandwidth', metric_label_zh: '頻寬（IQI 中位數）',
        metric_label_en: 'Bandwidth (IQI median)', unit: 'Mbps', latest: NATIONAL_VALUE,
        speed_test: { bandwidth_download: 124.69, latency_idle: 81.67 },
    }],
    counties: counties.features.map((feature) => ({
        iso: feature.properties.iso,
        name_zh: feature.properties.name_zh,
        name_en: feature.properties.name_en,
        status: 'national_only', error_reason: 'geoid_ignored_by_radar',
        is_group_value: false, metric_id: 'iqi_bandwidth', unit: 'Mbps',
        higher_is_better: true, is_speed: true, latest: NATIONAL_VALUE,
        level: 'unknown', anomalies: [], speed_test: null,
        series: { timestamps: [], values: [], bucket_hours: 3 },
    })),
};

const map = {
    zoom: 6,
    setView() { return this; }, fitBounds() { return this; }, invalidateSize() {},
    flyTo(coords, zoom) { this.zoom = zoom; return this; }, getZoom() { return this.zoom; },
    removeLayer() { return this; },
};
const markerLayer = { addTo() { return this; }, clearLayers() {} };
const L = {
    map: () => map,
    tileLayer: () => ({ addTo: () => ({}) }),
    layerGroup: () => markerLayer,
    latLngBounds: (points) => points,
    geoJSON: (data, options) => {
        (data.features || []).forEach((feature) => {
            const style = options.style(feature);
            const layer = {
                tooltip: '', popup: '',
                bindTooltip(text) { this.tooltip = text; return this; },
                bindPopup(content) { this.popup = content; return this; },
                on() { return this; }, openPopup() { return this; },
            };
            options.onEachFeature(feature, layer);
            geoLayers.push({ iso: feature.properties.iso, style, layer });
        });
        return { addTo() { return this; } };
    },
    circleMarker: () => ({
        bindTooltip() { return this; }, bindPopup() { return this; },
        addTo() { return this; }, openPopup() { return this; },
    }),
};

const ctx = {
    console, Math, Date, Set, JSON, Promise, setTimeout, clearTimeout, L,
    window: { addEventListener: () => {} },
    i18n: { getLang: () => 'zh' },
    document: {
        getElementById: element,
        addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') domReady.push(fn); },
    },
    fetch: async (url) => {
        const body = url.startsWith('ioda.json') ? ioda
            : url.startsWith('tw_counties.geojson') ? counties
                : url.startsWith('cf_radar_counties.json') ? countyMetrics : cf;
        return { ok: true, json: async () => body };
    },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path.join(DOCS, 'js', 'network-traffic.js'), 'utf8'), ctx);

(async () => {
    domReady.forEach((fn) => fn());
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert(!elements.countyModes.innerHTML.includes('網速'),
        'the speed mode is withdrawn when Radar returns one undifferentiated national value');
    const legend = elements.countyLegend.innerHTML;
    assert(legend.includes('全國單一值'),
        'legend explains that the value is national and the region filter was ignored');
    assert(legend.includes('14.4'), 'the national value itself is still shown');
    assert(legend.includes('124.69') || legend.includes('125'),
        'the national Speed Test median is still shown');
    // 地圖可以退回 IODA 的逐縣市可達性上色，但**絕不能**把全國網速畫成縣市值：
    // 任何一格 tooltip 出現 Mbps 就是把全國值當縣市值在講。
    geoLayers.forEach((entry) => {
        assert(!entry.layer.tooltip.includes('Mbps'),
            entry.iso + ' must not show the national Mbps value as its own');
        assert(!entry.layer.popup.includes('非本縣市單獨量測') ||
            !entry.layer.popup.includes('Mbps'),
            entry.iso + ' popup must not present the national value as a regional one');
    });
    console.log('✅ national-only fallback smoke test passed — ' +
        geoLayers.length + ' counties, none carrying the national value as their own');
})().catch((err) => { console.error(err); process.exitCode = 1; });
