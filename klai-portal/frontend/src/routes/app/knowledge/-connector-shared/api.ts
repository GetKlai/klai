import { apiFetch } from '@/lib/apiFetch'
import type { CookieRow } from '../$kbSlug/-kb-types'
import type { AuthProbeResult, PreviewResult } from '../-connector-types'

export type CrawlerAuthPayload = {
  cookies?: unknown[]
  use_saved_credentials?: boolean
}

export type CrawlerAuthProbeRequest = CrawlerAuthPayload & {
  url: string
}

export type CrawlerPreviewRequest = CrawlerAuthPayload & {
  url: string
  content_selector?: string
  try_ai?: boolean
}

// A row with a name but no value means "keep the one already saved", which is
// how you replace ONE expired cookie without fetching the others out of
// DevTools again. Dropping those rows here is what made the backend replace
// the whole set with whatever you happened to type, silently losing the rest.
// Removing a row with its x still removes the cookie: the name is then gone.
export function buildCrawlerCookies(rows: CookieRow[], baseUrl: string): unknown[] | undefined {
  const named = rows.filter((row) => row.name.trim())
  // Only a form with no names left at all means "do not touch the cookies".
  // Sending the named rows even when none carries a new value is what makes
  // REMOVING one work: the row that is gone is then genuinely absent, instead
  // of the whole field being omitted and the backend restoring both.
  if (named.length === 0) return undefined

  const domain = (() => {
    try {
      return new URL(baseUrl).hostname
    } catch {
      return ''
    }
  })()

  // Key order is kept as it was: a row WITH a value serialises exactly like
  // before, so nothing downstream sees this change unless a value is blank.
  return named.map((row) => {
    const value = row.value.trim()
    // A prefilled row carries the stored cookie's own domain and path; only a
    // freshly typed row falls back to the base URL. Overwriting them would
    // re-point a kept cookie at whatever host the base URL now names.
    return {
      name: row.name.trim(),
      ...(value ? { value } : {}),
      domain: row.domain ?? domain,
      path: row.path ?? '/',
    }
  })
}

function savedCredentialFields(
  connectorId: string | undefined,
  payload: CrawlerAuthPayload,
): Record<string, unknown> {
  if (connectorId === undefined) {
    return { cookies: payload.cookies || null }
  }

  const useSavedCredentials = payload.use_saved_credentials === true
  return {
    cookies: useSavedCredentials ? null : (payload.cookies || null),
    // Always sent, not only with use_saved_credentials: a row left blank means
    // "keep the stored value", and the server cannot look that up without
    // knowing which connector. It stays the server's decision what to hand
    // back -- the same-origin check on the probe URL is enforced there.
    connector_id: connectorId,
    use_saved_credentials: useSavedCredentials,
  }
}

export function probeCrawlerAuth(
  kbSlug: string,
  request: CrawlerAuthProbeRequest,
  connectorId?: string,
): Promise<AuthProbeResult> {
  const { url, ...auth } = request
  return apiFetch<AuthProbeResult>(`/api/app/knowledge-bases/${kbSlug}/connectors/auth-probe`, {
    method: 'POST',
    body: JSON.stringify({ url, ...savedCredentialFields(connectorId, auth) }),
  })
}

export function previewCrawlerPage(
  kbSlug: string,
  request: CrawlerPreviewRequest,
  connectorId?: string,
): Promise<PreviewResult> {
  const { url, content_selector, try_ai, ...auth } = request
  return apiFetch<PreviewResult>(`/api/app/knowledge-bases/${kbSlug}/connectors/crawl-preview`, {
    method: 'POST',
    body: JSON.stringify({
      url,
      content_selector: content_selector || null,
      try_ai: try_ai ?? false,
      ...savedCredentialFields(connectorId, auth),
    }),
  })
}
