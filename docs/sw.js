/**
 * Taiwan Gray Zone Monitor - Service Worker
 * Provides offline support and caching
 */

const CACHE_NAME = 'taiwan-grayzone-v1';
const STATIC_ASSETS = [
    '/',
    '/index.html',
    '/dark-vessels.html',
    '/statistics.html',
    '/css/main.css',
    '/js/map.js',
    '/js/charts.js',
    '/js/app.js'
];

// Install event - cache static assets
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => {
                console.log('Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => self.skipWaiting())
    );
});

// Activate event - clean up old caches
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames
                    .filter(name => name !== CACHE_NAME)
                    .map(name => caches.delete(name))
            );
        }).then(() => self.clients.claim())
    );
});

// Fetch event - network first for data, cache first for static
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // Network first for data.json to get fresh data
    if (url.pathname.endsWith('data.json')) {
        event.respondWith(
            fetch(event.request)
                .then(response => {
                    // Clone and cache the response
                    const responseClone = response.clone();
                    caches.open(CACHE_NAME).then(cache => {
                        cache.put(event.request, responseClone);
                    });
                    return response;
                })
                .catch(() => {
                    // Fall back to cache if network fails
                    return caches.match(event.request);
                })
        );
        return;
    }

    // Cache first for static assets
    event.respondWith(
        caches.match(event.request)
            .then(cachedResponse => {
                if (cachedResponse) {
                    return cachedResponse;
                }
                return fetch(event.request)
                    .then(response => {
                        // Don't cache non-success responses or external resources
                        if (!response || response.status !== 200 || response.type !== 'basic') {
                            return response;
                        }
                        const responseClone = response.clone();
                        caches.open(CACHE_NAME).then(cache => {
                            cache.put(event.request, responseClone);
                        });
                        return response;
                    });
            })
    );
});
