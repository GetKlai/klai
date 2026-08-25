---
id: SPEC-PRIVACY-MISTRAL-PII-001
version: "1.0.0"
status: draft
created: 2026-08-20
updated: 2026-08-25
author: Mark Vletter
priority: high
related:
  - SPEC-PRIVACY-QUERY-SHADOW-001 (owns telemetry_level off/shadow/full; REQ-8 must not widen what is logged)
  - SPEC-SHIELD-001 (owns the working BSN elfproef this SPEC reuses as a Presidio recognizer)
  - SPEC-CHAT-GUARDRAILS-001 (shipped LLM-safety layer; orthogonal — it has no PII category)
roadmap: docs/architecture/knowledge-rag-improvement-plan.md
---

# HISTORY

| Version | Date       | Author       | Change |
|---------|------------|--------------|--------|
| 1.0.0   | 2026-08-25 | Mark Vletter | **General availability.** `KLAI_PII_ENFORCE_ORG_IDS=*`: every request carrying an `org_id` is enforced, replacing the one-named-org rollout that ran from 2026-08-21. The allowlist gained a wildcard rather than an enumerated list of org_ids, because an enumerated list goes stale at the next signup and does so in the unsafe direction — the new tenant would be the uncovered one. Neither of the two guarantees the staged rollout was built on changes: an absent or empty value still means no orgs at the hook, and a request with no `org_id` is still never enforced. Where that stops: Compose supplies `*` when the host variable is unset, so the deployment-level off switch is an explicitly empty `KLAI_PII_ENFORCE_ORG_IDS=`, not a deleted one. Capacity was measured before flipping rather than assumed: presidio-analyzer sits at 214MiB/512MiB and 0.01% CPU while the Phase 2 observer already calls `/analyze` for every tenant, so enforcement roughly doubles a volume of ~3 calls per minute. The residual risk is availability, not capacity — REQ-10 fails closed, so the analyzer being down is now a chat outage for every tenant instead of one; `container_down` in `infra-rules.yaml` already covers `klai-core-.*`. Also records what GA does NOT cover: the `org_id=None` path carried 1619 of 1650 observed detections over 2026-08-18 to 2026-08-25, against 29 on attributable tenant chat, and masking cannot reach it. |
| 0.9.0   | 2026-08-21 | Mark Vletter | Phase 3 merged and deployed **inert** (`adde54046`), analyzer image carrying the four system-review fixes pinned (`060459e28`). Verified on core-01 rather than assumed: `KLAI_PII_ENFORCE=false`, seven Phase 3 modules mounted with no import errors, the observer still emitting counts and changing nothing, and all four recogniser fixes confirmed against the running analyzer — `Factuurdatum 20200201` no longer masked, `bearer` prose no longer a SECRET, `2026 en` no longer a postcode, Rotterdam `010` still detected. Everything between LiteLLM and Mistral is now built and in place; the only remaining step to activate is the flag. |
| 0.8.0   | 2026-08-20 | Mark Vletter | **System review of the merged stack** — the first review of the pieces together rather than each PR alone, and it found what per-diff review structurally cannot. REQ-8's overlap rule was wrong: it said "drop any span *contained* in one already taken", derived from the single IBAN⊃PHONE example, but the recogniser set produces pairs where the higher-scoring span sits INSIDE the lower-scoring one (`NL_BSN` 1.00 inside `NL_BTW` 0.70; a JWT span inside a Bearer span). A containment-only rule accepts both and corrupts the text. Now: drop any **overlap**, and never-restore entities win exact ties, because `NL_BSN` and `NL_KVK` can produce a byte-identical span with an identical score and a tie must not decide whether a value becomes restorable. The Phase 3 implementation already dropped overlaps — the defect was in this document, not the code. Three irreversible-false-positive fixes shipped alongside: 8-digit BSN now needs context (9.1% of `YYYYMMDD` dates passed the elfproef and were destroyed unrestorably), the `Bearer` pattern is anchored to an `Authorization:` header (it matched two words of ordinary Dutch prose), and the postcode letter pair is case-sensitive again (registry `IGNORECASE` made `2026 en` a postcode). A1 marked partly falsified. |
| 0.7.0   | 2026-08-20 | Mark Vletter | Maintenance pass — the SPEC is a working document, not a record of what we once believed. Adds a **Status** table (per-phase state with the commit that proves it) so this doubles as the progress overview, plus a short "what this SPEC got wrong, and how it was caught" table: three of four errors were found by measuring, not reading, and two had already been written down as confident conclusions. Removed three claims that are now false: that the LiteLLM integration is configuration rather than code (REQ-5 and REQ-0a each measured otherwise), that `PHONE_NUMBER` is a stock built-in enabled via a YAML region key (the loader drops it — it is `NLPhoneRecognizer` now), and assumption A6 (struck through with the real root cause and the fixing commits). Re-validated every `file:line` anchor **semantically**, not just for range: the `MISTRAL_API_KEY` anchor had drifted from 388-389 to 421-422 as compose grew, still pointing inside the file and therefore passing a naive check while being wrong. |
| 0.6.0   | 2026-08-20 | Mark Vletter | **Measurement gates removed; build-then-observe instead.** Klai's current traffic volume is far too low to produce a statistically meaningful 30-day, three-tenant, hand-annotated sample, so requiring one was not caution — it was a stall dressed as rigour. The workflow is: build on stated assumptions, ship, watch it in practice, correct. Every assumption that replaced a measurement is written down under "Assumptions" so it can be checked against reality rather than forgotten. Phase 3's design is also settled: REQ-0a proved the native restore path unusable for streaming, so Klai owns mask, map and restore end to end, keyed by `litellm_call_id` in process memory. Everything ships behind `KLAI_PII_ENFORCE`, default **off**, so Phase 3 can land, deploy and be exercised in production while inert — activation stays a separate, deliberate flip. |
| 0.5.0   | 2026-08-20 | Mark Vletter | **Phase 0 ran on core-01 and answered both questions.** REQ-0a is negative for streaming: `output_parse_pii` restores correctly non-streaming but returns an empty token map on the streaming path — the second failure shape in litellm#6247, still live on v1.96.2, and NOT the Anthropic-native bug 0.2.0 dismissed from reading. Both Klai chat paths stream, so REQ-8's restore moves to our own post-call hooks. REQ-0b: the verbatim-token instruction takes `PHONE_NUMBER` survival from 58.3% to 95.8%, so it is mandatory rather than advisory. Two gaps the run exposed: six of thirty Dutch phone numbers were never detected despite `supported_regions: [NL]` (a detection gap, separate from survival), and `PERSON` is **unmeasurable** today because REQ-2 disables SpacyRecognizer and GLiNER is not deployed — so REQ-0b's PERSON half must be re-run after REQ-9, and PERSON must not be enabled before that. |
| 0.4.1   | 2026-08-20 | Mark Vletter | REQ-6 tightened after the Phase 2 delta review. The language label is now derived from the latest user turn rather than the whole payload — the KB context block is English-structured by design and dwarfs the question, so detecting on combined text would have labelled a Dutch question `en` on essentially every RAG request and quietly invalidated REQ-2's per-language comparison. Records the honest limitation that the observer uses a stopword heuristic rather than the canonical lingua detector, because `lingua-language-detector` is not in the stock litellm image, with the escalation path if Phase 2 data proves too noisy. |
| 0.4.0   | 2026-08-20 | Mark Vletter | REQ-5 corrected before it was built. It specified the native guardrail in `mode: "logging_only"` as the shadow-measurement mechanism; that mode masks what goes to **observability** and sends the payload to the provider **unmasked** — inverted for our purpose, and LiteLLM's Presidio guardrail has no detect-only mode at all. Phase 2 is now a read-only observer callback (`klai_pii_observe.py`) that calls the analyzer, emits REQ-6's counts, returns the payload unchanged, never calls the anonymizer, runs out of band so it cannot add latency or fail a request, and is deleted by the Phase 3 PR. The Phase 0 experiment guardrail stays registered so the REQ-0a/REQ-0b harness remains runnable. Also records what Phase 0 and Phase 1 actually shipped: both merged (`d55d6adeb`, `d2aa35fd2`), nine recognizers verified loaded across en/nl/de on core-01 with spaCy disabled per language, and the Phase 0 harness still **unrun**. |
| 0.3.0   | 2026-08-20 | Mark Vletter | Language-agnosticism corrected. 0.2.0's REQ-2 pinned `presidio_language: "nl"` and dismissed mixed traffic as a known limitation — wrong for a product that ships a language detector, language-neutral KB context, and per-language correctness monitoring (SPEC-RAG-MULTILINGUAL-CHAT-001). REQ-2 now makes detection language-agnostic **by construction**: every REQ-3 entity is regex-plus-checksum and therefore jurisdiction-specific rather than language-specific, registered across all languages; `PERSON` is the only language-sensitive entity and uses multilingual GLiNER instead of a per-language spaCy pipeline. Net effect is a simplification — no spaCy model is loaded at all, which also removes the per-language model matrix and the memory it would cost. Phase 2 telemetry now carries detected language so per-language recall is measured, not assumed. Phase 0 keeps the stock English engine as an explicit, bounded exception because it measures the restore mechanism, not detection quality. |
| 0.2.0   | 2026-08-20 | Mark Vletter | Reversibility reinstated after correcting a misread. 0.1.0 excluded reversible pseudonymisation because `output_parse_pii` "does not un-mask streaming" — that bug (#22821) is **Anthropic-native-specific and closed**, and we route Mistral over the OpenAI-compatible path, so it never applied to us. Decision is now hybrid: irreversible for the never-return set (`SECRET`, `NL_BSN`), reversible for the set the drafting use case needs back (names, phone, email, IBAN, KvK, BTW, postcode). Adds Phase 0 (REQ-0a, REQ-0b) — prove the restore path on `v1.96.2` and measure Dutch token-survival rate **before** building the recognizer pack, since the remaining uncertainty (#6247, closed as stale) is not answerable by reading. Adds REQ-11: the placeholder map is request-scoped and never persisted, because a shared map is the one failure mode that is worse than masking. GLiNER moves from shadow-only to viable, on the reasoning that restore makes precision non-binding. Adds a Deployment section: two CPU services on core-01, no secrets, no GPU. |
| 0.1.0   | 2026-08-20 | Mark Vletter | Initial draft. Answers two questions: are Presidio + GLiNER still the right tools in August 2026, and how do we wire them into the calls that go to Mistral. Research re-run 2026-08-20; the earlier architecture note (`klai-knowledge-architecture.md:1661`) named the right framework but predates three facts that change the implementation: Presidio ships **no Dutch recognizers at all**, LiteLLM has since gained a **native Presidio guardrail** that removes the need for custom hook code, and its reversible `output_parse_pii` path is **broken on streaming**, which decides pseudonymisation versus anonymisation for us. |

---

# SPEC-PRIVACY-MISTRAL-PII-001: PII removal on the Mistral call path

## Summary

Every LLM call in the platform leaves through one container: `MISTRAL_API_KEY` is declared
once, in the `litellm` service block (`deploy/docker-compose.yml:421-422`). No service holds
a provider key of its own and no code calls `api.mistral.ai` directly.

This SPEC puts PII removal on that boundary using Presidio, deployed as two self-hosted
containers. Klai owns the wiring: measurement (Phase 2) needs a read-only observer because
the native guardrail has no detect-only mode (REQ-5), and enforcement (Phase 3) owns mask,
map and restore end to end because REQ-0a **measured** the native restore path returning an
empty token map on streaming — which is every Klai chat request. It adds the Dutch
recognizer pack that Presidio does not ship, and it splits
handling by intent: credentials and BSN are removed and never restored, while names, phone
numbers and account identifiers are tokenised on the way out and restored on the way back —
so drafting an email still works while Mistral never receives the real values.

Nothing here touches PII at rest, and nothing here depends on that work.

## Status — 2026-08-21

**Maintenance rule.** Update this document whenever a phase lands, a measurement lands, or a
claim in it turns out to be false — and *delete* what is no longer true rather than layering
a correction on top. A SPEC that records what we used to believe is worse than no SPEC,
because it is read as current. Re-check `file:line` anchors semantically when you touch it: a
line number can stay inside the file and still point at the wrong thing, which is how the
`MISTRAL_API_KEY` anchor silently drifted by 33 lines.

Kept current as each phase lands. If this table disagrees with the requirement text below,
the table is right and the text is stale — say so rather than working around it.

| Phase | State | Evidence |
|---|---|---|
| **0** — prove the restore path | **Done, answered** | Ran on core-01. `output_parse_pii` restores non-streaming, returns an **empty map on streaming**. Verbatim-token instruction takes `PHONE_NUMBER` survival 58.3% → 95.8%. See the RESULT block under Phase 0 |
| **1** — recognizer pack | **Live** | `d55d6adeb`, `d2aa35fd2`. Nine recognizers loaded across en/nl/de, spaCy disabled per language, verified in production logs. Rotterdam fix `4af66f4e0` + `b5b592051`, verified live |
| **2** — read-only observer | **Live, measuring** | `c6dd946e4`. Real `pii_observed` events in VictoriaLogs, including `org_id=None` requests the existing hook skips. Changes no payload |
| **3** — mask + restore | **Live, all tenants** | `adde54046` + `060459e28` shipped it inert; one named org from 2026-08-21; `KLAI_PII_ENFORCE_ORG_IDS=*` from 2026-08-25. Klai owns mask/map/restore because Phase 0 measured the native path unusable for streaming |
| **4** — `PERSON` via GLiNER | **Blocked, deliberately** | No PERSON detector is deployed at all (REQ-2 disables SpacyRecognizer). REQ-0b's PERSON half is unmeasurable until GLiNER lands |

**What is masked today.** `SECRET` and `NL_BSN`, for every request that carries an `org_id`.
The seven `RETURN_SET` entities remain per-org and default off, so a tenant that has opted
into nothing gets those two and nothing else. Defaulting the return set on is a separate
decision, not implied by general availability.

**What is still never masked.** A request with no `org_id` — the widget and internal
service-key paths, including knowledge-graph extraction and query rewriting. Measured over
2026-08-18 to 2026-08-25, that path carried 1619 of the 1650 observed detections, against 29
on attributable tenant chat. General availability therefore covers the chat call and not the
larger flow behind it; closing that is its own work, and the gap should not be described to
customers as covered.

### What this SPEC got wrong, and how it was caught

Recorded because the pattern matters more than the individual errors: **three of the four
were caught by measuring, not by reading**, and two of them had already been written down as
confident conclusions.

| Claim | Reality | Caught by |
|---|---|---|
| `presidio_language: "nl"` is the right approach (0.2.0) | Wrong for a language-agnostic product; detection is jurisdiction-specific and needs no language at all | Review of the product's own multilingual contract |
| Streaming restore is fine on our path (0.2.0) | Empty token map on streaming, still live on v1.96.2 | The Phase 0 run |
| `logging_only` is a shadow-measurement mode (0.4.0) | It masks observability and sends the payload **unmasked** to the provider | Reading the docs before building |
| Six undetected phone numbers are a format gap (A6) | The YAML region key was silently ignored; Dutch detection worked by accident | Introspecting the running container |

## Motivation

### Are the previously chosen tools still the right ones?

`docs/architecture/klai-knowledge-architecture.md:1661` names **Presidio + GLiNER
(`gliner_multi-v2.1`)**. Research re-run on 2026-08-20 says: the framework choice is right,
the NER choice is right but belongs behind a gate, and one thing that has changed since that
note was written now does most of the work for us.

| Tool | Verdict | Evidence |
|---|---|---|
| **Presidio** | **Adopt** as the framework | MIT. Moved from Microsoft to the independent Data Privacy Stack org in 2026, actively maintained; images now `ghcr.io/data-privacy-stack/presidio-*`, the old `mcr.microsoft.com` tags are frozen. LiteLLM ships a native Presidio guardrail, but **the integration turned out to be code, not configuration** — its `logging_only` mode is inverted for measurement (REQ-5) and its restore path returns an empty token map on streaming (REQ-0a). Presidio itself is still the right framework; the LiteLLM glue is ours |
| **Presidio's default detection** | **Do not rely on it** | REDACT (25 languages, [arXiv:2606.19881](https://arxiv.org/abs/2606.19881), 18 Jun 2026) measures the rule-based detector at F1 0.195 overall and **recall 0.07 on the highest-sensitivity categories**. The framework is good; the stock recognizers are not a control |
| **Dutch coverage** | **Must be built** | Presidio's `default_recognizers.yaml` ships 73 recognizers with country packs for DE, ES, IT, PL, FI, SE, UK, US, AU, IN, ZA, KR, TH, TR, CA, NG, PH — **and nothing for the Netherlands**. `nl` is not in `supported_languages`. There is no BSN, KvK or BTW recognizer to enable. A June 2026 release added a German pack and a Philippine TIN; still no Dutch |
| **GLiNER `gliner_multi_pii-v1`** | **Adopt for names, gated** | License verified directly on the model card: **apache-2.0** — commercially usable. REDACT F1 0.320, better than Presidio's default but with a documented high-recall/low-precision profile. Presidio supports it as a pluggable NER engine |
| **Piiranha-v1** | **Reject** | License verified directly on the model card: **cc-by-nc-nd-4.0**. Non-commercial, no derivatives. It explicitly supports Dutch and would otherwise be attractive — this is a hard blocker for a commercial product, and the reason is recorded here so nobody re-proposes it |
| **Azure AI Language / AWS Comprehend / Google DLP** | **Reject** | No self-host path. Sending text to a third-party PII API to avoid sending it to a model provider is not a minimisation win |
| **LLM-as-redactor** | **Reject for the hot path** | Best measured accuracy (REDACT: GPT-4.1 F1 0.597, Claude Sonnet 4.6 F1 0.636) but 1.5–4 s added latency, and it puts a prompt-injectable model in front of every request |

So: **Presidio as the framework, a Klai-supplied Dutch recognizer pack as the substance,
GLiNER behind a gate for names.** The 2026 delta versus the original note is that the
Dutch gap is explicit rather than assumed. The other 2026 delta — that LiteLLM's native
guardrail would make this pure configuration — **did not survive contact**: REQ-5 and REQ-0a
each measured it doing something other than what its documentation implies, so Klai owns the
observer, the mask, the map and the restore.

### Pseudonymisation or anonymisation?

Both, split by intent. Neither alone fits the product.

Irreversible masking is right for values that should never come back: a credential, a BSN.
It is wrong for the drafting use case. If an agent asks Klai to write an email to Jan de
Vries on 06-12345678, masking returns a draft containing `<PERSON>` and `<PHONE_NUMBER>` —
technically minimised, practically useless. For those entities the model needs a stable
handle it can write around, and the real values need to reappear in the output.

**Correction to an earlier reading of the evidence.** A previous draft of this SPEC excluded
reversibility on the grounds that LiteLLM's `output_parse_pii` does not un-mask streaming
responses. That is not our situation. [BerriAI/litellm#22821](https://github.com/BerriAI/litellm/issues/22821)
is specific to the **Anthropic native** path — where response chunks arrive as raw SSE bytes
rather than `ModelResponseStream` objects, so the unmasking iterator never fires — and it is
**closed**, fixed by PR #30028. The OpenAI-compatible path was never affected. Klai routes
`mistral/mistral-small-2603` (`deploy/litellm/config.yaml:5-8`) over that path on LiteLLM
`v1.96.2` (`deploy/docker-compose.yml:309`).

What remains genuinely uncertain is the older
[#6247](https://github.com/BerriAI/litellm/issues/6247): corrupted token maps
(`{'<PERSON>': 'Mike. Wh'}` — analyzer versus anonymizer coordinates) and the map not
surviving from the pre-call hook to the post-call hook. It is closed as **stale**, not as
fixed. Whether it still reproduces on `v1.96.2` is unknown and is not knowable from reading;
Phase 0 exists to answer it.

**The legal position does not change with the choice.** Pseudonymised data remains personal
data for whoever holds the mapping (GDPR Art. 4(5)) — already recorded in this repo at
`docs/research/knowledge-pipeline-architecture.md:328` — and neither operation makes a
payload "anonymous" under the three-criteria bar in [EDPB Guidelines 02/2026](https://www.edpb.europa.eu/system/files/2026-07/edpb_guidelines_202602_anonymisation_v1_en_0.pdf)
(adopted 7 July 2026). Both reduce what the provider receives. That is the whole benefit, and
it is enough.

**Decision:**

| Entity set | Operation | Why |
|---|---|---|
| `SECRET`, `NL_BSN` | **Irreversible** (`replace`, no restore) | Never wanted back. A credential in a draft is an incident; a BSN is not Klai's to hold |
| `PERSON`, `PHONE_NUMBER`, `EMAIL_ADDRESS`, `IBAN_CODE`, `NL_KVK`, `NL_BTW`, `NL_POSTCODE` | **Reversible** (`replace` + restore) | The output is only useful if these come back |

This is one `pii_entities_config` with two operator behaviours, not two systems.

### What reversibility actually costs, and one thing it makes cheaper

Four layers, in increasing order of difficulty:

1. **Plumbing** — possibly one config line (`output_parse_pii: true`), possibly our own
   restore. If it must be ours, the pattern already exists: `klai_knowledge.py:1667-1703`
   buffers streaming chunks with one-item lookahead and mutates them before yielding, for
   the citation footer. Not research, precedent.
2. **Name detection becomes required** — and this is where reversibility is *easier*, not
   harder. Under masking, a false positive destroys information permanently and visibly.
   Under restore, a false positive round-trips invisibly: the word is replaced, sent as
   `<PERSON_3>`, and returns as itself. **Precision stops being the binding constraint.**
   That is what makes GLiNER usable here (REQ-9) when it was not usable for masking.
3. **Token survival** — the genuinely new problem. The model must echo the placeholder
   verbatim. Dutch makes this harder than English: declension (`de heer Jan de Vries` →
   `meneer De Vries`), salutation rewriting, and plain paraphrase to "de genoemde persoon".
   Every token that does not return intact is a visible artefact. REQ-0b measures it.
4. **Map scoping** — small, and the only way reversibility can fail *worse* than masking.
   A map leaking across requests restores tenant A's name into tenant B's draft. REQ-11.

Note the failure modes differ in kind but neither leaks toward the provider: masking fails
safe-but-useless, restore fails useful-but-ugly.

### Why the native guardrail and not our own hook

Klai's existing `KlaiKnowledgeHook` cannot host this. Two lines after its entry it returns
early on `_klai_openai_passthrough` (`deploy/litellm/klai_knowledge.py:426-427`) — a flag set
by `klai_kb_query_rewrite.py` on the call that forwards pasted customer correspondence
verbatim. It returns early again when `org_id` is absent (`:481-483`), which is every widget
and partner request, since `partner_chat.py` calls with the master key and no `user` field.

LiteLLM guardrails are not subject to either: they are evaluated by the proxy, and
`default_on: true` runs them on every request without the caller opting in. That is the
supported path, it covers the two cases our own hook misses, and it is less code.

## Scope

### In scope

- Two self-hosted Presidio containers (analyzer, anonymizer) in `deploy/docker-compose.yml`.
- A Klai Dutch recognizer pack: BSN, KvK, BTW, postcode — plus credentials.
- LiteLLM guardrail configuration with `default_on: true`.
- A round-trip harness proving the restore path and measuring Dutch token survival (Phase 0).
- A read-only observer for Phase 2 measurement, removed again by Phase 3.
- Shadow-then-enforce rollout, per-entity and per-org policy.
- Reversible restore for the return set; irreversible for the never-return set.
- GLiNER as the NER engine for `PERSON`, gated on REQ-0b's survival rate.

### Out of scope

- **PII already at rest** — Qdrant chunk payloads, FalkorDB entity nodes, transcripts,
  crawler tables. Separate work; this SPEC neither covers nor depends on it.
- **The two ungated query-text log sites** found while researching this
  (`klai_knowledge.py:606-611`, `gap_rescorer.py:141-169`). Real, unrelated, handled
  separately.
- Deduplicating the two `shield_compliance.py` copies. REQ-3 reuses the elfproef from one of
  them; consolidating them is not this SPEC's job.
- Changing provider, model routing, or adding a service outside the two Presidio containers.
- **Cross-turn placeholder consistency.** Needs a persisted map; forbidden by REQ-11.
- Restoring anything in the never-return set. Not a limitation — a requirement.

## Functional Requirements (EARS)

### Phase 0 — settle the reversibility question with a measurement

#### RESULT — measured on core-01, 2026-08-20

Run: `docker exec klai-core-litellm-1 python scripts/eval_pii_restore_live.py`, against
LiteLLM `v1.96.2`, `klai-fast` (`mistral-small-2603`), analyzer image
`ghcr.io/getklai/presidio-analyzer@sha256:fff95b84…`. Script exit code 1 — it refuses to
report a pass on incomplete data.

**REQ-0a — restore works non-streaming, fails streaming.**

| Path | Entity | Outcome |
|---|---|---|
| non-streaming | `PHONE_NUMBER` | `exact_match` |
| non-streaming | `EMAIL_ADDRESS` | `exact_match` |
| **streaming** | `PHONE_NUMBER` | **`empty_map`** |
| **streaming** | `EMAIL_ADDRESS` | **`empty_map`** |
| both | `PERSON` | `not_masked` — inconclusive, see below |

This is the second failure shape in [#6247](https://github.com/BerriAI/litellm/issues/6247)
— the map does not survive from the pre-call hook to the post-call hook — and it is still
live on `v1.96.2`. It is **not** the Anthropic-native SSE-bytes bug (#22821, closed), which
genuinely does not apply to us. 0.2.0 of this SPEC concluded from reading that streaming
restore was fine for our path. That conclusion was wrong, and only the measurement showed it.

**Consequence, per REQ-0a's own conditional:** both Klai chat paths stream, so REQ-8's
reversible restore **SHALL** be implemented in
`async_post_call_streaming_iterator_hook` / `async_post_call_success_hook`
(`klai_knowledge.py:1667-1703` is the working precedent), not via `output_parse_pii`. The
native flag may still be used for genuinely non-streaming callers; it must not be relied on
for chat.

**REQ-0b — the verbatim-token instruction roughly doubles survival.**

| Entity | Condition | Survived | Verdict |
|---|---|---|---|
| `PHONE_NUMBER` | with instruction | 23/24 — **95.8%** | at the bar |
| `PHONE_NUMBER` | without instruction | 14/24 — **58.3%** | far below |
| `PERSON` | both | 0/0 (`not_masked` ×30) | **unmeasurable today** |

The instruction is therefore **mandatory**, not advisory: without it, roughly two in five
phone numbers would come back mangled or missing.

`PHONE_NUMBER` also showed `not_masked=6` of 30 despite `PhoneRecognizer` being configured
with `supported_regions: [NL]`. Six Dutch numbers in the corpus were not detected at all —
a **detection** gap distinct from the survival question, and an input to REQ-9's tuning.

**A sequencing defect in this SPEC, exposed by the run.** REQ-0b gates `PERSON` enforcement
on a survival rate ≥95%, but REQ-2 disables `SpacyRecognizer`
(`deploy/presidio/analyzer/conf/analyzer.yaml:63-65`) and GLiNER (REQ-9) is not deployed, so
**no PERSON detector exists** and the rate cannot be produced. `not_masked=30` is the
designed behaviour, not a failure. **THE `PERSON` half of REQ-0b SHALL** therefore be re-run
after REQ-9 lands, and `PERSON` **SHALL NOT** be enabled in REQ-7's policy before that
re-run exists.

#### REQ-0a — the restore path is proven on our version before anything is built on it (event-driven)

**BEFORE** any recognizer work begins, **THE implementation SHALL** stand up stock Presidio
and run a round-trip harness against LiteLLM `v1.96.2` with a real `mistral/*` model,
covering **both** streaming and non-streaming, and **SHALL** record whether
`output_parse_pii` restores correctly.

Stock recognizers are sufficient here — `PERSON`, `PHONE_NUMBER`, `EMAIL_ADDRESS` are
built in. The Dutch pack (REQ-3) is not needed to answer this question and **SHALL NOT**
block it.

**THE harness SHALL** specifically probe the two failure shapes named in
[#6247](https://github.com/BerriAI/litellm/issues/6247): a corrupted map (restored value not
equal to the original) and an empty map at post-call (nothing restored at all).

**IF** the restore is correct, **THEN** REQ-8 uses the native path.
**IF** it is not, **THEN** REQ-8 is implemented in
`async_post_call_streaming_iterator_hook` / `async_post_call_success_hook`, and the cost of
that is recorded in the SPEC before Phase 1 proceeds.

#### REQ-0b — token survival is measured, not assumed (ubiquitous)

**THE harness SHALL** report a **token survival rate** per entity type over at least 30
Dutch drafting prompts — write an email, summarise a call, draft a reply — each containing
at least one person name and one phone number.

Survival is defined per placeholder emitted: the placeholder appears in the model output
exactly as sent. **THE report SHALL** separate the failure kinds, because they need
different fixes: not returned at all, returned altered (declension, case), and returned
paraphrased.

**THE system prompt SHALL** carry an instruction to reproduce placeholder tokens verbatim,
and the harness **SHALL** measure with and without it, so its effect is known rather than
believed.

A survival rate below 95% for `PERSON` means reversible mode is not fit for the drafting use
case yet, and REQ-7's per-entity policy is where that is expressed — not a silent downgrade.

### Phase 1 — deploy the tools

#### REQ-1 — Presidio runs self-hosted, pinned (ubiquitous)

**THE deployment SHALL** add `presidio-analyzer` and `presidio-anonymizer` to
`deploy/docker-compose.yml`, from `ghcr.io/data-privacy-stack/presidio-*`, **pinned by
digest**, on the internal network only, with no Caddy route and no published port.

**THE images SHALL NOT** be taken from `mcr.microsoft.com/presidio-*`: those tags are frozen
at the pre-transition state and no longer receive updates.

Neither container needs a secret, so this adds no `env_file` scope and no
`SECRETS_MATRIX.md` entry.

#### REQ-2 — detection is language-agnostic by construction (ubiquitous)

Klai is a language-agnostic product. The KB context block is deliberately
language-neutral and the model is instructed to answer in the user's language rather than
the language of the source chunks (SPEC-RAG-MULTILINGUAL-CHAT-001); retrieval-api ships a
language detector (`klai-retrieval-api/retrieval_api/util/language_detect.py`) and
per-language correctness monitoring (`docs/runbooks/multilingual-chat-observability.md`).
A PII control that assumes one language is therefore wrong for the product, not merely
imprecise.

**THE detection layer SHALL NOT** depend on a per-request language setting for any entity in
REQ-3 except `PERSON`.

This is achievable rather than aspirational because of what those entities are. **A BSN in
an English sentence is still a BSN.** Every entity in REQ-3 is a regex plus a checksum:
they are **jurisdiction-specific, not language-specific**, and they match identically
regardless of the surrounding prose. They are registered for all languages the analyzer
serves, not scoped to `nl`.

**THE `PERSON` recognizer** is the only language-sensitive one, and **SHALL** be the
multilingual `gliner_multi_pii-v1` (REQ-9) rather than a per-language spaCy pipeline.
GLiNER is zero-shot across ~100 languages from one model.

Consequences, which are simplifications rather than costs:

- **No spaCy model is loaded.** The stock image's `en_core_web_lg` is not used, no
  `nl_core_news_lg` is added, and the analyzer runs with a regex-and-checksum registry plus
  GLiNER. This removes the per-language model matrix that a spaCy-based design would need —
  one model per supported language, each a memory cost on a host already running 78
  services.
- **`presidio_language` stops being a correctness-relevant setting** for everything except
  `PERSON`, so a wrong or absent value cannot silently disable BSN or credential detection.
- **Adding a jurisdiction later is adding a recognizer**, not adding a language pipeline.

**THE Phase 2 telemetry SHALL** carry the detected language alongside the per-entity counts
(REQ-6), reusing the existing detector rather than adding one, so that per-language recall
can be compared instead of assumed. **IF** that comparison shows `PERSON` recall varying
materially by language, **THEN** that is a REQ-9 gating input, not a reason to reintroduce
per-language pipelines.

**Phase 0 exception.** Phase 0 uses the stock image as shipped, English NLP engine included,
because it is measuring the restore mechanism rather than detection quality. That is why
REQ-0a requires a direct analyzer pre-check confirming an entity was actually detected before
a round trip is scored — an undetected name echoed verbatim would otherwise be
indistinguishable from a successful restore. This exception ends with Phase 1.

#### REQ-3 — Klai supplies the Dutch recognizer pack (ubiquitous)

**THE deployment SHALL** register a Klai recognizer pack in the analyzer covering:

| Entity | Validation | Source |
|---|---|---|
| `NL_BSN` | **elfproef** (weighted sum mod 11) | Port `shield_compliance.py:96-104` — already working in production, do not rewrite it |
| `NL_KVK` | 8 digits, context words (`kvk`, `handelsregister`) | new |
| `NL_BTW` | `NL` + 9 digits + `B` + 2 digits | new |
| `NL_POSTCODE` | `1234 AB` shape | `shield_compliance.py:38` |
| `IBAN_CODE` | mod-97 | **built-in**, enable rather than write |
| `CREDIT_CARD` | Luhn | **built-in**, enable rather than write |
| `EMAIL_ADDRESS` | shape | **built-in**, enable rather than write |
| `PHONE_NUMBER` | libphonenumber, regions NL + BE prepended to the stock list | `NLPhoneRecognizer` — a subclass, **not** a YAML `supported_regions:` key, which the registry loader silently drops (see ~~A6~~) |

**THE checksum-validated recognizers SHALL** be Python `PatternRecognizer` subclasses
overriding `validate_result()`, not YAML entries. Presidio's YAML registry can express a
regex and a score but not a checksum, and for BSN the checksum is the whole point: a bare
nine-digit pattern matches order numbers and customer references, and the elfproef is what
makes it a control instead of a nuisance.

The pack ships as a small image layered on the stock analyzer, mounted through the
recognizer registry.

#### REQ-4 — credentials are recognised (ubiquitous)

**THE pack SHALL** include a `SECRET` recognizer covering PEM private-key blocks, JWTs,
`Authorization: Bearer` values, and provider key prefixes (`sk-`, `ghp_`, `xox[baprs]-`).

**THE PEM span SHALL** run from `-----BEGIN [<type> ]PRIVATE KEY-----` through the matching
`-----END [<type> ]PRIVATE KEY-----`. The type token is optional — canonical PKCS#8 keys are
literally `-----BEGIN PRIVATE KEY-----`, and requiring a type lets the most common key format
through. Matching only the header leaves the key material in the payload.

### Phase 2 — measure before changing anything

#### REQ-5 — measurement runs on every request through a read-only observer (ubiquitous)

**Correction (0.4.0).** Earlier versions of this requirement specified the native guardrail
with `mode: "logging_only"` and `default_on: true`. That does not do what it sounds like.
LiteLLM's `logging_only` means *"only apply PII masking before logging to Langfuse, etc. Not
on the actual llm api request / response"* — it masks what reaches **observability** and
sends the payload to the provider **unmasked**. For our purpose that is exactly inverted: it
would leave provider egress untouched (the status quo) while blinding the one place we want
counts. LiteLLM's Presidio guardrail has **no** detect-only mode; all four modes either mask
or block.

**THE Phase 2 implementation SHALL** therefore be a read-only observer:
`deploy/litellm/klai_pii_observe.py`, a `CustomLogger` registered in `callbacks:` alongside
the existing hooks.

**IT SHALL** call the analyzer's `/analyze` endpoint with the outbound payload and emit the
telemetry in REQ-6. **IT SHALL** return the payload **unchanged**, and **SHALL NOT** call the
anonymizer at all.

**IT SHALL NOT** honour `_klai_openai_passthrough`, `org_id` absence, or any other early
return — the two blind spots in `KlaiKnowledgeHook` named in the Motivation are precisely
what Phase 2 needs to see.

**IT SHALL** run **out of band** of the response path: the measurement call **SHALL NOT**
add latency to the user's request, and a failure or timeout in it **SHALL NOT** fail the
request. Phase 2 changes nothing, so it must not be able to break anything either. This is
the deliberate inverse of REQ-10, which applies to enforcement.

**THE observer SHALL** be deleted in the Phase 3 PR. It exists to answer REQ-8's activation
question; once the native guardrail enforces, a second path evaluating the same payload is
duplicate machinery, and `clean over clever, no parallel old+new` applies.

The Phase 0 experiment guardrail (`presidio-pii-phase0`, opt-in, no `default_on`) stays
registered and unchanged so the REQ-0a/REQ-0b harness remains runnable.

#### REQ-6 — detections are recorded without recording the values (ubiquitous)

**THE Phase 2 telemetry SHALL** emit, per request: `org_id`, `call_type`, model alias, the
detected language (REQ-2), and a count per entity type.

**IT SHALL NOT** emit matched values, surrounding text, character offsets, or a hash of a
matched value. A hash of a BSN is a BSN: the search space is nine digits and brute-forcing it
is trivial.

**THE language label SHALL** be derived from the latest **user turn**, not the whole payload.
The KB context block is deliberately English-structured (SPEC-RAG-MULTILINGUAL-CHAT-001) and
usually dwarfs the question, so detecting on the combined text would label a Dutch question
`en` on essentially every RAG request. A language field that is wrong is worse than absent,
because REQ-2's per-language recall comparison would silently rest on it. The PII scan itself
still covers the full payload — only the label is narrowed.

**Known limitation, recorded rather than hidden.** The observer uses a local stopword
heuristic, not the canonical lingua detector in
`klai-retrieval-api/retrieval_api/util/language_detect.py`. `lingua-language-detector` is a
dependency of retrieval-api and knowledge-ingest but **not** of the litellm container, which
runs the stock `ghcr.io/berriai/litellm` image with bind-mounted modules — importing it would
mean building and maintaining a custom litellm image, which is disproportionate for a
telemetry label in a phase that is deleted again by Phase 3. The heuristic returns an explicit
unknown rather than guessing on a tie. **IF** Phase 2 data shows the language dimension is too
noisy to support REQ-9's gating decision, **THEN** the correct response is a custom litellm
image with the canonical detector — not tuning the word lists.

Recording that a BSN was found, without recording which, is what accountability needs.
Storing the value to prove it was removed moves the exposure into the log store — and that
store has 30-day retention.

## Assumptions

Klai's traffic volume is too low for a 30-day, three-tenant, annotated sample to mean
anything. Requiring one was a stall dressed as rigour. The workflow is build → ship →
observe → correct, so the things a measurement would have settled are stated here instead,
each with what would falsify it.

| # | Assumption | Basis | What falsifies it |
|---|---|---|---|
| A1 | The elfproef reduces false BSN matches to roughly 1 in 11 of **nine**-digit runs | Arithmetic property of the checksum | **Partly falsified 2026-08-20.** The recogniser also accepted **eight**-digit runs, which is the shape of a `YYYYMMDD` date: 365 of 4018 dates in 2020-2030 (9.1%) pass the padded elfproef. Since `NL_BSN` is masked for every org and never restored, `Factuurdatum 20200201` was destroyed with no way back. Nine digits now stands on the checksum alone; eight requires a BSN context word |
| A2 | Typed, numbered placeholders survive the model well enough to be useful, given the verbatim instruction | Measured for `PHONE_NUMBER` (95.8%), assumed to generalise to `IBAN`, `EMAIL`, `KVK`, `BTW`, `POSTCODE` — all shorter and more literal than a phone number | Any of those entities showing survival below 95% once enabled |
| A3 | `PERSON` behaves worse than the other entities and needs its own evidence | Names are inflected in Dutch, the others are not | A GLiNER-era re-run showing `PERSON` ≥95% |
| A4 | An in-process map keyed by `litellm_call_id` is sufficient isolation | The id is a per-request UUID generated by LiteLLM | A collision, or a restore writing one request's value into another's output. AC-0e-style concurrency test guards it |
| A5 | Enforcement adds under 60 ms p95 | Regex plus checksums, no model, one in-cluster hop | The NFR's own measurement once enabled |
| ~~A6~~ | ~~The six undetected Dutch phone numbers are a format-coverage gap~~ | — | **FALSIFIED and fixed, 2026-08-20.** Introspecting the running analyzer showed `PhoneRecognizer lang=nl regions=('US','UK','DE',…)`: the YAML `supported_regions:` key is silently dropped on a `type: predefined` entry, so Dutch detection ran on Presidio's defaults and worked only because most Dutch numbers also parse as valid German ones. Rotterdam's `010` has no German equivalent and was never detected. Fixed by `NLPhoneRecognizer` (`4af66f4e0`), digest pinned (`b5b592051`), verified live |

An assumption that turns out wrong here costs one flag flip, not a redesign — which is the
point of shipping it off by default.

### Phase 3 — enforce

#### REQ-7 — the policy is per entity, and two entries are not negotiable (state-driven)

**THE guardrail SHALL** be configured with `pii_entities_config`, where:

- `SECRET` and `NL_BSN` are **`MASK` for every org, unconditionally**.
- `IBAN_CODE`, `CREDIT_CARD`, `EMAIL_ADDRESS`, `PHONE_NUMBER`, `NL_KVK`, `NL_BTW`,
  `NL_POSTCODE` are **per-org, default off**.

`SECRET` is unconditional because forwarding a credential to a model provider is an incident
regardless of what a tenant prefers.

`NL_BSN` is unconditional for a different reason: a private company may process a BSN only
where a statute authorises it ([Wet algemene bepalingen burgerservicenummer](https://wetten.overheid.nl/BWBR0022428/)).
Absent that, it is not Klai's to send anywhere — a lawfulness question, so not a checkbox.
**IF** an org has a documented statutory basis, **THEN** it is recorded as an audited
exception naming the statute.

Default-off for the rest is deliberate: an agent asking "klopt IBAN NL91 ABNA 0417 1643 00?"
needs the model to see it.

#### REQ-8 — placeholders are typed, numbered, and restored for the return set (ubiquitous)

**THE anonymizer SHALL** use `replace` with a typed, **instance-numbered** placeholder —
`<PERSON_1>`, `<PERSON_2>`, `<IBAN_CODE_1>` — not a blank, not a uniform `[REDACTED]`, and
not an unnumbered type.

Numbering carries the distinction the drafting case depends on: two different people in one
email must not collapse into one token, or the restore writes the same name twice. Typing
keeps the sentence intact and tells the model what kind of thing was removed, which is what
stops it inventing a value to fill the gap.

**THE return set** (REQ-7) **SHALL** be restored in the response on both the streaming and
non-streaming paths. REQ-0a's measurement settled which mechanism: `output_parse_pii`
restores correctly non-streaming but yields an **empty token map on streaming**, and both
chat paths stream — so the restore **SHALL** be implemented in
`async_post_call_streaming_iterator_hook` / `async_post_call_success_hook`, using the
buffered chunk-rewrite pattern already working at `klai_knowledge.py:1667-1703`.

**THE never-return set** (`SECRET`, `NL_BSN`) **SHALL NOT** be restored under any
configuration. If the native path cannot restore selectively per entity type, the never-return
set is masked with a non-restorable operator instead, so that a configuration mistake cannot
put a credential back into a response.

**IF** restore is implemented in our own hook, **THEN** it **SHALL** hold back a tail of at
least the longest possible placeholder length when matching across streamed chunks. A
placeholder split as `<PERS` + `ON_1>` across a chunk boundary that is emitted unrestored is
a defect, and **SHALL** have a regression test.

**THE masking step SHALL** resolve **overlapping spans** before substituting. Presidio
returns them and does not deduplicate across entity types. Measured on the deployed analyzer,
2026-08-20:

```
Betaal op IBAN NL91 ABNA 0417 1643 00 graag.
IBAN_CODE    [15:37] score=1.00
PHONE_NUMBER [25:37] score=0.40   <- fully inside the IBAN span
```

Substituting naively corrupts the text: replace the IBAN first and the phone offsets are
stale; replace the phone first and the IBAN no longer matches its own span. **THE
implementation SHALL** substitute from the **end of the string backwards** so earlier offsets
stay valid, and **SHALL** drop any span that **overlaps** one already taken — not merely one
*contained* in it. Containment alone is not sufficient, and the earlier wording of this
requirement was wrong: the deployed recogniser set produces pairs where the higher-scoring
span sits INSIDE the lower-scoring one, so a containment-only rule accepts both.

```
Ons BTW-nummer is NL123456782B01.
NL_BSN [20:29] score 1.00   <- higher score, inside
NL_BTW [18:32] score 0.70
```

Selection order is: higher score, then longer span, then **never-restore entity wins** — an
8-digit KvK that also passes the padded elfproef produces `NL_BSN` and `NL_KVK` at a
byte-identical span with an identical score, and a tie must not decide whether a value lands
in the restore map. This overlap predates the Phase 1 deployment —
it is a property of running several recognisers over one text, not a regression — and it
needs a regression test using exactly the IBAN case above.

#### REQ-11 — the placeholder map is request-scoped and never persisted (ubiquitous)

**THE map** from placeholder to original value **SHALL** live only for the lifetime of the
request that created it, **SHALL** be keyed such that it cannot be reached by another
request, and **SHALL NOT** be written to Redis, Postgres, disk, or any log.

**Concretely.** The native guardrail stores its map in `request_data["metadata"]["pii_tokens"]`,
and REQ-0a measured that this does not survive to the streaming response hook — that is
exactly why streaming restore returned `empty_map`. Klai therefore owns the map: a
**process-local dict keyed by `litellm_call_id`** (a per-request UUID that LiteLLM puts in
`request_data`, and which the `aim` and `cato_networks` guardrails already read in their own
streaming iterator hooks).

**THE entry SHALL** be deleted when the stream ends, on the success path and on the error
path alike. **A TTL sweep SHALL** additionally drop entries older than a bounded age, because
a client that disconnects mid-stream never reaches either path and would otherwise leak the
entry — a leak that is both a memory growth problem and a privacy problem, since the entry
holds real values.

**THE store SHALL** be bounded in size, and **SHALL** drop oldest-first when full rather than
grow without limit. Losing a map degrades one response to visible placeholders; an unbounded
map degrades the process.

This is the one way reversibility can fail worse than masking: a map reachable across
requests restores one tenant's personal data into another tenant's output. Everything else in
this SPEC fails toward a degraded answer; this fails toward a cross-tenant disclosure.

**THE implementation SHALL** carry a test that runs two concurrent requests from different
orgs, each containing a different person name, and asserts neither response contains the
other's value. `/klai:tenant-review` applies to this PR.

Cross-turn consistency — the same person keeping the same placeholder across a conversation —
is **out of scope**. It requires a store with a TTL, which is a persisted map, which is the
thing this requirement forbids. Revisit only with an explicit retention and scoping design.

#### REQ-9 — GLiNER supplies PERSON, gated on the survival rate (state-driven)

**THE `PERSON` recognizer SHALL** be `gliner_multi_pii-v1` (apache-2.0, licence verified on
the model card) plugged in as the analyzer's NER engine.

Its documented profile — high recall, moderate precision — disqualified it for masking and
does not disqualify it here. Under restore, an over-detection round-trips invisibly: a
non-name replaced by `<PERSON_4>` comes back as itself and the user sees nothing. Recall is
what protects the tenant; precision only costs tokens.

**THE `PERSON` entity SHALL** be enabled for enforcement only where REQ-0b's survival rate is
at or above 95%. Below that, an unrestored `<PERSON_1>` in a draft is a visible defect, and
the correct response is to leave the entity off rather than ship a broken drafting
experience.

**IF** GLiNER cannot meet the latency budget on CPU, **THEN** `PERSON` stays off and the rest
of the pack — all regex-and-checksum — ships without it. The Dutch identifier coverage does
not depend on NER.

### Both phases

#### REQ-10 — failure does not silently pass the payload through (state-driven)

**IF** the analyzer or anonymizer is unreachable or errors, **THEN** the request **SHALL**
fail with an error rather than proceed unminimised, and the failure **SHALL** be logged at
`warning` with the org and `call_type`.

**Exception:** during Phase 2 (`logging_only`) the guardrail **SHALL** fail open, because it
is changing nothing and an outage in a measurement path must not take down chat.

Fail-closed in Phase 3 is deliberate. A control that disables itself under load is worse than
none, because the dashboard still says it is on. The latency budget below is what makes it
safe to assert.

## Non-Functional Requirements

- **Latency.** Two in-cluster HTTP calls plus regex and checksums, no model. Budget: **p95
  under 60 ms** added per request for a 10 000-character payload, measured at the proxy.
  Per REQ-2 no spaCy pipeline is loaded, so the regex-and-checksum registry is the baseline —
  community measurements put that configuration in single-digit milliseconds. GLiNER for
  `PERSON` is the only model in the path (~75 ms CPU in published measurements) and is the
  term that can breach the budget. **IF** it does, **THEN** `PERSON` is disabled and every
  other entity still ships; do not relax the budget to keep it.
- **Tenant isolation.** Per-org policy is resolved through the existing settings path and
  cached per org. A cache key omitting `org_id` would apply one tenant's policy to another;
  `/klai:tenant-review` applies to the Phase 3 PR.
- **No new secret surface.** Neither container takes credentials.
- **Backwards compatibility.** Phases 1 and 2 change no payload. The first behavioural change
  to a model request is REQ-7.

## Acceptance Criteria

| AC ID | Test | Expected outcome |
|-------|------|-------------------|
| AC-0a | Phase 0: non-streaming Mistral call, prompt containing a name and a phone number, `output_parse_pii: true` | Response contains the original values, byte-identical to what was sent. A corrupted or empty restore is a REQ-0 negative and routes to the own-hook implementation |
| AC-0b | Same, streaming | Same outcome; no placeholder visible in any chunk sequence |
| AC-0c | Phase 0: token-survival run, ≥30 Dutch drafting prompts, with and without the verbatim-token system instruction | Survival rate reported per entity type, split by failure kind (absent / altered / paraphrased), both with and without the instruction |
| AC-0d | Own-hook restore only: placeholder split across a chunk boundary (`<PERS` + `ON_1>`) | Restored correctly; no partial placeholder emitted |
| AC-0e | Two concurrent requests, different orgs, different person names | Neither response contains the other's value (REQ-11) |
| AC-0f | Grep/inspection of Redis, Postgres and log sinks after a reversible request | Placeholder map absent from all of them |
| AC-1 | Both containers up; `POST /analyze` with `language: "nl"` | 200, not a language error — proves REQ-2 |
| AC-2 | Compose image references | Both pinned by digest, both from `ghcr.io/data-privacy-stack`, neither from `mcr.microsoft.com` |
| AC-3 | Analyze a valid BSN, and a nine-digit number failing the elfproef | First returns `NL_BSN`; second returns nothing |
| AC-4 | Analyze `NL91 ABNA 0417 1643 00` and a mod-97-invalid variant | First returns `IBAN_CODE` covering the grouped form; second does not |
| AC-5 | Analyze a complete PKCS#8 block with no type token, a JWT, a Bearer header, an `sk-` key; and separately a bare unmatched PEM header | First four return `SECRET` with the span reaching the END marker; the unmatched header returns nothing |
| AC-6 | Analyze a Dutch KvK, BTW and postcode | Each returns its entity type |
| AC-7 | Phase 2: request carrying `_klai_openai_passthrough` and a BSN | Guardrail evaluates it and counts the detection. This is the regression test for the hook's blind spot |
| AC-8 | Phase 2: request with no `org_id` (widget/partner shape) | Guardrail evaluates and counts |
| AC-9 | Phase 2 telemetry schema test | No field carries a matched value, offset, or hash |
| AC-10 | Latency: p95 over 1000 payloads of 10 000 chars | Under 60 ms added |
| AC-11 | Phase 3: BSN with an empty org policy | Replaced with `<NL_BSN>` anyway |
| AC-12 | Phase 3: IBAN with empty org policy, then with `IBAN_CODE` enabled | Untouched, then `<IBAN_CODE>` |
| AC-13 | Phase 3: analyzer container stopped, then a chat request | Request errors; no unminimised payload reaches Mistral. Same test in Phase 2 config: request succeeds |
| AC-14 | Phase 2 output over whatever window exists when Phase 3 is ready | Per-entity detection rate and per-language split reported as an input, not a gate. At current volume this is directional only, and the SPEC says so rather than implying significance it cannot have |
| AC-15 | Streaming chat request in Phase 3 with a BSN in the user turn | Model receives `<NL_BSN>`; response streams normally; the BSN is **not** restored |
| AC-16 | Phase 3 drafting request: "schrijf een mail aan Jan de Vries, 06-12345678" with `PERSON` and `PHONE_NUMBER` enabled | Mistral receives `<PERSON_1>` and `<PHONE_NUMBER_1>`; the delivered draft contains the real name and number |
| AC-17 | Same request with two different people | Two distinct placeholders; both restored to the correct respective values, not the same one twice |

## Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| GLiNER blows the latency budget on CPU | medium | medium | It is the only model in the path. Every other REQ-3 entity is regex-plus-checksum and unaffected, so the documented response is to disable `PERSON` and ship the rest |
| Over-detection: a nine-digit order number read as a BSN | medium | medium | The elfproef makes an accidental match roughly 1 in 11 — an assumption, not a measurement (see Assumptions). The per-org bound this row used to claim is gone as of GA (2026-08-25): a bad rate now shows on every tenant at once, and `NL_BSN` is never restored, so a false positive is irreversible. What remains is the 8-digit context gate (`klai_pii_recognizers.py`, `NLBSNRecognizer.analyze`), which removed the `YYYYMMDD` collision that made this likely; the 9-digit form still stands on the checksum alone |
| A tenant genuinely needs a BSN to reach the model | low | medium | REQ-7's audited statutory-basis exception. If there is no statutory basis, the correct outcome is that it does not reach the model |
| `PERSON` recall varies by language even with multilingual GLiNER | medium | medium | REQ-2 requires Phase 2 telemetry to carry detected language so this is measured per language rather than assumed uniform. A material gap gates REQ-9, and the checksum entities are unaffected either way |
| Fail-closed turns a Presidio outage into a chat outage | low | critical | No longer bounded to a pilot: as of GA (2026-08-25) an analyzer outage rejects chat for every tenant carrying an `org_id`, so likelihood stays low but the blast radius is now platform-wide. `container_down` in `deploy/grafana/provisioning/alerting/infra-rules.yaml` covers `klai-core-.*` and therefore the analyzer. Rollback is two levels: `KLAI_PII_ENFORCE=false`, or an explicitly empty `KLAI_PII_ENFORCE_ORG_IDS` — which is why the Compose default uses `${VAR-*}` and not `${VAR:-*}` |
| Presidio's upstream governance move stalls and the project goes quiet | low | medium | MIT-licensed and self-hosted; a frozen dependency stays working. Our recognizer pack is our own code and portable |
| Someone re-proposes Piiranha because it supports Dutch | medium | low | Rejected in the Motivation with the licence quoted, so the answer is on file |

## Deployment — how this lands on the servers

**Target: core-01, in the existing `deploy/docker-compose.yml`.** Both Presidio containers
are CPU-only. Nothing in the REQ-3 pack needs a GPU; every entity there is regex plus a
checksum. `gpu-01` is not involved, so `validate-gpu-compose.yml` and the manual gpu-01
approval path do not apply.

**The deploy is the merge.** `.github/workflows/deploy-compose.yml` runs on pushes to `main`
touching `deploy/docker-compose.yml`, validates image tags and pullability, then SSH-syncs
the compose file and configs to core-01. There is no separate deploy step to remember and no
runbook to follow for the config half.

**No secrets half.** Neither container takes a credential, so `deploy/deploy.sh` is not
involved, no `env_file` scope is added, and `deploy/SECRETS_MATRIX.md` needs no entry. This
also keeps the change clear of `deploy/check-env-file-scope.py`, which is the CI guard that
would otherwise apply.

**Constraints the implementer must satisfy:**

| Constraint | Why |
|---|---|
| Both images **digest-pinned**, from `ghcr.io/data-privacy-stack/presidio-*` | The image-tag validation step in `deploy-compose.yml` fails the deploy on an unpinned or unpullable tag. `mcr.microsoft.com/presidio-*` is frozen post-transition and must not be used |
| Explicit `deploy.resources.limits` on both services | The file already does this per service (`cpus`/`memory`, e.g. `:510-512`, `:1011-1013`). core-01 runs **78 services**; an unbounded analyzer with a spaCy model loaded is a real memory risk to its neighbours |
| Internal network only — no Caddy route, no published port | These services accept arbitrary text and have no authentication of their own. They must not be reachable from outside the Docker network |
| `PRESIDIO_ANALYZER_API_BASE` / `PRESIDIO_ANONYMIZER_API_BASE` point at the internal service names | This is how the LiteLLM guardrail reaches them |

**Memory sizing is a gate, not an estimate.** A regex-and-checksum-only analyzer is small; the
same analyzer with `nl_core_news_lg` loaded is roughly an order of magnitude larger, and
GLiNER larger again. The actual headroom on core-01 cannot be determined from this repository.
**THE PR SHALL** state the measured resident size of both containers under load and the
remaining host headroom, taken from the server, before the limits are chosen. If headroom is
insufficient, the NLP-engine-free configuration in the NFRs is the fallback — it covers every
Dutch identifier and drops only `PERSON`.

**Rollback.** Reverting the `guardrails:` block in `deploy/litellm/config.yaml` disables the
control without touching the containers; reverting the compose block removes them. Neither
requires a data migration, because this SPEC persists nothing.

## Implementation handoff

Three PRs, in order.

| PR | Phase | Files | Gate before merge |
|----|-------|-------|-------------------|
| 0 | 0 | `deploy/docker-compose.yml` (stock Presidio only), `deploy/litellm/config.yaml` (guardrail, temporary), harness script | AC-0a through AC-0f. **Merges first and its result is written into this SPEC before PR 1 starts** |
| 1 | 1 | `deploy/presidio/` (recognizer pack image + registry config), compose update | AC-1 through AC-6 |
| 2 | 2 | `deploy/litellm/klai_pii_observe.py` (new, read-only observer), `deploy/litellm/config.yaml` (callback registration), compose bind-mount | AC-7, AC-8, AC-9, AC-10, AC-13 (Phase 2 half) |
| 3 | 3 | `deploy/litellm/config.yaml` (`pre_call`, `pii_entities_config`), org policy column, migration, `docs/privacy/` update | AC-11, AC-12, AC-13, AC-15 + `/klai:tenant-review` |

Rules for the implementer:

- Port the elfproef from `shield_compliance.py:96-104`; it works. Do not reimplement it.
- Enable `IBAN_CODE`, `CREDIT_CARD`, `EMAIL_ADDRESS` and `PHONE_NUMBER` from Presidio's
  built-ins rather than writing regexes for them.
- AC-7 and AC-8 must be RED before the guardrail is registered — they are the tests that
  prove `default_on` covers what our own hook does not. Paste the failure output in the PR
  body.
- Do not add PII logic to `KlaiKnowledgeHook`. The Motivation explains why.
- PR 3 merges with `KLAI_PII_ENFORCE` **off**. It is expected to reach production inert:
  that is what makes the activation a one-line, reversible decision instead of a deploy.
- Do not gate the merge on accumulating Phase 2 data. Volume is too low for it to be
  meaningful, and the assumptions it would have replaced are written down instead.

## Sources

Source references:

- `deploy/docker-compose.yml:308,421-422` — litellm service block, sole provider key
- `deploy/litellm/config.yaml:5-80,91-95,126-128` — Mistral model list, self-hosted embedder, callbacks
- `deploy/litellm/klai_knowledge.py:423-427,481-483` — hook entry and the two early returns
- `deploy/litellm/klai_kb_query_rewrite.py` — sets `_klai_openai_passthrough`
- `klai-portal/backend/app/services/shield_compliance.py:36-41,96-104` — recognisers, working elfproef
- `klai-portal/backend/app/services/partner_chat.py` — master-key calls without `user`
- `docs/architecture/klai-knowledge-architecture.md:1661` — the original Presidio + GLiNER decision
- `docs/research/knowledge-pipeline-architecture.md:328` — pseudonymisation ≠ anonymisation, already recorded

External research (re-run 2026-08-20):

- [LiteLLM Presidio guardrail](https://docs.litellm.ai/docs/proxy/guardrails/pii_masking_v2) — modes, `pii_entities_config`, `output_parse_pii`, `PRESIDIO_*_API_BASE`
- [LiteLLM guardrails quick start](https://docs.litellm.ai/docs/proxy/guardrails/quick_start) — `default_on: true`
- [BerriAI/litellm#22821](https://github.com/BerriAI/litellm/issues/22821), [#6247](https://github.com/BerriAI/litellm/issues/6247) — `output_parse_pii` does not un-mask on streaming
- [Presidio default_recognizers.yaml](https://github.com/microsoft/presidio/blob/main/presidio-analyzer/presidio_analyzer/conf/default_recognizers.yaml) — 73 recognizers, no Dutch, `nl` absent from supported_languages
- [Presidio recognizer registry from file](https://microsoft.github.io/presidio/analyzer/recognizer_registry_provider/) — YAML registry; checksums require Python
- [Presidio project transition](https://presidio.dataprivacystack.org/project_transition/) — move to Data Privacy Stack, image registry change
- [urchade/gliner_multi_pii-v1](https://huggingface.co/urchade/gliner_multi_pii-v1) — licence apache-2.0 (verified on the card)
- [iiiorg/piiranha-v1](https://huggingface.co/iiiorg/piiranha-v1-detect-personal-information) — licence cc-by-nc-nd-4.0 (verified on the card)
- [REDACT benchmark, arXiv:2606.19881](https://arxiv.org/abs/2606.19881), 18 Jun 2026 — 25 languages; rule-based recall 0.07 on high-sensitivity categories
- [Tonic: protecting privacy and RAG performance](https://www.tonic.ai/blog/protecting-privacy-rag-performance) — retrieval degradation, standard vs aggressive redaction
- [EDPB Guidelines 02/2026 on Anonymisation](https://www.edpb.europa.eu/system/files/2026-07/edpb_guidelines_202602_anonymisation_v1_en_0.pdf), adopted 7 Jul 2026 — three-criteria test
- [Wet algemene bepalingen burgerservicenummer](https://wetten.overheid.nl/BWBR0022428/) — BSN needs a statutory basis
