// Published as sw.js instead of the real one when BRAIN_SITE_OFFLINE=0. The
// browser always fetches sw.js from the server, never from the worker it
// replaces, so this reaches a phone whose saved copy is wedged: it deletes the
// saved copy and unregisters itself on the next online open.
self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith("brain-")) await caches.delete(name);
      }
      await self.registration.unregister();
    })(),
  );
});
