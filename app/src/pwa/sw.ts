/// <reference lib="webworker" />
/**
 * Inspira service worker — hand-rolled, no Workbox runtime.
 *
 * Caching strategy:
 *  - App shell bundles   (/assets/*)            StaleWhileRevalidate
 *  - Static assets       (fonts / icons / imgs) CacheFirst (30-day max-age)
 *  - HTML navigations    (/ and deep links)     NetworkFirst w/ 3s timeout
 *                                                -> fall back to cached shell
 *                                                -> fall back to /offline.html
 *  - API calls           (/api/*)               NetworkOnly (NEVER cache auth / LLM)
 *
 * Built via vite-plugin-pwa in `injectManifest` mode. The plugin injects the
 * precache manifest via `self.__WB_MANIFEST`, which we use only to know *what*
 * shell URLs exist at build time — we manage caches ourselves.
 */

declare const self: ServiceWorkerGlobalScope & {
  __WB_MANIFEST: Array<string | { url: string; revision: string | null }>;
};

export {};

// ----- Version + cache names ------------------------------------------------

const BUILD_VERSION = "inspira-v0.1.0";

const CACHE_SHELL = `${BUILD_VERSION}-shell`;
const CACHE_ASSETS = `${BUILD_VERSION}-assets`;
const CACHE_STATIC = `${BUILD_VERSION}-static`;
const CACHE_HTML = `${BUILD_VERSION}-html`;

const ALL_CACHES = [CACHE_SHELL, CACHE_ASSETS, CACHE_STATIC, CACHE_HTML];
const CACHE_PREFIX = "inspira-";

const STATIC_MAX_AGE_MS = 30 * 24 * 60 * 60 * 1000; // 30 days
const NAV_TIMEOUT_MS = 3000;

const CORE_SHELL_URLS = [
  "/",
  "/offline.html",
  "/manifest.webmanifest",
  "/icon-192.svg",
  "/icon-512.svg",
];

const IS_DEV = self.location.hostname === "localhost";

// ----- Install: pre-cache the core shell -----------------------------------

self.addEventListener("install", (event) => {
  if (IS_DEV) console.log(`[sw] install ${BUILD_VERSION}`);
  event.waitUntil(
    (async () => {
      const shell = await caches.open(CACHE_SHELL);
      // Ignore failures for any single URL so install doesn't block on a miss.
      await Promise.all(
        CORE_SHELL_URLS.map(async (url) => {
          try {
            await shell.add(new Request(url, { cache: "reload" }));
          } catch {
            /* swallow — non-fatal */
          }
        }),
      );

      // Also precache the hashed build assets the plugin knows about.
      const manifest = self.__WB_MANIFEST ?? [];
      const assets = await caches.open(CACHE_ASSETS);
      await Promise.all(
        manifest.map(async (entry) => {
          const url = typeof entry === "string" ? entry : entry.url;
          try {
            await assets.add(new Request(url, { cache: "reload" }));
          } catch {
            /* swallow */
          }
        }),
      );
    })(),
  );
});

// ----- Activate: claim clients, clean old caches --------------------------

self.addEventListener("activate", (event) => {
  if (IS_DEV) console.log(`[sw] activate ${BUILD_VERSION}`);
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith(CACHE_PREFIX) && !ALL_CACHES.includes(name))
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

// ----- Opt-in skip-waiting trigger ----------------------------------------

self.addEventListener("message", (event) => {
  const data = event.data as { type?: string } | undefined;
  if (data?.type === "SKIP_WAITING") {
    void self.skipWaiting();
  }
});

// ----- Fetch routing -------------------------------------------------------

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Same-origin only for caching. Cross-origin requests hit the network.
  if (url.origin !== self.location.origin) return;

  // API: never cache.
  if (url.pathname.startsWith("/api/")) {
    return; // default network behavior
  }

  // HTML navigations.
  if (req.mode === "navigate" || req.destination === "document") {
    event.respondWith(handleNavigation(req));
    return;
  }

  // Hashed build assets under /assets/* — SWR.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(staleWhileRevalidate(req, CACHE_SHELL));
    return;
  }

  // Static assets (fonts, icons, images) — CacheFirst with max-age.
  if (isStaticAsset(url, req)) {
    event.respondWith(cacheFirstWithExpiry(req, CACHE_STATIC, STATIC_MAX_AGE_MS));
    return;
  }
});

// ----- Strategies ----------------------------------------------------------

async function handleNavigation(req: Request): Promise<Response> {
  const htmlCache = await caches.open(CACHE_HTML);
  const shellCache = await caches.open(CACHE_SHELL);

  try {
    const network = await fetchWithTimeout(req, NAV_TIMEOUT_MS);
    if (network && network.ok) {
      // Cache successful navigations (keyed by /) so we have a warm shell.
      try {
        await htmlCache.put("/", network.clone());
      } catch {
        /* swallow quota errors */
      }
      return network;
    }
  } catch {
    /* fall through to cache */
  }

  const cached = (await htmlCache.match("/")) ?? (await shellCache.match("/"));
  if (cached) return cached;

  const offline = await shellCache.match("/offline.html");
  if (offline) return offline;

  return new Response("Offline", {
    status: 503,
    statusText: "Offline",
    headers: { "Content-Type": "text/plain; charset=utf-8" },
  });
}

async function staleWhileRevalidate(req: Request, cacheName: string): Promise<Response> {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const networkPromise = fetch(req)
    .then((res) => {
      if (res && res.ok) {
        void cache.put(req, res.clone()).catch(() => undefined);
      }
      return res;
    })
    .catch(() => undefined);

  if (cached) return cached;
  const network = await networkPromise;
  if (network) return network;
  return new Response("", { status: 504, statusText: "Gateway Timeout" });
}

async function cacheFirstWithExpiry(
  req: Request,
  cacheName: string,
  maxAgeMs: number,
): Promise<Response> {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);

  if (cached && !isExpired(cached, maxAgeMs)) {
    return cached;
  }

  try {
    const network = await fetch(req);
    if (network && network.ok) {
      const stamped = await stampResponse(network.clone());
      void cache.put(req, stamped).catch(() => undefined);
    }
    return network;
  } catch {
    if (cached) return cached; // stale > nothing
    return new Response("", { status: 504, statusText: "Gateway Timeout" });
  }
}

// ----- Helpers -------------------------------------------------------------

function isStaticAsset(url: URL, req: Request): boolean {
  const dest = req.destination;
  if (dest === "font" || dest === "image") return true;
  return /\.(?:woff2?|ttf|otf|eot|png|jpg|jpeg|gif|webp|avif|svg|ico)$/i.test(url.pathname);
}

function fetchWithTimeout(req: Request, ms: number): Promise<Response> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      reject(new Error("timeout"));
    }, ms);
    fetch(req, { signal: controller.signal })
      .then((res) => {
        clearTimeout(timer);
        resolve(res);
      })
      .catch((err) => {
        clearTimeout(timer);
        reject(err);
      });
  });
}

const CACHED_AT_HEADER = "x-inspira-cached-at";

async function stampResponse(res: Response): Promise<Response> {
  const body = await res.blob();
  const headers = new Headers(res.headers);
  headers.set(CACHED_AT_HEADER, String(Date.now()));
  return new Response(body, {
    status: res.status,
    statusText: res.statusText,
    headers,
  });
}

function isExpired(res: Response, maxAgeMs: number): boolean {
  const stamped = res.headers.get(CACHED_AT_HEADER);
  if (!stamped) return false; // pre-stamp entries are considered fresh enough
  const cachedAt = Number(stamped);
  if (!Number.isFinite(cachedAt)) return false;
  return Date.now() - cachedAt > maxAgeMs;
}
