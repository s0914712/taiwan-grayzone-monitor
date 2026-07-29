/**
 * Mobile navigation smoke test — builds the shared mobile shell inside jsdom
 * at a phone viewport and asserts the invariants that broke in production:
 *
 *   1. The 工具 tab must open a sheet that actually has links (it used to be
 *      empty on every page except index — all the tool pages had ended up
 *      behind the 動畫 tab).
 *   2. No link appears in more than one entry point (bottom nav vs sheet).
 *   3. Under /en/ the links must climb out of the directory, since only the
 *      static content pages are mirrored there.
 *
 * Usage: node tests/mobile-nav-smoke.js
 * Requires: npm package jsdom (path via JSDOM_NM env or node_modules).
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
    console.error('jsdom not found — run: npm install jsdom');
    process.exit(2);
}
const { JSDOM } = require(path.join(NM, 'jsdom'));

const NAV_SRC = fs.readFileSync(
    path.join(__dirname, '..', 'docs', 'js', 'mobile-nav.js'), 'utf8');

function buildNav(url) {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>', {
        runScripts: 'dangerously', url,
    });
    const { window } = dom;
    Object.defineProperty(window, 'innerWidth', { value: 390, configurable: true });
    window.i18n = { t: k => k, applyAll: () => {} };
    window.eval(NAV_SRC);
    // The script defers to DOMContentLoaded, which jsdom fires asynchronously.
    return new Promise(resolve => {
        if (window.document.readyState !== 'loading') return resolve(window);
        window.document.addEventListener('DOMContentLoaded', () => resolve(window));
    });
}

async function main() {

    // ── Regular (zh) page ────────────────────────────────────────────────────
    let win = await buildNav('http://localhost/network-traffic.html');
    let doc = win.document;

    const nav = doc.querySelector('.mobile-bottom-nav');
    assert(nav, 'bottom nav rendered');
    assert.strictEqual(nav.children.length, 5, '5 tabs');
    assert(doc.getElementById('navToolsBtn'), 'tools tab present');
    assert(!doc.querySelector('.nav-popover'), 'animation popover removed');

    const sheet = doc.querySelector('.bottom-sheet');
    assert(sheet, 'bottom sheet rendered');
    const sheetLinks = [...sheet.querySelectorAll('.bs-nav-item')];
    assert(sheetLinks.length >= 7, `tools sheet must not be empty (got ${sheetLinks.length})`);

    // Tapping 工具 opens the sheet
    doc.getElementById('navToolsBtn').dispatchEvent(new win.Event('click'));
    assert(sheet.classList.contains('open'), 'tools tab opens the sheet');

    // Current page is highlighted in the sheet, and the tools tab itself is active
    assert(sheetLinks.some(a => a.getAttribute('href') === 'network-traffic.html'
        && a.classList.contains('active')), 'current tool page highlighted');
    assert(doc.getElementById('navToolsBtn').classList.contains('active'),
        'tools tab active on a tool page');

    // ── No duplicate destinations across entry points ────────────────────────
    const navHrefs = [...nav.querySelectorAll('a')].map(a => a.getAttribute('href'));
    const sheetHrefs = sheetLinks.map(a => a.getAttribute('href'));
    const all = navHrefs.concat(sheetHrefs);
    assert.strictEqual(new Set(all).size, all.length,
        'every destination appears exactly once: ' + all.join(', '));
    assert(navHrefs.includes('ais-animation.html'), '動畫 links straight to the animation page');

    // ── app.js injections land above the page-nav block ──────────────────────
    win.MobileNav.addSheetSection('<div class="bottom-sheet-section" id="injected"></div>');
    const sections = [...sheet.querySelectorAll('.bottom-sheet-section')];
    assert(sections.findIndex(s => s.id === 'injected')
         < sections.findIndex(s => s.classList.contains('bs-nav-section')),
        'page-specific sections stay above the page-navigation block');

    // ── /en/ article: links must escape the mirrored directory ───────────────
    win = await buildNav('http://localhost/en/blog-what-is-dark-vessel.html');
    doc = win.document;
    const enHrefs = [...doc.querySelectorAll('.mobile-bottom-nav a, .bs-nav-item')]
        .map(a => a.getAttribute('href'));
    ['../index.html', '../dark-vessels.html', '../statistics.html', '../ais-animation.html',
     '../network-traffic.html'].forEach(h =>
        assert(enHrefs.includes(h), `/en/ link must be ${h} (got ${enHrefs.join(', ')})`));
    // blog / intro / research ARE mirrored under /en/ — they must stay in-directory
    ['blog.html', 'intro.html', 'research-submarine-cable-legal.html'].forEach(h =>
        assert(enHrefs.includes(h), `/en/ mirrored page must stay local: ${h}`));
    assert([...doc.querySelectorAll('.bs-nav-item')]
        .some(a => a.getAttribute('href') === 'blog.html' && a.classList.contains('active')),
        'blog article highlights the 深度文章 entry');

    console.log('✅ mobile-nav smoke test passed');
}

main().catch(e => { console.error(e); process.exit(1); });
