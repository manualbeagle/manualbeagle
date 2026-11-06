// Manual Beagle's service worker: just enough for a phone to install the page as an app.
// It keeps a copy of the page's own files so the app opens without a connection (and then says it can't reach
// the server). It never stores anything from /api/: your library, lookups and photos stay on the server.
"use strict";

const CACHE = "manualbeagle-shell-v1";
const SHELL = ["/", "/static/style.css", "/static/app.js", "/static/icons/icon-192.png"];

self.addEventListener("install", (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (ev) => {
  ev.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// The network first, always; the stored copy of the page's own files only when the network fails.
self.addEventListener("fetch", (ev) => {
  const url = new URL(ev.request.url);
  if (ev.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;
  const shell = ev.request.mode === "navigate" ? "/" : url.pathname;
  if (!SHELL.includes(shell)) return;
  ev.respondWith(
    fetch(ev.request)
      .then((r) => {
        if (r.ok) {
          const copy = r.clone();
          caches.open(CACHE).then((c) => c.put(shell, copy));
        }
        return r;
      })
      .catch(() => caches.match(shell).then((r) => r || Response.error())),
  );
});
