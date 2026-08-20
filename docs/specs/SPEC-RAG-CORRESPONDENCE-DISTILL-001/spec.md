---
id: SPEC-RAG-CORRESPONDENCE-DISTILL-001
version: "0.7.0"
status: draft
created: 2026-08-18
updated: 2026-08-18
author: Mark Vletter
priority: medium
related:
  - SPEC-RAG-QUERY-REWRITE-001 (this SPEC extends its prompt + call site)
  - SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (REQ-5 brand-bridging is the direct precedent for a conditional prompt-variant on the same call)
  - SPEC-RAG-EVAL-001 (acceptance criteria use its eval harness + expected_chunks canary contract)
  - PR #1059 (pasted-correspondence detector this SPEC's gating flag reuses)
roadmap: docs/architecture/retrieval-improvements-roadmap.md
---

# HISTORY

| Version | Date       | Author       | Change        |
|---------|------------|--------------|---------------|
| 0.1.0   | 2026-08-18 | Mark Vletter | Initial draft |
| 0.2.0   | 2026-08-18 | Mark Vletter | Implementation landed (REQ-1..5) with full test coverage. Two prompt refinements found via live replay against production Mistral + retrieval-api, folded into REQ-2 and the shipped instruction text (see "Empirical findings" below). AC-1 confirmed. AC-2 is **not yet conclusively confirmed** — live re-verification after the second refinement was blocked by persistent Mistral `klai-fast` quota rate-limiting (pre-existing, unrelated production pressure documented earlier the same day). Recommend AC-2 be closed via the `chat.yaml` eval harness (REQ-6/AC-6) once quota headroom allows, rather than further ad-hoc production API calls. |
| 0.3.0   | 2026-08-18 | Mark Vletter | REQ-2 extended with **REQ-2a**: prompt instructions are requests, not guarantees — the second live trial (finding 4 below) showed the model followed the "drop the Call-ID" instruction but ignored "no markdown" and "no long identifiers" for the trunk number in the same output. Added deterministic, code-level enforcement (`_clean_distilled_query`: strips markdown emphasis chars and 5+-digit runs) as defense-in-depth on top of the prompt instruction, applied only on the successful-distillation path (never on the destructive-guard raw-text fallback, never on ordinary non-correspondence rewrites). Same pattern already used elsewhere in this codebase (`klai_citations.strip_model_citation_artifacts`) for the same class of problem. Unit-tested (4 new tests: markdown stripped, long digit run stripped, ordinary rewrite untouched, guard-fallback path untouched). |
| 0.4.0   | 2026-08-18 | Mark Vletter | Two structural follow-ups, both triggered by direct user feedback after 0.3.0 shipped. **(a) Rate-limiter parity.** A background investigation into a Mistral 429 storm on the same day found `klai_kb_query_rewrite.py`'s direct-to-`api.mistral.ai` call (pre-existing, from SPEC-RAG-QUERY-REWRITE-001) had never been throttled — an uncoordinated caller sitting next to knowledge-ingest's already-proven `shared_klai_fast_limiter()` token bucket, i.e. exactly the "system built next to an existing system" anti-pattern this codebase explicitly forbids (`pitfalls/process-rules.md`). Fixed by extracting `TokenBucketLimiter` out of knowledge-ingest into a new shared package (`klai-libs/llm-throttle`), migrating knowledge-ingest's `shared_klai_fast_limiter()` to import from it (zero behavior change, full regression suite green), and wiring the SAME shared class into both Mistral call sites in `klai_kb_query_rewrite.py` (`rewrite_query`, `rewrite_and_classify`) via a new `direct_mistral_limiter()` singleton (`DIRECT_MISTRAL_RATE_LIMIT_RPS`/`_BURST` env-tunable, defaults 0.5 rps / burst 5 — untested starting point, needs tuning against real production 429 telemetry post-deploy). A new drift-guard test (`test_direct_mistral_throttle_drift.py`, mirroring knowledge-ingest's `TestChatCompletionsThrottleDriftGuard`) fails CI if any `deploy/litellm/*.py` file references `chat/completions` without also referencing `direct_mistral_limiter`, so this exact bug class cannot silently recur in either codebase again. **(b) Real, repeatable, well-tested eval harness (REQ-6/AC-6 closure).** Built `klai_correspondence_eval.py` — pure, network-free canary-loading + chunk-matching + pass-rate-aggregation logic, unit-tested in normal CI (`tests/test_correspondence_eval.py`) — plus `scripts/eval_pasted_correspondence_live.py`, a thin, manually-invoked orchestration script that reuses the actual production `rewrite_query()` and `retrieve()` functions (so it automatically goes through the new shared rate limiter) to run the 3 shipped `pasted_correspondence` canaries `--samples` times each and report a pass-rate per canary. Mirrors the existing knowledge-ingest RAGAS eval harness's own constraint (server-side only, not standard CI, since it needs real Mistral quota and retrieval-api's Docker-internal hostname). Both new top-level files added to `deploy/docker-compose.yml`'s bind-mount list (required by `test_all_litellm_top_level_python_modules_are_mounted_in_compose`) so the live script is actually runnable via `docker exec klai-core-litellm-1 python scripts/eval_pasted_correspondence_live.py`. AC-2/AC-6 live confirmation is still pending an actual server-side run — the harness exists and is tested, but has not yet been executed against production. |
| 0.5.0   | 2026-08-18 | Mark Vletter | External review round on the 0.4.0 work surfaced 9 findings; all verified against source and resolved. **Timeout (high):** `direct_mistral_limiter().acquire()` sat outside the HTTP timeout, so limiter-wait under load could stall the user-facing pre-call hook indefinitely. Fixed with `_post_to_mistral_throttled` — acquire + POST wrapped in one `asyncio.wait_for(QUERY_REWRITE_TIMEOUT)`; a limiter timeout now fails open to the raw query like any other rewrite failure. **Budget (high):** 0.5 rps (30 rpm) on top of the router's 45+45 rpm alias budgets could exceed Mistral's ~100 rpm cap; defaults lowered to 0.1 rps / burst 3 (worst case 96 rpm, single-container deployment so the process-local bucket is the real ceiling — documented that replicas would require a shared limiter). **Cleanup regexes (medium):** underscore no longer stripped (ERR_AUTH_FAILED survives); 5+-digit runs preceded by a hyphen or an error/code/status/cve keyword survive (CVE-2026-12345, "error 10060"), bare incident numbers still stripped. **Order (medium):** cleanup now runs BEFORE the destructive-rewrite guard (`_finalize_distilled_rewrite`), with an `empty_after_distillation` fallback to raw — closes the "Ticket 123456"→"123456"→"" empty-query path. **Live eval (4 medium):** default suite path is a container mount (`/app/eval_suites/chat.yaml`, vendored copy + byte-drift test `test_chat_yaml_eval_suite_drift.py` since knowledge-ingest's tree is not on the host); flag computed by the REAL detector (negative-class control now genuinely exercises the plain path); calls `rewrite_and_classify` + sends `coreference_resolved` (mirrors the production call site); matching restricted to top-5 per AC-2's literal bar — with an explicit scope note that context_precision (AC-6) still requires the RAGAS harness. **Citations (low, CONFIRMED):** `evidence_label_ids` derived labels from a naive positional range while `render_evidence_context` skips blank chunks, so a never-rendered label (e.g. E1 after a blank first chunk) was wrongly strippable; now derived from `evidence_chunks_from_chunks` (same skip logic), TDD-fixed in the canonical klai-libs package. Suites after this round: deploy/litellm 570 passed, klai-libs/citations 51 passed. |
| 0.6.0   | 2026-08-18 | Mark Vletter | Two follow-ups before merge. **(a) max_chars edge properly investigated** (was flagged "dormant" in 0.5.0 with an incorrect supporting claim): full consumer inventory showed retrieval-api's `synthesis.py` DOES render with `max_chars=24_000` — but that path (dormant `POST /chat`) never consumes `evidence_label_ids`, and the two label consumers (litellm path A via `kb_meta["citation_chunks"] = context_chunks`, partner_chat path B) never truncate, so the edge is untriggerable today. Made that invariant mechanical instead of tribal: docstring warnings on both functions, a pinned known-limitation test in klai-libs/citations, and a source-scan guard (`test_truncated_render_label_guard.py`) failing CI if any production file ever combines a `render_evidence_context(max_chars=...)` call with a label-consuming call. **(b) Sol delta-review on the 0.5.0 fix round** surfaced 6 findings, all verified and fixed: real customer name + production conversation UUID redacted from this spec and both chat.yaml copies (public repo); deterministic stripping extended to SIP Call-IDs/emails (`token@host`) and IPv4 addresses; the digit-run hyphen exception narrowed to uppercase structured codes (`CVE-2026-12345`, `ERR-10060` survive; `ticket-123456`, `trunk-451030015`, `06-12345678` now stripped); the live-eval reserves its own tiny budget slice (0.05 rps/burst 1 via env-setdefault — 90 router + 6 hook + 3 eval = 99 < ~100 rpm even with the eval running as a second process) and paces samples client-side so the limiter never trips the 1.5s timeout; throttle-skipped samples are excluded from the pass-rate and reported separately instead of silently counting as distillation results; canaries without `expected_chunks` now fail loudly at load (vacuous-pass guard). Suite: deploy/litellm 582 passed, klai-libs/citations 52 passed, knowledge-ingest seed-suites 13 passed. |
| 0.7.0   | 2026-08-18 | Mark Vletter | Post-deploy live replay of the original 2026-08-17 incident found the distillation itself working (eval 9/9 top-5, `confidence_band: high` on all 9 legs, reranker scores 0.51–0.98 — this REFUTES the earlier "reranker scores keyword-style distillates poorly" hypothesis, which the 0.4.0-era manual trials never resolved) but the full chat path still refusing: the separately-landed sub-question fan-out (`_split_sub_questions`, PR #1065 work) ran on the RAW pasted email in `deploy/litellm/klai_knowledge.py`, splitting it into 5 `?`-fragment legs, each low-confidence, per-question coverage 0/5 → deterministic Strict refusal fired before the distilled query ever ran as a search leg. Fixed by computing `latest_turn_correspondence` once and reusing it at three points: the existing REQ-1 rewrite call site (no behavior change there), a new gate that skips `_split_sub_questions` entirely when the latest turn is pasted correspondence (`sub_queries=None` in the retrieve body instead of noise fragments), and a new gate on `multi_question` (`bool(sub_questions) or _is_multi_question_query(query)` only when NOT correspondence) — without the second gate, the raw pasted text's own `?` marks kept `_is_multi_question_query(query)` True even with `sub_questions=[]`, still suppressing the deterministic Strict low-confidence refusal and re-injecting the multi-part-question answer guard for what is really one distilled question. `multi_question` was confirmed (by reading `klai_knowledge.py` around the `not multi_question` Strict-refusal-suppression branch and the `multi_question_guard_text` prompt injection) to drive both prompt-instruction text and this refusal-suppression control flow, not just prompt tuning, so gating it was required, not optional. TDD: 3 new tests in `TestPastedCorrespondenceWiring` (`test_pasted_email_with_question_lines_sends_no_sub_queries`, `test_plain_multi_question_still_fans_out` regression guard, `test_pasted_correspondence_multi_question_flag_gated`), all RED before the fix, GREEN after. Suite: deploy/litellm 637 passed (634 baseline + 3). |

## Empirical findings (from live replay against production Mistral + retrieval-api, 2026-08-18)

Three real (not mocked) trials were run against the actual Voys production knowledge base and the actual `mistral-small-2603` model, replaying the 2026-08-17 incident's literal pasted-email text. This is the evidence base for REQ-2's exact wording, and the honest current confidence level for AC-2.

1. **Baseline (no distillation)**: the 5760-char raw pasted email as the retrieval query → `confidence_band: high` but the target article (`01_sip_response_codes.md`) absent from top-10. Confirms the Motivation's vector-dilution hypothesis.
2. **Hand-crafted terse control query** (`"SIP 404 Not Found response code oorzaak"`, not LLM-generated): `confidence_band: high`, target article top-1 at **score 0.974**. Proves the KB has a directly discoverable answer, independent of the LLM step — this is the ceiling REQ-2 is chasing, not the floor.
3. **First LLM distillation attempt** (initial REQ-2 wording — "preserve exact error codes, product names ... verbatim", no identifier exclusion): distilled query retained the raw Call-ID and trunk number verbatim. Retrieval result: `confidence_band: medium`, target article scored **0.089** (rank 5+), a *worse* rank than baseline's absence would suggest, and top-1 was an unrelated article (`FreePBX`, 0.571).
   - **Root cause, confirmed by a direct A/B on retrieval-api** (same underlying question, with vs. without the Call-ID/trunk-number tokens): with identifiers, top score 0.571 on the wrong article; without them, top scores 0.847 / 0.736 on directly relevant SIP articles. Unique per-incident identifiers (Call-IDs, specific trunk/account numbers) never appear in KB articles and measurably pull the query embedding away from the general topic.
   - **Action**: REQ-2 revised to explicitly instruct dropping unique per-incident identifiers while still preserving reusable domain terminology (error codes, protocol names) verbatim. Shipped in `_PASTED_CORRESPONDENCE_DISTILL_BLOCK` / `_PASTED_CORRESPONDENCE_DISTILL_TASK`.
4. **Second LLM distillation attempt** (post identifier-exclusion fix): the model dropped the Call-ID as instructed, but phrased the output as a full grammatical Dutch question with markdown bold (`"Wat veroorzaakt de **404 Not Found** met **Q.850;cause=1** bij ... trunk 451030015 ... na succesvolle sessie-opzet?"`) and still retained the trunk number. Retrieval result: **worse** than attempt 1 — `confidence_band: low`, target article absent from top-5 entirely, top score only 0.261.
   - **Hypothesis**: full-sentence question phrasing (question words, connective grammar, markdown syntax) reintroduces token-level noise relative to the terse keyword-style control query that scored 0.974. Not yet confirmed by a clean A/B (blocked by rate-limiting — see below).
   - **Action**: REQ-2 revised again to explicitly request "a short KEYWORD-STYLE phrase (like a search-engine query), NOT a full grammatical question ... no markdown formatting". Shipped in the same two constants. Unit-tested (instruction text presence); **not yet re-verified live** against production Mistral + retrieval-api.
5. **Attempted re-verification (3 trials)**: all three hit `429 Too Many Requests` on `api.mistral.ai` — the SAME `klai-fast`/Mistral quota pressure observed independently earlier in production logs that day, not caused by this testing. Each trial correctly fell back to the raw (undistilled) query per REQ-3's fail-open contract — a valid, if unplanned, confirmation that fail-open holds under real rate-limit conditions. No further live calls were made to avoid competing with production traffic for the same shared monthly-capped quota.

**Net assessment**: the underlying diagnosis (query noise dilutes retrieval; a clean, terse, identifier-free query recovers the answer) is solidly proven. The LLM-distillation *mechanism*'s reliability at producing that clean terse query is not yet conclusively proven — two real attempts each surfaced a genuine, distinct failure mode, each fixed in the instruction text, with the fix for the second not yet re-verified live. This is exactly the class of question the `chat.yaml` eval harness (REQ-6) is designed to answer with statistically meaningful repeated sampling rather than 1-shot manual trials; closing AC-2 through that harness is the recommended next step over further manual production calls.

---

# SPEC-RAG-CORRESPONDENCE-DISTILL-001: Query distillation for pasted correspondence

## Summary

When a user pastes third-party correspondence (a customer email, a support ticket, a forwarded thread) into chat, the litellm hook today sends that text — largely unmodified — as the retrieval query. Long, noisy text (mail headers, CC lists, names, dates, signatures, "RE:" chains) dilutes the embedding signal enough that a knowledge-base article which *does* answer the question fails to surface, even though the exact same question phrased as a short search query retrieves it with high confidence.

This SPEC adds a conditional prompt variant to the existing query-rewrite call (`klai_kb_query_rewrite.py`, introduced by SPEC-RAG-QUERY-REWRITE-001, already extended once by SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-5 brand-bridging): when the pasted-correspondence detector (`klai_pasted_correspondence.py`, PR #1059) fires on the current turn, the rewrite prompt distills the correspondence into a compact, noise-free search query before it reaches the embedder — instead of forwarding the raw text.

This is a **prompt-variant addition to an existing call site**, not a new LLM round-trip: the same `rewrite_query` / `rewrite_and_classify` call already receives the full pasted text as input today. No new latency dimension, no new failure mode beyond what the existing rewrite already fails open on.

## Motivation

### The incident

2026-08-17, Voys production, conversation ID known internally; redacted from this public spec (a Voys support agent, Strict mode): the user pasted two customer emails (~5760 characters total: headers, CC addresses, names, a "RE:" subject chain, and the actual technical question — a VoIP trunk failing with SIP `404 Not Found` on outbound calls) and asked "wat denk jij dat er niet goed is?". Retrieval returned low-confidence, mostly-irrelevant chunks; the chat correctly refused per SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001's deterministic low-confidence gate.

### Root-cause verification (this session, direct production queries)

Three direct queries against the live Voys knowledge base (org `368884765035593759`, via retrieval-api `/retrieve`, bypassing the chat layer) isolated the cause:

1. A **gerichte** query — `"SIP 404 Not Found response code oorzaak"` — returned `confidence_band: high` with the top hit (score **0.974**) on `01_sip_response_codes.md`, a chunk that states plainly: *"404 | Not Found | Gebruiker/toestel bestaat niet, of extensie niet gevonden"*. This is the correct, specific answer.
2. The **literal pasted-email text** (5760 chars) as the query returned `confidence_band: high` but with a *different* top-10: `01_sip_response_codes.md` did not appear at all. Broader, less specific chunks displaced it.
3. The **full hook replay** (same text, through the actual pre-call hook with conversation history and the existing coreference-only rewrite) degraded further to `confidence_band: low`, chunks_injected=3, and the deterministic Strict refusal fired (SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2 working exactly as designed — correctly refusing on genuinely weak retrieval).

The knowledge base had the answer. The retrieval **query construction** discarded it before the embedder ever saw a clean signal.

### This is a documented failure class, not a one-off

- **Vector dilution / vector bottleneck**: a dense embedding vector has finite representational capacity; concatenating a short technical question with several times more header/signature/prose noise compresses the relevant signal against irrelevant content before the query-chunk comparison happens ([Lost in a Single Vector, arXiv:2606.18781](https://arxiv.org/pdf/2606.18781); [When More Documents Hurt RAG, arXiv:2606.11350](https://arxiv.org/html/2606.11350); [Pooling and Semantic Shift, arXiv:2603.21437](https://arxiv.org/pdf/2603.21437)).
- **Industry precedent**: support-AI systems handling the identical "customer pastes a long email" pattern extract the core intent/details *before* the knowledge-base lookup, rather than embedding the raw pasted text ([Enhancing RAG Systems: Keyword Extraction and Parallel Search](https://medium.com/@vishnuvskvjl/enhancing-rag-systems-a-novel-approach-with-keyword-extraction-and-parallel-search-b526dafbb468); [Query rewriting for RAG — Meilisearch](https://www.meilisearch.com/blog/query-rewrite-rag)).
- HyDE (hypothetical-document generation) was evaluated and rejected for this specific gap: it adds 25–60% latency on small LLMs and carries its own hallucination risk ([HyDE — Emergent Mind](https://www.emergentmind.com/topics/hypothetical-document-embeddings-hyde)); a bounded, guard-railed distillation of text the model already receives is cheaper and lower-risk.

### Why this compounds with, not replaces, existing work

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 makes the system **fail safely** when retrieval is weak (no hallucination). This SPEC makes retrieval **not need to be weak** in the specific case where the query itself is the problem. The two are complementary: without this SPEC, every pasted-correspondence conversation with an answerable question still hits the (correct, but unhelpful) low-confidence refusal.

## Scope

### In scope

**`deploy/litellm/klai_kb_query_rewrite.py`**

- `rewrite_query()` and `rewrite_and_classify()` gain a keyword-only parameter `pasted_correspondence: bool = False`.
- When `True`, both prompt templates (`_QUERY_REWRITE_PROMPT`, `_QUERY_REWRITE_AND_CLASSIFY_PROMPT`) get an additional instruction block (see REQ-2) inserted the same way REQ-5's brand-bridging block was added — conditional text within the existing prompt, not a second LLM call.
- The existing destructive-rewrite guard (`_apply_rewrite_guard` / `_rewrite_preserves_current_query`) applies unchanged: a distilled query that shares no salient token with the raw pasted text is rejected and the raw text is used as-is (fail-open, same as today).
- The existing char budget (`rewritten[:500]`, "Maximum 200 characters" prompt instruction) applies unchanged.

**`deploy/litellm/klai_knowledge.py`**

- The call site (`_rewrite_and_classify(query, conversation_history, trees_for_classify)`) passes `pasted_correspondence=_latest_user_turn_has_correspondence(messages)` — reusing the exact detector function already shipped in PR #1059 for the Strict-mode user-content gate, so "does the CURRENT turn contain pasted correspondence" has one definition, not two.

**Telemetry**

- The existing `query_rewrite` log event gains a `pasted_correspondence_detected: bool` field. Raw query/rewritten-query text remains gated by the existing `telemetry_level` contract (SPEC-PRIVACY-QUERY-SHADOW-001) — this SPEC does not change what text is logged, only adds one boolean.

**Eval harness**

- New `pasted_correspondence` mix category in `klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml`: synthesized (anonymized — no real customer names/companies), covering the incident's failure shape plus a shorter pasted-ticket variant and a negative control.

### Out of scope

- A second, dedicated LLM call for distillation (two-call pattern). Rejected: the existing call already receives the full text as input; a prompt-variant costs ~150-200 extra prompt tokens (same order of magnitude as REQ-5's brand-bridging addition), a second call would double rewrite latency for every correspondence turn.
- HyDE-style hypothetical-answer generation. Rejected per Motivation — latency and hallucination risk with no evidence it outperforms bounded extraction here.
- Multi-query fan-out (generate N distilled queries, run N retrievals, fuse with RRF). Deferred: adds retrieval-api round-trips and fusion complexity; ship the single-query distillation first and measure whether the gap remains before reaching for this.
- Retroactively re-running distillation on already-sent turns / conversation history. Out of scope — this SPEC only changes how the CURRENT turn's query is built.
- Changing the pasted-correspondence *detector* itself (`klai_pasted_correspondence.py`). That module is out of scope here; this SPEC only consumes its existing output.
- Non-English/non-Dutch distillation quality tuning beyond what the existing rewrite prompt already provides (same-language-as-input contract, unchanged).

## Functional Requirements (EARS)

### REQ-1 — flag threading (ubiquitous)

**THE litellm-hook SHALL** pass `pasted_correspondence=_latest_user_turn_has_correspondence(messages)` into `rewrite_and_classify()` (and, transitively, into `rewrite_query()` on its no-taxonomy fallback path) at the existing call site. No new call site, no new function signature beyond the one added keyword-only parameter per function.

### REQ-2 — distillation instruction block (event-driven)

**WHEN** `pasted_correspondence=True`, **THE rewrite prompt SHALL** include an instruction block directing the model to:

- distill the pasted correspondence into a compact, self-contained search query describing the core technical/support question or problem;
- preserve reusable domain terminology verbatim (error codes, protocol/status codes, product and technology names) but explicitly NOT preserve unique per-incident identifiers (Call-IDs, specific account/trunk/ticket numbers, IP addresses, phone numbers) — added after finding 3 below showed these measurably hurt retrieval;
- output a short keyword-style phrase, like a search-engine query — explicitly NOT a full grammatical question, with no markdown formatting — added after finding 4 below showed a full-sentence, markdown-formatted output scored worse than a terse control query;
- drop mail headers, sender/recipient names and addresses, dates, greetings, signature blocks, and "RE:"/"FW:" subject-chain noise;
- stay within the existing 200-character instruction and 500-character hard cap — unchanged from today.

**WHEN** `pasted_correspondence=False` (default), **THE rewrite prompt SHALL** remain byte-identical to its current form. This is the primary regression guard: every non-correspondence turn must be provably unaffected.

### REQ-2a — deterministic enforcement, not just instruction (ubiquitous)

Prompt instructions are requests the model can partially ignore, not guarantees — finding 4 below showed the model dropping a Call-ID as instructed while simultaneously ignoring "no markdown" and "no long identifiers" for a trunk number in the SAME output. **THE distillation path SHALL** therefore also enforce, in code, on every successful distillation (never on the destructive-guard raw-text fallback, never on ordinary non-correspondence rewrites):

- strip markdown emphasis characters (`*`, `_`, `` ` ``);
- strip digit runs of 5 or more consecutive digits (SIP/HTTP status codes are always exactly 3 digits; any longer run is almost certainly a unique per-incident identifier, not reusable vocabulary).

This mirrors the existing `klai_citations.strip_model_citation_artifacts` pattern already used elsewhere in this codebase for the identical class of problem: ask the model AND verify deterministically, never ask alone.

### REQ-3 — fail-open (ubiquitous)

**THE distillation prompt-variant SHALL** use the exact same fail-open contract as the existing rewrite: on timeout, non-200, empty response, or destructive-rewrite-guard rejection, the raw pasted text SHALL be used as the retrieval query unchanged. No new failure mode is introduced; `meta["skipped"]` reason codes are unchanged in shape (existing consumers of `rewrite_decided()` need no changes).

### REQ-4 — destructive-rewrite guard still applies (ubiquitous)

**THE existing `_apply_rewrite_guard` / `_rewrite_preserves_current_query` check SHALL** run unmodified against distilled output. A distillation that drops every salient token from the raw pasted text (e.g. an over-aggressive rewrite that loses the actual error code) is rejected the same way any other destructive rewrite is today, and the raw text is used instead.

### REQ-5 — telemetry (ubiquitous)

**THE `query_rewrite` log event SHALL** include `pasted_correspondence_detected: bool`. Raw and rewritten query text visibility remains governed by the existing `telemetry_level` (`off`/`shadow`/`full`) contract — this SPEC does not widen or narrow what text is logged.

### REQ-6 — eval canaries (ubiquitous)

**THE `chat.yaml` suite SHALL** include a `pasted_correspondence` mix with at minimum:

- 1 query synthesizing the incident's shape (a multi-paragraph pasted email with headers/names/signature wrapping a specific technical question with a discoverable `expected_chunks` answer) — anonymized, no real customer names or company identifiers;
- 1 shorter pasted-ticket variant (a forwarded 3-4 line support ticket, still containing header noise);
- 1 negative-class control: a plain, short question with NO pasted correspondence, asserting the rewrite is unaffected (regression guard for REQ-2's "unchanged when False" clause).

Each canary with a discoverable answer MUST have `expected_chunks` populated per the existing suite contract.

## Non-Functional Requirements

- **Latency**: no added round-trip. The distillation prompt-variant adds ≤200 tokens to the SAME `rewrite_and_classify`/`rewrite_query` call already made for every turn with history or taxonomy in scope. Rewrite-call p95 MUST NOT exceed the existing `QUERY_REWRITE_TIMEOUT` (1.5s) budget — same ceiling as today, no change to the timeout value.
- **Cost**: per-turn added token cost ≤200 tokens on `klai-fast` (Mistral small) — negligible (<€0.0005/call), same order of magnitude as SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-5.
- **Fail-open**: every new code path degrades to current (pre-SPEC) rewrite behavior on any failure. No new hard-failure mode.
- **Backwards compatibility**: `pasted_correspondence` defaults to `False`; existing callers of `rewrite_query`/`rewrite_and_classify` that do not pass it are unaffected (Path B/C do not call this module at all — it is path-A-only infrastructure, same scope as the module already has today).
- **Multi-tenant**: applies uniformly across all orgs — no Voys-specific behavior. The Voys incident is the reproduction case, not the target.

## Acceptance Criteria

| AC ID | Test | Expected outcome |
|-------|------|-------------------|
| AC-1 | Replay the 2026-08-17 Voys incident's pasted-email text directly against `retrieval-api /retrieve` with the CURRENT (pre-SPEC) rewrite output as query | `01_sip_response_codes.md` absent from top-10 (reproduces the documented baseline failure) |
| AC-2 | Same replay, with distillation ON — call `rewrite_and_classify(raw_query=<pasted email text>, history=<incident history>, pasted_correspondence=True)`, then `/retrieve` with the distilled output | `01_sip_response_codes.md` (or an equally-specific SIP-error-code chunk) present in top-5, `confidence_band` >= `medium` |
| AC-3 | Unit test: `pasted_correspondence=False` prompt output is byte-identical to the current `_QUERY_REWRITE_PROMPT`/`_QUERY_REWRITE_AND_CLASSIFY_PROMPT` formatted strings | Exact string equality — proves zero behavior change for the non-correspondence path |
| AC-4 | Unit test: destructive-rewrite guard (REQ-4) rejects a synthetic distillation that drops the input's only salient token (e.g. an invented "SIP 404" input distilled to an unrelated topic) | `meta["skipped"] == "destructive_rewrite"`, raw text returned unchanged |
| AC-5 | Unit test: distillation output for a synthetic pasted-email fixture preserves at least one verbatim technical token present in the source (e.g. an error code or product name) | Assertion passes on 3+ synthetic fixtures covering different technical domains |
| AC-6 | `chat.yaml` eval run, `pasted_correspondence` mix (REQ-6) | All 3 canaries score `context_precision >= 0.5`; negative-class control shows zero prompt diff vs. baseline (AC-3 covers the mechanism, this covers the eval-level outcome) |
| AC-7 | Full `chat.yaml` suite (all 30 pre-existing queries), before vs. after this SPEC | Aggregate `context_precision` / `context_recall` MUST NOT regress by more than 0.02 (same regression bar as SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 AC-5) |
| AC-8 | Rewrite-call latency, `pasted_correspondence=True` turns, p95 over first 24h post-deploy | ≤ existing `QUERY_REWRITE_TIMEOUT` (1.5s); no new timeout-rate increase vs. pre-SPEC baseline |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Distillation over-compresses and drops a technical detail that mattered (e.g. a specific IP address or Call-ID needed for exact matching) | medium | medium | REQ-2 explicitly instructs verbatim preservation of error codes/product names/terminology; REQ-4's guard catches total salient-token loss. Residual risk: a *partially* lossy distillation that keeps some but not all relevant terms — not caught by the guard. Mitigation: AC-5's multi-fixture test set should include at least one case with a specific identifier (Call-ID-shaped string) to probe this; if it fails, tighten REQ-2's instruction wording, do not add a second guard layer pre-emptively. |
| Distillation prompt drifts toward summarizing SENTIMENT/OPINION content from the correspondence (e.g. the sender's own diagnosis) into the search query, re-introducing a milder form of the claims-as-fact problem PR #1059 fixed at the answer layer | low | low | REQ-2 scopes the instruction strictly to "the technical/support question or problem" — not "what the correspondence concludes". This is a retrieval-query construction step, not an answer-generation step; the model never sees the distilled query as something to answer from, only as a search string. No user-facing text is affected. |
| The 500-char post-truncation cap (existing, unchanged) still truncates mid-distillation if the model ignores the 200-char instruction on a very long correspondence | low | low | Pre-existing behavior, not introduced by this SPEC. If AC-2/AC-6 show this in practice, it is a prompt-instruction-strength issue addressable by prompt iteration, not an architecture change. |
| Eval canaries embed recognizable real-customer content if not properly anonymized | low | medium | REQ-6 explicitly requires anonymization; PR review checklist item — no real names, company names, or verbatim customer text in `chat.yaml`. |

## Sources

Research underpinning the requirements:

- [Lost in a Single Vector: Improving Long-Document Retrieval with Chunk Evidence Aggregation — arXiv:2606.18781](https://arxiv.org/pdf/2606.18781) — vector dilution on long inputs (Motivation).
- [When More Documents Hurt RAG: Mitigating Vector Search Dilution — arXiv:2606.11350](https://arxiv.org/html/2606.11350) — vector-bottleneck failure mode (Motivation).
- [Pooling and Semantic Shift: The Fundamental Challenges in Long Text Embedding and Retrieval — arXiv:2603.21437](https://arxiv.org/pdf/2603.21437) — semantic-shift-under-concatenation evidence (Motivation).
- [Enhancing RAG Systems: A Novel Approach with Keyword Extraction and Parallel Search](https://medium.com/@vishnuvskvjl/enhancing-rag-systems-a-novel-approach-with-keyword-extraction-and-parallel-search-b526dafbb468) — industry pattern: extract before embed (Motivation, REQ-2 shape).
- [Query rewriting for RAG: how to improve retrieval accuracy — Meilisearch](https://www.meilisearch.com/blog/query-rewrite-rag) — query-side transformation as the correct layer for this class of fix.
- [HyDE: Hypothetical Document Embeddings — Emergent Mind](https://www.emergentmind.com/topics/hypothetical-document-embeddings-hyde) — latency/hallucination tradeoff, basis for Out-of-scope HyDE rejection.

Internal references:

- [deploy/litellm/klai_kb_query_rewrite.py](deploy/litellm/klai_kb_query_rewrite.py) — `rewrite_query`, `rewrite_and_classify`, `_apply_rewrite_guard`, `_QUERY_REWRITE_PROMPT`, `_QUERY_REWRITE_AND_CLASSIFY_PROMPT`: all requirements land here.
- [deploy/litellm/klai_knowledge.py](deploy/litellm/klai_knowledge.py) — call site for REQ-1.
- [deploy/litellm/klai_pasted_correspondence.py](deploy/litellm/klai_pasted_correspondence.py) — `latest_user_turn_has_correspondence`, the detector this SPEC's flag reuses (PR #1059).
- SPEC-RAG-QUERY-REWRITE-001 (historical — SPEC removed in repo cleanup 2026-08-18) — original rewrite-call SPEC this extends.
- SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 (historical — SPEC removed in repo cleanup 2026-08-18) — REQ-5 brand-bridging: direct precedent for a conditional prompt-variant on the same call site; also the low-confidence gate this SPEC reduces unnecessary triggering of.
- [klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml](klai-knowledge-ingest/knowledge_ingest/eval/suites/chat.yaml) — REQ-6 destination.
- Production replay evidence: retrieval-api `/retrieve` direct queries against org `368884765035593759`, 2026-08-18 (this session) — see AC-1/AC-2.
