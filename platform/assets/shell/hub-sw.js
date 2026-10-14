/* hub-sw.js — hub service worker (wave 13, PWA). Scope `/`.
 *
 * - Caches only the hub "shell": CSS/JS/fonts/icons from /assets/ and the /offline page.
 * - Navigation: always over the network; without network, the /offline page (never a cached
 *   app page — authenticated pages do not go into the cache).
 * - /api, /_frag, /mcp, /oauth, /login, /auth, /_dev: never intercepted or cached.
 *   The apps' offline submission queue belongs to hub-device.js (IndexedDB in the page), not here.
 * - Assets: network first (development mode and new versions take effect immediately);
 *   the cache only answers when the network fails.
 */
const VERSION = '__HUB_SW_VERSION__';
const CACHE = `hub-shell-${VERSION}`;
const PRECACHE = [
  '/offline',
  '/assets/tokens.css', '/assets/theme.css', '/assets/styles.css', '/assets/hub.css',
  '/assets/hub-ux.js', '/assets/hub-safe.js', '/assets/hub-kit.js', '/assets/hub-kit-mount.js', '/assets/hub-chart.js', '/assets/hub-device.js', '/assets/hub-device-camera.js', '/assets/hub-device-forms.js', '/assets/hub-device-mount.js',
  '/assets/hub.js', '/assets/hub-nav.js',
  '/assets/logo.svg', '/assets/icon-192.png', '/assets/icon-512.png',
  '/assets/fonts/nunito-sans-latin-wght-normal.woff2', '/assets/fonts/red-hat-display-latin-wght-normal.woff2',
];
const NEVER = ['/api/', '/_frag/', '/_dev/', '/mcp', '/oauth/', '/login', '/logout', '/auth/', '/account/', '/admin/'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => Promise.all(PRECACHE.map((u) => c.add(new Request(u, { credentials: 'same-origin' })).catch(() => null)))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ks) => Promise.all(ks.filter((k) => k.startsWith('hub-shell-') && k !== CACHE).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (NEVER.some((p) => url.pathname === p.replace(/\/$/, '') || url.pathname.startsWith(p))) return;
  if (url.pathname.startsWith('/assets/')) {
    e.respondWith(fetch(req).then((res) => {
      if (res.ok && res.type === 'basic') { const copy = res.clone(); caches.open(CACHE).then((c) => c.put(req, copy)).catch(() => {}); }
      return res;
    }).catch(() => caches.match(req, { ignoreSearch: true }).then((r) => r || Response.error())));
    return;
  }
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/offline').then((r) => r || new Response('offline', { status: 503, headers: { 'content-type': 'text/plain; charset=utf-8' } }))));
  }
});
