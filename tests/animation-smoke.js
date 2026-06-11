/**
 * Animation pages smoke test — loads each animation page's real HTML into
 * jsdom, evaluates i18n.js + the extracted page script with REAL Leaflet,
 * dispatches DOMContentLoaded, and asserts the Leaflet map initialized
 * without throwing.
 *
 * Guards the inline-script → external-file extraction of
 * ais-animation.html / cn-fishing-animation.html.
 *
 * Usage: node tests/animation-smoke.js
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

function testPage(htmlFile, pageScript) {
    const html = fs.readFileSync(path.join(DOCS, htmlFile), 'utf8')
        // GTM inline script bootstraps network fetches — drop it for the test
        .replace(/<script>\(function\(w,d,s,l,i\)[\s\S]*?<\/script>/, '');
    const dom = new JSDOM(html, { runScripts: 'outside-only', pretendToBeVisual: true, url: 'http://localhost/' + htmlFile });
    const { window } = dom;

    const mapEl = window.document.getElementById('map');
    assert(mapEl, htmlFile + ' has #map container');
    Object.defineProperty(mapEl, 'clientWidth', { value: 800 });
    Object.defineProperty(mapEl, 'clientHeight', { value: 600 });

    // Chart.js stub — pages create/destroy charts; rendering not under test
    window.eval(`
        function Chart(ctx, cfg) {
            this.data = (cfg && cfg.data) || { labels: [], datasets: [] };
            this.options = (cfg && cfg.options) || {};
        }
        Chart.prototype.update = function () {};
        Chart.prototype.destroy = function () {};
        Chart.defaults = { color: '#fff', font: {} };
    `);
    // Canvas 2D stub (jsdom has no canvas implementation): every method no-ops,
    // every property is writable, so Leaflet's canvas renderer runs silently
    window.HTMLCanvasElement.prototype.getContext = function () {
        const store = {};
        return new Proxy(store, {
            get: (t, k) => (k in t ? t[k] : () => undefined),
            set: (t, k, v) => { t[k] = v; return true; },
        });
    };

    // fetch stub: data files unavailable → loaders take their error paths
    window.fetch = () => Promise.resolve({ ok: false, status: 404, json: () => Promise.reject(new Error('404')) });

    // jsdom lacks matchMedia (used for mobile-layout checks during bootstrap)
    window.matchMedia = () => ({ matches: false, addListener: () => {}, addEventListener: () => {} });

    window.eval(LEAFLET_SRC);
    // Top-level const/let in one eval call aren't visible to the next (unlike
    // real <script> tags) — re-expose i18n as a window property
    window.eval(fs.readFileSync(path.join(DOCS, 'js', 'i18n.js'), 'utf8') + '\n;window.i18n = i18n;');
    window.eval(fs.readFileSync(path.join(DOCS, 'js', pageScript), 'utf8'));

    // jsdom fires DOMContentLoaded asynchronously after this macrotask, which
    // triggers the page script's bootstrap listener — wait for it, then assert
    return new Promise((resolve, reject) => {
        window.document.addEventListener('DOMContentLoaded', () => {
            setTimeout(() => {
                try {
                    assert(mapEl.classList.contains('leaflet-container'),
                        htmlFile + ': Leaflet map initialized on #map');
                    console.log(`✅ ${htmlFile} + js/${pageScript}: evaluated, DOMContentLoaded init OK, Leaflet map mounted`);
                    resolve();
                } catch (e) { reject(e); }
            }, 50);
        });
    });
}

(async () => {
    await testPage('ais-animation.html', 'ais-animation.js');
    await testPage('cn-fishing-animation.html', 'cn-fishing-animation.js');
    console.log('✅ animation pages smoke test passed');
})().catch((e) => { console.error('❌', e); process.exit(1); });
