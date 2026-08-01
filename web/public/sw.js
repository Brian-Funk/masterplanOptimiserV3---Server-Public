/**
 * Service Worker for Masterplan Optimiser
 * Handles static application-shell caching and push notifications.
 */

const CACHE_NAME = "mp-opt-app-__MP_OPT_RELEASE__";
const NAVIGATION_TIMEOUT_MS = 8_000;

// App shell files to pre-cache on install
const APP_SHELL = [
  "/index.html",
  "/login.html",
  "/calendar.html",
  "/shared-schedule.html",
  "/admin.html",
  "/bootstrap.html",
  "/activate.html",
  "/activate/qr.html",
  "/about.html",
  "/privacy.html",
  "/legal.html",
  "/data-policy.html",
  "/retention.html",
  "/rights.html",
  "/processors.html",
  "/licence.html",
  "/terms.html",
  "/disclaimer.html",
  "/manifest.json",
];

// ---------------------------------------------------------------------------
// Install  -  pre-cache app shell
// ---------------------------------------------------------------------------
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

// ---------------------------------------------------------------------------
// Activate  -  clean old caches
// ---------------------------------------------------------------------------
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys
            .filter((k) => k.startsWith("mp-opt-app-") || k === "mp-opt-v6")
            .filter((k) => k !== CACHE_NAME)
            .map((k) => caches.delete(k))
        )
      )
      .then(() => self.clients.claim())
  );
});

// ---------------------------------------------------------------------------
// Fetch  -  network-first for API, cache-first for static assets
// ---------------------------------------------------------------------------
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Static assets (_next/*)  -  cache-first
  if (url.pathname.startsWith("/_next/")) {
    event.respondWith(cacheFirst(event.request));
    return;
  }

  // Navigation requests  -  network-first with app shell fallback
  if (event.request.mode === "navigate") {
    event.respondWith(networkFirstNavigation(event.request));
    return;
  }
});

function navigationShellForPath(pathname) {
  const path = pathname.endsWith("/") && pathname !== "/"
    ? pathname.slice(0, -1)
    : pathname;
  const shellByPath = {
    "/": "/index.html",
    "/login": "/login.html",
    "/calendar": "/calendar.html",
    "/shared-schedule": "/shared-schedule.html",
    "/admin": "/admin.html",
    "/bootstrap": "/bootstrap.html",
    "/activate": "/activate.html",
    "/activate/qr": "/activate/qr.html",
    "/about": "/about.html",
    "/privacy": "/privacy.html",
    "/legal": "/legal.html",
    "/data-policy": "/data-policy.html",
    "/retention": "/retention.html",
    "/rights": "/rights.html",
    "/processors": "/processors.html",
    "/licence": "/licence.html",
    "/terms": "/terms.html",
    "/disclaimer": "/disclaimer.html",
  };
  return shellByPath[path] || "/index.html";
}

async function networkFirstNavigation(request) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), NAVIGATION_TIMEOUT_MS);
  try {
    const response = await fetch(request, { signal: controller.signal });
    if (response.ok) return response;
    if (response.status >= 500) throw new Error(`upstream-${response.status}`);
    return response;
  } catch {
    const url = new URL(request.url);
    const shell = navigationShellForPath(url.pathname);
    return (
      (await caches.match(shell)) ||
      (await caches.match("/index.html")) ||
      new Response("Offline", { status: 503 })
    );
  } finally {
    clearTimeout(timeout);
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch {
    return new Response("Offline", { status: 503 });
  }
}

self.addEventListener("message", (event) => {
  if (event.data?.type !== "CLEAR_PRIVATE_CACHES") return;
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith("mp-opt-offline-"))
          .map((key) => caches.delete(key))
      )
    )
  );
});

// ---------------------------------------------------------------------------
// Push  -  show notification
// ---------------------------------------------------------------------------
self.addEventListener("push", (event) => {
  if (!event.data) return;

  let data;
  try {
    data = event.data.json();
  } catch {
    data = { title: "Masterplan Optimiser", body: event.data.text() };
  }

  const title = data.title || "Masterplan Optimiser";
  const options = {
    body: data.body || "",
    icon: "/icon-maskable-192.png",
    badge: "/badge-96.png",
    data: { url: data.url || "/" },
    tag: data.tag || "mp-opt-notification",
    renotify: true,
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// ---------------------------------------------------------------------------
// Notification click  -  open/focus the app
// ---------------------------------------------------------------------------
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const url = event.notification.data?.url || "/";

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((clients) => {
        // Focus existing window if open
        for (const client of clients) {
          if (new URL(client.url).pathname === url && "focus" in client) {
            return client.focus();
          }
        }
        // Otherwise open new window
        return self.clients.openWindow(url);
      })
  );
});
