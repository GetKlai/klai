# Assertion Modes in RAG Systems: Research Reference

> Compiled: 2026-03-28
> Status: Decision-support document for Klai knowledge architecture
> Scope: Should epistemic assertion modes become an active part of the retrieval pipeline?

---

## 1. Context

Klai's knowledge architecture defines five assertion modes -- `factual`, `procedural`, `quoted`, `belief`, `hypothesis` -- along with three metadata axes (provenance type, synthesis depth, confidence). These are stored in `knowledge.artifacts` at ingest time, extracted from YAML frontmatter.

**Current state:** Assertion mode is written to PostgreSQL and displayed in the portal UI as a badge. It is *not* used in retrieval, reranking, or generation. The question this document addresses: should assertion modes become an active signal in the RAG pipeline, and what does the evidence say about the expected benefit?

---

## 2. Industry Landscape

Standard RAG systems treat all chunks as flat text with structural metadata only (source document, page number, section header, timestamp). No production system currently tags chunks with an epistemic type label (fact / opinion / hypothesis) and uses it as a retrieval or reranking signal.

However, several adjacent building blocks exist:

| Building block | What it does | Scale / maturity |
|---|---|---|
| **Certainty classifiers** | Classify scholarly assertions into 3 certainty categories | 89.2% cross-validation accuracy on biomedical text (Prieto et al. 2020) |
| **Nanopublications** | RDF-based atomic assertions with provenance and publication metadata graphs | 10M+ published, predominantly biomedical (Kuhn et al. 2018) |
| **TrustGraph** | Per-triple confidence scores via RDF-star reification; source provenance, conflict resolution | Production platform (trustgraph.ai), uses RDF 1.2 quoted triples |
| **Corpus-level type routing** | Medical RAG selects from guidelines vs. abstracts vs. case reports based on query type | Research prototypes; no chunk-level epistemic labeling |
| **MDKeyChunker** | Single LLM call extracts 7 metadata fields per chunk (title, summary, keywords, entities, hypothetical questions, semantic key) | March 2026 preprint; demonstrates multi-field extraction feasibility |

**Key observation:** Nobody has assembled epistemic type labeling, metadata-enriched retrieval, and confidence-weighted reranking into a single production pipeline. The pieces exist separately but the integration is untested.

---

## 3. Evidence: Metadata Enrichment Improves Retrieval

The following studies demonstrate that adding structured metadata to chunks improves RAG retrieval quality. None use epistemic type labels specifically, but they establish that metadata enrichment works.

### Mishra et al. (2025) -- Content type classification on AWS docs

- **Method:** LLM-generated per-chunk metadata (content type, entities, taxonomy, user intents, QA summaries) combined with multiple embedding strategies and cross-encoder reranking.
- **Result:** +12.5% precision on AWS S3 documentation (precision 0.825 vs. baseline). Content type was one of several metadata fields; the study does not isolate its individual contribution.
- **Source:** [Meta-RAG framework overview](https://www.emergentmind.com/topics/meta-rag-framework)

### Multi-Meta-RAG (Poliakov & Shvai, 2024) -- Source and date filtering

- **Method:** LLM extracts symbolic metadata (source publication, date, permissions) from queries; vector DB is filtered before embedding search.
- **Result:** Google PaLM accuracy +25.6% (0.47 to 0.608); GPT-4 accuracy +7.89% (0.56 to 0.606) on MultiHop-RAG benchmark.
- **Limitation:** Requires domain-specific prompt templates for metadata extraction.
- **Source:** [arXiv:2406.13213](https://arxiv.org/abs/2406.13213); published at ICTERI 2024.

### Anthropic Contextual Retrieval (2024) -- Chunk context summaries

- **Method:** Prepend a document-context summary sentence to each chunk before embedding. Combined with BM25 and reranking.
- **Result:** Top-20-chunk retrieval failure rate reduced by 67% (5.7% to 1.9%) with full pipeline (contextual embeddings + contextual BM25 + reranking). Contextual embeddings alone: -35%; with BM25: -49%.
- **Cost:** ~$1.02 per million document tokens for contextualization (via prompt caching).
- **Source:** [Anthropic blog](https://www.anthropic.com/news/contextual-retrieval)

### Metadata-Driven RAG for Financial QA (2025) -- Thematic clusters + entities

- **Method:** Memory RAG with semantically rich entity representations and thematic clustering on FinanceBench dataset.
- **Result:** F1 score +18.2% (32.9 to 38.9); Faithfulness +6.9% (76.4 to 81.7); Hallucination rate nearly halved (18.5% to 12.2%).
- **Caution:** Chunk expansion techniques that boost recall can simultaneously increase hallucination in financial domains -- the paper notes this tradeoff explicitly.
- **Source:** [arXiv:2510.24402](https://arxiv.org/html/2510.24402v1)

### Legal RAG (Maniyar et al., 2026) -- Document metadata on MAUD and PrivacyQA

- **Method:** Metadata-enriched hybrid RAG (parties, financial terms, legal sections, jurisdictions) + DPO for safe refusal when context is inadequate.
- **Results by dataset:**
  - **MAUD** (merger agreements): Document Retrieval Mismatch reduced from 87% to 13% (-84% relative). Span recall improved ~320% relative at k=64. Highly structured contracts benefit enormously from metadata.
  - **PrivacyQA** (privacy policies): Minimal improvement. Both baseline and enriched reach ~113% span recall at k=64. Simple document structure limits metadata discriminative value.
  - **Australian Legal QA:** Moderate gains. Span recall +35-40%, DRM reduction 18-28%.
- **Takeaway:** Metadata enrichment benefit is highly domain-dependent. Structured, typed documents gain the most.
- **Source:** [arXiv:2603.19251](https://arxiv.org/abs/2603.19251)

### MEGA-RAG (Xu et al., 2025) -- Multi-evidence guided answer refinement

- **Method:** Multi-evidence retrieval with answer refinement for public health domains. Designed to mitigate hallucinations by cross-referencing multiple retrieved evidence passages.
- **Result:** Described as "highly effective in generating factually reliable and medically accurate responses." Related multi-source RAG approaches show 35-60% error reduction in hybrid architectures.
- **Note:** Specific hallucination reduction percentages vary by configuration; the -40% figure comes from the broader multi-source RAG literature on hybrid architectures rather than MEGA-RAG alone.
- **Source:** [Frontiers in Public Health](https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2025.1635381/full)

### SELF-RAG (Asai et al., 2024, ICLR Oral) -- Runtime relevance/support assessment

- **Method:** Model generates special reflection tokens (`ISREL`, `ISSUP`, `ISUSE`) to self-assess whether retrieved passages are relevant and whether generated text is supported by evidence. No pre-labeling of chunks; assessment happens at generation time.
- **Result:** On ASQA benchmark, 13B model achieves citation precision 70.3% and recall 71.3%. Outperforms ChatGPT in citation precision. Increasing the `ISSUP` weight improves citation precision at the cost of fluency (MAUVE score).
- **Key insight:** Runtime epistemic assessment (is this chunk relevant? does it support this claim?) works. But SELF-RAG assesses at generation time, not at index time.
- **Source:** [ICLR 2024 Oral](https://openreview.net/forum?id=hSyW5go0v8); [arXiv:2310.11511](https://arxiv.org/abs/2310.11511)

---

## 4. Evidence Gap: Epistemic Type Labels Specifically

**No study tests fact/hypothesis/opinion labels on chunks and measures their impact on retrieval quality.**

The closest work:
- **Mishra et al. (2025):** Classifies content *type* (tutorial, reference, FAQ, etc.) -- structural, not epistemic. Does not distinguish "this is a fact" from "this is a hypothesis."
- **SELF-RAG (2024):** Performs epistemic assessment at runtime (is this passage relevant? does it support the claim?) but does not pre-label chunks with an epistemic type.
- **Medical RAG literature:** Shows that corpus-level source type selection matters (clinical guidelines outperform abstracts for treatment questions). But this operates at corpus/document level, not chunk level.
- **Legal RAG (2026):** Uses document metadata (parties, jurisdiction, document type) which partially overlaps with epistemic context but is structural, not about the assertional status of a claim.

**The fundamental gap:** We have strong evidence that metadata enrichment improves retrieval (Section 3). We have no evidence that *epistemic type* metadata specifically improves retrieval over other metadata types. The hypothesis is plausible but untested.

---

## 5. Classification Feasibility

### Human agreement on epistemic categories

- **Rubin (2007):** 4 annotators on 272 sentences from NYT articles. Five certainty levels (absolute, high, moderate, low, uncertainty). Inter-annotator agreement was low -- individual perceptions of boundaries between levels were "highly subjective."
- **Rubin (2006, 2010):** Collapsing to 3 categories (high certainty, two lower levels) yielded "significant degree of inter-annotator agreement." The 3-category version is workable; the 5-category version is not.
- **Prieto et al. (2020):** 3,221 author-annotated scholarly assertions. Three certainty categories. Machine learning classifier achieved **89.2% cross-validation accuracy** against author-annotated corpus and 82.2% against publicly-annotated corpus. Kappa of 0.649 (substantial) for majority rule vs. author classification.
- **Source:** Rubin 2007: [ACL Anthology](https://aclanthology.org/N07-2036/); Prieto et al. 2020: [PeerJ](https://peerj.com/articles/8871/)

**Summary:** Human agreement at 5 categories is approximately 67% (poor). At 3 categories, agreement rises to approximately 91% (Prieto's classifier accuracy as a proxy for the upper bound of what well-defined categories enable).

### LLM zero-shot classification

- **"Facts are Harder Than Opinions" (2025):** Evaluated GPT-4o, GPT-3.5 Turbo, LLaMA 3.1, Mixtral 8x7B on 61,514 claims across multiple languages. GPT-4o achieved highest accuracy but declined to classify 43% of claims. Opinion-based claims had 2.7x lower odds of misclassification than fact-based claims. Factual-sounding claims are harder to verify because they require real-world knowledge, not just linguistic cues.
- **Source:** [arXiv:2506.03655](https://arxiv.org/abs/2506.03655)

Note: This study evaluates fact-*checking* (is this claim true?), not fact-*typing* (is this claim stated as a fact or as a hypothesis?). The tasks are related but distinct. Fact-typing relies on linguistic markers ("we hypothesize that..." vs. "studies show that...") which are more tractable than verifying truth.

### Fine-tuned models

Fine-tuned models outperform zero-shot by 5-69 percentage points depending on task granularity and domain (per meta-analyses in the NLP literature). Prieto's 89.2% was achieved with a modest 5-layer neural network on 3,221 examples.

### Practical recommendation for Klai

1. **Use 3 categories, not 5.** Map to: `assertion` (factual + quoted), `speculation` (hypothesis + belief), `procedure` (procedural). Human agreement and classifier accuracy are dramatically better at 3 categories.
2. **Piggyback on existing HyPE LLM call.** MDKeyChunker (2026) demonstrates that a single LLM call can extract 7 metadata fields simultaneously. Adding assertion mode classification as an additional field in the existing HyPE prompt is feasible with minimal latency cost.
3. **Confidence gating.** Only apply the label when the LLM confidence exceeds 85%. Below that threshold, default to a neutral value. This raises effective accuracy to ~90%+ by avoiding low-confidence edge cases.

---

## 6. Error Asymmetry

Not all misclassification errors are equal:

| Error direction | Risk level | Consequence |
|---|---|---|
| Hypothesis labeled as fact | **HIGH** | User trusts unverified information. Could lead to decisions based on speculative content presented as established knowledge. |
| Fact labeled as hypothesis | **MEDIUM** | User seeks unnecessary verification. Annoying but safe -- errs on the side of caution. |
| Procedure labeled as assertion | **LOW** | Minor relevance mismatch. User gets factual content when seeking instructions, or vice versa. |

**Conservative strategy:** When uncertain, the system should label toward `speculation`. A false conservative label degrades user experience slightly (unnecessary hedging). A false confident label degrades trust fundamentally (presenting speculation as fact).

This asymmetry has a direct implementation consequence: the classification threshold for `assertion` should be higher than for `speculation`. If the model is 70% confident something is a fact, it should still be labeled `speculation`.

---

## 7. User-Facing Confidence Display

Should Klai show assertion mode labels or confidence indicators to end users?

### CHI 2024: Showing AI confidence does NOT improve task accuracy

- Multiple CHI 2024 studies found a trust-performance paradox: showing AI confidence helps users *calibrate reliance* (they adjust behavior based on confidence) but does NOT improve actual task performance.
- "Increased self-reported trust doesn't necessarily correlate with improved reliance behaviors." (CHI 2024, Impact of Model Interpretability)
- Miscalibrated confidence is actively harmful -- both overconfidence and underconfidence increase error rates.
- **Source:** [CHI 2024](https://dl.acm.org/doi/10.1145/3613904.3642780); [arXiv:2402.07632](https://arxiv.org/html/2402.07632v4)

### ACL 2024: Overconfident epistemic markers cause lasting trust damage

- Zhou et al. (ACL 2024): When LLMs express overconfidence, user trust is damaged. Critically, **lowered scores persist in later calibrated rounds** -- trust damage from overconfidence is sticky even after the model returns to being well-calibrated.
- 15.22% of GPT-4o generations with strong epistemic markers are incorrect; for LLaMA 70B: 39.15%; for LLaMA 8B: 49.04%.
- **Source:** [ACL 2024](https://aclanthology.org/2024.acl-long.198.pdf)

### ACL 2025: LLMs cannot reliably produce calibrated epistemic markers

- Liu et al. (ACL 2025): Epistemic markers (e.g., "fairly confident", "possibly") generalize within the same distribution but are **inconsistent in out-of-distribution scenarios**. A model's use of "I'm fairly sure" does not reliably correlate with actual correctness across different domains.
- Lee et al. (NAACL 2025): LLM-judges show negative bias toward uncertainty markers -- text with hedging language is rated lower regardless of actual correctness.
- **Source:** [ACL 2025](https://aclanthology.org/2025.acl-short.18/)

### Conclusion for Klai

**Surfacing epistemic labels directly to users is risky.** The evidence suggests:
1. Confidence indicators do not improve task accuracy (CHI 2024).
2. Miscalibrated indicators cause lasting trust damage (ACL 2024).
3. LLMs cannot produce calibrated epistemic markers (ACL 2025).

**Safer approach:** Use assertion mode as an *internal* ranking/filtering signal in the retrieval pipeline. Do not expose raw epistemic labels to end users. If any indicator is shown, it should be at the source level ("from clinical guidelines" vs. "from discussion forum"), not at the claim level ("fact" vs. "hypothesis").

---

## 8. Klai Implementation Status

### What exists

| Component | Status | Details |
|---|---|---|
| DB schema | Done | `knowledge.artifacts.assertion_mode`, 5-value enum: `factual`, `procedural`, `quoted`, `belief`, `hypothesis` |
| Ingest parsing | Done | Extracted from YAML frontmatter at ingest (`routes/ingest.py`). Default: `factual`. |
| PostgreSQL storage | Done | `pg_store.py` writes and reads `assertion_mode`. |
| Frontend display | Done | Badge shown in knowledge base item list (`$kbSlug.tsx`). |
| MCP tool interface | Done, with mapping gap | MCP tools (`main.py`) accept `{fact, claim, note}` -- these do not align with DB values `{factual, procedural, quoted, belief, hypothesis}`. Fallback: invalid values map to `note`. |

### What does NOT exist

| Component | Status | Details |
|---|---|---|
| HyPE classification | Missing | The HyPE prompt (hypothetical question generation) does NOT classify assertion mode. It is set only from frontmatter, not from content analysis. |
| Qdrant payload | Not used | `qdrant_store.py` does not include `assertion_mode` in the Qdrant point payload. Cannot be used as a filter or reranking signal. |
| Retrieval API | Missing | No retrieval endpoint returns `assertion_mode`. It is a write-only field from the retrieval perspective. |
| Reranker weighting | Missing | No reranking step uses assertion mode as a signal. |
| Taxonomy alignment | Broken | DB: 5 values. MCP: 3 values (`fact`, `claim`, `note`). No mapping layer. `note` in MCP has no clear DB equivalent. |

### Summary

The gap is entirely in **consumption**, not storage. The schema and ingest pipeline are complete. Everything downstream of storage -- retrieval, reranking, generation context -- ignores assertion mode.

---

## 9. Open Questions

1. **Does assertion mode as a Qdrant filter or reranker weight improve retrieval quality?**
   Untested. Would require an A/B evaluation on Klai's actual content mix. The evidence from Section 3 suggests metadata enrichment helps, but the specific benefit of epistemic type labels (vs. other metadata types) is unknown.

2. **Does a 3-category taxonomy work for Klai's content mix?**
   Needs a 200-sample evaluation. Annotate 200 chunks from representative knowledge bases, classify them into `assertion` / `speculation` / `procedure`, measure inter-annotator agreement. If agreement is below 80%, the categories need refinement.

3. **Can the HyPE prompt reliably classify assertion mode as a marginal addition?**
   Needs prompt engineering + evaluation. MDKeyChunker (2026) shows multi-field extraction in a single LLM call is feasible. The question is whether adding assertion mode classification degrades other field quality or reduces overall throughput.

4. **Is the benefit domain-dependent?**
   Almost certainly yes. Legal RAG (2026) showed dramatic improvement on structured contracts (MAUD) but near-zero on privacy policies. Klai serves mixed-domain tenants. The benefit may vary significantly across customers.

5. **Should the MCP taxonomy be aligned with the DB taxonomy?**
   Yes, regardless of whether assertion mode becomes active in retrieval. The current mapping gap (`fact`/`claim`/`note` vs. `factual`/`procedural`/`quoted`/`belief`/`hypothesis`) creates data quality issues at the point of entry.

6. **What is the minimum viable experiment?**
   Add `assertion_mode` to Qdrant point payload. Build a test set of 50 queries with known-relevant chunks spanning multiple assertion modes. Measure whether filtering or boosting by assertion mode changes recall@10 or precision@10. This can be done without any classification model -- use the existing frontmatter-derived labels.

---

## 10. Key Sources

### Metadata enrichment in RAG

| Citation | URL |
|---|---|
| Mishra et al. (2025). Meta-RAG: content type classification on AWS S3 docs. | [emergentmind.com](https://www.emergentmind.com/topics/meta-rag-framework) |
| Poliakov, M. & Shvai, N. (2024). Multi-Meta-RAG: Improving RAG for Multi-Hop Queries using Database Filtering with LLM-Extracted Metadata. ICTERI 2024. | [arXiv:2406.13213](https://arxiv.org/abs/2406.13213) |
| Anthropic (2024). Contextual Retrieval. | [anthropic.com](https://www.anthropic.com/news/contextual-retrieval) |
| Metadata-Driven RAG for Financial QA (2025). FinanceBench evaluation. | [arXiv:2510.24402](https://arxiv.org/html/2510.24402v1) |
| Maniyar et al. (2026). Enhancing Legal LLMs through Metadata-Enriched RAG Pipelines and Direct Preference Optimization. | [arXiv:2603.19251](https://arxiv.org/abs/2603.19251) |
| Xu, S. et al. (2025). MEGA-RAG: Multi-Evidence Guided Answer Refinement for Mitigating Hallucinations. Frontiers in Public Health. | [frontiersin.org](https://www.frontiersin.org/journals/public-health/articles/10.3389/fpubh.2025.1635381/full) |
| Asai, A. et al. (2024). Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection. ICLR 2024 (Oral). | [arXiv:2310.11511](https://arxiv.org/abs/2310.11511) |

### Epistemic classification and certainty

| Citation | URL |
|---|---|
| Rubin, V.L. (2007). Stating with Certainty or Stating with Doubt: Intercoder Reliability Results for Manual Annotation of Epistemically Modalized Statements. NAACL 2007. | [ACL Anthology](https://aclanthology.org/N07-2036/) |
| Prieto, M. et al. (2020). Data-driven classification of the certainty of scholarly assertions. PeerJ. | [peerj.com](https://peerj.com/articles/8871/) |
| Aicher, C. et al. (2025). Facts are Harder Than Opinions -- A Multilingual, Comparative Analysis of LLM-Based Fact-Checking Reliability. | [arXiv:2506.03655](https://arxiv.org/abs/2506.03655) |

### Epistemic building blocks

| Citation | URL |
|---|---|
| Kuhn, T. et al. (2018). Nanopublications: A Growing Resource of Provenance-Centric Scientific Linked Data. | [arXiv:1809.06532](https://arxiv.org/abs/1809.06532) |
| TrustGraph. Context Graphs: AI-Optimized Knowledge Graphs. | [trustgraph.ai](https://trustgraph.ai/guides/key-concepts/context-graphs/) |
| MDKeyChunker (2026). Single-Call LLM Enrichment with Rolling Keys and Key-Based Restructuring for High-Accuracy RAG. | [arXiv:2603.23533](https://arxiv.org/abs/2603.23533) |

### User-facing confidence and trust

| Citation | URL |
|---|---|
| CHI 2024. Impact of Model Interpretability and Outcome Feedback on Trust in AI. | [ACM DL](https://dl.acm.org/doi/10.1145/3613904.3642780) |
| CHI 2024. "Are You Really Sure?" Understanding the Effects of Human Self-Confidence Calibration in AI-Assisted Decision Making. | [arXiv:2403.09552](https://arxiv.org/html/2403.09552) |
| Zhou et al. (2024). Relying on the Unreliable: The Impact of Language Models' Reluctance to Express Uncertainty. ACL 2024. | [ACL Anthology](https://aclanthology.org/2024.acl-long.198.pdf) |
| Liu, J. et al. (2025). Revisiting Epistemic Markers in Confidence Estimation. ACL 2025. | [ACL Anthology](https://aclanthology.org/2025.acl-short.18/) |
| Lee, D. et al. (2025). Are LLM-Judges Robust to Expressions of Uncertainty? NAACL 2025. | [ACL Anthology](https://aclanthology.org/2025.naacl-long.452/) |

---

*End of document. Last updated 2026-03-28.*
