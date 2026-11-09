/* PsychoPal service worker: offline reading.
   Network first (so you always get the latest edition when online), falling back to the copy saved on the phone.
   On install it saves the app itself plus the three most recent weeks, so the feed opens on the Underground. */
const CACHE = "psychopal-v1";
const SHELL = ["./", "index.html", "app.js", "styles.css", "config.js", "manifest.webmanifest", "icons/icon.svg", "icons/icon-192.png",
               "archive/sync.js"];

self.addEventListener("install", e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    await Promise.all(SHELL.map(u => c.add(new Request(u, {cache: "reload"})).catch(() => {})));
    try {
      const r = await fetch("data/index.json", {cache: "reload"});
      if (r.ok) {
        await c.put("data/index.json", r.clone());
        const idx = await r.json();
        await Promise.all((idx.weeks || []).slice(0, 3).map(w => c.add(`data/weeks/${w.week}.json`).catch(() => {})));
      }
    } catch (x) {}
    self.skipWaiting();
  })());
});

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", e => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;   // GitHub sync, OpenAlex etc. go straight to the network
  e.respondWith((async () => {
    const c = await caches.open(CACHE);
    try {
      const r = await fetch(req);
      if (r.ok && r.type === "basic") c.put(req, r.clone()).catch(() => {});
      return r;
    } catch (x) {
      const hit = await c.match(req, {ignoreSearch: true}) || (req.mode === "navigate" ? await c.match("index.html") : null);
      return hit || new Response("Offline, and this page has not been saved on this device yet.", {status: 503, headers: {"Content-Type": "text/plain; charset=utf-8"}});
    }
  })());
});
