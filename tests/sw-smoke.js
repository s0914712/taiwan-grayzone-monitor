/**
 * Service worker smoke test — runs docs/sw.js against stubbed caches/fetch and
 * asserts the caching contract: precache under the current CACHE_NAME, purge of
 * older caches on activate, network-first for app code + navigations (the stale
 * CSS/JS bug), cache-first for images, and offline fallback to cache.
 *
 * Usage: node tests/sw-smoke.js  (run from repo root)
 */
const fs = require('fs'); const vm = require('vm'); const assert = require('assert');

function makeEnv({ networkFails = false } = {}) {
    const handlers = {};
    const store = new Map();          // CACHE_NAME -> Map(url -> body)
    const netLog = [];
    const cacheApi = {
        open: async (name) => {
            if (!store.has(name)) store.set(name, new Map());
            const m = store.get(name);
            return {
                add: async (u) => { if (networkFails) throw new Error('offline'); m.set(u, 'net:' + u); },
                put: async (req, res) => m.set(String(req.url || req), res.body),
            };
        },
        keys: async () => [...store.keys()],
        delete: async (name) => store.delete(name),
        match: async (req) => {
            const u = String(req.url || req);
            for (const m of store.values()) if (m.has(u)) return { body: m.get(u), status: 200, type: 'basic', clone() { return this; } };
            return undefined;
        },
    };
    const self = {
        addEventListener: (t, fn) => { handlers[t] = fn; },
        skipWaiting: async () => {}, clients: { claim: async () => {} },
        location: { origin: 'https://x.test' },
    };
    const ctx = {
        self, caches: cacheApi, console, URL, Promise, Map, Set, RegExp, JSON,
        fetch: async (req) => {
            netLog.push(String(req.url || req));
            if (networkFails) throw new Error('offline');
            return { body: 'NET', status: 200, type: 'basic', clone() { return this; } };
        },
    };
    ctx.globalThis = ctx;
    vm.createContext(ctx);
    vm.runInContext(fs.readFileSync('docs/sw.js', 'utf8'), ctx, { filename: 'sw.js' });
    return { handlers, store, netLog, self };
}

const run = (h, ev) => { let p; h({ ...ev, waitUntil: (x) => { p = x; }, respondWith: (x) => { p = x; } }); return p; };
const req = (url, mode = 'no-cors') => ({ url, method: 'GET', mode });

(async () => {
    // install precaches under the new name; a 404 asset must not abort install
    let env = makeEnv();
    await run(env.handlers.install, {});
    const names = [...env.store.keys()];
    assert.deepStrictEqual(names, ['taiwan-grayzone-v4'], 'cache name: ' + names);
    assert(env.store.get('taiwan-grayzone-v4').has('/css/main.css'), 'main.css precached');
    assert(env.store.get('taiwan-grayzone-v4').has('/js/mobile-nav.js'), 'mobile-nav.js precached');

    // activate deletes every older cache
    env.store.set('taiwan-grayzone-v3', new Map([['/css/main.css', 'STALE']]));
    await run(env.handlers.activate, {});
    assert(!env.store.has('taiwan-grayzone-v3'), 'old cache should be deleted');

    // CSS goes to the network even when a (stale) cached copy exists
    env.store.get('taiwan-grayzone-v4').set('https://x.test/css/main.css', 'STALE');
    let res = await run(env.handlers.fetch, { request: req('https://x.test/css/main.css') });
    assert.strictEqual(res.body, 'NET', 'css should be network-first, got ' + res.body);

    // navigation → network
    res = await run(env.handlers.fetch, { request: req('https://x.test/index.html', 'navigate') });
    assert.strictEqual(res.body, 'NET', 'navigation should be network-first');

    // images stay cache-first
    env.store.get('taiwan-grayzone-v4').set('https://x.test/og-banner.png', 'CACHED_IMG');
    res = await run(env.handlers.fetch, { request: req('https://x.test/og-banner.png') });
    assert.strictEqual(res.body, 'CACHED_IMG', 'images should be cache-first');

    // offline: CSS falls back to cache
    const off = makeEnv({ networkFails: true });
    await run(off.handlers.install, {});
    (await off.store.has('taiwan-grayzone-v4')) || off.store.set('taiwan-grayzone-v4', new Map());
    off.store.get('taiwan-grayzone-v4').set('https://x.test/css/main.css', 'CACHED_CSS');
    res = await run(off.handlers.fetch, { request: req('https://x.test/css/main.css') });
    assert.strictEqual(res.body, 'CACHED_CSS', 'offline should fall back to cache');

    console.log('✅ sw.js: v4 precache, old-cache purge, network-first app code, cache-first images, offline fallback');
})().catch(e => { console.error('❌', e.message); process.exit(1); });
