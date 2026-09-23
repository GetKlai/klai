// The gap inbox groups rows by (source, query_text, gap_type, language,
// diagnosis, nearest_kb_slug, audience, group_key) — there is no persisted row
// id (SPEC-RAG-SUPPORT-GAP groups are computed, not stored). Both the list and
// the detail route need to name one group in a URL, so this is the one place
// that turns that tuple into a route param.
//
// The param is a DIGEST, never the tuple itself: query_text is the visitor's
// own question, which docs/privacy/telemetry-modes.md keeps out of anything
// that outlives its 7-day window. A URL lands in browser history, in the
// referrer of anything the page links to and in the proxy's access log, none of
// which honour that window. The digest is one-way, so it leaks nothing, and the
// detail screen finds its group by digesting the same list the user just saw.
export interface GapIdentity {
  source: string
  query_text: string
  gap_type: string
  language: string | null
  diagnosis: string | null
  nearest_kb_slug: string | null
  audience: string | null
  group_key?: string | null
}

/** FNV-1a over the group's tuple, hex. Not cryptographic: it only has to keep
    one tenant's few hundred groups apart within one rendered list, and a
    collision shows the wrong group rather than leaking anything. */
export function gapGroupDigest(gap: GapIdentity): string {
  const tuple = JSON.stringify([
    gap.source,
    gap.query_text,
    gap.gap_type,
    gap.language ?? null,
    gap.diagnosis ?? null,
    gap.nearest_kb_slug ?? null,
    gap.audience ?? null,
    gap.group_key ?? null,
  ])
  // Two 32-bit lanes instead of one: 64 bits of output over a list of this size
  // keeps the collision chance far below the point where anyone would notice.
  let high = 0x811c9dc5
  let low = 0x01000193
  for (let i = 0; i < tuple.length; i++) {
    const code = tuple.charCodeAt(i)
    high = Math.imul(high ^ code, 0x01000193) >>> 0
    low = Math.imul(low ^ (code + i), 0x85ebca6b) >>> 0
  }
  return high.toString(16).padStart(8, '0') + low.toString(16).padStart(8, '0')
}

/** The group a digest names, or undefined when the list no longer holds it
    (resolved, aged out, or a stale bookmark). */
export function findGapByDigest<T extends GapIdentity>(gaps: T[], digest: string): T | undefined {
  return gaps.find((gap) => gapGroupDigest(gap) === digest)
}
