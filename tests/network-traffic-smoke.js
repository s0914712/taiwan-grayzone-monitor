/** Smoke test for the network-status dashboard with both JSON feeds. */
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
const mapMarkers = [];
const cf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'cf_radar.json')));
const ioda = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'ioda.json')));
const counties = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'tw_counties.geojson')));
const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'network-traffic.html'), 'utf8');

// 縣市指標檔是選配（要 Cloudflare token，管線跑過才有），因此這裡合成一份：
// 一半縣市有網速、一半沒有，正好驗證「有資料上色、沒資料灰色」兩條路。
const countyMetrics = {
    generated_at: new Date().toISOString(),
    counties: counties.features.map((feature, index) => (index % 2 === 0 ? {
        iso: feature.properties.iso,
        name_zh: feature.properties.name_zh,
        name_en: feature.properties.name_en,
        status: 'available', metric_id: 'iqi_bandwidth',
        metric_label_zh: '頻寬（IQI 中位數）', metric_label_en: 'Bandwidth (IQI median)',
        unit: 'Mbps', higher_is_better: true, is_speed: true,
        latest: 50 + index * 3, baseline: 60 + index * 3, pct_vs_baseline: -5,
        level: index === 0 ? 'alert' : 'normal', anomalies: [],
        series: { timestamps: ['2026-08-01T00:00:00Z'], values: [50 + index * 3], bucket_hours: 3 },
    } : {
        iso: feature.properties.iso,
        name_zh: feature.properties.name_zh,
        name_en: feature.properties.name_en,
        status: 'unavailable', error_reason: 'no_metric_available',
        metric_id: null, level: 'unknown', latest: null, anomalies: [],
        series: { timestamps: [], values: [], bucket_hours: 3 },
    })),
};

const geoLayers = [];
const map = {
    zoom: 6,
    setView() { return this; },
    fitBounds() { return this; },
    invalidateSize() {},
    flyTo(coords, zoom) { this.zoom = zoom; return this; },
    getZoom() { return this.zoom; },
    removeLayer() { return this; },
};
const markerLayer = {
    addTo() { return this; },
    clearLayers() { mapMarkers.length = 0; },
};
const L = {
    map: () => map,
    tileLayer: () => ({ addTo: () => ({}) }),
    layerGroup: () => markerLayer,
    latLngBounds: (points) => points,
    geoJSON: (data, options) => {
        const layer = { addTo() { return this; } };
        (data.features || []).forEach((feature) => {
            const style = options.style(feature);
            const featureLayer = {
                tooltip: '', popup: '',
                bindTooltip(text) { this.tooltip = text; return this; },
                bindPopup(content) { this.popup = content; return this; },
                on() { return this; },
                openPopup() { return this; },
            };
            options.onEachFeature(feature, featureLayer);
            geoLayers.push({ iso: feature.properties.iso, style, layer: featureLayer });
        });
        return layer;
    },
    circleMarker: (coords, options) => {
        const marker = {
            coords, options, tooltip: '', popup: '',
            bindTooltip(text) { this.tooltip = text; return this; },
            bindPopup(content) { this.popup = content; return this; },
            addTo() { mapMarkers.push(this); return this; },
            openPopup() { return this; },
        };
        return marker;
    },
};
const ctx = {
    console, Math, Date, Set, JSON, Promise, setTimeout, clearTimeout,
    L,
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
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'docs', 'js', 'network-traffic.js'), 'utf8'), ctx);

(async () => {
    domReady.forEach((fn) => fn());
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(html.includes('id="connectivityMap"'), 'map container exists');
    assert(html.includes('leaflet@1.9.4'), 'Leaflet runtime is loaded');
    assert(html.includes('id="trendPanel"'), 'trend panel contract exists');
    assert(elements.statusConclusion.textContent, 'current status rendered');
    assert(elements.islandStatus.innerHTML.includes('金門'), 'Kinmen card rendered');
    assert(elements.islandStatus.innerHTML.includes('馬祖'), 'Matsu card rendered');
    assert(elements.islandStatus.innerHTML.includes('資料不足'), 'missing Matsu is not misreported as online');
    assert(elements.islandStatus.innerHTML.includes('澎湖'), 'Penghu card rendered');
    assert.strictEqual(mapMarkers.length, 4, 'Taiwan and three outer islands rendered as markers');
    const labels = mapMarkers.map((marker) => marker.tooltip).join(' ');
    assert(labels.includes('台灣本島'), 'Taiwan marker rendered');
    assert(labels.includes('金門') && labels.includes('馬祖') && labels.includes('澎湖'), 'outer-island markers rendered');

    // ── 縣市色塊 ────────────────────────────────────────────────────────────
    assert(html.includes('id="countyModes"'), 'county metric switch container exists');
    assert(html.includes('id="countyLegend"'), 'county legend container exists');
    assert.strictEqual(geoLayers.length, 22, 'all 22 counties rendered as polygons');
    const matsu = geoLayers.find((entry) => entry.iso === 'TW-LIE');
    assert(matsu, 'Matsu (Lienchiang) has a polygon — Natural Earth would be missing it');

    const withData = geoLayers.filter((entry) => entry.layer.tooltip.includes('Mbps'));
    assert(withData.length > 0, 'counties with a speed metric show the value in the tooltip');
    const noData = geoLayers.filter((entry) => !entry.layer.tooltip.includes('Mbps'));
    assert(noData.length > 0 && noData.every((entry) => entry.style.fillColor.includes('138,164,200')),
        'counties without data are gray, never colored as if healthy');
    assert(noData[0].layer.popup.includes('資料不足'),
        'no-data popup says insufficient data rather than implying an outage');

    assert(elements.countyModes.innerHTML.includes('網速'), 'speed mode chip rendered');
    assert(!elements.countyModes.innerHTML.includes('流量指數'),
        'modes with no county data are not offered');
    assert(elements.countyLegend.innerHTML.includes('Mbps'), 'legend shows the metric unit');

    console.log('✅ network traffic dashboard smoke test passed');
})().catch((err) => { console.error(err); process.exitCode = 1; });
