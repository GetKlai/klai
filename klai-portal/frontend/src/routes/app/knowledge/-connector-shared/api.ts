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

export function buildCrawlerCookies(rows: CookieRow[], baseUrl: string): unknown[] | undefined {
  const filled = rows.filter((row) => row.name.trim() && row.value.trim())
  if (filled.length === 0) return undefined

  const domain = (() => {
    try {
      return new URL(baseUrl).hostname
    } catch {
      return ''
    }
  })()

  return filled.map((row) => ({
    name: row.name.trim(),
    value: row.value.trim(),
    domain,
    path: '/',
  }))
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
    connector_id: useSavedCredentials ? connectorId : null,
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
