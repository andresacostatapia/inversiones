// Service worker: deja la app disponible sin conexión y muestra las notificaciones push.
// Los DATOS no pasan por aquí (viven cifrados en IndexedDB); esto solo guarda el código de la app.
const CACHE = 'inversiones-app-v3'
// Carpeta donde vive la app, p. ej. https://usuario.github.io/inversiones/
const SCOPE = self.registration.scope

self.addEventListener('install', (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CACHE)
      const res = await fetch(SCOPE, { cache: 'no-store' })
      const html = await res.clone().text()
      await cache.put(SCOPE, res)
      // Guarda también los archivos que la página necesita (JS, CSS, íconos, manifiesto).
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

// ---------- notificaciones push ----------

// Llega un aviso del servidor de alertas: { title, body, tag, url }. Siempre se muestra algo
// (los navegadores cancelan la suscripción si un push no produce una notificación visible).
self.addEventListener('push', (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    // contenido ilegible: se muestra el aviso genérico
  }
  event.waitUntil(
    self.registration.showNotification(data.title || 'Inversiones', {
      body: data.body || 'Se cumplió una alerta. Abre la app para ver el detalle.',
      icon: new URL('icon-192.png', SCOPE).href,
      badge: new URL('icon-192.png', SCOPE).href,
      tag: data.tag,
      data: { url: typeof data.url === 'string' && data.url.startsWith('#') ? data.url : '#/alertas' },
    }),
  )
})

// Al tocar el aviso: trae la app al frente si ya está abierta; si no, la abre en Alertas.
self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  event.waitUntil(
    (async () => {
      const open = (await self.clients.matchAll({ type: 'window', includeUncontrolled: true })).find((c) => c.url.startsWith(SCOPE))
      if (open) return open.focus()
      return self.clients.openWindow(SCOPE + event.notification.data.url)
    })(),
  )
})
