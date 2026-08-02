/**
 * Taiwan Gray Zone Monitor - Service Worker
 * Offline support + caching.
 *
 * Strategy note: app code (HTML/CSS/JS) and data (JSON) are **network-first**,
 * with the cache only as an offline fallback. The previous version was
 * cache-first for every static asset with a hardcoded CACHE_NAME, so a returning
 * visitor kept the CSS/JS from whenever they first loaded the site — after a
 * deploy the browser mixed new HTML with old CSS (panels rendered permanently
 * expanded, tiles unstyled) and there was no way to invalidate it short of
 * unregistering the worker. These files are a few hundred KB total; serving them
 * from the network first costs little and keeps the dashboard honest.
 */

const CACHE_NAME = 'taiwan-grayzone-v4';

// Precached so the first offline visit still works. Anything else the page
// requests gets cached opportunistically on first fetch.
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/dark-vessels.html',
    '/statistics.html',
    '/css/main.css',
    '/js/i18n.js',
    '/js/map-data.js',
    '/js/map-baseline.js',
    '/js/map-vessels.js',
    '/js/map-routes.js',
    '/js/map-cables.js',
    '/js/map-bathymetry.js',
    '/js/map.js',
    '/js/charts.js',
    '/js/mobile-nav.js',
    '/js/app.js'
];

// Extensions served network-first (app code + data). Everything else — images,
// fonts, tiles — stays cache-first, where staleness is harmless.
const NETWORK_FIRST = /\.(html|css|js|json)$/;

// Install: warm the cache, then take over immediately
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            // addAll rejects the whole install if any single URL 404s
            .then(cache => Promise.all(
                STATIC_ASSETS.map(url => cache.add(url).catch(() => null))
            ))
            .then(() => self.skipWaiting())
    );
});

// Activate: drop every cache from a previous CACHE_NAME
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => Promise.all(
            cacheNames
                .filter(name => name !== CACHE_NAME)
                .map(name => caches.delete(name))
        )).then(() => self.clients.claim())
    );
});

function cacheResponse(request, response) {
    if (!response || response.status !== 200 || response.type !== 'basic') return response;
    const clone = response.clone();
    caches.open(CACHE_NAME).then(cache => cache.put(request, clone));
    return response;
}

self.addEventListener('fetch', event => {
    const { request } = event;
    if (request.method !== 'GET') return;

    const url = new URL(request.url);
    const sameOrigin = url.origin === self.location.origin;

    // Navigations + app code + data: network first, cache as offline fallback
    if (request.mode === 'navigate' || (sameOrigin && NETWORK_FIRST.test(url.pathname))) {
        event.respondWith(
            fetch(request)
                .then(response => cacheResponse(request, response))
                .catch(() => caches.match(request).then(
                    cached => cached || caches.match('/index.html')
                ))
        );
        return;
    }

    // Everything else (images, fonts, map tiles): cache first
    event.respondWith(
        caches.match(request).then(cached => cached
            || fetch(request).then(response => cacheResponse(request, response)))
    );
});
