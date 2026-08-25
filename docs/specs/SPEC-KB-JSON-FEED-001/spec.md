---
id: SPEC-KB-JSON-FEED-001
version: "0.1.1"
status: implemented (pending merge)
created: 2026-08-25
updated: 2026-08-25
author: Claude (Fable), commissioned by Mark Vletter
priority: high
related:
  - SPEC-RAG-SOURCE-SELECTION-001 (source selection defects observed in the same Voys week; this SPEC removes one of its stressors — a source whose chunks are indistinguishable)
  - SPEC-INGEST-RECONCILE-001 (sync reconciliation + skip_reasons this SPEC extends with stale-ref cleanup for feed groups)
  - SPEC-KB-021 (source_label / source_aware_select; json_feed source_label semantics stay unchanged)
  - SPEC-CONNECTOR-CLEANUP-001 (connector-scoped deletion layers; group-doc cleanup must cover the same stores)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author | Change |
|---------|------------|--------|--------|
| 0.1.1   | 2026-08-25 | Claude (Fable) | Post-implementation review amendments. (1) The safe record-line cap is **900 chars**, not the drafted implicit assumption that any single line under the chunk size survives: the chunker only accepts a `\n\n` soft boundary past the window midpoint, so lines must stay ≤ child_size/2 − overlap (2000/2 − 200); measured with the real chunker: 0/200 mid-record splits at 900, 132/200 at 1790. (2) Stale-group cleanup (REQ-5) gained a shrink guard: cleanup is refused (and logged as `stale_cleanup_refused_shrink`, baseline retained) when stale refs exceed half the previous baseline — a partially-returning feed must never wipe the KB. (3) Review items deliberately deferred to backlog: non-breaking cleanup-failure handling (collect-and-continue instead of fail-fast), slug-collision disambiguation via hash suffix, render-mode-flip guard, queued-enrichment cancellation on document delete, group_title_template config instead of the hardcoded `category/entity/brand` title. (4) Delta-review backlog: a 900–1800-char record now hard-fails its group (and thus the sync) instead of being emitted as a single-record document or truncated with a marker; and a refused shrink-cleanup latches permanently for intentional catalogue shrinks (>50%) with only a warning log — needs a two-consecutive-syncs escape hatch or surfacing on the sync_run row. |
| 0.1.0   | 2026-08-25 | Claude (Fable), commissioned by Mark Vletter | Initial draft, written after a production trace of the 2026-08-18/20 Voys PriceRight sessions (org `368884765035593759`, KB `priceright-prijzen-voys`, connector `29a84f05-2476-4209-889c-4e714ce8f71b`). Implementation is delegated (Codex/Sol); this document is the complete, self-contained brief. |

---

# SPEC-KB-JSON-FEED-001: Structure-aware ingestion for JSON feed connectors

## Summary

The `json_feed` connector ingests an entire JSON endpoint as **one document**: it
pretty-prints the parsed payload (`json.dumps(..., indent=2)`) and hands the whole
blob to the generic ingest pipeline. For the real-world case it was built for — a
pricing feed that is a flat array of thousands of small records — this produces one
464 KB "article" that is:

1. chunked by blind character windows (the chunker is markdown-heading-aware only;
   raw JSON has no headings, no blank lines, no `". "` boundaries, so records are
   cut mid-object and keys are separated from their values);
2. silently excluded from LLM enrichment and HyPE question generation because it
   exceeds the 200-chunk cap (`enrichment_policy.py` → `too_many_chunks`), which
   removes one of the three RRF retrieval legs for exactly the content that needs
   it most;
3. embedded as raw JSON syntax, which a natural-language query matches poorly
   compared to the prose help-centre articles it competes against;
4. full of near-duplicate rows (the same product across countries/brands differs by
   a few tokens), which makes chunk embeddings mutually indistinguishable and
   reranking effectively random within the feed.

The customer-visible symptom: a tenant uploads their pricing data and the assistant
cannot reliably answer "wat kost X?" even when feed chunks reach the evidence pack.
The Voys/PriceRight attempt (2026-08-18/20) went through three upload/delete cycles,
one full KB purge, and source pinning — none of which could work, because the
failure is in how the feed is turned into documents, not in retrieval settings.

This SPEC makes the `json_feed` adapter structure-aware: it splits a feed into
**per-group markdown documents** with **one verbalized line per record**, so that
chunk boundaries always coincide with record boundaries, every chunk carries its
group context, enrichment/HyPE run normally, and sync becomes incremental and
cleanable per group. No new services, no LLM calls at ingest time, no retrieval-api
changes.

## Motivation

### The production trace

Org `368884765035593759` (Voys), 2026-08-18 12:30–13:21 UTC and onwards:

- `ingest_complete` for the feed artifact: **247 chunks, one artifact**
  (path = the Supabase RPC URL). After the KB was rebuilt on 2026-08-19 via the
  dedicated `json_feed` connector: **310 chunks, one artifact**
  (`Parsed text document 29a84f05-….json: 464569 characters`).
- `enrichment_enqueue_skipped, reason: too_many_chunks` for both — the feed never
  received context prefixes, HyPE questions, or entity extraction, and nothing
  surfaced this to the user.
- ~24 test queries on 18/08: feed chunks did reach the evidence pack (1–8 per
  query, confidence often "high"), yet the user kept deleting and re-uploading —
  the served chunks were arbitrary 1500–2000-char JSON slices, so the *right*
  record for a specific country/brand was usually not among them.
- The feed itself is a flat array of price records in effectively random order,
  interleaving markets and brands, e.g.:

  ```json
  {"entity_brand_id":"at-voys","entity":"at","brand":"voys",
   "name":"Nummer Italie","name_en":"Number Italy",
   "category":"Telefoonnummers","propositie":"maatwerk",
   "monthly":9.25,"once_new":23.50,"start_tariff":null,
   "per_minute":null,"per_quarter_hour":null,
   "updated_at":"2026-08-24T09:08:14.247+00:00"}
  ```

  Dozens of records differ only in a country name or a number
  ("Nummer Italie €9,25" vs "Nummer Portugal €9,25"), which is the worst possible
  input for windowed chunking + dense embedding.

### Why this design (external grounding)

Row-shaped structured data is a solved category in RAG practice, and the consensus
is consistent across sources:

- **Row/record-level chunking with schema context** — embed each record (or a
  small, coherent group) as its own unit, always including field names, never
  splitting a record across chunks. (Common practice; see e.g. "Chunking
  Strategies for Structured Data in RAG Systems", HackerNoon 2025; row-level
  chunking with schema headers is its explicit starting recommendation.)
- **Verbalization beats raw serialization** — rendering a record as a natural
  sentence/labelled line ("In 2024, ACME Corp had revenue of $450K…") retrieves
  measurably better than embedding raw `key:value` JSON, because text embedders
  are trained on prose (TabRAG, arXiv:2511.06582, shows NL descriptions of
  structured rows outperform raw representations for both retrieval and
  generation; the same conclusion appears in practitioner reports).
- **Group by a low-cardinality dimension** so a chunk answers a whole family of
  questions ("all number prices for market NL") instead of three random rows.
- **Keep structure in metadata** for future filtering (Qdrant payload), and route
  truly analytical queries to structured lookup — the latter is explicitly out of
  scope here (see Non-goals) but the metadata this SPEC adds is its prerequisite.
- Generic hierarchy-preserving JSON splitters exist (LangChain
  `RecursiveJsonSplitter`, LlamaIndex `JSONNodeParser`) and inform REQ-6
  (fallback for non-tabular JSON), but for flat record arrays the right unit is
  the record group, not a byte-budgeted subtree.

### Why not fix it in the chunker instead

A JSON-aware chunker in knowledge-ingest would still receive one 464 KB artifact:
the 200-chunk enrichment cap, the single content fingerprint (all-or-nothing
re-ingest), the single citation URL, and the impossibility of per-group cleanup
all live at the *document* boundary, not the chunk boundary. The adapter is the
only place that still has the parsed structure and can choose document boundaries.
Hence: fix document shaping in the adapter; the existing heading-aware chunker
then does the right thing for free.

## Requirements

### REQ-1 — Per-group documents for flat record arrays

When the fetched feed parses to a **flat array of objects** (all items are objects
whose values are scalars/null; tolerance: items with ≤10% non-scalar fields are
still treated as records, non-scalar fields rendered via compact `json.dumps`),
the adapter MUST split it into multiple documents:

- **Grouping key**: `connector.config.group_by` — an optional list of field names
  (e.g. `["category", "entity", "brand"]`). Records are grouped by the tuple of
  those field values (missing field → literal `"overig"` bucket).
- **Default (no `group_by` configured)**: deterministic batching — preserve feed
  order, split into consecutive batches of at most `max_records_per_doc`
  (default 200) records per document. No value-based auto-detection heuristics
  (they misfire silently; explicit config wins).
- **Document identity**: `DocumentRef.path = "json-feed/{connector_id}/{group_slug}"`
  and `source_ref = "json-feed:{connector_id}:{group_slug}"` where `group_slug`
  is a stable slug of the group key values (or `part-{n:04d}` for default
  batching). Stable across syncs so reconciliation and dedup work per group.
- `content_type` stays `"kb_article"` (keeps the HyPE-enabled content profile).
- `source_url` stays the credential-stripped origin (`_public_source_url`),
  identical for every group doc.

### REQ-2 — Record verbalization (deterministic, no LLM)

Each document is rendered as **markdown**, one line per record:

- Document head: `# {feed_title} — {group title}` (feed_title from
  `connector.config.title`, fallback `"JSON feed"`; group title from the
  `group_by` values, e.g. `Telefoonnummers — voys (nl)`), followed by one intro
  line naming the fields present in this group (schema header).
- Record line format: `- **{record label}** — {field}: {value}; {field}: {value}; …`
  - Record label: value of the first present field from
    `connector.config.record_label_fields` (default candidates:
    `name`, `title`, `label`, `id`); if none present, the record's positional
    index.
  - All remaining fields as `humanized_field_name: value` pairs, in feed order.
    `null`/empty values are **omitted**. Field names are humanized
    (`per_minute` → `per minute`); an optional `connector.config.field_labels`
    map overrides per-field display names (e.g. `{"monthly": "per maand (EUR)"}`).
  - Numbers rendered trimmed (no float noise: `9.25`, not `9.25000`). No
    currency/locale guessing — if the tenant wants `€`, they configure it via
    `field_labels`.
- **Records are separated by blank lines** (`\n\n`). This is load-bearing: the
  size-splitter's first soft boundary is `\n\n`, so chunk cuts always fall
  *between* records, never inside one (AC-3).
- Sort records within a group by record label (stable output → stable content
  fingerprint → downstream dedup keeps working when the feed reorders).
- Exact duplicate records (identical rendered line) are collapsed to one.

### REQ-3 — Size safety and enrichment cap

- A group document exceeding `max_doc_chars` (default 120 000 — comfortably below
  the 200-chunk enrichment cap at ~2 000 chars/chunk) MUST be split at record
  boundaries into `… — deel {i}/{n}` documents with their own stable paths
  (`{group_slug}--{i}`).
- The adapter MUST guarantee no emitted document can trigger
  `enrichment_enqueue_skipped: too_many_chunks` (assert via AC-5).
- Existing feed-level limits stay: `_MAX_FEED_SIZE` (2 MiB), SSRF guard
  (`validate_json_feed_url_strict`), `MAX_INGEST_CONTENT_CHARS` per document.

### REQ-4 — Structured metadata passthrough

For each group document, the adapter MUST provide an `extra` payload (existing
connector→ingest→Qdrant passthrough) containing at least:

```json
{
  "json_feed_group": {"category": "Telefoonnummers", "entity": "nl", "brand": "voys"},
  "json_feed_record_count": 42
}
```

(only the configured `group_by` fields; flat strings, no nesting beyond this).
Per the "Procrastinate enrichment passthrough" rule, these fields MUST survive the
enrichment re-upsert (`extra_payload`), verified by test. These payload fields are
the prerequisite for future filtered retrieval; retrieval-api changes are out of
scope here.

### REQ-5 — Sync reconciliation and stale-group cleanup

Today `get_cursor_state` returns `{}` (every sync refetches everything — fine, the
feed is one HTTP GET) and vanished refs are never cleaned. With per-group docs:

- Refetch-always stays (`get_cursor_state` → `{}`), but unchanged groups MUST NOT
  produce duplicate chunks: identical rendered content ⇒ identical content
  fingerprint ⇒ existing content-hash dedup skips re-ingest
  (verify, don't assume — AC-4).
- After a **successful** sync, group documents whose refs are in the previous
  `synced_refs` but absent from the current `list_documents` output MUST be
  deleted downstream (Qdrant chunks + `knowledge.artifacts` + graph episodes for
  that artifact), using existing per-artifact/connector deletion paths in
  knowledge-ingest. If no per-artifact deletion endpoint reachable from the
  connector exists, add a minimal one (internal, `X-Internal-Secret`, org-scoped,
  delete-by-`source_ref`). Never delete on a failed/partial sync (a transient
  fetch error must not wipe the KB).
- **Migration for existing connectors is this same mechanism**: the legacy
  single-doc ref `json-feed/{connector_id}.json` simply stops being listed, so
  the first sync after deploy cleans it up. A regression test MUST cover exactly
  this transition.

### REQ-6 — Fallback for non-tabular JSON (no more raw blobs)

When the payload is **not** a flat record array (nested object, mixed array):

- Render depth-first as markdown: top-level keys become `#`/`##` headings
  (2 levels max), leaf values as `- {json.path}: {value}` lines with blank-line
  separation; arrays of scalars inline, arrays of objects at deeper levels via
  compact one-line JSON per item.
- Split into multiple documents at top-level-key boundaries when over
  `max_doc_chars` (same stable-path scheme, `{connector_id}/{top_level_key}`).
- The literal `json.dumps(parsed, indent=2)` whole-feed output MUST NOT be
  emitted in any code path anymore.

### REQ-7 — Fail loudly, observably

- A record that cannot be rendered (defensive: rendering is deterministic, so
  this means malformed structure) fails its **group document** — counted in
  `documents_failed` with an `error_details` entry naming group + record index.
  No silent per-record skips.
- The sync-complete log line MUST include: `groups_total`, `records_total`,
  `duplicates_collapsed`, `stale_groups_deleted`.
- Config errors (`group_by` naming a field absent from >50% of records) fail the
  sync with an explicit message, not a semi-empty ingest.

### REQ-8 — Scope guard

- No changes to retrieval-api, the chunker, content profiles, portal UI, or the
  crawler in this SPEC. `connector.config` keys are documented in the adapter
  docstring; UI form support for `group_by`/`field_labels`/`title` is a
  follow-up. (Config can be set through the existing connector-config JSON until
  then.)
- `source_label` remains the connector type (`"json_feed"`); label semantics are
  owned by SPEC-KB-021 / SPEC-RAG-SOURCE-SELECTION-001.

## Acceptance criteria

- **AC-1** Unit: a flat-array fixture (Voys-shaped: ≥3 categories × ≥2 markets,
  incl. nulls, duplicate rows, float noise `9.25000`) with
  `group_by=["category","entity","brand"]` yields one document per non-empty
  group, stable slugged paths, markdown head + schema line, one verbalized line
  per record with humanized field names, nulls omitted, numbers trimmed,
  duplicates collapsed, records sorted by label.
- **AC-2** Unit: same fixture without `group_by` yields deterministic
  `part-0001…` batching, ≤ `max_records_per_doc` records per doc, feed order
  preserved.
- **AC-3** Property/unit: for any emitted document, chunking with the production
  chunker settings (2 000 chars / 200 overlap, `\n\n` soft boundary) never splits
  a record line across two chunks, and every chunk contains the document's
  heading context via the existing parent/heading mechanism. (Test imports the
  real chunker from knowledge-ingest if importable in the connector test env;
  otherwise replicate the boundary contract in a fixture-level test in
  knowledge-ingest.)
- **AC-4** Integration (fakes): two consecutive syncs of an unchanged feed
  produce no duplicate ingests (content-hash dedup path exercised, not assumed).
- **AC-5** Unit: a fixture large enough to exceed `max_doc_chars` splits at
  record boundaries into `deel i/n` docs, each under the 200-chunk enrichment
  cap.
- **AC-6** Integration (fakes): sync N lists groups {A,B,C}; sync N+1 lists
  {A,B} → C's artifacts are deleted downstream after the successful sync;
  a failed sync N+2 deletes nothing. Plus the legacy-path migration case
  (`json-feed/{id}.json` cleaned up on first new-style sync).
- **AC-7** Unit: nested-object fixture renders via REQ-6 with headings and path
  lines; asserting the output contains no `{`-prefixed pretty-printed blob.
- **AC-8** Unit: `extra` payload contains `json_feed_group` +
  `json_feed_record_count` and is included in the enrichment `extra_payload`
  passthrough.
- **AC-9** Retrieval smoke (fixture-level, no live services): embedding-input
  text for a chunk containing "Nummer Italie" also contains its monthly price
  and its group context (category/market) in the same chunk text.

## Non-goals

- Text-to-SQL / structured query tool over feed data (future SPEC; REQ-4's
  metadata is its prerequisite).
- Retrieval-api ranking changes (SPEC-RAG-SOURCE-SELECTION-001 owns that).
- LLM-based verbalization at ingest (cost/latency/nondeterminism; deterministic
  templates are sufficient for record-shaped data and testable).
- Auto-detection of grouping fields (explicit config only).
- Portal UI for the new config keys (follow-up).
- Fixing the crawl-path secret leak (feed URLs with tokens logged in
  knowledge-ingest `path` field for *crawl* ingests) — separate, pre-existing
  issue; tracked outside this SPEC.

## Risks

- **Feeds that are huge flat arrays with no useful grouping** degrade to
  `part-nnnn` batching — better than today (records stay whole, enrichment
  runs), but chunk-level coherence depends on feed order. Mitigation: `group_by`
  documentation + sync log counters make the shape visible.
- **Stale-group deletion** is the only destructive step; gated on
  successful-sync-only and covered by AC-6 both ways.
- **Re-render churn**: changing the template later changes every fingerprint and
  re-ingests all feeds. Accepted; note in HISTORY when it happens.
