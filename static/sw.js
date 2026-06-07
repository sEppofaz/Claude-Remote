const CACHE = "claude-remote-v3";
const SHELL = ["./", "./index.html", "./manifest.json"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", e => {
  if (e.request.method !== "GET") return;
  if (e.request.url.includes("/api/")) return;

  const url = new URL(e.request.url);
  const isHtml = url.pathname.endsWith("/") || url.pathname.endsWith(".html");

  if (isHtml) {
    // Network-first für HTML: immer aktuelle Version, Cache als Fallback
    e.respondWith(
      fetch(e.request)
        .then(r => {
          caches.open(CACHE).then(c => c.put(e.request, r.clone()));
          return r;
        })
        .catch(() => caches.match(e.request))
    );
    return;
  }

  // Cache-first für Assets (Icons, manifest, sw.js)
  e.respondWith(
    caches.match(e.request).then(cached => cached || fetch(e.request))
  );
});
