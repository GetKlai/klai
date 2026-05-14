const RELOAD_KEY = 'klai:stale-chunk-reload'
const RELOAD_TTL_MS = 30_000

const CHUNK_ERROR_PATTERNS = [
  /Failed to fetch dynamically imported module/i,
  /error loading dynamically imported module/i,
  /Importing a module script failed/i,
  /Loading chunk .* failed/i,
  /ChunkLoadError/i,
  /Expected a JavaScript-or-Wasm module script/i,
  /Unable to preload CSS/i,
]

interface ReloadRecord {
  href: string
  at: number
}

interface StaleChunkReloadOptions {
  now?: () => number
  reload?: () => void
  locationHref?: () => string
  storage?: Pick<Storage, 'getItem' | 'setItem'>
}

type VitePreloadErrorEvent = Event & {
  payload?: unknown
  detail?: unknown
}

function getErrorText(error: unknown): string {
  if (!error) return ''
  if (typeof error === 'string') return error
  if (error instanceof Error) return `${error.name} ${error.message} ${error.stack ?? ''}`
  if (typeof error === 'object') {
    const maybeError = error as { name?: unknown; message?: unknown; stack?: unknown; cause?: unknown }
    return [
      typeof maybeError.name === 'string' ? maybeError.name : '',
      typeof maybeError.message === 'string' ? maybeError.message : '',
      typeof maybeError.stack === 'string' ? maybeError.stack : '',
      maybeError.cause ? getErrorText(maybeError.cause) : '',
    ].join(' ')
  }
  if (typeof error === 'number' || typeof error === 'boolean' || typeof error === 'bigint') {
    return error.toString()
  }
  if (typeof error === 'symbol') return error.description ?? ''
  return ''
}

function getReloadRecord(storage: Pick<Storage, 'getItem'>): ReloadRecord | null {
  let raw: string | null
  try {
    raw = storage.getItem(RELOAD_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as Partial<ReloadRecord>
    if (typeof parsed.href === 'string' && typeof parsed.at === 'number') {
      return { href: parsed.href, at: parsed.at }
    }
  } catch {
    return null
  }
  return null
}

export function isStaleChunkLoadError(error: unknown): boolean {
  const text = getErrorText(error)
  return CHUNK_ERROR_PATTERNS.some((pattern) => pattern.test(text))
}

export function reloadForStaleChunk(options: StaleChunkReloadOptions = {}): boolean {
  const storage = options.storage ?? window.sessionStorage
  const href = options.locationHref?.() ?? window.location.href
  const now = options.now?.() ?? Date.now()
  const previous = getReloadRecord(storage)

  if (previous?.href === href && now - previous.at < RELOAD_TTL_MS) {
    console.warn('Stale frontend chunk detected, but reload was already attempted for this URL.')
    return false
  }

  try {
    storage.setItem(RELOAD_KEY, JSON.stringify({ href, at: now }))
  } catch {
    console.warn('Stale frontend chunk detected, but reload state could not be stored.')
  }
  console.warn('Stale frontend chunk detected. Reloading to fetch the latest build.')
  const reload = options.reload ?? (() => window.location.reload())
  reload()
  return true
}

export function registerStaleChunkReloadHandler(options: StaleChunkReloadOptions = {}): () => void {
  const handlePreloadError = (event: Event) => {
    const preloadEvent = event as VitePreloadErrorEvent
    const error = preloadEvent.payload ?? preloadEvent.detail
    if (!isStaleChunkLoadError(error)) return

    if (reloadForStaleChunk(options)) {
      event.preventDefault()
    }
  }

  const handleUnhandledRejection = (event: PromiseRejectionEvent) => {
    if (!isStaleChunkLoadError(event.reason)) return

    if (reloadForStaleChunk(options)) {
      event.preventDefault()
    }
  }

  window.addEventListener('vite:preloadError', handlePreloadError)
  window.addEventListener('unhandledrejection', handleUnhandledRejection)

  return () => {
    window.removeEventListener('vite:preloadError', handlePreloadError)
    window.removeEventListener('unhandledrejection', handleUnhandledRejection)
  }
}
