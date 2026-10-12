const DB_NAME = 'zvms4-cache'
const STORE_NAME = 'responses'
const MAX_ENTRIES = 300

export interface CachedEntry {
  t: number
  data: unknown
}

let dbPromise: Promise<IDBDatabase | null> | null = null

function openDB(): Promise<IDBDatabase | null> {
  if (dbPromise) {
    return dbPromise
  }
  dbPromise = new Promise((resolve) => {
    try {
      if (typeof indexedDB === 'undefined') {
        resolve(null)
        return
      }
      const request = indexedDB.open(DB_NAME, 1)
      request.onupgradeneeded = () => {
        const db = request.result
        if (!db.objectStoreNames.contains(STORE_NAME)) {
          const store = db.createObjectStore(STORE_NAME)
          store.createIndex('t', 't')
        }
      }
      request.onsuccess = () => resolve(request.result)
      request.onerror = () => resolve(null)
      request.onblocked = () => resolve(null)
    } catch {
      resolve(null)
    }
  })
  return dbPromise
}

async function getStore(mode: IDBTransactionMode) {
  const db = await openDB()
  if (!db) {
    return null
  }
  try {
    return db.transaction(STORE_NAME, mode).objectStore(STORE_NAME)
  } catch {
    return null
  }
}

export async function cacheGet(key: string): Promise<CachedEntry | null> {
  const store = await getStore('readonly')
  if (!store) {
    return null
  }
  return await new Promise((resolve) => {
    const request = store.get(key)
    request.onsuccess = () => {
      const entry = request.result as CachedEntry | undefined
      resolve(entry && typeof entry.t === 'number' ? entry : null)
    }
    request.onerror = () => resolve(null)
  })
}

export async function cacheSet(key: string, data: unknown): Promise<void> {
  const store = await getStore('readwrite')
  if (!store) {
    return
  }
  store.put({ t: Date.now(), data }, key)
  const countRequest = store.count()
  countRequest.onsuccess = () => {
    let excess = countRequest.result - MAX_ENTRIES
    if (excess <= 0) {
      return
    }
    const cursorRequest = store.index('t').openCursor()
    cursorRequest.onsuccess = () => {
      const cursor = cursorRequest.result
      if (!cursor || excess <= 0) {
        return
      }
      cursor.delete()
      excess -= 1
      cursor.continue()
    }
  }
}