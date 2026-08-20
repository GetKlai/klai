---
id: SPEC-RAG-SOURCE-SELECTION-001
version: "0.1.1"
status: draft
created: 2026-08-19
updated: 2026-08-19
author: Mark Vletter
priority: high
related:
  - SPEC-KB-021 (introduced source_label, source_aware_select, and the three-layer router this SPEC rewrites)
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (owns _compute_confidence_band; REQ-4 changes its input semantics)
  - SPEC-RAG-CORRESPONDENCE-DISTILL-001 (the distillation whose output was neutralised by the defect this SPEC fixes)
  - SPEC-RAG-ANSWER-EPISTEMICS-001 (sibling SPEC; independent, shares the Phase 0 observability precondition)
  - SPEC-RAG-EVAL-001 (acceptance criteria use its eval harness)
  - SPEC-PRIVACY-QUERY-SHADOW-001 (REQ-1 must not widen what query text is logged)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author       | Change        |
|---------|------------|--------------|---------------|
| 0.1.1   | 2026-08-19 | Codex | Review clarification: the chat suite keeps only the retrieval-testable brand-token canary; the single-strong-hit confidence contract is proved through the retrieval decision record. A one-chunk served pack deliberately remains `medium` in shadow and must be analysed separately before enforcement. |
| 0.1.0   | 2026-08-19 | Mark Vletter | Initial draft. Written after a production trace of the 2026-08-19 Voys trunk conversation (`request_id=d83d2c14-c2bb-4557-91a1-5831fa9a5a78`) showed the shipped correspondence distillation working correctly while the answer was still wrong, because source selection narrowed the evidence pack on the tenant's own brand name. Four phases, ordered by impact and by dependency. Implementation is delegated (Codex/Sol); this document is the complete, self-contained brief. |

---

# SPEC-RAG-SOURCE-SELECTION-001: Source selection by comparison, not by string match

## Summary

Retrieval-api decides which knowledge sources get preference by **substring-matching
source-label tokens against the raw query text**. For a tenant whose brand name appears
in its own source domain, this fires on essentially every query and hands one source the
entire evidence pack — demoting materially better-scoring chunks from other sources.

The same defective heuristic exists in two independent places, and in one of them it
short-circuits the semantically correct mechanism that already exists next to it.

This SPEC replaces matching with comparison, bounds the damage any source preference can
do, and makes the whole decision observable. It changes behaviour for **all** tenants —
that is intended, not a side effect.

## Motivation

### The production trace

2026-08-19 07:06 CEST, Voys production (org `368884765035593759`), one minute after the
SPEC-RAG-CORRESPONDENCE-DISTILL-001 v0.7.0 deploy (litellm container restart
`05:04:58Z`). A support agent pasted a customer email about a VoIP trunk failing with SIP
`404 Not Found` / `Q.850;cause=1` and asked for a diagnosis.

The distillation worked. The FalkorDB graph-search query recorded for
`request_id=d83d2c14-c2bb-4557-91a1-5831fa9a5a78` shows the effective search terms:

```
Voys | trunk | 404 | Found | uitgaand | bellen | 407 | Proxy |
Authentication | 183 | Session | Progress | Q | 850 | cause | 1 | VGUA
```

That is a keyword-style distillate, not the raw 5760-character email. The sub-question
fan-out was correctly skipped. Everything SPEC-RAG-CORRESPONDENCE-DISTILL-001 promised
held.

The answer was still wrong, and `01_sip_response_codes.md` — the article that states
plainly *"404 | Not Found | Gebruiker/toestel bestaat niet, of extensie niet gevonden"* —
did not reach the model. The `retrieval_decision_record` for the same request shows why:

| Field | Value |
|---|---|
| `source_select.source_select_mode` | `mentioned` |
| `source_select.mentioned_sources` | `["help.voys.nl"]` |
| `source_select.source_counts` | `{help.voys.nl: 12, notion: 6, support: 2}` |
| `reranker_scores_top5` | `[0.8661, 0.6584, 0.6156, 0.2806, 0.2689]` |
| `evidence_pack.sources` | FreePBX 0.8661 · Android Probleemoplosser 0.2806 · VoIP-trunk configuratie 0.2082 |
| `retrieval_confidence_band` | `high` |
| `router.router_layer_used` | `skipped` |

The chunks scoring **0.6584 and 0.6156 never reached the evidence pack**, while chunks
scoring **0.2806 and 0.2082 did**. Nothing about relevance caused that. Source label did.

### Root cause 1 — the match has no normalisation

`retrieval_api/services/diversity.py:66` `_detect_mentioned_sources` splits each
`source_label` on `[-./:]`, drops tokens of ≤3 characters and a hand-curated `STOP_WORDS`
list, then does a **substring check against the query**. For `help.voys.nl`: `help` is in
`STOP_WORDS`, `nl` is too short, leaving exactly one token — **`voys`**.

That is the tenant's own brand name. It is present in nearly every query this tenant
sends, and in nearly every document this tenant owns. A signal that is uniformly present
cannot discriminate, yet it is being used to make a discriminating decision.

On a match, `source_aware_select` (`diversity.py:125-134`) gives the matched source
**all** `top_n` slots; other sources only fill the remainder. That is a hard filter
wearing the costume of a soft preference.

The same class of false positive exists for every label: `notion` fires on any query
mentioning Notion for unrelated reasons, `meetings` on any query about meetings. A
hand-maintained stop-word list cannot fix this, because the offending token is
tenant-specific (`voys`, `mitel`, …) and the list is global.

### Root cause 2 — the good mechanism is short-circuited by the bad one

`retrieval_api/services/router.py` implements a three-layer router. Layer 2
(`layer2_semantic`, `router.py:139-172`) compares the query vector against per-source
centroids and commits only when the top-1/top-2 cosine margin exceeds a threshold.

**Layer 1 (`router.py:119-127` `layer1_keyword`, fed by `_build_keyword_map`,
`router.py:89-117`) is the same substring heuristic, importing the same `STOP_WORDS`
from `diversity.py` (`router.py:9`).** `route_to_sources` returns immediately on a Layer-1 match
(`router.py:257-264`), so the semantic layer is never reached for exactly those queries
where the keyword signal is a false positive.

Why comparison is structurally immune where matching is not: ambient tenant vocabulary
("Voys") is present in **every** source's content, so it lifts every centroid's
similarity equally and **cancels in the top1−top2 margin**. Substring matching has no
such normalisation — it is a direct hit on the one label that happens to contain the
brand. A margin-based comparison also *abstains* when all sources look alike, which is
the correct behaviour for a tenant whose sources are all about one company. The keyword
layer always commits.

### Root cause 3 — the router does not run on the path that matters

`retrieval_api/api/retrieve.py:557-561` gates the router on `req.kb_slugs is None`. When
the caller pins KBs (as the litellm hook does — the footer for this conversation read
"Kennisbanken in scope: org, sip, support"), the router is skipped entirely, while
`source_aware_select`'s keyword narrowing still runs unconditionally. The stronger
mechanism is conditional; the weaker one is not.

### Root cause 4 — the confidence band is the max, so one strong wrong hit reads as "high"

`retrieval_api/api/ranking.py:20-44` `_compute_confidence_band` buckets on the **maximum**
post-rerank score (`confidence_band_high_threshold: 0.60`, `config.py:92`). In this
request, FreePBX at 0.8661 alone produced `confidence_band: high` while six of the seven
served items scored ≤0.28.

`high` is what suppresses the deterministic low-confidence abstain of
SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2 and licenses assertive phrasing. A single
uncorroborated hit should not be able to do that.

### Root cause 0 — none of this was visible

Every decision-relevant event in the litellm hook (`query_rewrite`,
`query_rewrite_metadata`, `pasted_correspondence_detected`,
`sub_question_split_skipped_pasted_correspondence`, `KB injection`) is emitted at
`logger.info` and does not reach VictoriaLogs in production. The code already knows this
(`deploy/litellm/klai_knowledge.py:791-795`) and works around it for one event only.
Establishing the facts above required reconstructing the distilled query from an
**unrelated service's FalkorDB error stack trace**.

Consequently SPEC-RAG-CORRESPONDENCE-DISTILL-001 AC-8 ("rewrite-call latency p95 over
first 24h post-deploy") has never been measurable, and no acceptance criterion in this
SPEC could be verified in production either. Observability is therefore Phase 0, not an
afterthought.

### Why this is a documented failure class

- Hard metadata filters destroy recall irrecoverably: once the gold document is excluded,
  no reranker or better embedding can recover it, while every downstream metric still
  looks reasonable ([OptyxStack](https://optyxstack.com/rag-reliability/metadata-filters-in-rag-why-good-documents-disappear-before-retrieval-starts)).
- The prevailing decision rule: hard-filter only when correctness is binary (ACL,
  jurisdiction, published-vs-draft); prefer a soft ranking signal when relevance is
  graded (locale, recency, source preference). Source preference is graded.
- Routing strategies trade off predictably: rule-based is fast and brittle,
  semantic/embedding-based is fast and robust, LLM/logical is most accurate and most
  expensive ([Milvus](https://milvus.io/blog/build-smarter-rag-routing-hybrid-retrieval.md),
  [BetterLink](https://eastondev.com/blog/en/posts/ai/20260513-rag-query-routing/)).
  What is deployed today is rule-based in its most brittle possible form: a substring
  test on a single token.
- The recommended instrumentation is *filter suppression rate* — how often an
  answer-bearing document exists in the full corpus but not in the filtered subset —
  plus empty-filtered-candidate rate and fallback rate. None of these are measured today.

## Scope

### In scope

**Phase 0 — observability (`deploy/litellm/klai_knowledge.py`)**

- Promote a named, closed set of decision events from `info` to `warning`, matching the
  precedent already established in this file for `query_rewrite_destructive_blocked`
  (`klai_knowledge.py:791-798`) and in `klai_kb_citation_render.py:756`.

**Phase 1 — bounded preference (`klai-retrieval-api/retrieval_api/services/diversity.py`)**

- Replace slot allocation in `source_aware_select`'s `mentioned` branch with a bounded
  additive score boost.
- New telemetry fields on the `retrieval_decision_record` `source_select` block.

**Phase 2 — comparison-based selection (`diversity.py`, `router.py`, `api/retrieve.py`)**

- Delete `_detect_mentioned_sources`, `layer1_keyword`, `_build_keyword_map`, and
  `STOP_WORDS`.
- Run the router on the pinned-KB path, scoped to the pinned KBs.

**Phase 3 — corroborated confidence (`klai-retrieval-api/retrieval_api/api/ranking.py`)**

- Band derived from the served pack rather than its maximum, shipped in shadow first.

**Eval**

- A retrieval-testable brand-token canary in
  `klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml`.
- The single-strong-hit band case is asserted on `retrieval_decision_record`; an empty
  `expected_chunks` list is not a RAGAS canary and therefore must not be presented as one.

### Out of scope

- Changing `compute_source_label()` (`klai-knowledge-ingest/knowledge_ingest/source_label.py:12`).
  The domain-derived label is an identity used by citation titles
  (`klai_kb_citation_render.py:284`, `:454`), the router's Facet catalogue
  (`router.py::fetch_source_catalog`), and traceability
  (`klai_kb_traceability.py:50`). The defect is in what the routing does with the label,
  not in the label. Stripping the domain would break three consumers and fix nothing.
- Enabling the Layer-3 LLM router fallback (`router_llm_fallback`, default `False`).
  Unchanged by this SPEC.
- Re-indexing, re-embedding, or any change to chunking or the reranker.
- Anything in the answer layer — that is SPEC-RAG-ANSWER-EPISTEMICS-001.
- Per-org IDF-based stop-word computation. Considered and deliberately rejected for this
  SPEC: once Phase 2 removes all lexical matching, there is no stop-word list left to
  compute. Revisit only if REQ-6's abstention rate proves unacceptable and a lexical
  signal must be reintroduced.

## Functional Requirements (EARS)

### Phase 0 — observability (precondition for verifying every later phase)

#### REQ-1 — decision events must reach VictoriaLogs (ubiquitous)

**THE litellm hook SHALL** emit the following events at a level that reaches production
log aggregation: `query_rewrite` / `query_rewrite_metadata`,
`pasted_correspondence_detected`, `sub_question_split_skipped_pasted_correspondence`, and
the `KB injection` summary.

Implementation constraint: promote these specific call sites to `logger.warning`, in line
with the two existing precedents in this codebase. **Do NOT** raise a global log level —
that floods the stream with third-party litellm output and is not the established pattern.

**THE promotion SHALL NOT** widen what query text is logged. The
`telemetry_level` branch (`klai_knowledge.py:810-838`) governing raw vs. redacted query
text is unchanged, per SPEC-PRIVACY-QUERY-SHADOW-001 REQ-6.

Verification is external: after deploy, a LogsQL query for each event name must return
rows. A unit test asserting the log level is required but is not sufficient evidence.

### Phase 1 — bounded preference (kills the failure class independently of routing)

#### REQ-2 — source preference is an additive bounded boost (event-driven)

**WHEN** a set of preferred source labels is available, **THE selection SHALL** apply a
bounded additive boost `β` to the ranking score of chunks belonging to those labels, and
**SHALL NOT** allocate extra or guaranteed slots based on preferred status. The existing
per-source diversity cap remains unchanged and applies equally to preferred and
non-preferred labels.

The boosted value is derived from the same field `ranking_score()` already uses
(`final_rank_score` when the ranking contract is active, `reranker_score` otherwise) so
that shadow and contract modes stay consistent.

`β` is configurable as `source_preference_boost` (env `SOURCE_PREFERENCE_BOOST`), default
**0.05**. This default is an untested starting point chosen to be a mild tiebreak; REQ-7's
eval run is what validates or replaces it.

#### REQ-3 — rank inversion is bounded by construction (ubiquitous)

**THE source-preference step SHALL** guarantee that it does not rank or displace a chunk
below another whose unboosted ranking score is more than `β` lower.

This is a property of REQ-2's bounded additive form, not additional runtime logic: an
additive boost capped at `β` cannot move a chunk past one scoring more than `β` above it.
The requirement exists so it is **tested as an invariant** rather than assumed, and so any
future source-preference mechanism inherits the bound.

A property test SHALL assert this over randomised score/label distributions. It SHALL
also compare the served pack with `β=0` against `β>0` while the production diversity cap
is enabled, so any displacement caused by preference remains bounded. Diversity may
independently select a lower-scoring label in both packs; that pre-existing, symmetric
cap behaviour is not a preference-caused inversion.

#### REQ-4 — the counterfactual is logged (ubiquitous)

**THE `retrieval_decision_record` `source_select` block SHALL** additionally carry:

- `preference_applied: bool`
- `preferred_labels: list[str]`
- `boost: float`
- `pack_without_preference: list[str]` — chunk ids that would have been served with `β=0`
- `suppressed_count: int` — chunks displaced from the served pack purely by preference
- `max_score_inversion: float` — largest observed (unboosted) score gap across a
  preference-caused reordering

`suppressed_count` is the filter-suppression-rate signal named in the Motivation. It is
the primary post-deploy monitoring field for this SPEC.

### Phase 2 — comparison replaces matching

#### REQ-5 — all lexical source matching is removed (ubiquitous)

**THE codebase SHALL NOT** contain a source-selection path that decides preference by
substring-matching source-label tokens against query text.

Concretely, delete:

- `diversity.py:66-91` `_detect_mentioned_sources` and its call in `source_aware_select`
- `router.py:89-117` `_build_keyword_map`
- `router.py:119-127` `layer1_keyword`
- `router.py:9` the `STOP_WORDS` import
- the Layer-1 branch in `route_to_sources` (`router.py:257-264`)
- `diversity.py:27-63` `STOP_WORDS`

The retrieval source-selection `STOP_WORDS` constant has exactly two consumers
(`diversity.py:86` and `router.py:111`), both removed here — verified 2026-08-19 by
repository-wide grep. Unrelated stop-word lists outside retrieval source selection are
not part of this requirement.

`RoutingDecision.layer_used` loses the `"keyword"` value. Update its docstring and every
consumer, including the `router_layer_used` telemetry field and its tests.

A source-scan guard test SHALL fail CI if `source_label` is ever again compared against
query text with `in`, mirroring the drift-guard pattern already used by
`test_direct_mistral_throttle_drift.py` and `test_truncated_render_label_guard.py`.

#### REQ-6 — the router runs on the pinned-KB path (event-driven)

**WHEN** `req.kb_slugs` is not `None`, **THE router SHALL** still run, with its source
catalogue restricted to labels occurring within those KBs.

`fetch_source_catalog` gains a `kb_slugs` parameter and adds a `kb_slug` condition to its
Qdrant facet filter. `kb_slug` is a keyword-indexed payload field
(`klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:126`) — verified 2026-08-19;
re-confirm before implementing. The per-org catalogue cache key SHALL include the
`kb_slugs` set, or the cache will serve a catalogue from the wrong scope.

**WHEN** `layer2_semantic` returns `None` (margin below `router_margin_dual`), **THE
selection SHALL** apply no source preference at all. Abstention is the correct outcome for
a tenant whose sources are semantically similar, and must not fall back to any lexical
heuristic.

#### REQ-7 — behaviour change is measured, not assumed (ubiquitous)

**THE change SHALL** be evaluated on the full `chat.yaml` suite before and after, with
per-canary deltas reported. A tenant-wide behaviour change is explicitly accepted by the
product owner (2026-08-19); an *unmeasured* one is not.

### Phase 3 — corroborated confidence

#### REQ-8 — `high` requires corroboration (state-driven)

**WHILE** the reranker is enabled and the served pack is non-empty, **THE confidence band
SHALL** be `high` only when at least two served chunks score ≥ `confidence_band_high_threshold`.
A single chunk at or above the threshold with no second supporting chunk yields at most
`medium`.

`low` and `unknown` semantics are unchanged. `medium` remains the residual bucket.
This includes a served pack containing exactly one strong chunk: it deliberately remains
`medium` in shadow. The later enforcement decision must report one-chunk packs separately,
rather than silently treating the shadow contract as already approved for serving.

#### REQ-9 — shadow before enforcement (ubiquitous)

**THE new band SHALL** first ship computed-but-not-acted-upon, emitting both
`confidence_band` (old, authoritative) and `confidence_band_corroborated` (new, shadow) on
the decision record — the same shadow pattern previously used by the now-retired gate
and evidence-tier experiments, and by the citation-rescue rollout.

Enforcement is a separate, later change, gated on a shadow-period comparison of how often
the two disagree and what that would have done to the Strict abstain rate. Flipping
enforcement in the same PR is out of scope.

> **Resolution 2026-08-20:** REQ-8/9 were decommissioned after production verification.
> The candidate changed only `high` to `medium`, while every current downstream policy
> reacts exclusively to `low`/`unknown`; it therefore had no behavioral outcome to
> evaluate. It also counted two high-scoring chunks rather than independent sources, so
> “corroborated” overstated the signal. The shadow field, metric, dashboard series, and
> helper branch were removed instead of leaving an ownerless experiment running.
> The gate and evidence-tier precedents were removed in the same audit; citation rescue
> graduated to active behavior.

## Non-Functional Requirements

- **Latency**: Phase 2 adds the router to the pinned-KB path. Layer 2 is 5–20 ms with a
  warm centroid cache (`router_centroid_ttl_seconds: 600`); a cold cache scrolls up to 10
  chunks per label for at most 50 labels. Retrieval p95 MUST NOT regress by more than 100 ms.
  If cold-start cost exceeds that, the centroid cache must be warmed rather than the
  requirement relaxed.
- **Tenant isolation**: centroid computation already filters on `org_id`
  (`router.py::_default_compute_centroids`, per audit-tenant-isolation-2026-05-05 finding
  B-1). Any new Qdrant filter added under REQ-6 MUST keep that condition. This is a
  tenant-isolation-relevant change — `/klai:tenant-review` applies.
- **Fail-open**: every new path degrades to "no source preference" on failure. A router
  error, a facet failure, or an empty catalogue MUST mean full recall, never a narrowed
  pack. `fetch_source_catalog` already returns `[]` on exception; preserve that.
- **Backwards compatibility**: `source_aware_select`'s signature may change; it has one
  production caller (`api/retrieve.py:762`, `:801`). No external contract changes.
- **Multi-tenant**: applies uniformly. Voys is the reproduction case, not the target.

## Acceptance Criteria

| AC ID | Test | Expected outcome |
|-------|------|-------------------|
| AC-1 | After Phase 0 deploy, LogsQL for each event name in REQ-1 over a 1h window with real chat traffic | ≥1 row per event name; raw query text absent for orgs whose `telemetry_level` is not `full` |
| AC-2 | Unit: `source_aware_select` with two labels, chunk A (label X) at 0.66 and chunk B (label Y) at 0.28, preference on Y | A still ranks above B; B is not promoted past A |
| AC-3 | Property test over randomised (score, label) sets with preference on a random label, including the production diversity cap | No preference-caused reorder or `β=0` → `β>0` pack displacement exceeds `β`; the independent diversity-cap outcome may differ from pure score order in both packs |
| AC-4 | Replay of the incident shape: query containing the tenant brand token, chunks split across `help.voys.nl` and another label | `suppressed_count == 0`; chunks at 0.658/0.616 present in the served pack |
| AC-5 | Retrieval source-selection grep after Phase 2 | Zero occurrences of `_detect_mentioned_sources`, `layer1_keyword`, `_build_keyword_map`, or `STOP_WORDS` in `retrieval_api/services/{diversity,router}.py`; drift-guard test present and failing on a deliberately reintroduced substring match |
| AC-6 | Unit: `route_to_sources` on a catalogue whose labels are all semantically similar (Voys-shaped) | Returns `selected_source_labels=None`, `layer_used="none"` — abstains rather than committing |
| AC-7 | Unit: `fetch_source_catalog` with `kb_slugs=["sip"]` | Facet filter carries both `org_id` and `kb_slug`; cache key differs from the unscoped call |
| AC-8 | Full `chat.yaml` suite before vs. after Phase 2 | Aggregate `context_recall` MUST NOT decrease; aggregate `context_precision` MUST NOT decrease by more than 0.02. Per-canary deltas reported in the PR body regardless of outcome |
| AC-9 | Historical; decommissioned 2026-08-20 | Candidate had no downstream behavioral effect |
| AC-10 | Historical; decommissioned 2026-08-20 | Candidate did not establish independent-source corroboration |
| AC-11 | Historical; decommissioned 2026-08-20 | The observed band disagreement could not change current serving policy, so no enforcement decision remained |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Router abstains far more often than the keyword layer committed, so source preference effectively disappears for most tenants | high | low | This is the intended direction (recall over precision) and the failure mode is full recall, not a wrong pack. AC-8 quantifies it. If precision loss exceeds the bar, the correct response is to tune `router_margin_dual`, not to reinstate lexical matching |
| `β = 0.05` is wrong for the actual reranker score distribution | high | low | Explicitly declared an untested starting point. Env-tunable, validated by AC-8, and bounded by REQ-3 so a wrong value cannot reproduce the original failure class — only weaken or strengthen a tiebreak |
| Cold centroid cache adds latency on the pinned-KB path, which previously skipped the router entirely | medium | medium | 10-chunk sample per label, ≤50 labels, 600 s TTL. NFR sets a 100 ms p95 ceiling. If exceeded, warm the cache on catalogue build rather than relax the ceiling |
| Cache key omits `kb_slugs` and serves a catalogue from the wrong scope | medium | high | Called out explicitly in REQ-6. AC-7 tests it. Tenant-isolation-adjacent: a wrong cache key across orgs would be a cross-tenant leak, so `/klai:tenant-review` is mandatory on this PR |
| Router catalogues and centroids are organisation-scoped, not user-visibility-scoped | low | medium | Serving-time Qdrant filters remain authoritative, so inaccessible chunks cannot enter the response. A broader per-user router geometry redesign is intentionally deferred; it needs an explicit cache-cardinality and private-KB routing contract rather than a partial change in this SPEC |
| Corroboration requirement raises the Strict abstain rate and users see more refusals | medium | high | Exactly why REQ-9 mandates shadow-first. Enforcement is a separate decision with AC-11 data behind it |
| Phase 2 lands before Phase 1 and a routing regression reproduces the demotion bug | low | high | Phase ordering is a hard requirement, not a preference: Phase 1's bound is what makes Phase 2 safe to get wrong. Enforce via PR sequencing |

## Implementation handoff

Implementation is delegated. Four PRs, in this order; do not combine.

| PR | Phase | Files | Gate before merge |
|----|-------|-------|-------------------|
| 1 | 0 | `deploy/litellm/klai_knowledge.py` + tests | AC-1 (needs a deploy; state explicitly if not yet observed) |
| 2 | 1 | `retrieval_api/services/diversity.py`, `api/retrieve.py`, config, tests | AC-2, AC-3, AC-4 |
| 3 | 2 | `retrieval_api/services/diversity.py`, `services/router.py`, `api/retrieve.py`, tests, drift guard | AC-5, AC-6, AC-7, AC-8 + `/klai:tenant-review` |
| 4 | 3 | `retrieval_api/api/ranking.py`, `api/retrieve.py`, tests | AC-9, AC-10 |

Rules for the implementer:

- Write the failing test first for every requirement. AC-4 and AC-9 must be RED against
  current `main` before the fix, and that must be stated in the PR body with the actual
  failure output — not asserted.
- Run `mcp__codeindex__impact` on `source_aware_select`, `route_to_sources`,
  `fetch_source_catalog`, and `_compute_confidence_band` before editing. These are shared
  helpers on a cross-service contract; AGENTS.md requires it.
- Do not "improve" adjacent code. `minimal-changes` applies. Deleting the functions named
  in REQ-5 is in scope; refactoring what remains is not.
- Remove what you replace in the same PR. No feature flag keeping the keyword path alive
  beside the new one — `clean over clever, no parallel old+new`.
- Report `git diff --stat` and the actual test command output in each PR body. "Tests
  pass" without the command and its output scores zero.

## Sources

Production evidence (2026-08-19, VictoriaLogs, org `368884765035593759`):

- `request_id=d83d2c14-c2bb-4557-91a1-5831fa9a5a78` — post-deploy run, `retrieval_decision_record`
  and `kb_citations_rendered_structured`.
- `request_id=58d22917-bae2-4956-b4c4-08566d2a1795` — pre-deploy run 15 minutes earlier,
  byte-identical retrieval scores, which is what established that the distillation change
  was not the variable.
- litellm container restart at `2026-08-19T05:04:58Z` — the deploy boundary between the two.

Source references:

- `klai-retrieval-api/retrieval_api/services/diversity.py:27-63,66-91,94-156`
- `klai-retrieval-api/retrieval_api/services/router.py:9,89-117,119-127,139-172,236-264`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:555-583,762,801`
- `klai-retrieval-api/retrieval_api/api/ranking.py:20-44`
- `klai-retrieval-api/retrieval_api/config.py:92-93,107-112`
- `klai-knowledge-ingest/knowledge_ingest/source_label.py:12-38`
- `klai-knowledge-ingest/knowledge_ingest/qdrant_store.py:126`
- `deploy/litellm/klai_knowledge.py:774,790-838,890-896`
- `deploy/litellm/klai_kb_citation_render.py:756`

External research:

- [Metadata Filters in RAG: Why Good Documents Disappear Before Retrieval Starts — OptyxStack](https://optyxstack.com/rag-reliability/metadata-filters-in-rag-why-good-documents-disappear-before-retrieval-starts)
- [Build Smarter RAG with Routing and Hybrid Retrieval — Milvus](https://milvus.io/blog/build-smarter-rag-routing-hybrid-retrieval.md)
- [RAG Query Routing in Practice — BetterLink](https://eastondev.com/blog/en/posts/ai/20260513-rag-query-routing/)
- [Hybrid Search and Re-Ranking in Production RAG — Towards Data Science](https://towardsdatascience.com/hybrid-search-and-re-ranking-in-production-rag/)
- [Rerankers and Two-Stage Retrieval — Pinecone](https://www.pinecone.io/learn/series/rag/rerankers/)
