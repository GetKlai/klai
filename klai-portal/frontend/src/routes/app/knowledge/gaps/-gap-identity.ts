// A gap group has no persisted row id: `GET /api/app/gaps` computes it
// (SPEC-RAG-SUPPORT-GAP groups are aggregated, not stored). The list and the
// detail route both have to name one group in a URL, so this is the one place
// that turns a group into a route param.
//
// The param is a DIGEST, never the tuple itself: query_text is the visitor's
// own question, which docs/privacy/telemetry-modes.md keeps out of anything
// that outlives its 7-day window. A URL lands in browser history, in the
// referrer of anything the page links to and in the proxy's access log, none of
// which honour that window.
//
// Only the endpoint's GROUP BY keys go into the digest, and that is the whole
// point of this file. Every other field on the row -- the displayed question,
// the nearest KB, whether any occurrence came from human review -- is a MAX()
// or BOOL_OR() over whichever rows fall inside that call's window. The list
// asks for 30 open days and the detail page for 90 days including closed ones,
// so those aggregates can differ between the two calls for one and the same
// group, and a digest over them would stop matching: a dead link on the group
// the user just clicked. The GROUP BY keys cannot differ, because they are what
// defines the group.
export interface GapIdentity {
  source: string
  query_text: string
  gap_type: string
  language: string | null
  group_key?: string | null
}

/** FNV-1a over the group's identifying keys, hex. Not cryptographic: it only
    has to keep one tenant's few hundred groups apart within one rendered list,
    and a collision shows the wrong group rather than leaking anything. */
export function gapGroupDigest(gap: GapIdentity): string {
  // A support group is keyed by its question_key alone (the endpoint groups
  // case-backed findings by that column only); a telemetry group by its key or,
  // for a row written before keys existed, its literal question plus type and
  // language. The two lists never share a row, so the leading tag is what keeps
  // a support and a telemetry group with the same key apart.
  const tuple =
    gap.source === 'support'
      ? JSON.stringify(['support', gap.group_key ?? gap.query_text])
      : JSON.stringify(['telemetry', gap.group_key ?? gap.query_text, gap.gap_type, gap.language ?? null])
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
    (aged out, or a stale bookmark). */
export function findGapByDigest<T extends GapIdentity>(gaps: T[], digest: string): T | undefined {
  return gaps.find((gap) => gapGroupDigest(gap) === digest)
}
