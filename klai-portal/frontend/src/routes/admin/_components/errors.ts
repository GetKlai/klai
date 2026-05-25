/**
 * apiFetch formats errors as "{status}: {detail}" - fine for logs, ugly in
 * UI toasts. cleanErrorMessage strips the "409: " / "404: " prefix so the
 * banner reads as natural prose.
 */
export function cleanErrorMessage(err: unknown, fallback: string): string {
  if (!(err instanceof Error)) return fallback
  const match = err.message.match(/^\d{3}:\s*(.+)$/)
  return match ? match[1] : err.message
}
