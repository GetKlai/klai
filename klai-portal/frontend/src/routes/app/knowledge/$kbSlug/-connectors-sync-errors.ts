export interface ConnectorLatestSyncRun {
  status: string
  documents_failed?: number | null
  error_details?: Array<Record<string, unknown>> | null
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function compact(text: string, maxLength = 180): string {
  const normalized = text.replace(/\s+/g, ' ').trim()
  if (normalized.length <= maxLength) return normalized
  return `${normalized.slice(0, maxLength - 1).trimEnd()}…`
}

export function summarizeConnectorSyncError(run: ConnectorLatestSyncRun | null | undefined): string | null {
  if (!run) return null
  const status = run.status.toUpperCase()
  if (status !== 'FAILED' && status !== 'AUTH_ERROR') return null

  const first = Array.isArray(run.error_details) ? run.error_details.find(Boolean) : null
  if (!first) {
    if ((run.documents_failed ?? 0) > 0) return 'Sync failed, but no error details were returned.'
    return null
  }

  const file = asText(first.file)
  const error = asText(first.error)
  const reason = asText(first.reason)
  const hostname = asText(first.hostname)
  const message = error || reason || hostname

  if (file && message) return compact(`${file}: ${message}`)
  if (message) return compact(message)
  if (file) return compact(file)
  return 'Sync failed, but no readable error details were returned.'
}
