# Research — SPEC-INGEST-LOGIN-WALL-DETECT-002

Date: 2026-05-06
Author: Mark Vletter
Status: foundational — informs spec.md and plan.md

This document captures the research and the design journey behind v2. It
deliberately documents the approaches considered AND REJECTED, with the
reasoning. Future maintainers should be able to read this and understand
why we did not, for example, use the soft404 library or stick with phrase
matching.

---

## 1. Why v1 needed redesigning

### 1.1 v1 design summary

`SPEC-INGEST-LOGIN-WALL-DETECT-001` (Phase A, shipped in PR #419) used a
two-tier rule:

- **Tier 1 (STRONG)**: substring match against a list of "canonical phrases"
  (12 EN + 4 NL). A single match flagged the page as a wall regardless of
  surrounding context. The FP-guard explicitly did NOT override Tier 1.
- **Tier 2 (WEAK)**: structural conditions (B/C/D — redirect density ≥ 5,
  login-link repetition ≥ 5, content-to-login ratio).

The premise was that canonical phrases are unambiguous wall markers.
Production data proved that wrong.

### 1.2 Production canary findings (2026-05-06)

Read-only application of the v1 detector on voys/support corpus
(422 pages):

| Outcome | Count | Examples |
|---|---|---|
| True positives | 150 | All `wiki.redcactus.cloud/*` walled stubs |
| False positives | 4 (NL) | `meld u aan`, `in te loggen` matched on Bubble setup tutorials and the Voys account-recovery FAQ |

After PR #432 attempted to fix the NL FPs by tightening NL phrases, a
re-scan caught 1 additional EN FP:

| FP URL | Matched phrase | Actual content |
|---|---|---|
| `https://help.voys.nl/2fa-freedom` | `log in with your` | 2FA setup tutorial; "Log in with your username and password" appears as STEP 2 of a numbered list |

Empirical phrase-level audit:

```
Phrase distribution across 150 voys/support flagged pages
  149  "have to log in"          (matched first per evaluation order)
    1  "log in with your"        (only on /2fa-freedom — confirmed FP)
    0  All other 10 EN phrases   (zero unique TPs)
```

**Key finding**: of the 12 EN canonical phrases, only `"have to log in"`
contributed unique TPs in production. The other 11 phrases collectively
produced 1 TP and 1 FP — net negative signal.

### 1.3 The deeper problem v1 missed

The 149 `have to log in` matches are not 149 distinct examples. They are
149 copies of the same RedCactus boilerplate template:

> "If you want to read this article, you will have to log in with your Red
> Cactus account."

So v1 was effectively validated on **one wall pattern × 149 instances** —
one datapoint, not 149.

This reveals what walls *actually are*:

> A login wall is a page where the source CMS serves a TEMPLATED STUB to
> anonymous visitors INSTEAD of the article's real content. The page is
> not the article — it is a substitute, by definition lacking unique
> content of its own.

The structural fact ("this page is a template repeated across many
URLs") is the wall. The phrasing ("have to log in") is incidental — a
side-effect of the CMS template being lexically similar across copies.

v1 detected a side-effect, not the cause. That is why every adjustment
(NL fix in PR #432, EN fix proposed) was a patch on the wrong abstraction.

### 1.4 Conditions B/C/D were noise

The Tier-2 structural conditions in v1 (redirect density ≥ 5, login-link
repetition ≥ 5, content-to-login ratio) were intended as fallbacks. Production data:

| Feature | Real walls (median) | v1 threshold | Fires? |
|---|---|---|---|
| `redirect_to=` count | 2 | ≥ 5 | NO |
| Login-anchor repetition | 2 | ≥ 5 | NO |
| Content/login ratio | n/a (long boilerplate) | varied | NO |

**No real wall in production triggers Conditions B/C/D.** They contributed
zero TPs and added FP risk on legitimate tutorials with sign-in chrome.
Pure noise.

---

## 2. The right framing — template detection, not phrase matching

### 2.1 What walls actually are

Walls are templates served INSTEAD of content. Their distinguishing feature
is that the SAME content is served across many URLs:

| Property | Wall | Real article |
|---|---|---|
| Content variation across pages | minimal (URL + 1-2 lines) | substantial (different topic, different prose) |
| Cluster behaviour in corpus | tight (149 near-identical copies) | dispersed (each unique) |
| Phrase content | side-effect of template | reflects topic |

A detector that targets the *cause* (templated content) rather than the
*side-effect* (phrasing) generalises across CMS, language, and tenant.

### 2.2 What the field actually does at scale

The field of soft-404 / boilerplate / paywall detection at scale uses
**near-duplicate detection over a corpus**:

- **Google "template match"** (public soft-404 documentation): detects when
  page content matches a site's known error-page template. Pages clustered
  by content similarity within the same site.
- **Boilerpipe** (Kohlschütter et al., 2010, "Boilerplate Detection using
  Shallow Text Features"): uses text-density features per DOM block, but at
  corpus scale combines with cross-page block-similarity to identify
  recurring boilerplate.
- **MinHash / SimHash** (Charikar 2002, Broder 1997): the standard
  near-duplicate detection primitives. Used by Google web index (for
  duplicate URL collapsing), Common Crawl, and ArchiveTeam for
  deduplication at billions-of-pages scale.
- **Trafilatura** (Barbaresi 2021, ACL Demo): rule-based content extractor
  with per-block link-density penalties, but its handling of templated
  content includes recommending corpus-level dedup as a downstream filter.

The common pattern: **a page is a template stub if N+ other pages in the
same source share its content fingerprint**. The fingerprint is a hash
that is robust to small per-page variations (URL, timestamp, etc.) but
sensitive to wholesale content differences.

### 2.3 Why this fits klai's actual problem

klai's symptom: 149 RedCactus pages return the same content with different
URLs. That is the textbook MinHash near-duplicate cluster.

klai's data: each KB has 100s–1000s of pages, well within brute-force
similarity scan range (no need for LSH bucketing infrastructure).

klai's environment: knowledge-ingest already computes `content_hash` (sha256
of fit_markdown text) per page. Adding a similarity-tolerant fingerprint
alongside it is a small extension.

---

## 3. Alternatives considered and rejected

This section is the most important part of this document. Each alternative
below was a serious candidate; documenting why each was rejected prevents
repeated rediscovery of dead ends.

### 3.1 Substring phrase matching (v1, rejected)

Approach: a list of "canonical phrases"; substring match flags a wall.

Why rejected:
- Production canary on 422 voys pages: 5 FPs (4 NL + 1 EN) at 2.6% rate.
- Phrase tightening (PR #432) reduced FP-rate but did not eliminate the
  category. Each tightening risks losing TPs while still leaving FP holes.
- v1 was empirically validated on **one wall pattern × 149 instances**, not
  149 distinct examples. "100% precision on voys" is misleading.
- Phrases are language-specific. Each new tenant onboarding could require
  curating new phrases per language and per CMS — unbounded maintenance.
- Phrases detect a SIDE-EFFECT of templating, not the cause. The wall is
  the structural fact "same content, many URLs", not the words.

Verdict: brittle by design. The right abstraction is the structural fact.

### 3.2 Multi-feature deterministic scorer (rejected)

Approach: combine 4–5 weak signals (obligation phrase, link density,
redirect density, fit_markdown brevity, chrome dominance) into a tiered
score (Tier 1 phrase, Tier 2 structural).

Why rejected:
- Solves a different problem than klai has. This pattern is appropriate
  for unsupervised classification of arbitrary pages where corpus access
  is not available (e.g., a search engine indexing 10^9 unique URLs).
- klai HAS corpus access. Every page is ingested into a tenant's KB; we
  can compare a page to its siblings in the same KB.
- Adds 5 features × thresholds × calibration framework — engineering
  theatre for what is fundamentally a clustering problem.
- Empirical data on real walls: structural features (link density,
  redirect density) do NOT fire on RedCactus walls (median 2 redirects,
  not 5). The features were chosen by analogy to soft-404 research that
  targets a different page distribution.
- "Per-page deterministic scoring" cannot beat "compare to other pages in
  the same source" when the latter is available.

Verdict: overengineering for a corpus-aware setting.

### 3.3 ML classifier — soft404 pip library (rejected)

Approach: `pip install soft404; soft404.probability(html) > 0.7`.

Why rejected:
- Library appears unmaintained (TeamHG-Memex/soft404 README itself points
  to "an alternative fork with newer Python and library versions").
- Trained on 120k pages with English-heavy bias. klai content is
  Dutch-primary; performance on non-English content is unmeasured.
- Black-box ML — explaining a TP/FP decision to a tenant operator
  requires reverse-engineering the model output. Not aligned with
  klai's preference for explainable systems.
- Requires HTML input. klai stores `raw_markdown` in
  `knowledge.crawled_pages`, not raw HTML. Backfill compatibility would
  require either re-fetching HTML through crawl4ai (slow) or schema
  extension to store HTML (storage cost).
- Adds runtime ML dependency (scikit-learn + serialised model file) to a
  service that currently does not load any ML libraries on the ingest
  path. Increases image size and cold-start time.
- Solves the classification problem in the same per-page deterministic
  way as 3.2 above — does not exploit corpus access.

Verdict: black-box dependency for a problem we can solve transparently.

### 3.4 Trafilatura content extractor (rejected as primary signal)

Approach: run `trafilatura.extract(html, min_extracted_size=N)`; if extraction
returns less than N words, treat as wall.

Why rejected as primary signal:
- crawl4ai already performs content extraction (`fit_markdown`) using a
  similar rule-based density approach. Layering trafilatura on top is
  duplicate work.
- We do not store HTML, only `raw_markdown`. Trafilatura's pruning
  algorithms work best on HTML DOM, not on already-extracted markdown.
- A length-only filter is too coarse: legitimate short pages (FAQs,
  redirect notices, glossary entries) would be flagged. We measured this:
  the production tutorial /2fa-freedom has only 364 words after stripping
  anchors — below a 500-word threshold.

Why considered: as a secondary check, trafilatura's quality filter can
provide an additional signal. We do not use it in v2 because crawl4ai's
`fit_markdown` already provides this signal when available.

Verdict: redundant with crawl4ai; length-only filter has high FP rate.

### 3.5 Structural features only (rejected)

Approach: keep v1's Tier-2 conditions (redirect density, link repetition,
content/login ratio) without phrase matching.

Why rejected:
- Empirical data: real RedCactus walls have median 2 redirects and 2
  login-anchors. v1's threshold of ≥ 5 caught zero walls. Lowering the
  threshold to ≥ 2 would catch walls AND many tutorials with sign-in
  chrome (e.g., a tutorial linking to Bubble + Outlook + Slack login
  pages would trigger).
- These features are "what does a wall look like in isolation" features.
  They are useful when corpus access is unavailable. With corpus access,
  cross-page similarity is dramatically more discriminative.

Verdict: weak signal compared to corpus clustering when corpus is
available.

### 3.6 Length-only filter on fit_markdown (rejected)

Approach: if `fit_markdown` word count below threshold N, treat as wall.

Why rejected:
- Single signal: legitimate short pages (FAQ entries, redirect notices,
  glossary stubs) get flagged. Measurement: the production tutorial
  /2fa-freedom has 364 words content; threshold tuning is fragile.
- Misses walls with substantial template text (e.g., a CMS that fills the
  page with a "what is this site?" essay alongside the login prompt).
- Does not exploit the cross-page redundancy that is the actual wall
  signal.

Verdict: too noisy alone; works only as a secondary signal.

### 3.7 Authenticated re-crawl (out of scope)

Approach: crawl with credentials; pages that show different content
authenticated vs anonymous = wall.

Why out of scope for v2:
- Requires per-tenant credential management and secure cookie storage
  across the crawler.
- Solves a different problem: "make walled content available", not
  "detect that a page is a wall".
- Complementary to v2 but a separate effort. Tracked under the
  `klai-libs/connector-credentials` follow-up issue.

---

## 4. Adopted design — corpus-level near-duplicate detection

### 4.1 Core mechanism

Detect template stubs by their cross-page near-duplicate behaviour:

1. At ingest, compute a similarity-preserving fingerprint of each page's
   normalised text content (URLs, anchors, and per-page variation
   stripped).
2. Store the fingerprint in `knowledge.crawled_pages` alongside the
   existing `content_hash` (which is exact-match only).
3. At detection time, count how many pages in the same `(org_id,
   kb_slug)` have a fingerprint near-identical to the current page's.
4. If the cluster size is ≥ a threshold N (default 5), the page is
   classified as a template stub — i.e., a wall.

### 4.2 Fingerprint choice

SimHash (Charikar 2002) is the default. Properties relevant here:

- 64-bit hash; near-duplicate test via Hamming distance.
- Documented threshold: Hamming ≤ 3 ≈ 95%+ content overlap; Hamming ≤ 5
  ≈ 90%+ overlap. We adopt Hamming ≤ 3 as the default near-duplicate
  threshold and validate against production fixtures.
- SQL-friendly: stored as `bigint`, distance computed via XOR + popcount
  (`bit_count(a # b)` in PostgreSQL 14+).
- Linear-scan within a single KB is sub-millisecond at klai's scale
  (low thousands of pages per KB). Bucketing infrastructure (LSH banding)
  is not needed at this scale.

MinHash is a viable alternative for set-based Jaccard similarity. We
prefer SimHash because:
- One scalar fingerprint per page (vs MinHash's signature vector).
- Simpler PostgreSQL storage and query.
- Equally effective for "this content is the same template" detection at
  klai's content lengths.

If empirical validation reveals that SimHash misses real-world walls due
to per-page variation (URL substitution dominating the hash), we fall
back to MinHash with banded LSH. This is documented as a Phase D
contingency.

### 4.3 Pre-fingerprint normalisation

Walls share a template but each instance contains a unique URL (the
page's own canonical URL appears in the boilerplate). Without
normalisation, the URL variation reduces similarity below threshold.

Normalisation steps before hashing:
1. Replace all URLs with a placeholder `<URL>`.
2. Replace all markdown anchor text with the inner text only (drop the
   URL).
3. Lowercase and collapse whitespace.
4. Tokenise on word boundaries.

This isolates the textual template from per-page accidents.

### 4.4 Cluster-size threshold

Default: a page is flagged as wall if ≥ 5 OTHER pages in the same KB have
a fingerprint within Hamming distance 3 of its own.

Rationale for ≥ 5:
- Real RedCactus walls: 149 instances, easily clears 5.
- Single-page wall (a tenant onboarding with one walled URL): NOT
  flagged. Acceptable: one stub does not pollute retrieval; the
  detection accumulates as the corpus grows.
- Real templates that are NOT walls (consistent header/footer): even if
  the boilerplate dominates, these pages also have substantial unique
  content (the article body), pushing fingerprints apart. We validate
  this empirically against the captured FP fixtures.

Threshold is configurable per-tenant via env var
`KLAI_INGEST_TEMPLATE_CLUSTER_MIN`. Operators can tune for tenants with
unusual content distributions.

### 4.5 Cold-start behaviour

A new tenant with few pages cannot form clusters. The detector returns
"not a wall" for all pages until a cluster forms. This is the right
behaviour:

- Single-page walls do not pollute retrieval at cold-start.
- As the tenant's corpus grows, walled pages accumulate; the first time
  a cluster forms, all members are simultaneously flagged.
- Backfill task re-runs detection across the whole KB, so retroactive
  cluster discovery happens automatically.

Cold-start protection: NO phrase fallback. The system explicitly
declines to detect walls in tenants with ≤ 4 same-template pages, on
the principle that one or two stub pages do not constitute a retrieval
problem.

### 4.6 Recovery for FP-purged pages

The /2fa-freedom page was purged by the v1 detector with placeholder
`content_hash = '__login_wall_purged__'`. Recovery under v2:

1. Clear the placeholder hash for any page whose v2 fingerprint does NOT
   appear in a wall cluster.
2. Trigger re-ingest at next scheduled crawl (the placeholder ≠ live
   content_hash forces re-fetch).
3. The page returns to the KB with a fresh fingerprint, classified
   correctly under v2.

---

## 5. Operational implications

### 5.1 New dependencies

- Python `simhash` library OR a small in-tree implementation (~50 LOC).
  Recommendation: in-tree implementation to avoid dependency churn.
- No new database engine. PostgreSQL `bit_count` exists since v14;
  klai-postgres is on v17.

### 5.2 Schema changes

Single new column on `knowledge.crawled_pages`:

```sql
ALTER TABLE knowledge.crawled_pages
ADD COLUMN content_simhash bigint;

CREATE INDEX idx_crawled_pages_simhash_org_kb
ON knowledge.crawled_pages (org_id, kb_slug, content_simhash);
```

The index supports the cluster-size scan within a tenant's KB.

### 5.3 Backfill cost

Compute SimHash for all existing pages: ~422 pages voys + ~5 pages
getklai = ~430 pages. SimHash compute is < 10ms per page; full backfill
runs in seconds.

Cluster scan: O(N²) within each KB. For voys's 422 pages: ~178k pairwise
comparisons, sub-second.

### 5.4 Removed code

The v1 detector and its dependents (`_OBLIGATION_EN`, `_OBLIGATION_NL`,
`_match_canonical`, conditions B/C/D code paths, fixture sets for
phrase-based testing) are deleted. The replaced public symbol is
`detect_anonymous_auth_wall`; the new function takes the same inputs and
returns the same `AuthWallSignal | None` shape, so callers
(`_ingest_crawl_result`, `backfill_tasks`) need no signature changes.

---

## 6. References

### Implementations
- TeamHG-Memex/soft404 — https://github.com/TeamHG-Memex/soft404
- Mozilla Readability — https://github.com/mozilla/readability
- Internet Archive tarb_soft404 — https://github.com/internetarchive/tarb_soft404
- Trafilatura — https://trafilatura.readthedocs.io/
- `simhash` Python library — https://github.com/seomoz/simhash-py

### Foundational research
- Charikar 2002, "Similarity Estimation Techniques from Rounding Algorithms"
  (introduces SimHash) — STOC 2002.
- Broder 1997, "On the Resemblance and Containment of Documents" (introduces
  MinHash) — Compression and Complexity of Sequences.
- Manku, Jain, Sarma 2007, "Detecting Near-Duplicates for Web Crawling" —
  WWW 2007. (Google-internal report; SimHash + LSH banding for the web
  index.)
- Kohlschütter, Fankhauser, Nejdl 2010, "Boilerplate Detection using
  Shallow Text Features" — WSDM 2010 (Boilerpipe origin).
- Sun, Wu, Yang, Zhang 2011, "Content Extraction via Text Density" —
  SIGIR 2011.
- Barbaresi 2021, "Trafilatura: A Web Scraping Library and Command-Line
  Tool for Text Discovery and Extraction" — ACL Demo 2021.
- "An Empirical Comparison of Web Content Extraction Algorithms" — SIGIR
  2023, https://dl.acm.org/doi/10.1145/3539618.3591920.

### Klai-specific
- SPEC-INGEST-LOGIN-WALL-DETECT-001 (the v1 detector this SPEC supersedes).
- PR #419 (Phase A–E shipping v1).
- PR #432 (NL phrase tightening — superseded by v2's structural approach).
- 2026-05-06 production canary on voys/support — finding 5 FPs at 2.6%
  rate, leading to this redesign.
