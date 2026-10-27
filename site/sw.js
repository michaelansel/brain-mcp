// Service worker for the brain site: every page readable offline, and a page
// that is already saved and still current opens without asking the server for
// it. scripts/offline.py copies this into each release unchanged, so the
// browser reinstalls the worker only when its code changes; a new build of
// the notes is the worker's own job.
//
// Each build publishes offline.json, every file with a hash of its content,
// and version.txt, a hash of offline.json. Opening a page asks for version.txt
// and nothing else: if it names the build already saved, every file is
// served from the saved copy. If it names a newer one, the worker fetches the
// new offline.json, serves each file whose hash is unchanged from the saved
// copy and each changed one from the server, and saves the changed files in
// the background, pages first and the search index last. Each file is used
// as soon as it is saved, and a sync cut short resumes where it stopped.
const CONTENT = "brain-content"; // the saved files, kept across builds
const STATE = "brain-state"; // what the saved files are: see load()
const BUDGET = 800; // ms to wait for version.txt before using the saved copy
const TIMEOUT = 5000; // ms to wait for a changed file before using the saved one
const PARALLEL = 6;
const SEARCH = "search/search_index.json";
const root = new URL("./", self.location); // the site root, wherever it is mounted
const at = (path) => new URL(path, root).href;

// The saved copy's state, persisted in STATE after every few saved files:
//   held:   path -> hash of the saved copy of that file
//   want:   path -> hash in the newest build seen (its offline.json)
//   wanted: hash of that offline.json, which is what version.txt names
//   synced: the build held completely, if any
let state = null;
let latest = null; // the newest navigation's check(), for the requests it causes: see respond()
let syncing = null;
const served = new Map(); // client id -> hash of the page it was given

// Saves the site before taking over, as far as it can: a device with no copy
// yet gets all of it, and one the previous worker saved keeps that copy.
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      await adopt();
      if ((await check().catch(() => "offline")) === "newer") await sync().catch(() => {});
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      for (const name of await caches.keys()) {
        if (name.startsWith("brain-") && name !== CONTENT && name !== STATE) await caches.delete(name);
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
  // What the freshness check itself reads, and the banner's way to the login.
  if (url.href === at("version.txt") || url.href === at("offline.json")) return;
  if (url.searchParams.has("network")) return;
  // Material's bundles have content hashes in their names; a saved one is current.
  if (url.href.startsWith(at("assets/"))) {
    event.respondWith(caches.match(url.href, { ignoreSearch: true }).then((r) => r || fetch(event.request)));
    return;
  }
  event.respondWith(respond(event));
});

async function respond(event) {
  const request = event.request;
  const navigate = request.mode === "navigate";
  // One check per page opened. The page's own requests follow right behind it
  // and use that answer, so a page open costs one request, not one per file.
  // It gets BUDGET ms to hear from the server, counted from the page open, not
  // per request; once version.txt has answered, it is worth waiting for the
  // rest (a newer build's offline.json), since the server is plainly there.
  if (navigate || !latest) {
    const pending = { deadline: Date.now() + BUDGET, answered: false };
    pending.result = check(pending).catch(() => "offline");
    latest = pending;
    event.waitUntil(pending.result.then((fresh) => fresh === "newer" && sync()).catch(() => {}));
  }
  const pending = latest;
  const late = new Promise((r) => setTimeout(r, Math.max(0, pending.deadline - Date.now())));
  const fresh = await Promise.race([pending.result, late.then(() => (pending.answered ? pending.result : "slow"))]);
  const path = pathOf(request.url);
  const st = await load();

  if (fresh === "current" || fresh === "newer") {
    const hit = st.held[path] && st.held[path] === st.want[path] && (await saved(request));
    // The previous search index is fine until the sync gets to the new one.
    const stale = !hit && path === SEARCH && (await saved(request));
    const res = hit || stale;
    if (res) {
      if (navigate) served.set(event.resultingClientId, st.held[path]);
      return res;
    }
    const net = await networkFirst(request, fresh === "newer" ? "" : null);
    if (navigate && net.ok) served.set(event.resultingClientId, st.want[path]);
    if (net.ok && !net.redirected && net.type === "basic" && path in st.want) {
      event.waitUntil(keep(path, net.clone()));
    }
    return net;
  }

  // Offline, a lapsed login, or a slow answer: the saved copy, saying so.
  const mark = fresh === "login" ? "login" : "";
  const hit = await saved(request, mark);
  if (hit) {
    if (navigate) served.set(event.resultingClientId, st.held[path]);
    return hit;
  }
  return networkFirst(request, mark);
}

// "current" if the saved copy is the server's build, "newer" if the server has
// a newer one (whose offline.json is then in state.want), "login" if the
// server redirected to the login, "offline" if it could not be reached.
// `pending.answered` is set once version.txt has come back.
async function check(pending = {}) {
  let res;
  try {
    res = await fetch(at("version.txt"), { cache: "no-store", redirect: "manual" });
  } catch {
    return "offline";
  }
  pending.answered = true;
  if (res.type === "opaqueredirect") return "login";
  if (!res.ok) return "offline";
  const version = (await res.text()).trim();
  const st = await load();
  if (version === st.synced) {
    st.want = st.held;
    st.wanted = version;
    return "current";
  }
  if (version !== st.wanted) {
    const listing = await fetch(at("offline.json"), { cache: "no-store", redirect: "error" });
    if (!listing.ok) return "offline";
    const body = await listing.arrayBuffer();
    st.want = JSON.parse(new TextDecoder().decode(body)).files;
    st.wanted = await hash(body); // as offline.py writes version.txt
    await store();
  }
  return "newer";
}

// Saves every file whose hash differs from the newest build's, then forgets
// files the build no longer has. A failure stops it with what was saved so
// far kept. If a newer build turns up meanwhile, it starts over on that one.
function sync() {
  if (!syncing) {
    syncing = (async () => {
      let target;
      do target = await syncTo((await load()).wanted);
      while (target !== (await load()).wanted);
    })().finally(() => (syncing = null));
  }
  return syncing;
}

async function syncTo(target) {
  const st = await load();
  const order = (p) => (p === SEARCH ? 2 : p === "" || p.endsWith("/") ? 0 : 1);
  const paths = Object.keys(st.want)
    .filter((p) => st.held[p] !== st.want[p])
    .sort((a, b) => order(a) - order(b));
  const cache = await caches.open(CONTENT);
  let next = 0;
  let unsaved = 0;
  async function worker() {
    while (next < paths.length && st.wanted === target) {
      const path = paths[next++];
      // A redirect means the login has lapsed; stop rather than save the login page.
      const res = await fetch(at(path), { cache: "no-cache", redirect: "error" });
      if (!res.ok) throw new Error(`${path}: ${res.status}`);
      await put(cache, path, res);
      if (++unsaved % 10 === 0) await store();
    }
  }
  try {
    await Promise.all(Array.from({ length: PARALLEL }, worker));
  } finally {
    await store();
  }
  if (st.wanted !== target) return target;
  for (const path of Object.keys(st.held)) {
    if (!(path in st.want)) {
      await cache.delete(at(path));
      delete st.held[path];
    }
  }
  if (Object.keys(st.want).every((p) => st.held[p] === st.want[p])) st.synced = target;
  await store();
  await tell();
  return target;
}

// Saves a file the page fetched itself while the sync had not got to it.
async function keep(path, res) {
  await put(await caches.open(CONTENT), path, res);
  await store();
}

// Records the hash of what was actually saved, not what offline.json said:
// if the server has moved on to a newer build, the next sync fetches it again.
async function put(cache, path, res) {
  const body = await res.arrayBuffer();
  await cache.put(at(path), new Response(body, { status: res.status, headers: res.headers }));
  (await load()).held[path] = await hash(body);
}

// Tells each open page whose saved copy has changed since it was shown.
async function tell() {
  const st = await load();
  for (const client of await self.clients.matchAll({ type: "window" })) {
    const shown = served.get(client.id);
    if (shown && shown !== st.held[pathOf(client.url)]) client.postMessage({ brain: "updated" });
  }
}

async function load() {
  if (!state) {
    const res = await (await caches.open(STATE)).match(at("state.json"));
    state = res ? await res.json() : { held: {}, want: {}, wanted: null, synced: null };
  }
  return state;
}

async function store() {
  const st = await load();
  // `want` is only worth keeping when it differs from `held`; `check` restores it.
  const keep = { ...st, want: st.wanted === st.synced ? {} : st.want };
  await (await caches.open(STATE)).put(at("state.json"), new Response(JSON.stringify(keep)));
}

// The first time this worker runs where the previous one did: take over the
// copy that one saved (a cache named for its build, holding that build's
// offline.json), so the site is not downloaded again.
async function adopt() {
  if ((await caches.keys()).includes(STATE)) return;
  const st = await load();
  const content = await caches.open(CONTENT);
  for (const name of await caches.keys()) {
    if (!/^brain-[0-9a-f]{16}$/.test(name)) continue;
    const old = await caches.open(name);
    const listing = await old.match(at("offline.json"));
    if (!listing) continue; // an install that never finished
    const body = await listing.arrayBuffer();
    const files = JSON.parse(new TextDecoder().decode(body)).files;
    for (const path of Object.keys(files)) {
      const res = await old.match(at(path));
      if (!res) continue;
      await content.put(at(path), res);
      st.held[path] = files[path];
    }
    st.want = files;
    st.wanted = st.synced = await hash(body);
    break;
  }
  await store();
}

// The server's answer, or after TIMEOUT the saved copy; `mark` says why a
// saved copy is being shown ("" for offline, "login"). null: never the saved copy.
async function networkFirst(request, mark) {
  const network = fetch(request);
  let timer;
  const late = new Promise((resolve) => (timer = setTimeout(resolve, TIMEOUT, null)));
  // Whatever the server says, a login redirect or a 404 included, is the answer.
  const res = await Promise.race([network.catch(() => null), late]);
  clearTimeout(timer);
  if (res || mark === null) return res || network;
  return (await saved(request, mark)) || network;
}

// The saved copy of a request, or null. `mark`, when given, tags a page as a
// saved copy rather than the server's current one.
async function saved(request, mark) {
  const url = new URL(request.url);
  url.search = url.hash = "";
  const cache = await caches.open(CONTENT);
  const hit = await cache.match(url.href);
  const navigate = request.mode === "navigate";
  if (hit) return navigate && mark !== undefined ? marked(hit, mark) : hit;
  if (!url.pathname.endsWith("/") && (await cache.match(url.href + "/"))) {
    return Response.redirect(url.href + "/", 301); // as nginx would
  }
  if (navigate && mark !== undefined) {
    const missing = await cache.match(at("404.html"));
    if (missing) return marked(missing, mark, 404);
  }
  return null;
}

// Tags a saved page so extra.css can say it is a saved copy, and why.
async function marked(res, mark, status = res.status) {
  const attr = mark ? `data-brain-saved="${mark}"` : "data-brain-saved";
  const html = (await res.text()).replace("<body", `<body ${attr}`);
  return new Response(html, { status, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

// The path offline.json lists a URL by: relative to the root, no query.
function pathOf(href) {
  const url = new URL(href);
  return decodeURIComponent(url.pathname).slice(decodeURIComponent(root.pathname).length);
}

// As offline.py hashes a file: the first 16 hex digits of its sha256.
async function hash(body) {
  const digest = new Uint8Array(await crypto.subtle.digest("SHA-256", body));
  return Array.from(digest.slice(0, 8), (b) => b.toString(16).padStart(2, "0")).join("");
}
