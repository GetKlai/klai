---
id: SPEC-KB-SOURCES-001
version: "1.5.0"
status: implemented
created: 2026-04-24
updated: 2026-04-24
author: Mark Vletter
priority: high
source_inspiration: |
  klai-focus/research-api on origin/feat/chat-first-redesign — specifically
  app/services/youtube.py, app/services/docling.py, app/api/sources.py and
  app/services/ingestion.py. Code is not ported verbatim; the three extractor
  shapes (URL crawl → markdown, YouTube → transcript, text → raw) are lifted
  as concepts and rebuilt against the current knowledge-ingest sink.
---

# SPEC-KB-SOURCES-001: Restore URL / YouTube / Text ingest paths on Knowledge

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-04-24 | 1.0.0 | Initial draft. Activates the three "Coming soon" tiles on the unified add-source grid (PR #139) by wiring them to the existing knowledge-ingest pipeline. Research-api is **not** resurrected; the three extractor helpers are ported into portal-api as thin routes that forward to `POST /ingest/v1/document`. URL and YouTube are separate tiles per user preference (no auto-detect collapse). |
| 2026-04-24 | 1.1.0 | Post-review tweaks. **Dedup via stable source_ref** (D7/R4): re-submitting the same URL / YouTube video / identical text is a no-op on the ingest pipeline, not a duplicate row — knowledge-ingest already honours source_ref + content-hash dedup. **Text and URL/YouTube go direct to knowledge-ingest, not via Gitea** (made explicit in D1/architecture; file upload stays on the Gitea path). **Per-hour rate limits removed** (R5): portal-api has no generic per-route rate-limiter today, existing KB-write paths rely on per-plan KB-item quota + middleware auth. This SPEC matches that pattern; SSRF guard stays as a security requirement, not a throughput one. **oembed confirmed** as the YouTube title source (R3.4), public endpoint, no auth. |
| 2026-04-24 | 1.2.0 | **Implemented.** Backend extractors (text / url / youtube) + SSRF guard + three routes under `/api/app/knowledge-bases/{kb_slug}/sources/{type}` landed on branch `feature/SPEC-KB-SOURCES-001`. Frontend tiles activated with active forms + shared `useSourceSubmit` hook + 13 new i18n keys (EN + NL). Status → `implemented`. 124 new tests / 94% coverage on new modules. See "Implementation Notes" at the bottom for scope deltas. |
| 2026-04-24 | 1.3.0 | **Post-prod-smoke YouTube hardening.** Voys smoke test revealed two issues: (a) `youtube-transcript-api` error mapping conflated `RequestBlocked` with `NoTranscriptFound` → users saw "no transcript" on IP-blocked videos (lie); (b) even after fixing the mapping, YouTube blocks core-01's datacenter IP regardless. Changes: swapped `youtube-transcript-api` for **yt-dlp** (Android + web player clients, JSON3 subtitle parsing); split error classification into "no transcript" (422) vs "upstream blocked" (502); added `settings.youtube_proxy_url` for optional residential-proxy retry. Verified on prod container: yt-dlp hits the same datacenter-IP block YouTube raised against `youtube-transcript-api` — "Sign in to confirm you're not a bot". yt-dlp is strictly better (more robust + actively maintained), but not a silver bullet for the datacenter-IP problem. |
| 2026-04-24 | 1.4.0 | **YouTube tile disabled, explainer shipped.** Given that yt-dlp confirms datacenter IPs are blocked and that user-facing cookie upload / OAuth scope hacks were rejected as either too technical or API-limited, we pulled the YouTube tile out of the active upload flow. New state: tile shows "Tijdelijk uit" pill + stays clickable. Click routes to an explainer panel stating WHY (YouTube anti-scraping on datacenter IPs) and WHAT WE'RE DOING (privacy-friendly EU residential proxy), with two concrete alternatives that already work today — URL tile for page metadata + Scribe for audio transcription. Backend route stays live: flipping `available: true` + setting `YOUTUBE_PROXY_URL` in SOPS re-enables the full path with zero code changes. New i18n keys (7 × EN + NL) carry the explainer text. |
| 2026-04-24 | 1.5.0 | **YouTube tile removed from UI entirely** (per product direction: "tijdelijk uit" is noise, just don't show it). Surface cleanup: YouTube dropped from `UploadType` union, `SOURCE_TYPES`, `VALID_UPLOAD_TYPES`, orchestrator switch; `YouTubeSourceForm.tsx` deleted; `SourceTypeTile.tsx` reverted to the 2-state version (no `disabledHint` field, no explainer routing); `SourceKind` in `useSourceSubmit.ts` back to `'url' \| 'text'`; 15 YouTube-specific i18n keys removed from both EN and NL. Backend unchanged — yt-dlp extractor + routes + 51 tests all stay. Re-enabling is now a single-PR restore: re-add the tile entry, the form component, the orchestrator branch, and the i18n keys. |

---

## Summary

The unified add-source grid at `/app/knowledge/$kbSlug/add-source` (SPEC-KB-CONNECTORS-001 Phase 7 + PR #139) currently shows three greyed-out tiles with "Binnenkort beschikbaar":

- **URL** — paste a web page link, ingest its content
- **YouTube** — paste a YouTube link, ingest its transcript
- **Text** — paste raw text (notes, pasted article, quote)

Focus (pre-SPEC-PORTAL-UNIFY-KB-001) had all three working via `research-api`. That service was decommissioned; the frontend tiles were greyed because no portal-api → knowledge-ingest path existed for non-file sources.

This SPEC re-activates the three tiles as **three distinct code paths** on portal-api, each forwarding to the existing `POST /ingest/v1/document` endpoint on knowledge-ingest. The three extractor concepts (crawl, transcript, raw) are lifted from Focus but rebuilt against klai's current infrastructure (crawl4ai instead of Docling, direct knowledge-ingest sink instead of research-api's own DB).

**URL and YouTube are kept as separate tiles** (no `detectUrlType` auto-collapse). Users pick the type explicitly; the backend paths are distinct enough that conflating them in one UI would hide real UX differences (YouTube needs video-ID validation + transcript availability checks; generic URL needs SSRF guarding + crawl4ai fetch).

---

## Motivation

1. **Focus had it, users miss it.** The pre-UNIFY-KB demo surface supported URL and YouTube pastes. Removing them regressed the experience.
2. **Backend sink is already there.** `POST /ingest/v1/document` in knowledge-ingest accepts any content string up to 500KB with `source_type`, `content_type`, `source_ref` and `extra` metadata (see `klai-knowledge-ingest/knowledge_ingest/models.py:9`). No new ingest primitive needed.
3. **crawl4ai is already running.** The HTTP client for URL → markdown conversion is already deployed for the `web_crawler` connector (see `.claude/rules/klai/projects/knowledge.md` → crawl4ai section). Reusing it avoids shipping a second URL-fetch dependency.
4. **youtube-transcript-api is a thin Python lib.** Focus already depended on it; port the usage pattern, not the service around it.
5. **No research-api resurrection.** SPEC-PORTAL-UNIFY-KB-001 explicitly decommissioned it. This SPEC respects that — the new routes live on portal-api, not in a standalone service.

---

## Scope

### In scope

**Backend — portal-api**
- Three new routes under `klai-portal/backend/app/api/app_knowledge_bases.py` (or a new sibling `app_knowledge_sources.py` if the former is already large), gated by the `knowledge` product capability:
  - `POST /api/app/knowledge-bases/{kb_slug}/sources/url`
  - `POST /api/app/knowledge-bases/{kb_slug}/sources/youtube`
  - `POST /api/app/knowledge-bases/{kb_slug}/sources/text`
- Each route validates its input, runs the extractor, and forwards the extracted text to `POST /ingest/v1/document` on knowledge-ingest with the proper `X-Internal-Secret` header via `app.trace.get_trace_headers()` for request-id correlation.
- SSRF validation on URL input: reuse or mirror the validation pattern from knowledge-ingest's existing `validate_url` (rfc1918 / link-local / localhost / docker-internal hostnames blocked).
- Per-tenant rate-limit on URL + YouTube endpoints (both SSRF-capable or external-fetch-capable) to cap blast radius.

**Backend — extractor helpers**
- `klai-portal/backend/app/services/source_extractors/url.py` — calls crawl4ai's HTTP API (`POST http://crawl4ai:11235/crawl` with a single-URL batch) and normalises the response into `(title, markdown)`.
- `klai-portal/backend/app/services/source_extractors/youtube.py` — ports Focus's `extract_video_id` regex + `youtube_transcript_api` fetch, returns `(video_title, transcript_text)`.
- `klai-portal/backend/app/services/source_extractors/text.py` — trivial: validate length + whitespace-normalise, returns `(title or "Untitled note", content)`.
- Each helper raises typed exceptions (`SourceFetchError`, `UnsupportedSourceError`, `RateLimitedError`) that the route layer translates to HTTP statuses.

**Frontend**
- `source-types.ts`: add `youtube` as a new `SourceType`. Update `SOURCE_TYPES` array — URL, YouTube, Text tiles all `available: true`. YouTube gets its own tile with a distinct SiYoutube icon and subtitle "Paste a YouTube link".
- Activate `UrlSourceForm.tsx` — single URL input, submit mutation → `POST /api/app/knowledge-bases/{kb_slug}/sources/url`. Error banner for 4xx/5xx. Success animation + invalidate `kb-items` query + navigate back to items tab.
- Create `YouTubeSourceForm.tsx` (new component) — single URL input with inline YouTube-specific helper text + link validation regex. Submit → `.../sources/youtube`.
- Activate `TextSourceForm.tsx` — title input + multiline textarea (max 500KB). Submit → `.../sources/text`.
- Update the orchestrator `$kbSlug_.add-source.tsx` to route to the right form based on selected upload type (already has the switch; extend the union).

**Dependencies**
- Add `youtube-transcript-api` to `klai-portal/backend/pyproject.toml`.
- crawl4ai is already a network-reachable service; no new Python dep needed for URL extraction (just httpx which is already there).

**i18n**
- 18 new keys (9 en + 9 nl) for form labels, hints, error messages, and the new "YouTube" tile label + subtitle.

**Tests**
- Backend: pytest coverage ≥ 85% on the three new routes + the three extractors. Mock crawl4ai + youtube_transcript_api.
- Frontend: no new unit tests required; lint + typecheck + paraglide compile must be clean.

### Out of scope (explicit)

- **Revive research-api.** SPEC-PORTAL-UNIFY-KB-001 decommissioned it. This SPEC does not bring it back in any form.
- **Docling.** Focus used Docling for URL conversion; klai's standard is crawl4ai. Not re-introducing Docling.
- **Image tile.** Jantine's add-source had an image tile; not in this SPEC. If image-as-source becomes a need, it's a separate SPEC because it requires vision-OCR or captioning.
- **Site-wide crawl.** Paste one URL, ingest one page. Multi-page web-crawler already exists as the `web_crawler` connector; this SPEC does not duplicate it.
- **YouTube playlist.** Paste one video, ingest one transcript. Playlists are out of scope.
- **Transcript-in-other-language fallback.** If no transcript is available in any language, the route returns a 422 — no auto-translate.
- **Proxy rotation for YouTube IP blocks.** Focus had optional `YOUTUBE_PROXY_URL`. Not in this SPEC; flagged as a known limitation if YouTube starts rate-limiting core-01's IP.
- **Rich text / HTML / PDF paste in the Text tile.** Plain text only — anything else should go through the File tile.

### Buiten scope (toekomstig werk)

- RSS feed ingest (possible follow-up SPEC if the market calls for it)
- Bulk URL import (CSV of URLs → batch crawl)
- Re-crawl / re-fetch of a previously-ingested URL source

---

## Architecture

```
Frontend (/app/knowledge/$kbSlug/add-source)
  │
  ├── UrlSourceForm     ─┐
  ├── YouTubeSourceForm  │
  └── TextSourceForm     │
                         ▼
        POST /api/app/knowledge-bases/{kb_slug}/sources/{url|youtube|text}
        (portal-api, authenticated, quota-checked)
                         │
                         │ extractor helper runs:
                         │   url.py    → crawl4ai → markdown
                         │   youtube.py → transcripts  → plain text
                         │   text.py   → raw (validated)
                         │
                         ▼
        POST http://knowledge-ingest:8000/ingest/v1/document
        (X-Internal-Secret + X-Request-ID propagated)
                         │
                         ▼
        IngestRequest → chunker → embedder → Qdrant upsert
        (the existing knowledge-ingest pipeline, unchanged)
```

Key points:
- Every path terminates at the **same** `POST /ingest/v1/document` sink. No new ingest primitive.
- **Text / URL / YouTube bypass Gitea entirely.** They go straight to knowledge-ingest. Gitea stays on the file-upload path (via `klai-docs`, per PR #136) where it belongs — pasted notes and fetched URLs shouldn't bloat the per-KB git repo.
- `source_type` discriminates downstream: `"url"`, `"youtube"`, `"text"` — knowledge-ingest already honours `source_type` string for labelling (see models.py line 15).
- `content_type` values: `"web_page"`, `"youtube_transcript"`, `"plain_text"`.
- **`source_ref` is deliberately stable per logical source** so repeat submissions dedup against the existing row instead of creating a second chunk-set (see D7):
  - URL: canonical URL (`scheme://host/path` with fragment + default port stripped)
  - YouTube: `youtube:{video_id}` (the 11-char YouTube video ID)
  - Text: `text:sha256:{hex}` — hash of the whitespace-normalised content
- `extra`: `{source_url: <original-user-input>, video_id: <yt_id>, original_title: <user-supplied-for-text>}` where applicable — per the "Extra JSONB passthrough" rule in `.claude/rules/klai/projects/knowledge.md`.

---

## Design decisions

### D1: Each source type gets its own route (no dispatcher)

Three POST routes, three URLs. No `POST /sources` with a polymorphic body. Reasons:

- Clearer in code, easier to gate with rate-limits individually
- Easier to document in OpenAPI
- Easier to test — one fixture per route, no polymorphic request-body mocks
- Matches how Focus had it (`/sources` with `type` field) — but improved: URL-based discrimination is more REST-y and makes middleware (SSRF guard, rate-limit) easy to scope per-type.

### D2: Extractors are pure helpers, not services

`source_extractors/url.py` etc. are **functions**, not classes, not FastAPI routers. Input: the raw user input + context (org_id, kb_slug). Output: `(title, content)` tuple. Any failure raises a typed exception.

Route code calls the helper, handles the exception, forwards to knowledge-ingest. Helpers have no knowledge of HTTP / Request / Response — makes them trivially testable with pytest + mock.

### D3: URL and YouTube are separate tiles in UI

Per user preference: no `detectUrlType` auto-collapse. YouTube tile has distinct icon (SiYoutube from react-simple-icons), distinct subtitle, distinct backend path. User intent is explicit; backend paths stay focused.

Rejected alternative: combined URL tile that auto-detects YouTube and routes internally. Reason: hides real UX differences (YouTube may fail with "no transcript available" — generic URL never has that failure mode; separating the tiles lets the error banners speak the user's language).

### D4: crawl4ai for URL, not Docling

Focus used `docling-serve`. klai's current stack has `crawl4ai` deployed for the `web_crawler` connector. Reusing crawl4ai:
- Avoids a second URL-fetch dependency
- Reuses existing ops / monitoring / deploy
- Matches the pattern in `.claude/rules/klai/projects/knowledge.md` ("crawl4ai usage")

The helper calls `POST http://crawl4ai:11235/crawl` with a single-URL batch, parses the markdown result.

### D5: youtube-transcript-api, port Focus's usage

Focus's `youtube.py` did:
1. Regex for video-ID extraction
2. `YouTubeTranscriptApi.get_transcript(video_id)` with language fallback
3. Optional proxy via `YOUTUBE_PROXY_URL` for IP-blocked retries

Port step 1 + step 2 as-is. Step 3 (proxy) is **not** ported in this SPEC — documented as a known limitation. If YouTube starts blocking core-01's IP we add it in a follow-up.

### D6: Security — SSRF guarding on URL + YouTube inputs

Both routes fetch a user-supplied URL. This is a classic SSRF vector. Before handing off to crawl4ai or youtube-transcript-api, the route must:

1. Parse the URL
2. Resolve the hostname via `getaddrinfo`
3. Reject if resolved IP is in: rfc1918 (10/8, 172.16/12, 192.168/16), link-local (169.254/16), loopback (127/8, ::1), IPv6 loopback + link-local + ULA (`::1`, `fe80::/10`, `fc00::/7`), or matches known docker-internal hostnames (`docker-socket-proxy`, `portal-api`, `redis`, `knowledge-ingest`, etc.)
4. Pin the resolved IP when handing to the HTTP client (prevents TOCTOU via DNS rebinding)

This mirrors the pattern already in knowledge-ingest's `validate_url` helper. Reuse it if it's extractable; otherwise mirror the logic in a new portal-api helper. Preferred: extract `validate_url` into `klai-libs/` so portal-api and knowledge-ingest share one implementation — follow-up refactor, not blocking for this SPEC.

SSRF is a security control, not a throughput control. Rate-limiting is addressed separately in D8 (not added here).

### D7: Stable source_ref → natural dedup, no duplicate rows

A second submission of the same URL / YouTube video / identical text **does not create a second row**. Mechanism:

- `source_ref` is computed deterministically per source type (canonical URL, `youtube:{id}`, `text:sha256:{hex}`).
- The existing knowledge-ingest pipeline already honours `source_ref` + content-hash dedup: same `source_ref` + same content → update metadata (timestamps) but no re-embed. Different content on the same `source_ref` (e.g. the web page changed) → re-embed. See `.claude/rules/klai/projects/knowledge.md` → content-addressed storage.
- Text specifically: the sha256 is computed over the whitespace-normalised, NUL-stripped content. The user-supplied `title` is **not** part of the hash, so re-submitting the same paragraph with a different title is still a no-op on the chunk store (the title lives in `extra.original_title`, not in the content).

Result: a user pasting the same YouTube link twice, or the same paragraph twice, sees no surprise — the KB does not grow, the sync badge stays green.

Rejected alternative: generate fresh UUIDs for text. That would create a duplicate row for every re-paste, which matches neither user expectation nor how files are handled elsewhere.

No per-hour rate limit is applied. See D8.

### D8: Error UX — typed exceptions → HTTP statuses

| Helper exception | HTTP status | Frontend message |
|---|---|---|
| `InvalidUrlError` | 400 | "Not a valid URL" |
| `SSRFBlockedError` | 400 | "This URL is not allowed" (deliberately vague) |
| `SourceFetchError` (network timeout, 5xx upstream) | 502 | "Could not reach the page — try again" |
| `UnsupportedSourceError` (YouTube without transcript) | 422 | "This video has no transcript available" |
| `KbQuotaExceededError` (KB full per plan) | 403 | "This KB has reached its document limit" |
| `KbNotFoundError` | 404 | "KB not found" |

All frontend error banners are i18n strings, not literal backend messages.

**No per-hour rate limit on these routes.** Portal-api has no generic per-route rate-limiter today — existing KB-write paths (file upload via klai-docs, connector create, etc.) rely on per-plan KB-item quota + middleware auth, and this SPEC follows the same pattern. Adding a rate-limit primitive just for these three routes would be premature. If abuse ever emerges (e.g. a runaway client paste-looping), we add the primitive repo-wide in a separate SPEC.

---

## Requirements

### Module 1: Backend routes

**R1 [Ubiquitous]:**
Portal-api **will** expose three new routes under `/api/app/knowledge-bases/{kb_slug}/sources/{type}` where `{type} ∈ {url, youtube, text}`. Each route requires an authenticated user with the `knowledge` capability and ownership (or membership) of the target KB.

**R1.1 [Ubiquitous]:**
Each route **will** enforce the per-KB item quota (`KBQuotaService` from SPEC-PORTAL-UNIFY-KB-001) before accepting the request. Quota-exceeded → 403.

**R1.2 [Ubiquitous]:**
Each route **will** forward the extracted `(title, content)` pair to `POST http://knowledge-ingest:8000/ingest/v1/document` with:
- `X-Internal-Secret` header from settings
- `X-Request-ID` + `X-Org-ID` propagated via `app.trace.get_trace_headers()`
- body: `IngestRequest` with the correct `source_type`, `content_type`, `source_ref`, and `extra` fields per D1 architecture diagram

**R1.3 [Ubiquitous]:**
Routes **will** return typed HTTP errors per D8 table. No upstream error bodies are echoed back to the client.

### Module 2: URL extractor

**R2 [Ubiquitous]:**
`source_extractors/url.py::extract_url` **will** accept a raw URL string and return `(title: str, markdown_content: str)` on success.

**R2.1 [Ubiquitous]:**
URL validation **will** reject any URL that resolves to rfc1918, link-local, loopback, or known docker-internal hostnames before any outbound fetch. See D6.

**R2.2 [Ubiquitous]:**
The extractor **will** call crawl4ai via `POST http://crawl4ai:11235/crawl` with a single-URL batch and parse the returned `markdown` field. Timeout: 30 seconds.

**R2.3 [Ubiquitous]:**
If crawl4ai returns empty content or non-200, the extractor **will** raise `SourceFetchError` with the upstream status code as context (logged, not echoed to client per R1.3).

**R2.4 [Ubiquitous]:**
Title **will** be derived from: `<h1>` in markdown > first non-empty line > URL hostname. Never empty.

**R2.5 [Ubiquitous]:**
`source_ref` **will** be the canonical URL: `scheme://host/path` with the fragment stripped and the default port (80/443) stripped. Query string is preserved (different queries on the same path are different pages — e.g. archive pagination). This flows through to `IngestRequest.source_ref` so repeat submissions dedup against the existing row.

### Module 3: YouTube extractor

**R3 [Ubiquitous]:**
`source_extractors/youtube.py::extract_youtube` **will** accept a raw URL string and return `(video_title: str, transcript_text: str)` on success.

**R3.1 [Ubiquitous]:**
Video-ID extraction **will** use the regex pattern from Focus's `youtube.py` (`youtube.com/watch?v=` / `youtu.be/` / `m.youtube.com` hosts). Invalid URL → `InvalidUrlError`.

**R3.2 [Ubiquitous]:**
Transcript fetch **will** use `youtube_transcript_api.YouTubeTranscriptApi.get_transcript(video_id)` with language preference `['en', 'nl', ...any]`. No transcript in any language → `UnsupportedSourceError`.

**R3.3 [Ubiquitous]:**
Transcript segments **will** be concatenated with single spaces; timestamps **will not** be embedded in the stored text.

**R3.4 [Ubiquitous]:**
Video title **will** be set to the YouTube video title fetched from the public oembed endpoint (`https://www.youtube.com/oembed?url=<video-url>&format=json`) — this is the "pretty" title the user sees on YouTube, which is exactly what we want to display in the KB. No auth, no API key. Fall back to `"YouTube video {video_id}"` on fetch failure (network error or 4xx). Oembed timeout: 5 seconds; oembed failure does NOT fail the whole ingest — transcript is the primary payload, title is best-effort.

**R3.5 [Ubiquitous]:**
`source_ref` **will** be `f"youtube:{video_id}"` where `video_id` is the 11-character YouTube ID extracted in R3.1. Stable across all URL variants (`youtu.be/ID`, `youtube.com/watch?v=ID`, `m.youtube.com/watch?v=ID`) → re-submitting the same video via any URL shape is a dedup match.

### Module 4: Text extractor

**R4 [Ubiquitous]:**
`source_extractors/text.py::extract_text` **will** accept `(title: str | None, content: str)` and return `(title: str, content: str, source_ref: str)` on success.

**R4.1 [Ubiquitous]:**
Content length **will** be validated: `len(content) ≤ 500_000` characters. Over or empty after normalisation → `InvalidContentError`.

**R4.2 [Ubiquitous]:**
Normalisation pipeline: strip NUL bytes (`\x00`), collapse runs of whitespace to single spaces, strip leading/trailing whitespace. The normalised form is used for both the stored `content` and the hash in R4.4.

**R4.3 [Ubiquitous]:**
Title **will** be derived from: explicit input (trimmed) > first non-empty line of normalised content (truncated to 120 chars) > `"Untitled note"`.

**R4.4 [Ubiquitous]:**
`source_ref` **will** be `f"text:sha256:{hex}"` where `hex` is the hex-encoded SHA-256 of the normalised content from R4.2. The user-supplied title is **not** part of the hash (title is metadata, not content) — so re-submitting the same paragraph with a different title is still a dedup match. This source_ref flows through to `IngestRequest.source_ref`; knowledge-ingest's dedup layer does the rest.

### Module 5: Security + quota

**R5 [Ubiquitous]:**
All three routes **will** call the existing `KBQuotaService` item-count check before invoking the extractor. Quota exceeded → 403 with the "KB has reached its document limit" error per D8.

**R5.1 [Ubiquitous]:**
URL routes **will** pin the resolved IP when calling crawl4ai to prevent DNS rebinding (fetch uses the IP literally, not the hostname). This matches the TOCTOU pattern called out in `.claude/rules/klai/projects/knowledge.md`.

**R5.2 [Ubiquitous]:**
No per-route rate limit **will** be added in this SPEC. Matching the pattern of the existing KB-write routes (file upload, connector create, etc.) which enforce quota + auth and nothing else. A rate-limit primitive is out of scope — if abuse materialises, it gets added repo-wide in a follow-up SPEC, not just on these three routes.

**R5.3 [Ubiquitous]:**
The SSRF guard from D6 **will** block any URL that resolves to rfc1918, link-local, loopback, or docker-internal hostnames BEFORE any outbound fetch. This is a security control, unrelated to throughput. Coverage includes IPv4 (10.x, 172.16/12, 192.168.x, 127.x, 169.254.x) and IPv6 (`::1`, `fe80::/10`, `fc00::/7`).

### Module 6: Frontend

**R6 [Ubiquitous]:**
`source-types.ts` **will** be extended with `youtube` as a new `SourceType` in the `upload` group. The tile uses `SiYoutube` from `@icons-pack/react-simple-icons`.

**R6.1 [Ubiquitous]:**
`UrlSourceForm.tsx`, `YouTubeSourceForm.tsx` (new), and `TextSourceForm.tsx` **will** implement active forms with submit mutations to the corresponding portal-api routes. Error banners use i18n strings per D8.

**R6.2 [Ubiquitous]:**
Successful submission **will** invalidate `useQuery({ queryKey: ['kb-items', kbSlug] })` and navigate back to `/app/knowledge/$kbSlug?tab=items`.

**R6.3 [Ubiquitous]:**
Form component files **will not** exceed 200 lines each. Shared submission logic (mutation setup + error parsing) **will** extract into a `useSourceSubmit` hook if repetition justifies it.

### Module 7: Quality & observability

**R7 [Ubiquitous]:**
Backend coverage on the three new routes + three extractors **will** be ≥ 85%, measured via `pytest --cov`.

**R7.1 [Ubiquitous]:**
All new Python files **will** pass `uv run ruff check` and `uv run --with pyright pyright` without new errors.

**R7.2 [Ubiquitous]:**
All extractors **will** emit structured logs with `org_id`, `kb_slug`, `source_type`, `duration_ms`. No user-supplied URLs logged at info-level (privacy + potential token leakage in URLs); URL hostnames only.

**R7.3 [Ubiquitous]:**
`youtube-transcript-api` **will** be added to `klai-portal/backend/pyproject.toml` via `uv add`. `uv.lock` regenerated.

---

## Verification

1. **Per extractor** (unit tests):
   - URL: crawl4ai mocked with `httpx.MockTransport`, verify markdown parsed, title extracted, SSRF rejected for `http://localhost/x`, `http://169.254.169.254/`, `http://docker-socket-proxy/`.
   - YouTube: `youtube_transcript_api` mocked, verify video-ID extraction for all four hostname variants, "no transcript" raises `UnsupportedSourceError`, oembed mocked for title.
   - Text: length limits enforced, NUL stripped, title derivation works for all three fallbacks.

2. **Per route** (integration):
   - Mock the relevant extractor + knowledge-ingest client; verify happy-path returns 201 + correct `IngestRequest` body (including deterministic `source_ref`).
   - Verify 403 on KB quota exceed (`KBQuotaService` mocked to reject).
   - Verify 404 on unknown `kb_slug`.
   - Verify 401 on unauthenticated request.
   - Verify dedup: submit the same text twice → both return 2xx, but knowledge-ingest receives the same `source_ref` and the second submission is a no-op on chunk count.

3. **End-to-end** (staging):
   - Paste a real URL → doc appears in items list within ~30 seconds.
   - Paste a YouTube video with transcript → doc appears in items list.
   - Paste a YouTube video without transcript → user sees "no transcript available" banner.
   - Paste a text snippet → doc appears immediately.
   - Attempt `http://localhost/` via the URL form → user sees "This URL is not allowed" banner.

4. **Security spot-check**:
   - `ripgrep 'hmac.compare_digest' klai-portal/backend/app/api/app_knowledge_sources.py` — any secret comparison uses constant-time.
   - `ripgrep 'request.client.host' klai-portal/backend/app/api/app_knowledge_sources.py` — should be zero (auth is via Zitadel cookie, not IP).
   - Verify SSRF guard via `pytest -k ssrf` with fixture IPs covering all IPv4 rfc1918/link-local/loopback ranges AND IPv6 `::1` / `fe80::/10` / `fc00::/7`.

5. **Observability**:
   - In VictoriaLogs: `service:portal-api AND msg:"source ingested"` returns entries for every successful submission with `source_type`, `kb_slug`, `duration_ms`.
   - No `api_key`, `access_token`, or full URL (with query string) appears in logs.

---

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| YouTube blocks core-01's IP; all youtube submissions start failing | Medium | High | Known limitation documented. Follow-up SPEC adds proxy rotation. Monitor via dashboard; if hit rate < 50% for a week, escalate. |
| crawl4ai returns truncated content for large pages | Medium | Low | Log content length; if consistently near upper bound, add a size banner in the success UI. |
| SSRF guard has a blind spot for DNS rebinding on IPv6 | Low | High | Tests cover `::1`, `fe80::/10`, and `fc00::/7`. IP-pin the hostname before the crawl4ai call so TOCTOU is closed. |
| User pastes a 500KB text blob; /ingest/v1/document is slow | Low | Low | Already bounded by `Field(max_length=500_000)` in IngestRequest. Frontend shows progress toast. |
| User paste-loops the same YouTube URL to spam the KB | Low | Low | Dedup via `source_ref = youtube:{video_id}` — second+ submissions are no-ops on the chunk store. No rate-limit needed for this vector. |
| User paste-loops different URLs to fill the KB quota | Low | Low | KB item quota (per-plan) caps the count. Quota exceeded → 403 per R5. Existing protection, nothing new. |
| Text submissions bypass content-filter and store malicious content | Low | Low | No filter today for file uploads either — consistent, and the KB is per-tenant so the attacker only pollutes their own KB. |
| youtube-transcript-api version pin drifts, breaking ID extraction | Low | Medium | Pin in `pyproject.toml`; Renovate keeps it current; integration test runs against a real transcript in CI (or a recorded fixture). |
| User expects transcripts in their own language but gets English fallback | Medium | Low | Document in the YouTube tile subtitle: "Transcripts are fetched in English or original language". |

---

## Out of scope (recap — do not drift)

- Research-api revival (frozen per SPEC-PORTAL-UNIFY-KB-001)
- Docling migration (crawl4ai is the monorepo standard)
- Image tile
- Site-wide web crawl via the URL tile (use the web_crawler connector)
- YouTube playlist support
- RSS feeds
- Multi-language transcript auto-translate
- Proxy rotation for YouTube IP blocks
- Rich text / HTML / PDF in the Text tile

---

## Open vragen

1. ~~**Text tile submission: hard-replace existing doc with same title, or always create new?**~~ **Beantwoord (2026-04-24):** Geen van beide. We gebruiken een deterministische `source_ref` gebaseerd op sha256(genormaliseerde content) — hetzelfde tekst-blok twee keer plakken is een dedup-no-op op de ingest-pipeline, geen dubbele rij. Zie D7 + R4.4.
2. ~~**Should YouTube title come from `oembed` or from the transcript's inferred title?**~~ **Beantwoord (2026-04-24):** oembed. Dat is de titel die de gebruiker op YouTube ziet — dus ook wat 'ie in de KB wil zien. Zie R3.4.
3. ~~**Is the text tile gated behind KB `docs_enabled`, or always available?**~~ **Beantwoord (2026-04-24):** Altijd beschikbaar. Tekst/URL/YouTube lopen direct naar knowledge-ingest, niet via Gitea. De `docs_enabled`/`gitea_repo_slug` restrictie geldt alleen voor het file-upload pad (PR #136). Zie architecture diagram.
4. ~~**Rate limit: 30/hour URL+YouTube, 60/hour text. Too tight for power users?**~~ **Vervallen (2026-04-24):** Per-route rate-limits zijn eruit. Portal-api heeft geen generieke rate-limit primitive voor KB-write paden; dit SPEC matcht dat patroon. Alleen KB-quota + SSRF-guard blijven. Als misbruik materialiseert, volgt een aparte SPEC die een rate-limit primitive repo-wide introduceert. Zie D8 + R5.2.
5. ~~**YouTube oembed endpoint auth — does Zitadel org IP need to be allowlisted?**~~ **Beantwoord (2026-04-24):** Publiek endpoint, geen auth, geen allowlist. Zie R3.4.

---

## References

- **SPEC-KB-CONNECTORS-001** — Connector adapter architecture. This SPEC extends the same source-type space with three non-connector paths (url/youtube/text).
- **SPEC-PORTAL-UNIFY-KB-001** — Decommissioned research-api; reason this SPEC cannot revive it.
- **PR #139** — Unified add-source grid that currently greys these three tiles.
- **PR #136** — File upload restoration (the active fourth tile).
- **Focus code on `origin/feat/chat-first-redesign`** (reference only — not ported):
  - `klai-focus/research-api/app/services/youtube.py` — regex + transcript fetch
  - `klai-focus/research-api/app/services/docling.py` — URL convert (replaced by crawl4ai in this SPEC)
  - `klai-focus/research-api/app/services/ingestion.py` — orchestration (replaced by portal-api routes + direct knowledge-ingest sink)
  - `klai-focus/research-api/app/api/sources.py` — route structure (matches our three-route shape)
- **`klai-knowledge-ingest/knowledge_ingest/models.py:9`** — `IngestRequest` schema (the sink).
- **`klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:493`** — `POST /ingest/v1/document` route definition.
- **`.claude/rules/klai/projects/knowledge.md`** — crawl4ai usage + SSRF + Procrastinate passthrough rules.
- **`.claude/rules/klai/projects/portal-security.md`** — `_get_{model}_or_404` pattern + RLS coverage.

---

## Implementation Notes

Recorded at 1.2.0 sync, reflecting what actually shipped versus the 1.1.0 plan. SPEC level 1 (spec-first) — these notes are the authoritative record of scope deltas.

### What landed as planned

All seven modules (R1–R7) implemented without scope reduction:
- Three extractors (`text.py`, `url.py`, `youtube.py`) as pure async/sync functions raising typed exceptions.
- SSRF guard (`_url_validator.py`) covering IPv4 rfc1918/link-local/loopback + IPv6 ::1/fe80::/10/fc00::/7 + docker-internal hostname deny list.
- Three routes under `app_knowledge_sources.py` forwarding to `knowledge_ingest_client.ingest_document` with `X-Internal-Secret` + `get_trace_headers()`.
- Dedup via stable `source_ref` (canonical URL / `youtube:{id}` / `text:sha256:{hex}`) — verified by the `test_same_content_same_source_ref_dedup` and `test_source_ref_dedup_across_url_variants` cases.
- Frontend tiles flipped to `available: true`; three active forms with shared `useSourceSubmit` hook; 13 new i18n keys (EN + NL).

### Deltas from plan

| Area | Planned | Actual | Rationale |
|---|---|---|---|
| **Capability gate** | SPEC R1 said "`knowledge` product capability" | Used schrijf-rol check (`contributor`/`owner`) via `get_user_role_for_kb` + `assert_can_add_item_to_kb`, **NO** `require_capability("kb.connectors")` | `kb.connectors` is complete-plan-only in `plan_limits.py` — gating these three routes on it would exclude core/professional users who see the tiles as available. The existing KB-write paths (file upload via klai-docs, connector create) follow the same role+quota pattern. |
| **URL scheme policy** | SPEC silent on http vs https | Both accepted | Product decision during /moai run. SSRF guard runs for both; matching the common case where users paste blog/docs URLs that are still HTTP. |
| **IP pinning (R5.1)** | "Pin resolved IP when calling crawl4ai" | Not implementable | crawl4ai's HTTP API accepts only a URL, not IP+Host-header split. Portal-api's SSRF guard rejects private/loopback IPs before calling crawl4ai; crawl4ai's own resolver is out of our control. DNS rebinding between our check and crawl4ai's fetch is a residual risk — adding to the risks table below. |
| **SSRF extract to klai-libs** | SPEC D6 called it "preferred: extract `validate_url` into klai-libs/" | Mirrored the logic in portal-api | SPEC marked extract as non-blocking follow-up. Left as-is for a dedicated SPEC. |
| **Route file location** | SPEC offered both `app_knowledge_bases.py` extension or new sibling file | Chose new `app_knowledge_sources.py` | `app_knowledge_bases.py` already holds 28 handlers + 1400 lines; adding three more would hurt readability. The new module reuses `_get_caller_org`, `bearer`, and the same dependency patterns. |

### New residual risks

Adding to the Risks table at 1.2.0:

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| DNS rebinding: record changes between portal-api's SSRF check and crawl4ai's fetch, landing on a private IP | Low | Medium | crawl4ai runs inside docker — no path to RFC1918 routes outside the compose network from its container (unless the compose network exposes them). Follow-up: either add `dns_rebind_protection` to crawl4ai (upstream feature), or move first-hop fetch into portal-api and send HTML payload to crawl4ai (would require crawl4ai API change). |

### Test + coverage summary

**Backend (pytest):** 127 tests across five files, all green.
- `test_source_extractors_text.py` — 22 cases (validation, normalisation, title derivation, source_ref determinism).
- `test_source_extractors_ssrf.py` — 47 cases, including the IPv4/IPv6 block ranges, docker-internal deny list, and three post-review regressions for FQDN trailing dots (`http://redis./api`).
- `test_source_extractors_url.py` — 15 cases (crawl4ai mock, title derivation, failure modes, canonical source_ref).
- `test_source_extractors_youtube.py` — 28 cases (regex variants, transcript mocks, oembed best-effort).
- `test_app_knowledge_sources.py` — 15 route-level integration cases.

**Frontend (vitest):** 17 tests across two files cover the user-facing error pipeline:
- `lib/__tests__/apiFetch.test.ts` — 7 cases, including three new regressions for the dict/string/array detail shapes.
- `routes/app/knowledge/$kbSlug_.add-source._components/__tests__/useSourceSubmit.test.ts` — 10 cases pinning every SPEC D8 row (non-ApiError → generic, 403 error_code → kb_full, 400 "not allowed" → blocked URL, 400 → invalid URL, 422 on YouTube → no-transcript, 422 elsewhere → generic, 502 → fetch-failed, unmapped status → generic, error_code precedence over status).

**Coverage:** 94% on the new backend modules (target 85% per R7). Route module 96%. Ruff + pyright clean. 32 existing KB/quota tests regression-checked green. Frontend: paraglide compile ✓, `tsc -b` ✓, `eslint .` ✓.

### IngestRequest.path usage verified

`path` on `IngestRequest` is a logical identifier (pg store + procrastinate queue-lock key), **not** a filesystem or S3 key. Confirmed via:
- `pg_store.get_active_content_hash(org_id, kb_slug, path)` — database lookup.
- Queueing lock: `f"{org_id}:{kb_slug}:{path}"` — string key, colons are irrelevant.
- No filesystem writes path'd by this value.

Therefore the colons in `text:sha256:{hex}` and `youtube:{video_id}` are safe to reuse both as `source_ref` AND as `path`. No separate path format is needed.

### Not verified (requires staging)

Per SPEC §Verification step 3, end-to-end happy-path against real crawl4ai + real YouTube transcripts + real Zitadel cookie was not run — requires a staging deploy. Plan: manual smoke after merge, before announcing the feature.

### Follow-up tickets

Not created yet; candidates:
1. Extract SSRF `validate_url` into `klai-libs/` for reuse between portal-api and knowledge-ingest (SPEC D6 recap).
2. crawl4ai DNS rebinding mitigation (either upstream feature request or architectural change — whichever is cheaper).
3. Proxy rotation for YouTube IP blocks (SPEC risks table item "YouTube blocks core-01's IP") — already flagged in 1.0.0 as known limitation; materialises as a separate SPEC only when hit rate drops.
