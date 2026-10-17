const DB_NAME = 'zvms4-cache'
const STORE_NAME = 'responses'
const MAX_ENTRIES = 300
const MAX_AGE_MS = 5 * 60 * 1000

export interface CachedEntry {
  t: number
  data: unknown
}

let dbPromise: Promise<IDBDatabase | null> | null = null
let generation = 0

function openDB(): Promise<IDBDatabase | null> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve) => {
    let settled = false
    const finish = (db: IDBDatabase | null) => {
      if (settled) { db?.close(); return }
      settled = true
      clearTimeout(timer)
      resolve(db)
    }
    const timer = setTimeout(() => finish(null), 1500)
    try {
      if (typeof indexedDB === 'undefined') { finish(null); return }
      // v1 could contain authenticated responses. Discard them on upgrade.
      const request = indexedDB.open(DB_NAME, 2)
      request.onupgradeneeded = () => {
        const db = request.result
        if (db.objectStoreNames.contains(STORE_NAME)) db.deleteObjectStore(STORE_NAME)
        db.createObjectStore(STORE_NAME).createIndex('t', 't')
      }
      request.onsuccess = () => {
        request.result.onversionchange = () => request.result.close()
        finish(request.result)
      }
      request.onerror = request.onblocked = () => finish(null)
    } catch { finish(null) }
  })
  return dbPromise
}

async function transaction<T>(
  mode: IDBTransactionMode,
  fallback: T,
  operation: (store: IDBObjectStore, setResult: (value: T) => void) => void
): Promise<T> {
  const db = await openDB()
  if (!db) return fallback
  return new Promise((resolve) => {
    let result = fallback
    const timer = setTimeout(() => resolve(fallback), 1500)
    const finish = (value: T) => { clearTimeout(timer); resolve(value) }
    try {
      const tx = db.transaction(STORE_NAME, mode)
      tx.oncomplete = () => finish(result)
      tx.onerror = tx.onabort = () => finish(fallback)
      operation(tx.objectStore(STORE_NAME), (value) => { result = value })
    } catch { finish(fallback) }
  })
}

export async function cacheGet(key: string): Promise<CachedEntry | null> {
  return transaction<CachedEntry | null>('readonly', null, (store, setResult) => {
    const request = store.get(key)
    request.onsuccess = () => {
      const entry = request.result as CachedEntry | undefined
      const age = entry ? Date.now() - entry.t : NaN
      if (Number.isFinite(age) && age >= 0 && age <= MAX_AGE_MS) setResult(entry!)
    }
  })
}

export async function clearCache(): Promise<void> {
  generation += 1
  await transaction('readwrite', undefined, (store) => { store.clear() })
}

export async function cacheSet(key: string, data: unknown): Promise<void> {
  const started = generation
  // Prevent a write awaiting openDB from reintroducing data after logout.
  await openDB()
  if (started !== generation) return
  await transaction('readwrite', undefined, (store) => {
    if (started !== generation) return
    store.put({ t: Date.now(), data }, key)
    const count = store.count()
    count.onsuccess = () => {
      let excess = count.result - MAX_ENTRIES
      if (excess <= 0) return
      const cursorRequest = store.index('t').openCursor()
      cursorRequest.onsuccess = () => {
        const cursor = cursorRequest.result
        if (!cursor || excess <= 0) return
        cursor.delete()
        excess -= 1
        cursor.continue()
      }
    }
  })
}
