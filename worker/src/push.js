/**
 * Envío de notificaciones push (Web Push) sin librerías.
 *
 * - El contenido viaja cifrado de extremo a extremo (RFC 8291, "aes128gcm"): Google o Apple
 *   transportan el aviso pero no pueden leerlo; solo el celular suscrito lo descifra.
 * - El servidor se identifica ante el servicio de push con una firma VAPID (RFC 8292).
 */

const enc = new TextEncoder()

export const b64url = (buf) => {
  const u = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  let s = ''
  for (const b of u) s += String.fromCharCode(b)
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}
export const fromB64url = (s) => {
  const bin = atob(s.replace(/-/g, '+').replace(/_/g, '/'))
  const u = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i)
  return u
}
const concat = (...parts) => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0))
  let at = 0
  for (const p of parts) (out.set(p, at), (at += p.length))
  return out
}
async function hkdf(salt, ikm, info, bytes) {
  const key = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveBits'])
  return new Uint8Array(await crypto.subtle.deriveBits({ name: 'HKDF', hash: 'SHA-256', salt, info }, key, bytes * 8))
}

/**
 * Cifra `plaintext` para una suscripción (claves p256dh y auth en base64url).
 * Devuelve el cuerpo completo del mensaje: cabecera (sal, tamaño de registro, clave pública
 * efímera) seguida del texto cifrado.
 */
export async function encryptPayload(p256dh, auth, plaintext) {
  const uaPublic = fromB64url(p256dh)
  const authSecret = fromB64url(auth)
  const uaKey = await crypto.subtle.importKey('raw', uaPublic, { name: 'ECDH', namedCurve: 'P-256' }, false, [])
  const pair = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits'])
  const asPublic = new Uint8Array(await crypto.subtle.exportKey('raw', pair.publicKey))
  const shared = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, pair.privateKey, 256))

  const ikm = await hkdf(authSecret, shared, concat(enc.encode('WebPush: info\0'), uaPublic, asPublic), 32)
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const cek = await hkdf(salt, ikm, enc.encode('Content-Encoding: aes128gcm\0'), 16)
  const nonce = await hkdf(salt, ikm, enc.encode('Content-Encoding: nonce\0'), 12)

  const key = await crypto.subtle.importKey('raw', cek, 'AES-GCM', false, ['encrypt'])
  // 0x02 marca el último (y único) registro.
  const cipher = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, key, concat(plaintext, new Uint8Array([2]))))
  const header = concat(salt, new Uint8Array([0, 0, 16, 0]), new Uint8Array([asPublic.length]), asPublic) // tamaño de registro = 4096
  return concat(header, cipher)
}

/** Token VAPID: un JWT firmado con ES256, válido 12 horas, para el servicio de push indicado. */
export async function vapidToken(env, audience) {
  const jwk = JSON.parse(env.VAPID_PRIVATE_JWK)
  const key = await crypto.subtle.importKey('jwk', jwk, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign'])
  const part = (o) => b64url(enc.encode(JSON.stringify(o)))
  const unsigned = `${part({ typ: 'JWT', alg: 'ES256' })}.${part({ aud: audience, exp: Math.floor(Date.now() / 1000) + 12 * 3600, sub: env.VAPID_SUBJECT })}`
  const sig = await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, key, enc.encode(unsigned))
  return `${unsigned}.${b64url(sig)}`
}

// Solo se envía a servicios de push conocidos: evita que el servidor sea usado para llamar a otras direcciones.
const PUSH_HOSTS = ['.googleapis.com', '.push.apple.com', '.mozilla.com', '.notify.windows.com']
export function validSubscription(sub) {
  try {
    const u = new URL(sub.endpoint)
    return u.protocol === 'https:' && PUSH_HOSTS.some((h) => u.hostname.endsWith(h)) && typeof sub.keys?.p256dh === 'string' && typeof sub.keys?.auth === 'string'
  } catch {
    return false
  }
}

/** Envía una notificación. Devuelve el código de respuesta del servicio de push (201 = aceptada; 404 o 410 = suscripción caducada). */
export async function sendPush(env, sub, message) {
  const body = await encryptPayload(sub.keys.p256dh, sub.keys.auth, enc.encode(JSON.stringify(message)))
  const res = await fetch(sub.endpoint, {
    method: 'POST',
    headers: {
      Authorization: `vapid t=${await vapidToken(env, new URL(sub.endpoint).origin)}, k=${env.VAPID_PUBLIC}`,
      'Content-Encoding': 'aes128gcm',
      'Content-Type': 'application/octet-stream',
      TTL: '3600',
      Urgency: 'high',
    },
    body,
  })
  return res.status
}
