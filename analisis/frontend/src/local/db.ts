// Almacenamiento local del teléfono (IndexedDB): historia de precios, precios, señales, operaciones y alertas.
// Es privado de la app: Android lo borra solo si desinstalas la app o borras sus datos.

export type StoreName = 'kv' | 'bars' | 'quotes' | 'signals' | 'transactions' | 'alerts'

export class Db {
  private dbp: Promise<IDBDatabase> | null = null

  constructor(private name = 'analisis-inversiones') {}

  private open(): Promise<IDBDatabase> {
    if (!this.dbp) {
      this.dbp = new Promise((resolve, reject) => {
        const req = indexedDB.open(this.name, 1)
        req.onupgradeneeded = () => {
          const db = req.result
          db.createObjectStore('kv')
          db.createObjectStore('bars')
          db.createObjectStore('quotes', { keyPath: 'symbol' })
          db.createObjectStore('signals', { keyPath: ['symbol', 'date'] })
          db.createObjectStore('transactions', { keyPath: 'id', autoIncrement: true })
          db.createObjectStore('alerts', { keyPath: 'id', autoIncrement: true })
        }
        req.onsuccess = () => resolve(req.result)
        req.onerror = () => reject(req.error)
      })
    }
    return this.dbp
  }

  private async run<T>(store: StoreName, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
    const db = await this.open()
    return new Promise((resolve, reject) => {
      const tx = db.transaction(store, mode)
      const req = fn(tx.objectStore(store))
      tx.oncomplete = () => resolve(req.result)
      tx.onerror = () => reject(tx.error ?? req.error)
      tx.onabort = () => reject(tx.error ?? new Error('transacción cancelada'))
    })
  }

  get<T>(store: StoreName, key: IDBValidKey): Promise<T | undefined> {
    return this.run(store, 'readonly', (s) => s.get(key)) as Promise<T | undefined>
  }

  put<T>(store: StoreName, value: T, key?: IDBValidKey): Promise<IDBValidKey> {
    return this.run(store, 'readwrite', (s) => (key === undefined ? s.put(value) : s.put(value, key)))
  }

  add<T>(store: StoreName, value: T): Promise<IDBValidKey> {
    return this.run(store, 'readwrite', (s) => s.add(value))
  }

  delete(store: StoreName, key: IDBValidKey): Promise<undefined> {
    return this.run(store, 'readwrite', (s) => s.delete(key))
  }

  all<T>(store: StoreName): Promise<T[]> {
    return this.run(store, 'readonly', (s) => s.getAll()) as Promise<T[]>
  }

  keys(store: StoreName): Promise<IDBValidKey[]> {
    return this.run(store, 'readonly', (s) => s.getAllKeys())
  }

  /** Para pruebas: cierra la conexión. */
  async close() {
    if (this.dbp) (await this.dbp).close()
    this.dbp = null
  }
}
