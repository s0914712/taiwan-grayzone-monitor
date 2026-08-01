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
            addEventListener: () => {}, querySelectorAll: () => [],
        };
    }
    return elements[id];
}

const domReady = [];
const cf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'cf_radar.json')));
const ioda = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'docs', 'ioda.json')));
const dashboardSource = fs.readFileSync(path.join(__dirname, '..', 'docs', 'js', 'network-traffic.js'), 'utf8');
assert(!dashboardSource.includes('buildDemo'), 'dashboard must not contain a synthetic-data fallback');
assert(!dashboardSource.includes('DEMO BULKER'), 'dashboard must not contain synthetic vessels');
const ctx = {
    console, Math, Date, Set, JSON, Promise, setTimeout, clearTimeout,
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
vm.runInContext(dashboardSource, ctx);

(async () => {
    domReady.forEach((fn) => fn());
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(elements.currentStatusTitle.textContent, 'current status rendered');
    assert(elements.islandGrid.innerHTML.includes('台灣本島'), 'Taiwan card rendered');
    assert(elements.islandGrid.innerHTML.includes('金門'), 'Kinmen card rendered');
    assert(elements.islandGrid.innerHTML.includes('馬祖'), 'missing Matsu is explicitly rendered');
    assert(elements.islandGrid.innerHTML.includes('澎湖'), 'Penghu card rendered');
    assert(elements.focusEventTitle.textContent.includes('已結束'), 'latest ended event is not presented as ongoing');
    assert(elements.historyAnomalyList.innerHTML.includes('anomaly-card'), 'older anomalies are moved into history');
    assert(!elements.anomalyList.innerHTML.includes('穩健 z'), 'technical score is hidden from the primary decision card');

    const failedElements = {};
    const failedReady = [];
    const failedContext = {
        console, Math, Date, Set, JSON, Promise, setTimeout, clearTimeout,
        window: { addEventListener: () => {} },
        i18n: { getLang: () => 'zh' },
        document: {
            getElementById: (id) => failedElements[id] ||= {
                id, textContent: '', innerHTML: '', style: {}, dataset: {}, open: false,
                addEventListener: () => {}, querySelectorAll: () => [],
            },
            addEventListener: (name, fn) => { if (name === 'DOMContentLoaded') failedReady.push(fn); },
        },
        fetch: async () => { throw new Error('feed unavailable'); },
    };
    vm.createContext(failedContext);
    vm.runInContext(dashboardSource, failedContext);
    failedReady.forEach((fn) => fn());
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(failedElements.currentStatusTitle.textContent.includes('資料不足'), 'failed feeds render an unknown state');
    assert(failedElements.dataStatus.textContent.includes('資料不足'), 'failed feeds are clearly reported');
    assert(!failedElements.anomalyList.innerHTML.includes('anomaly-card'), 'failed feeds never create synthetic anomalies');
    console.log('✅ network traffic dashboard smoke test passed');
})().catch((err) => { console.error(err); process.exitCode = 1; });
