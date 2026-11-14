import type { CookieRow } from '../$kbSlug/-kb-types'

export class CookieJsonError extends Error {}

// Browsers export ".example.com" for a cookie that app.example.com also
// receives, so a parent domain covers the host only with that leading dot;
// without it the cookie is host-only and the crawler never sends it to a
// subdomain. A bare suffix such as ".com" never covers. The backend applies
// the same rule when it validates a save.
export function cookieCoversHost(domain: string, host: string): boolean {
  const d = domain.toLowerCase()
  const bare = d.replace(/^\./, '')
  return host === bare || (d.startsWith('.') && bare.includes('.') && host.endsWith(d))
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase()
  } catch {
    return ''
  }
}

// Rows the name/value inputs show: typed by hand. An imported row is listed
// by name under the paste box instead, never with its value.
export function isManualRow(row: CookieRow): boolean {
  return !row.imported
}

function expiryOf(entry: Record<string, unknown>): string | undefined {
  // Cookie-Editor writes seconds since the epoch as expirationDate; other
  // exports write an ISO date as expires. A session cookie has neither.
  const seconds = entry.expirationDate
  if (typeof seconds === 'number') return new Date(seconds * 1000).toISOString()
  if (typeof entry.expires === 'string' && !Number.isNaN(Date.parse(entry.expires))) {
    return new Date(entry.expires).toISOString()
  }
  return undefined
}

// Parses a Cookie-Editor JSON export into cookie rows. Throws CookieJsonError
// naming the first problem, so a malformed paste is refused instead of
// silently yielding fewer cookies than the operator thinks they added.
export function parseCookieEditorJson(text: string, defaultDomain: string): CookieRow[] {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    throw new CookieJsonError('not valid JSON')
  }
  if (!Array.isArray(parsed) || parsed.length === 0) {
    throw new CookieJsonError('expected a non-empty array of cookies')
  }
  return parsed.map((item, index) => {
    const entry = (item ?? {}) as Record<string, unknown>
    if (typeof entry.name !== 'string' || !entry.name.trim()) {
      throw new CookieJsonError(`cookie ${index + 1} has no name`)
    }
    if (typeof entry.value !== 'string' || !entry.value) {
      throw new CookieJsonError(`cookie "${entry.name}" has no value`)
    }
    const expires = expiryOf(entry)
    return {
      name: entry.name.trim(),
      value: entry.value,
      domain: typeof entry.domain === 'string' && entry.domain ? entry.domain : defaultDomain,
      path: typeof entry.path === 'string' && entry.path ? entry.path : '/',
      ...(expires ? { expires } : {}),
      secure: entry.secure !== false,
      httpOnly: entry.httpOnly === true,
      imported: true,
    }
  })
}
