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
const html = fs.readFileSync(path.join(__dirname, '..', 'docs', 'network-traffic.html'), 'utf8');
const map = {
    zoom: 6,
    setView() { return this; },
    fitBounds() { return this; },
    invalidateSize() {},
    flyTo(coords, zoom) { this.zoom = zoom; return this; },
    getZoom() { return this.zoom; },
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
    fetch: async (url) => ({
        ok: true,
        json: async () => url.startsWith('ioda.json') ? ioda : cf,
    }),
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
    console.log('✅ network traffic dashboard smoke test passed');
})().catch((err) => { console.error(err); process.exitCode = 1; });
