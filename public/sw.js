// Service worker: deja la app disponible sin conexión.
// Los DATOS no pasan por aquí (viven cifrados en IndexedDB); esto solo guarda el código de la app.
const CACHE = 'inversiones-app-v2'
// Carpeta donde vive la app, p. ej. https://usuario.github.io/inversiones/
const SCOPE = self.registration.scope

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      const res = await fetch(SCOPE, { cache: 'no-store' })
      const html = await res.clone().text()
      await cache.put(SCOPE, res)
      // Guarda también los archivos que la página necesita (JS, CSS, ícono, manifiesto).
      const urls = [...html.matchAll(/(?:src|href)="([^"#]+)"/g)]
        .map((m) => new URL(m[1], SCOPE).href)
        .filter((u) => u.startsWith(SCOPE))
      await cache.addAll(urls)
      await self.skipWaiting()
    })(),
  )
})

self.addEventListener('activate', (event) =>
  event.waitUntil(
    (async () => {
      // Borra las copias de versiones anteriores del service worker.
      for (const name of await caches.keys()) if (name.startsWith('inversiones-app-') && name !== CACHE) await caches.delete(name)
      await self.clients.claim()
    })(),
  ),
)

self.addEventListener('fetch', (event) => {
  const req = event.request
  if (req.method !== 'GET' || !req.url.startsWith(SCOPE)) return

  // La página: primero la red (para recibir versiones nuevas); sin conexión, la copia guardada.
  if (req.mode === 'navigate') {
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) {
            const copy = res.clone()
            caches.open(CACHE).then((c) => c.put(SCOPE, copy))
          }
          return res
        })
        .catch(() => caches.match(SCOPE, { ignoreVary: true })),
    )
    return
  }

  // El resto (archivos con nombre único por versión): primero la copia guardada.
  event.respondWith(
    caches.match(req, { ignoreVary: true }).then(
      (hit) =>
        hit ||
        fetch(req).then((res) => {
          if (res.ok) {
            const copy = res.clone()
            caches.open(CACHE).then((c) => c.put(req, copy))
          }
          return res
        }),
    ),
  )
})
