/**
 * Dark-vessel page smoke test — the daily chart's ratio line and the
 * "provisional ratio" caveat, both of which exist because GFW's numbers are
 * not final when first published.
 *
 * Usage: node tests/dark-vessels-smoke.js  (run from repo root)
 */
const vm = require('vm');
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const ROOT = path.join(__dirname, '..');

// ── 1. ChartsModule.renderDailyChart ─────────────────────────────────────
let lastChart = null;
function makeCtx(lang) {
    const ctx = {
        console, Math, Date, Set, Map, JSON, RegExp, Promise, Number, Array, Object, String,
        setTimeout, clearTimeout, setInterval, clearInterval,
        window: { addEventListener: () => {} },
        navigator: {},
        document: {
            getElementById: (id) => ({
                id, style: {}, innerHTML: '', textContent: '',
                getContext: () => ({}), removeAttribute: () => {},
            }),
            addEventListener: () => {},
            createElement: () => ({ style: {} }),
        },
        fetch: () => new Promise(() => {}),
        i18n: { t: (k) => k, getLang: () => (lang || 'zh') },
        Chart: function (c, cfg) { lastChart = cfg; this.destroy = () => {}; },
    };
    ctx.globalThis = ctx;
    return vm.createContext(ctx);
}

const chartsCtx = makeCtx('zh');
vm.runInContext(
    fs.readFileSync(path.join(ROOT, 'docs/js/charts.js'), 'utf8')
    + '\n;globalThis.__cap = ChartsModule;', chartsCtx, { filename: 'charts.js' });
const ChartsModule = chartsCtx.__cap;

// 有分母 → 總偵測數長條 + 暗船長條 + 比例折線（右軸）
ChartsModule.renderDailyChart('dailyChart',
    { '2026-09-08': 25, '2026-09-09': 50 },
    { '2026-09-08': 100, '2026-09-09': 100, '2026-09-10': 40 });
assert.strictEqual(lastChart.data.datasets.length, 3, 'three datasets with totals');
const ratioSet = lastChart.data.datasets.find(d => d.type === 'line');
assert.deepStrictEqual(ratioSet.data, [25, 50, 0], 'ratio in percent, zero-dark day kept');
assert.deepStrictEqual(lastChart.data.labels, ['09-08', '09-09', '09-10'],
    'labels union both series — a day with detections but no dark vessels still shows');
assert.strictEqual(lastChart.options.scales.y1.display, true, 'ratio axis shown');
assert.strictEqual(ratioSet.spanGaps, true, 'no-pass days must not be drawn as a fake drop');

// 無分母 → 退回單一長條圖，不得畫出無意義的比例軸
lastChart = null;
ChartsModule.renderDailyChart('dailyChart', { '2026-09-08': 25 });
assert.strictEqual(lastChart.data.datasets.length, 1, 'single dataset without totals');
assert.strictEqual(lastChart.options.scales.y1.display, false, 'ratio axis hidden');

// ── 2. 頁面內嵌的判讀註記 ────────────────────────────────────────────
const html = fs.readFileSync(path.join(ROOT, 'docs/dark-vessels.html'), 'utf8');
const inline = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)]
    .map(m => m[1]).find(src => src.includes('function renderDarkCaveat'));
assert(inline, 'inline page script with renderDarkCaveat found');

function runCaveat(dv, lang) {
    const els = {};
    const pageCtx = makeCtx(lang);
    pageCtx.document.getElementById = (id) => (els[id] = els[id] || {
        id, style: {}, innerHTML: '', textContent: '', removeAttribute: () => {},
    });
    pageCtx.L = { map: () => ({}) };
    vm.runInContext(inline + '\n;globalThis.__caveat = renderDarkCaveat;', pageCtx,
        { filename: 'dark-vessels.html' });
    pageCtx.__caveat(dv);
    return els;
}

const base = {
    data_range: { start: '2026-08-13', end: '2026-09-12' },
    overall: { dark_ratio: 40.5, dark_by_date: { '2026-09-09': 1566 },
               total_by_date: { '2026-09-09': 4000, '2026-09-12': 0 } },
};

// 尚未觀察到修訂時：仍要標暫定，並說明資料落後
let els = runCaveat(JSON.parse(JSON.stringify(base)), 'zh');
assert(els.darkCaveat.innerHTML.includes('暫定'), 'ratio flagged provisional');
assert(els.darkCaveat.innerHTML.includes('2026-09-09'), 'last data date shown');
assert(els.darkCaveat.innerHTML.includes('落後 3 天'), 'lag vs query window shown');
assert.strictEqual(els.ratioProvisional.textContent, '暫定', 'badge on the ratio tile');
assert.strictEqual(els.darkCaveat.style.display, 'block');

// 量到回溯修訂時：把幅度講出來
const revised = JSON.parse(JSON.stringify(base));
revised.revision = {
    dates_tracked: 28, dates_revised: 20, dates_revised_down: 19,
    dark_delta_pct: -30.1, first_ratio_pct: 40.5, latest_ratio_pct: 21.3,
    ratio_delta_pts: -19.2, ratio_dates: 28,
};
els = runCaveat(revised, 'zh');
assert(els.darkCaveat.innerHTML.includes('-30.1%'), 'dark-count revision reported');
assert(els.darkCaveat.innerHTML.includes('40.5% → 目前 21.3%'), 'ratio revision reported');

// 英文版也要有內容（前端是雙語頁）
els = runCaveat(revised, 'en');
assert(els.darkCaveat.innerHTML.includes('provisional'), 'english caveat rendered');
assert.strictEqual(els.ratioProvisional.textContent, 'prov.');

console.log('✅ dark-vessels smoke test passed');
