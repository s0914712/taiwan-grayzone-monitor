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
vm.runInContext(fs.readFileSync(path.join(__dirname, '..', 'docs', 'js', 'network-traffic.js'), 'utf8'), ctx);

(async () => {
    domReady.forEach((fn) => fn());
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert(elements.currentStatusTitle.textContent, 'current status rendered');
    assert(elements.islandGrid.innerHTML.includes('台灣本島'), 'Taiwan card rendered');
    assert(elements.islandGrid.innerHTML.includes('金門'), 'Kinmen card rendered');
    assert(elements.islandGrid.innerHTML.includes('馬祖'), 'missing Matsu is explicitly rendered');
    assert(elements.islandGrid.innerHTML.includes('澎湖'), 'Penghu card rendered');
    console.log('✅ network traffic dashboard smoke test passed');
})().catch((err) => { console.error(err); process.exitCode = 1; });
