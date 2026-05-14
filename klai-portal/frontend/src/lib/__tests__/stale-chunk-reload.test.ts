import { describe, expect, it, vi } from 'vitest'
import {
  isStaleChunkLoadError,
  registerStaleChunkReloadHandler,
  reloadForStaleChunk,
} from '@/lib/stale-chunk-reload'

function createStorage(): Pick<Storage, 'getItem' | 'setItem'> {
  const values = new Map<string, string>()
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value) },
  }
}

describe('stale chunk reload handling', () => {
  it('recognizes Vite dynamic import and module MIME failures', () => {
    expect(isStaleChunkLoadError(new TypeError('Failed to fetch dynamically imported module: /assets/page.js'))).toBe(true)
    expect(isStaleChunkLoadError(new Error('Expected a JavaScript-or-Wasm module script but the server responded with a MIME type of "text/html"'))).toBe(true)
    expect(isStaleChunkLoadError(new Error('regular API request failed'))).toBe(false)
  })

  it('reloads once for the same URL inside the cooldown window', () => {
    const storage = createStorage()
    const reload = vi.fn()
    const options = {
      storage,
      reload,
      locationHref: () => 'https://getklai.test/app/docs/help/page',
      now: () => 1000,
    }

    expect(reloadForStaleChunk(options)).toBe(true)
    expect(reloadForStaleChunk(options)).toBe(false)
    expect(reload).toHaveBeenCalledTimes(1)
  })

  it('prevents Vite preload errors and reloads the page', () => {
    const storage = createStorage()
    const reload = vi.fn()
    const unregister = registerStaleChunkReloadHandler({
      storage,
      reload,
      locationHref: () => 'https://getklai.test/app/docs/help/page',
      now: () => 1000,
    })

    const event = new CustomEvent('vite:preloadError', {
      cancelable: true,
      detail: new TypeError('Failed to fetch dynamically imported module: /assets/_pageId.lazy-old.js'),
    })

    window.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
    expect(reload).toHaveBeenCalledTimes(1)

    unregister()
  })
})
