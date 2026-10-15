// Replaces the app's service worker on the old github.io address (ADR 0017):
// it clears the cached copy of the site and unregisters itself, so the old
// address only serves the redirect page.
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const key of await caches.keys()) await caches.delete(key);
      await self.registration.unregister();
    })(),
  );
});
