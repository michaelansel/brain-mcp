// Service worker for the brain site: every page readable offline, and an
// online load always the current build. scripts/offline.py copies this into
// each release with __VERSION__ replaced by a hash of that release's
// offline.json, so a build that changes anything changes this file, which is
// how the browser learns there is a new build to save.
//
// Online, pages come from the network; the saved copy is used only when the
// network fails or is slow. Offline, every page is there, opened before or
// not, because install saves the whole site listed in offline.json. Install
// fetches only what changed since the last saved copy, and fails as a whole
// if any fetch fails, leaving the previous worker and its copy in place.
const VERSION = "__VERSION__";
const CACHE = `brain-${VERSION}`;
const TIMEOUT = 5000; // ms to wait for the network before using the saved copy
const PARALLEL = 6;
const root = new URL("./", self.location); // the site root, wherever it is mounted
const at = (path) => new URL(path, root).href;

self.addEventListener("install", (event) => {
  event.waitUntil(save().then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith("brain-") && name !== CACHE) await caches.delete(name);
      }
      await self.clients.claim();
    })(),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // The reset page must work when everything here is broken: never touch it.
  if (url.href.startsWith(at("reset/"))) return;
  if (event.request.method !== "GET" || !url.href.startsWith(root.href)) return;
  // Material's bundles have content hashes in their names; a saved one is current.
  if (url.href.startsWith(at("assets/"))) {
    event.respondWith(caches.match(url.href, { ignoreSearch: true }).then((r) => r || fetch(event.request)));
    return;
  }
  event.respondWith(networkFirst(event.request));
});

async function save() {
  const listing = await fetch(at("offline.json"), { cache: "no-cache", redirect: "error" });
  if (!listing.ok) throw new Error(`offline.json: ${listing.status}`);
  const manifest = await listing.clone().json();

  // The copy being replaced, if any, and what it holds.
  let old = null;
  let held = {};
  for (const name of await caches.keys()) {
    if (!name.startsWith("brain-") || name === CACHE) continue;
    const prev = await caches.open(name);
    const prevListing = await prev.match(at("offline.json"));
    if (prevListing) {
      old = prev;
      held = (await prevListing.json()).files;
    }
  }

  const cache = await caches.open(CACHE);
  // The search index last: the page fetches it on every load, so by the time
  // this gets to it the page has usually fetched it already, and this request
  // is answered "not modified" instead of downloading it a second time.
  const search = "search/search_index.json";
  const paths = Object.keys(manifest.files).filter((p) => p !== search);
  if (search in manifest.files) paths.push(search);
  let next = 0;
  async function worker() {
    while (next < paths.length) {
      const path = paths[next++];
      const url = at(path);
      const kept = old && held[path] === manifest.files[path] && (await old.match(url));
      if (kept) {
        await cache.put(url, kept);
        continue;
      }
      // A redirect means the login has lapsed; fail rather than save the login page.
      const res = await fetch(url, { cache: "no-cache", redirect: "error" });
      if (!res.ok) throw new Error(`${path}: ${res.status}`);
      await cache.put(url, res);
    }
  }
  await Promise.all(Array.from({ length: PARALLEL }, worker));
  await cache.put(at("offline.json"), listing); // last: marks the copy complete
}

async function networkFirst(request) {
  const network = fetch(request);
  let timer;
  const late = new Promise((resolve) => (timer = setTimeout(resolve, TIMEOUT, null)));
  // Whatever the server says, a login redirect or a 404 included, is the answer.
  const res = await Promise.race([network.catch(() => null), late]);
  clearTimeout(timer);
  if (res) return res;
  return (await saved(request)) || network;
}

async function saved(request) {
  const url = new URL(request.url);
  url.search = url.hash = "";
  const cache = await caches.open(CACHE);
  const hit = await cache.match(url.href);
  if (hit) return request.mode === "navigate" ? marked(hit) : hit;
  if (!url.pathname.endsWith("/") && (await cache.match(url.href + "/"))) {
    return Response.redirect(url.href + "/", 301); // as nginx would
  }
  if (request.mode === "navigate") {
    const missing = await cache.match(at("404.html"));
    if (missing) return marked(missing, 404);
  }
  return null;
}

// Tags a saved page so extra.css can say it is a saved copy.
async function marked(res, status = res.status) {
  const html = (await res.text()).replace("<body", "<body data-brain-saved");
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}
