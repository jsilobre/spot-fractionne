// Service worker: makes the page installable as an app and lets it start
// without the network. Classic script (no ES modules in every browser's
// service workers); tests load it in a sandbox to check requestStrategy.
//
// - Page files: network first, so a deploy shows up at once; the cached copy
//   is used offline.
// - Pinned CDN libraries: cache first, their versioned URLs never change.
// - Everything else (segments, tiles, basemaps, geocoder, routes, analytics): not
//   touched, always from the network.

const CACHE = "spot-fractionne-v1";

// Files of the page, relative to the service worker's scope.
const SHELL = [
  "./",
  "style.css",
  "app.js",
  "analytics.js",
  "basemaps.js",
  "coursestyle.js",
  "filters.js",
  "geocode.js",
  "pwa.js",
  "route.js",
  "profile.js",
  "tiles.js",
  "vendor/goatcounter-count.js",
  "manifest.webmanifest",
  "icons/icon.svg",
  "icons/icon-192.png",
];

// The libraries pinned in index.html (import map and stylesheet).
const CDN = [
  "https://unpkg.com/maplibre-gl@6.11.2/dist/maplibre-gl.css",
  "https://unpkg.com/maplibre-gl@6.11.2/dist/maplibre-gl.mjs",
  "https://unpkg.com/maplibre-gl@6.11.2/dist/maplibre-gl-shared.mjs",
  "https://unpkg.com/pmtiles@4.5.0/dist/esm/index.js",
  "https://unpkg.com/fflate@0.8.3/esm/browser.js",
];

/**
 * How to answer a request: "network-first", "cache-first" or null (left to
 * the browser). `scope` is the service worker's scope URL.
 */
function requestStrategy(method, url, hasRange, scope) {
  if (method !== "GET" || hasRange) return null;
  const target = new URL(url);
  if (CDN.includes(target.href)) return "cache-first";
  const base = new URL(scope);
  if (target.origin !== base.origin || !target.pathname.startsWith(base.pathname)) return null;
  // Published data changes with each run and can be large: never cached.
  if (target.pathname.startsWith(`${base.pathname}data/`)) return null;
  return "network-first";
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch (error) {
    // Shared links (?id=, ?lat=) open the cached page too.
    const cached = await cache.match(request, { ignoreSearch: request.mode === "navigate" });
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE);
  const cached = await cache.match(request.url);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) await cache.put(request.url, response.clone());
  return response;
}

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE);
      await cache.addAll(SHELL);
      await cache.addAll(CDN.map((url) => new Request(url, { mode: "cors" })));
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) if (key !== CACHE) await caches.delete(key);
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const strategy = requestStrategy(
    request.method,
    request.url,
    request.headers.has("range"),
    self.registration.scope,
  );
  if (strategy === "network-first") event.respondWith(networkFirst(request));
  else if (strategy === "cache-first") event.respondWith(cacheFirst(request));
});
