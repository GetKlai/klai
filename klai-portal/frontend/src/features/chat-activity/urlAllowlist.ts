/**
 * REQ-9 (Finding B-9): URL scheme allowlist for conversation source links.
 *
 * An LLM-controlled source URL could contain a `javascript:` URI. React 18+
 * still navigates on javascript: hrefs, enabling stored-XSS in the admin
 * session on my.getklai.com (CC-2 exploit chain).
 *
 * Only http: and https: schemes are allowed as clickable anchors. All other
 * schemes (javascript:, data:, vbscript:, file:, mailto:, scheme-less, etc.)
 * render as plain text without an href.
 *
 * Leading whitespace is stripped before the check to block bypass attempts
 * like "  javascript:alert(1)". Case is normalised by URL() itself.
 *
 * @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-9
 */
export function _isSafeHttpUrl(url: string): boolean {
  const trimmed = url.trim()
  if (!trimmed) return false
  try {
    const parsed = new URL(trimmed)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:'
  } catch {
    // URL() throws on relative paths, scheme-less, and malformed inputs.
    return false
  }
}
