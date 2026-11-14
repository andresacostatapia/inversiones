/**
 * Bóveda cifrada.
 *
 * - Todos los datos de la app son UN documento JSON cifrado con AES-GCM 256 usando una
 *   clave de datos aleatoria (DEK).
 * - La DEK se guarda "envuelta" (cifrada) con una clave derivada del PIN (PBKDF2-SHA256).
 * - Opcionalmente se guarda una segunda copia envuelta con un secreto que solo entrega el
 *   sensor biométrico del dispositivo (WebAuthn, extensión PRF).
 * - El PIN nunca se guarda. Sin PIN ni biometría los datos no se pueden leer ni recuperar.
 *
 * El registro vive en IndexedDB. El respaldo es ese mismo registro como archivo.
 */

const ITER = 600_000
const enc = new TextEncoder()
const dec = new TextDecoder()
const rand = (n: number) => crypto.getRandomValues(new Uint8Array(n))

interface Sealed {
  iv: string
  ct: string
}
export interface VaultRecord {
  v: 1
  salt: string
  iter: number
  pin: Sealed // DEK envuelta con la clave del PIN
  bio?: Sealed & { credId: string; prfSalt: string } // DEK envuelta con el secreto biométrico
  data: Sealed
}
export interface Session {
  rec: VaultRecord
  raw: Uint8Array<ArrayBuffer> // DEK en memoria mientras la app está desbloqueada
  key: CryptoKey
  queue: Promise<void> // serializa los guardados
}

// ---------- utilidades ----------

function toB64(buf: ArrayBuffer | Uint8Array): string {
  const u = buf instanceof Uint8Array ? buf : new Uint8Array(buf)
  let s = ''
  for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000))
  return btoa(s)
}
function fromB64(s: string) {
  const bin = atob(s)
  const u = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i)
  return u
}

const aesKey = (raw: BufferSource) => crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt'])

async function seal(key: CryptoKey, plain: BufferSource): Promise<Sealed> {
  const iv = rand(12)
  return { iv: toB64(iv), ct: toB64(await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plain)) }
}
async function unseal(key: CryptoKey, s: Sealed) {
  return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: fromB64(s.iv) }, key, fromB64(s.ct)))
}
async function pinKey(pin: string, salt: BufferSource, iterations: number) {
  const base = await crypto.subtle.importKey('raw', enc.encode(pin), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey({ name: 'PBKDF2', hash: 'SHA-256', salt, iterations }, base, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'])
}

// ---------- IndexedDB (un solo registro) ----------

function db(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const r = indexedDB.open('inversiones', 1)
    r.onupgradeneeded = () => r.result.createObjectStore('kv')
    r.onsuccess = () => resolve(r.result)
    r.onerror = () => reject(r.error)
  })
}
async function kv<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const d = await db()
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = d.transaction('kv', mode)
      const r = run(tx.objectStore('kv'))
      tx.oncomplete = () => resolve(r.result)
      tx.onerror = tx.onabort = () => reject(tx.error ?? r.error)
    })
  } finally {
    d.close()
  }
}
export const loadRecord = () => kv<VaultRecord | undefined>('readonly', (s) => s.get('vault'))
export const storeRecord = async (rec: VaultRecord) => void (await kv('readwrite', (s) => s.put(rec, 'vault')))
export const deleteRecord = async () => void (await kv('readwrite', (s) => s.delete('vault')))

// ---------- crear, abrir, guardar ----------

export const cryptoAvailable = () => typeof crypto !== 'undefined' && !!crypto.subtle && typeof indexedDB !== 'undefined'

export async function createVault(pin: string, data: unknown): Promise<Session> {
  const raw = rand(32)
  const salt = rand(16)
  const rec: VaultRecord = { v: 1, salt: toB64(salt), iter: ITER, pin: await seal(await pinKey(pin, salt, ITER), raw), data: { iv: '', ct: '' } }
  const session: Session = { rec, raw, key: await aesKey(raw), queue: Promise.resolve() }
  await saveData(session, data)
  // Pide al navegador que no borre estos datos por falta de espacio o desuso.
  navigator.storage?.persist?.().catch(() => {})
  return session
}

export function saveData(s: Session, data: unknown): Promise<void> {
  s.queue = s.queue
    .catch(() => {})
    .then(async () => {
      s.rec.data = await seal(s.key, enc.encode(JSON.stringify(data)))
      await storeRecord(s.rec)
    })
  return s.queue
}

async function openSession(rec: VaultRecord, raw: Uint8Array<ArrayBuffer>) {
  const key = await aesKey(raw)
  const data: unknown = JSON.parse(dec.decode(await unseal(key, rec.data)))
  const session: Session = { rec, raw, key, queue: Promise.resolve() }
  return { session, data }
}

async function rawFromPin(rec: VaultRecord, pin: string) {
  try {
    return await unseal(await pinKey(pin, fromB64(rec.salt), rec.iter), rec.pin)
  } catch {
    throw new Error('PIN incorrecto.')
  }
}

export const unlockWithPin = async (rec: VaultRecord, pin: string) => openSession(rec, await rawFromPin(rec, pin))

export async function changePin(s: Session, oldPin: string, newPin: string) {
  await rawFromPin(s.rec, oldPin)
  const salt = rand(16)
  const pin = await seal(await pinKey(newPin, salt, ITER), s.raw)
  await s.queue.catch(() => {})
  Object.assign(s.rec, { salt: toB64(salt), iter: ITER, pin })
  await storeRecord(s.rec)
}

// ---------- biometría (WebAuthn + PRF) ----------

export const bioSupported = () => typeof PublicKeyCredential !== 'undefined' && !!navigator.credentials

async function prfSecret(credId: BufferSource, salt: BufferSource): Promise<BufferSource> {
  const got = (await navigator.credentials.get({
    publicKey: {
      challenge: rand(32),
      allowCredentials: [{ type: 'public-key', id: credId }],
      userVerification: 'required',
      extensions: { prf: { eval: { first: salt } } },
    },
  })) as PublicKeyCredential | null
  const first = got?.getClientExtensionResults().prf?.results?.first
  if (!first) throw new Error('Este dispositivo no permite desbloquear datos cifrados con biometría.')
  return first
}

export async function enableBio(s: Session) {
  const cred = (await navigator.credentials.create({
    publicKey: {
      challenge: rand(32),
      rp: { name: 'Inversiones' },
      user: { id: rand(16), name: 'inversiones', displayName: 'Inversiones' },
      pubKeyCredParams: [
        { type: 'public-key', alg: -7 },
        { type: 'public-key', alg: -257 },
      ],
      authenticatorSelection: { authenticatorAttachment: 'platform', userVerification: 'required', residentKey: 'preferred' },
      extensions: { prf: {} },
    },
  })) as PublicKeyCredential | null
  if (!cred?.getClientExtensionResults().prf?.enabled)
    throw new Error('Este dispositivo no permite desbloquear datos cifrados con biometría. Sigue usando tu PIN.')
  const prfSalt = rand(32)
  const sealed = await seal(await aesKey(await prfSecret(cred.rawId, prfSalt)), s.raw)
  await s.queue.catch(() => {})
  s.rec.bio = { ...sealed, credId: toB64(cred.rawId), prfSalt: toB64(prfSalt) }
  await storeRecord(s.rec)
}

export async function disableBio(s: Session) {
  await s.queue.catch(() => {})
  delete s.rec.bio
  await storeRecord(s.rec)
}

export async function unlockWithBio(rec: VaultRecord) {
  if (!rec.bio) throw new Error('La biometría no está activada.')
  const key = await aesKey(await prfSecret(fromB64(rec.bio.credId), fromB64(rec.bio.prfSalt)))
  return openSession(rec, await unseal(key, rec.bio))
}

// ---------- respaldo ----------

/** El respaldo es el registro cifrado tal cual (sin la parte biométrica, que no sirve en otro dispositivo). */
export function exportBackup(rec: VaultRecord): string {
  const { bio: _bio, ...rest } = rec
  return JSON.stringify({ app: 'inversiones', ...rest })
}

export function parseBackup(text: string): VaultRecord {
  const bad = new Error('El archivo no es un respaldo válido de esta app.')
  let o: Partial<VaultRecord> & { app?: string }
  try {
    o = JSON.parse(text)
  } catch {
    throw bad
  }
  const sealed = (s?: Sealed) => typeof s?.iv === 'string' && typeof s?.ct === 'string'
  if (o?.app !== 'inversiones' || o.v !== 1 || typeof o.salt !== 'string' || typeof o.iter !== 'number' || !sealed(o.pin) || !sealed(o.data)) throw bad
  return { v: 1, salt: o.salt, iter: o.iter, pin: o.pin!, data: o.data! }
}
