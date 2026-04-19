# Architecture Changes — Week 17 (2026-04-13 → 2026-04-19)

> Working document. Catalogues every architectural change shipped in the past week
> so the four main architecture docs (`platform.md`, `klai-knowledge-architecture.md`,
> `knowledge-ingest-flow.md`, `knowledge-retrieval-flow.md`) can be updated against
> a single consolidated reference. Delete this file once all docs are synced.

---

## Summary

The week produced **roughly 400 commits** across eleven SPECs. The dominant theme
is **platform-surface expansion**: the portal now ships a third-party embeddable
chat widget, a dedicated Partner API, OAuth connectors, a KB editor with stable
page IDs, SSO with domain-based self-service, and a parallel `dev.getklai.com`
environment. Under the hood the retrieval pipeline moved to source-aware
selection, the web crawler became a two-phase quality-aware pipeline with
SimHash-LSH dedup, and the portal got a ground-up UI redesign.

### New SPECs shipped this week

| SPEC | Status | Subject |
|------|--------|---------|
| SPEC-WIDGET-001 | live | Embeddable Klai Chat Widget — `<script data-widget-id="wgt_...">` bundle at `cdn.getklai.com/widget/klai-chat.js` |
| SPEC-WIDGET-002 | deployed | Split Partner API keys and widgets into two independent first-class domains — separate tables, RLS, admin routes, wizards |
| SPEC-API-001 | live | Partner API (`/partner/v1/chat/completions`, feedback, knowledge append) with `pk_live_...` Bearer auth |
| SPEC-CRAWL-002 | live | Two-phase web crawler — BFS URL discovery + extraction split, cookie authentication |
| SPEC-CRAWL-003 | live | SimHash-LSH dedup for >200 page syncs + Layer A/B/C quality detection |
| SPEC-CRAWL-004 | live | Webcrawler connector UX — tabbed edit view, conditional auth step, automatic login-guard setup |
| SPEC-DOCS-001 | live | Reliable KB page creation, stable page UUID in URL, client-owned SHA + promise queue for save serialisation |
| SPEC-AUTH-001 | live | Social signup via Google + Microsoft IDPs through Zitadel |
| SPEC-AUTH-006 | live | SSO self-service: domain allowlist, join requests, multi-org workspace selection |
| SPEC-KB-021 | deployed | Multi-source retrieval quality — `source_aware_select`, router-as-signal, source-aware enrichment |
| SPEC-KB-025 | live | OAuth connectors for Google Drive (SharePoint scoped for v2) |
| SPEC-KB-IMAGE-001 | live (refactor) | Adapter-owned image URL resolution — image policy moved out of core pipeline |

### Other architecturally relevant changes (no dedicated SPEC)

- **Parallel dev environment** at `dev.getklai.com` (isolated LibreChat + LiteLLM containers)
- **Guardrails**: Rules + Templates applied in the LiteLLM pre-hook
- **Per-user personal KB architecture** — every user gets a personal KB, admin-only visibility
- **Portal UI redesign** — Superdock-style source wizard, LibreChat aesthetic, flat sidebar, chat-first redesign
- **401 storm elimination** — singleflight on Zitadel userinfo + coalesced token refresh in `apiFetch`
- **Widget bundle hosting** moved from `cdn.getklai.com` to `portal public/` (served from portal origin)
- **Auto-retry save on SHA mismatch** — KB docs editor returns 409 with fresh SHA, frontend retries automatically
- **CI widget-bundle sync** — automated PR workflow to keep `klai-chat.js` in portal `public/` in sync with widget repo

---

## 1. New domain: Partner API + Chat Widget

### 1.1 Partner API (SPEC-API-001)

First customer-exposed API surface. Lives at `api.getklai.com/partner/v1/`.

**Endpoints**:
- `POST /partner/v1/chat/completions` — OpenAI-compatible streaming SSE
- `POST /partner/v1/feedback`
- `POST /partner/v1/knowledge/append`
- `GET /partner/v1/widget-config?id=wgt_...` (public, no auth — added for widget)

**Auth**: Bearer token `pk_live_<40-hex>`, SHA-256 hashed in DB. Rate-limited per
key. KB-scoped — each key authorises only a subset of the org's KBs.

**Data model**: new `partner_api_keys` table with RLS tenant scoping, and
`partner_api_key_kb_access` junction carrying `access_level` ∈ {`read`,
`read_write`}.

**Admin surface**: `/admin/api-keys` with wizard-for-create + tabs-for-edit
(Details, Permissions, Knowledge bases, Rate limit, Danger zone).

### 1.2 Klai Chat Widget (SPEC-WIDGET-001)

Embeddable SolidJS bundle forked from FlowiseChatEmbed. Single script tag:

```html
<script src="https://cdn.getklai.com/widget/klai-chat.js" data-widget-id="wgt_..."></script>
```

**Bootstrap flow**:
1. Browser loads `klai-chat.js`, reads `data-widget-id`
2. Calls `GET /partner/v1/widget-config?id=<wgt_...>` with Origin header
3. Server validates Origin against `widget_config.allowed_origins` (fail-closed,
   empty list blocks everything; wildcard subdomains supported per commit `005fc104`)
4. Returns widget config + **short-lived JWT session token** (HS256, 1h TTL,
   signed with deployment-level `WIDGET_JWT_SECRET`)
5. Widget uses `Authorization: Bearer <jwt>` for subsequent
   `/partner/v1/chat/completions` calls

**Key security property**: no `pk_live_...` token ever reaches the browser. The
`wgt_...` ID is public; authentication is JWT-only.

**Bundle budget**: <200 kB gzipped, measured in CI.

**Styling**: four CSS variables (`--klai-primary-color`, `--klai-text-color`,
`--klai-background-color`, `--klai-border-radius`). Defaults match Klai brand
(amber `#fcaa2d`, ivory `#fffef2`, cream `#f3f2e7`, Parabole font).

**i18n**: NL + EN, auto-detected from browser locale, override via
`data-locale="en"`.

**System prompt**: grounded KB-only (no general knowledge), snarkdown for
markdown rendering, no JSX text leaks.

### 1.3 Domain split (SPEC-WIDGET-002)

The initial implementation multiplexed API keys and widgets on
`partner_api_keys` via an `integration_type` discriminator. This produced a
recurring defect surface (silent column leakage, revoke-vs-delete ambiguity,
pervasive `if (isWidget)` branches). SPEC-WIDGET-002 split them into two
independent domains:

| Concern | API keys | Widgets |
|---------|----------|---------|
| Table | `partner_api_keys` (stripped of widget cols) | `widgets` (new) |
| Junction | `partner_api_key_kb_access` | `widget_kb_access` (no `access_level`) |
| Admin endpoint | `/api/api-keys` | `/api/widgets` |
| Admin route | `/admin/api-keys` | `/admin/widgets` |
| Wizard steps | Details → Permissions → KBs → Rate limit | Details → KBs → Appearance → Embed |
| Auth | `pk_live_...` SHA-256 lookup | JWT via `WIDGET_JWT_SECRET`, **no per-widget secret** |
| Revoke | Removed (DELETE only) | Removed (DELETE only) |
| `active` column | Dropped | Never existed |

The `/api/integrations` prefix and `/admin/integrations` route were
**hard-removed** — no redirect, no deprecation window. Frontend and backend
deploy together.

**Observability**: structlog events now carry `domain="api_key"` or
`domain="widget"` instead of `integration_type`.

---

## 2. Web crawler — quality pipeline

### 2.1 Two-phase crawler (SPEC-CRAWL-002)

Replaces the single-pass crawler with **BFS discovery → extraction** split:

- **Discovery phase**: BFS traversal of internal links from `base_url`, bounded
  by `max_pages` and `path_prefix` filter. Full DOM preserved during discovery
  (fix in commit `842f5e94`). Output: URL list.
- **Extraction phase**: per-URL content extraction using `css_selector`
  (optional), with AI-assisted selector detection (`let AI find the content
  selector` button in the wizard).

**Cookie authentication**: the crawler now supports cookie-based auth for
gated sites. Admin pastes browser cookies; the crawler uses them in every
request. Implemented via Crawl4AI hooks (commit `ea6de9b7`).

**Auth guard setup**: the webcrawler wizard automatically configures a canary
URL + login indicator so the pipeline can detect redirects to a login page
mid-crawl and fail loudly (SPEC-CRAWL-004, commit `73d6fdba`).

### 2.2 Quality detection — Layer A/B/C (SPEC-CRAWL-003)

Each crawled page is scored on three independent layers:

- **Layer A**: structural quality — does the page have the expected selector,
  did we get content, was it a 200?
- **Layer B**: canary + login-indicator check — are we still authenticated?
- **Layer C**: content fingerprint — SimHash-based content hash used for dedup
  across a sync.

A new column `quality_status` on the crawled-document table records the worst
layer result per page. Added in migration `005_*` with conventional `NNN_` prefix.

### 2.3 SimHash-LSH dedup

For syncs over 200 pages, naive pairwise comparison is quadratic. Implemented
SimHash with Locality-Sensitive Hashing to find near-duplicates in linear time.
Dedup applied during the extraction phase (commit `dfefaffe`).

### 2.4 Adapter-owned image URL resolution (SPEC-KB-IMAGE-001)

Image URL resolution was tangled across the core pipeline and adapters. The
refactor moves **each adapter owns its own image URL resolution** — the core
pipeline only receives already-resolved URLs. Contract documented in
`refactor(connector): tighten types and document image contract`
(commit `51b7150b`).

Adapters updated:
- Web crawler: extract images from `crawl4ai.media` field, handle css_selector
  scope (commit `168380b9`)
- Google Drive: per REQ of SPEC-KB-025
- SharePoint: scoped for v2

### 2.5 Connector wizard UX (SPEC-CRAWL-004)

Edit-connector migrated from single-column field wall to three tabs (matching
the add-connector wizard):

| Tab | Content |
|-----|---------|
| Details | Name, base URL, path prefix, max pages, content type |
| Preview | Preview URL, content selector, AI detect, run preview |
| Authentication | Cookies, canary + login indicator |

URL-driven tabs via `?tab=details` search param (deep-linkable), matching the
pattern used by widgets and API keys.

---

## 3. Connector catalogue expansion

### 3.1 Google Drive OAuth (SPEC-KB-025a)

First OAuth connector. Flow:

1. Admin clicks "Connect Google Drive" in KB source wizard
2. Portal generates state token, returns authorize URL as JSON
3. User completes Google OAuth consent
4. Callback stores refresh token (encrypted) + selected folder scope
5. Sync worker uses refresh token to mint access tokens on demand

New env vars on portal-api: `GOOGLE_OAUTH_CLIENT_ID`,
`GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REDIRECT_URI`.

### 3.2 Superdock-style source catalogue

Unified add-source wizard showing **all integrations + file upload** in a
single grid. Replaces the per-type add-connector flows. Implemented in
commit `da0ea8a2`.

### 3.3 Web crawler: additional features

- Full DOM preservation during BFS (commit `842f5e94`)
- URLPatternFilter uses path-only wildcards (commit `18d0850b`)
- FilterChain serialisation fixed for Crawl4AI REST API (commit `0f06151a`)

---

## 4. Authentication & identity

### 4.1 Social signup (SPEC-AUTH-001)

Google + Microsoft social login via Zitadel IDPs. Flow:

1. User clicks "Sign up with Google" or "Sign up with Microsoft"
2. Portal redirects to Zitadel IDP flow
3. On callback, portal calls `POST /v2/idp_intents/{id}` to retrieve IDP intent
4. If no Zitadel user exists yet, portal creates one from IDP intent
5. Session creation retries on CQRS replication lag (commit `1a022961`)
6. New users land in `/signup/social` to complete workspace selection

### 4.2 Domain allowlist + join requests (SPEC-AUTH-006)

SSO self-service. Key flows:

- **Domain allowlist**: Org admins can add email domains (e.g. `@voys.nl`) to
  their org. Any user signing up with a matching domain is auto-joined.
- **Join requests**: If no matching org exists, the user can submit a join
  request with free-text justification. Org admins get notified via mailer
  (new endpoint `/internal/send` on mailer service). Admins approve/reject in
  `/admin/join-requests`.
- **Multi-org workspace selection**: Users belonging to multiple orgs pick
  which workspace to enter on login. Introduces a workspace-selector UI.
- **Pending session pattern**: between IDP callback and org selection, the
  user holds a short-lived pending session token (not a full session).

New DB tables: `org_domain_allowlist`, `join_requests`. New services:
`DomainValidationService`, `JoinRequestService`, `PendingSessionService`.

### 4.3 401 storm elimination

Under load, expired access tokens caused 4-108 Zitadel userinfo failures per
hour. Root cause: multiple parallel `/api/me` calls each independently hit
Zitadel with the same expired token.

Two fixes:
- **Singleflight on `get_userinfo()`** (backend, `zitadel.py`): concurrent
  requests for the same token share a single Zitadel call via `asyncio.Future`
- **Coalesced `signinSilent()`** (frontend, `apiFetch.ts`): N concurrent 401s
  share a single token refresh; all retry the original request with the fresh
  token

Commit `284e95e3`.

### 4.4 OAuth/OIDC fixes

- `redirect_uri` must use `settings.portal_url`, never a relative path
  (SAST suppression for OAuth log false positives) — commit `05a567d3`
- IDP intent retrieval is `POST`, not `GET` (Zitadel API change) — commit `a39ac82b`

---

## 5. Retrieval — multi-source quality (SPEC-KB-021)

### 5.1 `source_aware_select` replaces router + quota

The previous router-based selection applied a per-source quota *after*
retrieval, which produced brittle results when one source dominated the
chunk space. Replaced by a single `source_aware_select` function with two
modes:

- **mentioned mode**: when the user query explicitly names a source (e.g.
  "in the Voys manual"), boost that source's chunks above all others
- **diversify mode**: otherwise, apply diversity selection (MMR-style) across
  sources, ensuring multiple sources appear in the top-k when their chunks are
  competitive

### 5.2 Router as signal (not gate)

The router no longer filters candidates; it contributes a **signal** combining:

- **Keyword match** against source labels (title, path, domain)
- **Semantic centroid match** — each source has a centroid vector computed from
  its chunks; cosine similarity to query vector is a signal

Decision record logged per request for debugging.

### 5.3 Source-aware enrichment at ingest time

Chunk enrichment now sees the full source context: `kb_name`, `connector_type`,
`source_domain`, `content_type`, `source_label`. The enrichment prompt uses
these as grounding. Failure mode changed from silent fallback to **fail-loudly**
(ingest fails hard if enrichment fails).

### 5.4 `source_label` payload field in Qdrant

New keyword-indexed field on Qdrant payloads so the retrieval side can use the
**Qdrant Facet API** for source-aware filtering without scanning the full
collection (commit `2b7d6b19`).

### 5.5 STOP_WORDS deduplication

Stop-word set moved from private `_STOP_WORDS` in both `router.py` and
`diversity.py` to public `STOP_WORDS` in `diversity.py`. `router.py` imports
it. Single source of truth (commit `1b8dc354`).

---

## 6. KB editor — reliability (SPEC-DOCS-001)

### 6.1 Stable page UUIDs in URL

Replaces slug-based routing with `/docs/$kbSlug/$pageId` where `pageId` is a
full UUID. Previously used 8-char prefix; changed to full UUID to avoid
collision risk at scale. Slug→UUID redirect preserved for external links
(commit `41c17497`).

### 6.2 BlockNote JSON for lossless persistence

KB pages now store BlockNote editor JSON (not HTML/Markdown) for lossless
round-trip. Legacy content (HTML/Markdown) is parsed on-demand, deferred via
`requestIdleCallback` to unblock initial paint (commit `2908c276`, `284e95e3`).

### 6.3 Client-owned SHA + promise queue

Concurrent saves caused Gitea SHA conflicts (500). Fix:

- **Client owns the SHA**: the editor keeps the last-known SHA and sends it on
  every save. Gitea returns the new SHA in the response; client stores it for
  the next save.
- **Promise queue**: follow-up saves are queued instead of fired in parallel;
  only one in-flight save per page at a time.
- **Auto-retry on 409**: if SHA mismatches (stale client SHA), the server
  returns `409 Conflict` with the current SHA in the body; client retries
  once with the fresh SHA. Previously this surfaced as a generic 500.

Commits: `89879a07`, `484f9944`, `10955940`, `389ffd71`.

### 6.4 `beforeunload` save flush

Pending saves (held by a debounce timer) are flushed before the browser
navigates away. Prevents data loss on fast nav (commit `1e86bcaa`).

### 6.5 Pagination + page-index endpoint

KB overview now uses `/page-index` endpoint for doc counts (server-side count,
not client scan). Editor supports `?page=N` search param for pagination
(commit `4bcf6e1e`).

---

## 7. Portal UI — redesign

The portal went through a ground-up visual overhaul in this week. Key moves:

### 7.1 Superdock-style source catalogue

Source-add UI is a grid of all available integrations (Notion, GDrive, Web
crawler, file upload, …) in one view. Drops the old per-type flow (commit
`651f6bac`).

### 7.2 LibreChat aesthetic

- Inter font across the portal
- White sidebar (replaces cream), `rounded-lg` everywhere (not `rounded-full`)
- Config bar above chat matches LibreChat toolbar grid (`h-50px`, `pl-17px`)
- Chat-first redesign: three sidebar items (Chat, Kennis, Regels)

### 7.3 Flat sidebar unification

The admin section now has flat nav: API keys, Chat widgets, Team, MCP's,
Regels, Templates. Account sub-items grouped under Account. No more nested
admin menus (commit `9ad3c658`).

### 7.4 One-page account redesign

Account view consolidated to a single page with section headers + icons.
Profile fetched from `/api/me` (replacing ad-hoc calls). Help popup no longer
reopens on navigation (commit `d0b803ee`).

### 7.5 Uniform knowledge model (Option A)

"Every collection is uniform, every item is a bron." Knowledge overview shows
files + sources in a single list per collection with a summary row when items
exist but no sources/files (commit `183546ea`). "+ Bron toevoegen" removed —
sources are added via a collection (commit `71611ecc`).

### 7.6 Per-user personal KB

Every user gets a personal KB on signup. List view filters other users'
personal KBs. Admins see a visible "Mijn" badge + can delete any KB. Per-file
delete + collection sync/delete on overview (commits `4e671b4e`, `6f2f1138`,
`51fc6faa`).

### 7.7 Design system doc extracted

`docs/portal-design-system.md` extracted from the current codebase (commit
`a6ab3984`).

---

## 8. Guardrails — Rules + Templates in LiteLLM hook

Two new model-facing features both applied in the LiteLLM **pre-call hook**:

- **Rules**: strict guardrails (not instructions). Applied before the user
  message reaches the model. Rules CRUD mirrors Templates — same UI pattern,
  separate table. The `instruction` type was removed from rules — they are
  strictly guardrails (commit `326da19a`).
- **Templates**: reusable response templates scoped per KB.

Both are resolved in the hook using the org + KB context from the session,
then injected into the system prompt (commit `dba3791c`).

---

## 9. Infrastructure

### 9.1 Parallel dev environment (`dev.getklai.com`)

A full second stack on core-01 serving `dev.getklai.com`:

- **Isolated LibreChat + LiteLLM containers** — dev-only database, dev-only
  model configs (commit `24551078`)
- **Shared infra secrets** reused from prod for KB / connector CRUD to work
  without duplicating SOPS (commit `c06eeed6`)
- **Caddy**: dev `/api/*` routing via `route` block (commit `e654c73f`),
  CSP header + partner API handler restored after dev-block merge
  (commit `3a52f372`)
- **Dev tenant mapping**: portal dev subdomain uses its own LibreChat instance
  (commit `01b3ac85`)

Runbook in `docs/dev.md` (commit `7acb2367`).

### 9.2 Widget bundle hosting

Moved from `cdn.getklai.com` to `portal public/` (served from portal origin).
Simplifies CSP and eliminates a cross-origin dependency. CI workflow syncs
`klai-chat.js` bundle into portal repo via PR (commit `e7bd1405`,
`4a63b52e`, `fc655467`, `cc4f3f6e`).

### 9.3 RLS hardening

Several RLS-related fixes:
- `set_tenant` called in admin `_get_caller_org` so RLS has tenant context
  before every admin query (commit `e341f748`)
- Explicit `set_tenant` before KB validation and INSERT in integrations flow
  (commit `fe60f24b`)
- DELETE policy added on `partner_api_keys` (commit `219b1b7f`, wrapped in
  `DO` block per DDL pattern `2464089c`)
- Provisioning now sets RLS context before default KB + system group inserts
  (commit `b60a7432`)
- Capture org attributes before session commit to avoid `MissingGreenlet`
  async error (commit `e1328b9f`)

### 9.4 Partner API: DB connection pattern

Pin DB connection per request, remove all RLS workarounds (commit `09207a10`).
Cleaner than the previous `_ensure_tenant` scattered across endpoints.

### 9.5 Widget security

- **Wildcard subdomain support** in `origin_allowed` (commit `005fc104`) —
  `*.example.com` matches any subdomain
- XSS fix in widget config rendering (commit `bcdddc1c`)
- Embed URL sanitisation (commit `1142cc2f`)

---

## 10. Observability & security

### 10.1 Product events extension

New event types emitted to `product_events`:
- `widget.chat.started`, `widget.chat.completed`
- Widget requests traceable via `request_id` (Caddy generates, portal-api
  propagates — already documented in `observability.md`)
- No new service filter needed — widget traffic shows up under
  `service:partner-api` in VictoriaLogs

### 10.2 CI hardening

- Semgrep excludes minified widget JS (commit `a49ebcc8`)
- Widget-bundle workflow creates PR instead of direct push — repo blocks
  Actions PRs by default, this unblocks (`b1915478`, `487d9037`)
- pip-audit ignores CVE-2025-71176 on pytest (commit `fe15d46f`)
- `python-multipart` upgraded to `>=0.0.26` for CVE-2026-40347 (commit `a04635fc`)

---

## 11. Impact on existing architecture docs

Quick reference — full per-doc task list follows in section 12 after reading
each doc end-to-end.

| Doc | Needs | Reason |
|-----|-------|--------|
| `platform.md` | Medium | New: Partner API, Widget, Widget CDN, dev env, Widget JWT auth, Rules+Templates guardrails, Zitadel IDPs, RLS DELETE policy pattern |
| `klai-knowledge-architecture.md` | Medium | Retrieval §7 — source_aware_select; Ingestion §4 — source-aware enrichment + source_label; Multi-tenancy §10 — per-user personal KB; Publication §11 — widget-config endpoint |
| `knowledge-ingest-flow.md` | Minor | Already updated for SPEC-KB-021 (source_label, enrichment). Add: Layer A/B/C quality, SimHash-LSH dedup, adapter-owned image URLs, Google Drive OAuth connector, two-phase crawler, cookie auth |
| `knowledge-retrieval-flow.md` | Minor | Already updated for SPEC-KB-021. Add: widget retrieval path, session-token-based auth flow, Rules + Templates injection in LiteLLM hook |

---

## 12. Per-doc update task list

Concrete edits per architecture doc, ordered by section. Each task flagged with
priority: **[A]** essential (breaks accuracy), **[B]** important (missing
context), **[C]** nice-to-have (minor update).

---

### 12.1 `platform.md`

Structure is intact; the stack table, phases and AI-models sections are still
accurate. The main gap is the **public-facing surface** (Partner API + Widget +
dev environment) and the **auth extensions** (social signup, domain allowlist).
No section needs to be rewritten; additions only.

| # | Section | Priority | Action |
|---|---------|----------|--------|
| P1 | `## Stack` | A | Add row: "Public API" → `Partner API (FastAPI, /partner/v1/*)`. Add row: "Embed SDK" → `klai-widget (SolidJS, FlowiseChatEmbed fork), hosted from portal public/`. Update "Auth / Identity" row to mention Zitadel IDPs (Google, Microsoft) |
| P2 | `## Customer Portal` | A | Rewrite the opening paragraph — portal now has Chat, Kennis (KB editor + sources), Regels (guardrails), Team, MCP's, API keys, Chat widgets, Templates, Account. Not just "redirect to LibreChat". Replace "LibreChat iframe" framing with "chat-first portal with embedded LibreChat for the chat tab" |
| P3 | `## Customer Portal § User Groups & Lifecycle` | B | Add subsection "Personal KB": every user gets a personal KB on signup, admins see "Mijn" badge, per-user isolation enforced at retrieval layer (link to `klai-knowledge-architecture.md §10.2`) |
| P4 | `## Customer Portal` (new subsection after Lifecycle) | A | Add `### Public API surface` describing `api.getklai.com/partner/v1/*`, `pk_live_...` Bearer auth, KB-scoped keys, rate limiting, SPEC-API-001 |
| P5 | `## Customer Portal` (new subsection) | A | Add `### Chat widgets` — `cdn.getklai.com/widget/klai-chat.js` embeddable bundle, `wgt_...` public ID, JWT session-token auth (`WIDGET_JWT_SECRET`), allowed-origins fail-closed, wildcard subdomain support, 200 kB gzipped budget. Note: widget bundle currently served from portal `public/`, not CDN |
| P6 | `## Customer Portal` (new subsection) | B | Add `### Guardrails (Rules + Templates)` — rules + templates resolved in LiteLLM pre-call hook from org + KB context, injected into system prompt. Rules are strict guardrails, Templates are reusable response scaffolds |
| P7 | `## Multi-tenancy model` | B | Add paragraph on **per-widget tenant context** — widget session JWTs carry `org_id` + `kb_ids`, chat-completions endpoint honours the scoping |
| P8 | `## Architecture separation: public vs. AI stack` | A | Add `api.getklai.com` and `cdn.getklai.com` (or alternative: "widget bundle hosted from portal origin") to the core-01 list. Document the DNS + Caddy routing for `api.getklai.com/partner/v1/*` |
| P9 | `## Server layout (Phase 1 complete)` | A | The core-01 services list needs updating: add `partner-api` (if distinct) or note that Partner API endpoints live on `portal-api`. Document the `dev.getklai.com` parallel environment (isolated LibreChat + LiteLLM containers, shared infra secrets, dev Caddy routing) — add as new subsection or row |
| P10 | `## Branding` | C | Update the branding paragraph — Klai brand is now well-defined: amber `#fcaa2d`, ivory `#fffef2`, cream `#f3f2e7`, Parabole font. Widget defaults match. Portal design system doc at `docs/portal-design-system.md` |
| P11 | `## LibreChat: technical details` | B | Note the hook now applies Rules + Templates in addition to KB retrieval. Mention dev-env LibreChat isolation |
| P12 | `## Phases` | B | No structural change, but add a note to Phase 2 line about Rules + Templates guardrails being added as a sub-feature in April 2026 |
| P13 | `## GDPR / AVG Compliance` | C | Consider adding "Widget conversations" — what's the data retention for widget chat sessions? If the same product_events table + no persistent chat state, note it |
| P14 | `## Observability & Logging § Structured Logging` | B | Add bullet on `domain` field (api_key vs widget) replacing the old `integration_type`. Add that `request_id` now propagates from Caddy → portal-api → downstream services (already in `observability.md`, worth cross-referencing) |
| P15 | `## Observability & Logging § Dashboards` | C | Note new product_event types: `widget.chat.started`, `widget.chat.completed` (and other product events from SPEC-GRAFANA-METRICS: signup, billing.*, meeting.*, knowledge.uploaded, notebook.created/opened, source.added, knowledge.queried) |
| P16 | `## Compatibility Review (2026-03-03)` | C | Add a 2026-04 delta entry: Partner API live, Widget live, SPEC-WIDGET-002 (domain split) deployed, social signup live, domain allowlist live, two-phase crawler live. Or supersede the Review with a new-dated one |
| P17 | `## Compatibility Review § Summary table` | B | Add rows: `Partner API` ✅, `Chat widget` ✅, `Widget JWT auth` ✅, `Social signup (Zitadel IDPs)` ✅, `Domain allowlist + join requests` ✅, `Dev environment` ✅, `Two-phase web crawler` ✅, `SimHash-LSH dedup` ✅, `Google Drive OAuth connector` ✅ |

---

### 12.2 `klai-knowledge-architecture.md`

Structure is solid — all 14 sections still hold. Most updates are **additions**
to existing sections, not rewrites. The big exception: §7 retrieval needs a
subsection on source-aware selection (SPEC-KB-021 is already noted in the flow
doc but not here).

| # | Section | Priority | Action |
|---|---------|----------|--------|
| K1 | `## 0. Current State vs. Target Architecture § What exists today` | B | Update the table: `knowledge-ingest` endpoints list — two-phase crawler, cookie auth, adapter-owned image URLs. `retrieval-api` — `source_aware_select`, router-as-signal. Add row: `partner-api endpoints` on portal-api. Add row: `klai-widget` (external bundle) |
| K2 | `## 0. § What was recently built` | A | Add rows for April 2026: SPEC-KB-021 (multi-source retrieval quality), SPEC-KB-025 (Google Drive OAuth), SPEC-CRAWL-002 (two-phase crawler + cookies), SPEC-CRAWL-003 (quality Layer A/B/C + SimHash-LSH), SPEC-DOCS-001 (stable page UUIDs, SHA conflict handling), SPEC-WIDGET-001/002 (chat widget + domain split) |
| K3 | `## 0. § What does NOT exist yet` | C | Review — taxonomy / gap editorial inbox UI still missing? SharePoint OAuth connector scoped for v2? |
| K4 | `## 2. Platform Service Architecture § 2.1 Two products` | B | The flow `Focus → Save to Knowledge` is unchanged. But add a third interface-facing product: **Chat widget** (external-facing, read-only, JWT-scoped). Show in the diagram with its own arrow into the retrieval layer via `partner-api → widget-config → chat-completions` |
| K5 | `## 2. § 2.2 Shared infrastructure layer` | B | Add to the diagram: Chat Widget service arrow into Retrieval API via the Partner API |
| K6 | `## 2. § 2.3 Qdrant scope conventions` | C | No structural change. But clarify that widget-scoped retrieval uses `org_{zitadel_org_id}` scope filtered by the `kb_ids` whitelist in the session JWT |
| K7 | `## 2. § 2.7 LibreChat integration via LiteLLM hook` | A | Rename section or split: **(a) LibreChat integration**, **(b) Partner API integration** — because the hook now also serves partner API and widget traffic through a slightly different path. Document that Rules + Templates are also applied in the hook. Diagram needs an extra row: `LiteLLM hook → rules_resolver + templates_resolver → retrieval-api → model` |
| K8 | `## 4. Ingestion Architecture § 4.1 Ingestion adapters` | A | Update the table. Web crawler row: "Crawl4AI two-phase (BFS + extraction), canary + login indicator auth guard, cookie auth, SimHash-LSH dedup for >200 pages". Add new row: "Google Drive" via OAuth connector, SPEC-KB-025. Adjust "Documents (PDF, DOCX...)" to note adapter-owned image URL resolution (SPEC-KB-IMAGE-001) |
| K9 | `## 4. § 4.2 Enrichment pipeline` | A | Update **Phase 2 A — Contextual prefix**: the enrichment prompt is now source-aware (`kb_name`, `connector_type`, `source_domain`, `content_type`, `source_label`). Add note: enrichment failure is **fail-loudly** (ingest fails hard). Add new sub-item **F. Quality status** (Layer A/B/C) computed per page during ingest |
| K10 | `## 4. § 4.3 Helpdesk transcript extraction` | C | Unchanged — still one adapter example |
| K11 | `## 5. Storage Architecture § 5.1 Vector store: Qdrant` | B | Add a paragraph on new payload field: **`source_label`** (keyword-indexed) — enables the Qdrant Facet API for source-aware retrieval without full-scan filters |
| K12 | `## 7. Retrieval Architecture § 7.1 Six-step pipeline` | A | Add a new step or extend Step 4: **source-aware selection** (`source_aware_select`). Two modes: `mentioned` (boost when query names a source) and `diversify` (MMR across sources). Document router-as-signal (keyword match on source_label + semantic centroid per source). Decision record logged per request |
| K13 | `## 7. § 7.5 (new)` | A | Add new subsection **7.5 Source-aware multi-source retrieval** describing SPEC-KB-021: centroid-per-source, router-as-signal (not gate), two selection modes, STOP_WORDS deduplication, source_label filter via Facet API |
| K14 | `## 7. § 7.1` (new step) | B | Document where Rules + Templates injection happens in the pipeline — **before** retrieval (prompt enrichment), not after. Verify in code whether rules filter/augment the query or only the final system message |
| K15 | `## 9. AI Interface § 9.2 MCP integration` | C | Review — nothing substantive changed this week |
| K16 | `## 9. § 9.5 (check)` | B | If there is a "retrieval in chat" subsection, update to mention widget as third consumer (external). Otherwise add it |
| K17 | `## 10. Multi-tenancy § 10.2 Personal knowledge` | B | **Already documents Gitea path `personal/users/{user_uuid}/`** — verify still matches code. Add note: portal UI now surfaces per-user personal KB with "Mijn" badge; admins can delete any personal KB (admin override) |
| K18 | `## 10. § 10.4 Retrieval scope and attribution` | A | Update the "Guard" paragraph — external widget and Partner API **never** query personal scope; only `org_{zitadel_org_id}`. Widget JWT payload restricts even further to whitelisted `kb_ids` subset |
| K19 | `## 11. Publication Layer` | A | Add new subsection **11.4 KB editor reliability (SPEC-DOCS-001)**: full UUIDs in URL (not 8-char prefix), BlockNote JSON lossless persistence, client-owned SHA + promise queue, 409 Conflict auto-retry, beforeunload flush. Replace the "BlockNote editor (browser) → markdown" opening diagram — it's now `BlockNote JSON` not `markdown` (markdown is a legacy export path) |
| K20 | `## 11. § 11.2 Access control` | B | Add third value to the KB-setting table: `widget-scoped` — readable by an external JavaScript widget via the widget-config endpoint, constrained to the widget's `kb_ids` whitelist. Or document this under §10.4 if preferred |
| K21 | `## 11. (new subsection)` | B | Add **11.5 Embeddable chat widget** — not a publication *site* but a publication *surface*. External websites embed `klai-chat.js`, which renders a chat bubble that queries the org KB via the Partner API with a JWT session token. Reference SPEC-WIDGET-001 + SPEC-WIDGET-002 |
| K22 | `## 12. The Self-Improving Loop` | C | Update the top-of-loop trigger list: "Users ask questions (**chat widget**, internal tools, **Partner API consumers**)" |
| K23 | `## 13. Open Questions § 13.1/13.2` | C | Check against this week's decisions: SPEC-KB-021 closed one of the retrieval open questions (diversity + router). Reduce the open list accordingly |
| K24 | `## 14. Technology Stack` | A | Add rows: klai-widget (SolidJS fork of FlowiseChatEmbed), Partner API (FastAPI route set on portal-api), klai-connector Google Drive adapter, Crawl4AI (confirm version), SimHash/SimHash-LSH library. Drop anything unused |

---

### 12.3 `knowledge-ingest-flow.md`

Already updated 2026-04-16 for SPEC-KB-021. The gaps are **web-crawler and
connector-level additions** from SPEC-CRAWL-002/003, SPEC-KB-025, and
SPEC-KB-IMAGE-001.

| # | Section | Priority | Action |
|---|---------|----------|--------|
| I1 | `## Part 1: Where content comes from § 1.1 KB editor` | A | Update the save-flow — client-owned SHA, promise queue, 409 Conflict auto-retry, `beforeunload` flush. Auto-save debounce remains 1.5s but Gitea failure mode is documented (SPEC-DOCS-001) |
| I2 | `## Part 1: § 1.2 or similar (webcrawler)` | A | Rewrite the webcrawler subsection as a **two-phase** pipeline: (phase 1) BFS URL discovery with full DOM preserved, (phase 2) extraction via `css_selector`. Add subsection on **auth guard** (canary URL + login indicator), **cookie auth** (Crawl4AI hooks), **URLPatternFilter path-only wildcards** |
| I3 | `## Part 1: (new subsection)` | A | Add **§ 1.x Google Drive connector** — OAuth flow, refresh-token-based access, folder scoping, file type handling. Reference SPEC-KB-025 |
| I4 | `## Part 1: (connector image contract)` | B | Add note on **adapter-owned image URL resolution** (SPEC-KB-IMAGE-001) — each adapter resolves its own image URLs; core pipeline only receives resolved URLs. Reference: `refactor(connector): tighten types and document image contract` |
| I5 | `## Part 2: What happens inside knowledge-ingest § Enrichment` | A | The `source_aware` enrichment prompt is in here — verify it is documented. Add documentation on **fail-loudly** enrichment mode |
| I6 | `## Part 2: (new subsection)` | A | Add **§ 2.x Quality detection — Layer A/B/C (SPEC-CRAWL-003)** — Layer A structural, Layer B canary + login-indicator, Layer C content fingerprint. New `quality_status` column in migration 005 |
| I7 | `## Part 2: (new subsection)` | A | Add **§ 2.y SimHash-LSH near-duplicate detection** — kicks in for syncs >200 pages, linear time, applied during extraction phase |
| I8 | `## Part 2: Qdrant payload / source_label` | B | Verify `source_label` keyword-indexed field is documented (was added in `06403286` sync commit). Extend with: used by retrieval-side Qdrant Facet API |
| I9 | `## Part 4: Tenant provisioning` | B | Add: provisioning sets RLS tenant context before default KB + system group inserts. Reuses prod inter-service secrets for dev env (commit c06eeed6) |
| I10 | `## Service map (core-01 + gpu-01)` | B | Update to include: `partner-api` endpoints on portal-api, new OAuth callback endpoints. Add mention of dev.getklai.com parallel stack (isolated LibreChat + LiteLLM) |
| I11 | `## GPU inference services` | C | No changes this week |
| I12 | `## Self-learning feedback loop (SPEC-KB-015)` | C | No changes this week |
| I13 | `## Part 6: Assertion modes` | C | No changes this week |

---

### 12.4 `knowledge-retrieval-flow.md`

Also updated 2026-04-16 for SPEC-KB-021. Gaps are **widget retrieval path**,
**Rules + Templates injection**, and **Partner API consumer path**.

| # | Section | Priority | Action |
|---|---------|----------|--------|
| R1 | `## The big picture § diagram` | A | Extend the diagram with a second entry path: **External website → klai-widget.js → `GET /partner/v1/widget-config` → JWT → `POST /partner/v1/chat/completions` → LiteLLM hook → retrieval-api**. Note that the widget is a peer consumer of the same retrieval pipeline as LibreChat |
| R2 | `## Part 1: User preferences § KBScopeBar` | B | Note that widget + Partner API have no KBScopeBar — they are scope-locked at creation (KB whitelist). `kb_retrieval_enabled` does not apply to Partner API / widget; KB scope is carried in the API key's `kb_access` rows (API) or the JWT payload (widget) |
| R3 | `## Part 1: Setting 2 Personal KB` | A | Add a strong "Widget + Partner API never query personal scope" statement. The guard is enforced at the hook level by checking consumer type |
| R4 | `## Part 2: From message to chunks § Coreference resolution` | C | Unchanged — still klai-fast |
| R5 | `## Part 2: § Hybrid search` | C | The 3-leg RRF is unchanged |
| R6 | `## Part 2: § Reranking` | C | Unchanged |
| R7 | `## Part 2: § (new) Source-aware selection` | A | Add a subsection — should mirror the `klai-knowledge-architecture.md §7.5` addition but with the pipeline-level detail: router-as-signal formula, centroid-per-source computation, STOP_WORDS list, decision record structure, mentioned vs diversify mode selection logic |
| R8 | `## Part 3: From chunks to context § (new)` | A | Add a subsection **Rules + Templates injection** — before the context block is assembled, the hook queries rules (guardrails) and templates (response scaffolds) for the current org + active KB set. Injected into the system message alongside retrieved chunks. Rules come first, then templates, then KB context |
| R9 | `## Part 3: § Context injection` | B | Note that the widget's system prompt is **grounded KB-only** (no general knowledge) — different from LibreChat's default. snarkdown used for markdown rendering in the widget (vs. BlockNote in the portal) |
| R10 | `## Part 4: What actually changes per user action` | B | Add row: widget visitor sends a message → different consumer path but same retrieval output; widget-specific system prompt applied |
| R11 | `## Part 5: Trivial messages` | C | Unchanged — same trivial-check logic applies to widget traffic |
| R12 | `## Reference: configuration values` | B | Add widget-specific config values: `WIDGET_JWT_SECRET`, `WIDGET_SESSION_TTL_SECONDS` (default 3600), rate limits applied per-widget |
| R13 | `## Reference: key files` | B | Add `klai-portal/backend/app/services/widget_auth.py`, `klai-portal/backend/app/api/partner_dependencies.py`, `klai-portal/backend/app/api/admin_widgets*.py`, `klai-widget/src/main.ts`, and any rules/templates resolver files |

---

### 12.5 Cross-cutting — where to document once vs. duplicate

Some new concepts span multiple docs. Recommended homes:

| Concept | Canonical home | Cross-reference from |
|---------|----------------|----------------------|
| Partner API surface + `pk_live_...` auth | `platform.md` | knowledge-architecture §11.5, retrieval-flow Part 1 |
| Widget bundle + JWT session-token auth | `platform.md` | knowledge-architecture §11.5, retrieval-flow big-picture |
| Source-aware retrieval (`source_aware_select`) | `knowledge-retrieval-flow.md` Part 2 | knowledge-architecture §7.5 (summary + link) |
| Two-phase crawler + Layer A/B/C + SimHash-LSH | `knowledge-ingest-flow.md` Part 1 + Part 2 | knowledge-architecture §4.1 (summary + link) |
| Rules + Templates guardrails | `knowledge-retrieval-flow.md` Part 3 | platform.md (brief mention), knowledge-architecture §2.7 |
| Per-user personal KB (Gitea path, admin override) | `knowledge-architecture.md §10.2` | platform.md Customer Portal section |
| Dev environment (`dev.getklai.com`) | `platform.md` § Server layout | `docs/dev.md` (runbook) — already exists |
| KB editor reliability (SHA, promise queue, UUIDs) | `knowledge-architecture.md §11` | knowledge-ingest-flow Part 1.1 |
| SPEC-WIDGET-002 domain split | `platform.md` Customer Portal | knowledge-architecture §11 (brief note) |
| Social signup + domain allowlist | `platform.md` Customer Portal (Auth subsection) | nowhere else (it's platform-level, not knowledge-level) |

---

### 12.6 Suggested execution order

If a single person updates all four docs, this order minimises rework:

1. **`platform.md`** first — establishes Partner API + Widget + dev env as
   platform primitives. Other docs can then link here.
2. **`knowledge-architecture.md`** second — adds the retrieval and ingestion
   deltas at architecture level, referencing `platform.md` for surface details.
3. **`knowledge-ingest-flow.md`** third — fills in the engineering detail for
   the new connectors, auth modes, and quality layers.
4. **`knowledge-retrieval-flow.md`** last — fills in the widget retrieval path
   and Rules + Templates injection at engineering detail.

If time-budgeted, the **A-priority items across all four docs** form a
coherent minimum update set (~20 edits total) that brings the docs back to
an accurate state. B-priority items can follow in a second pass.

## 13. Workflow to consume this doc

1. Read each architecture doc top-to-bottom with this changes-doc open side-by-side.
2. For each §/subsection in the architecture doc, mark whether it is (a) still
   accurate, (b) stale — needs update, (c) missing — section needs to be added.
3. Produce a concrete task list per architecture doc: `Section X.Y: add paragraph
   about Z` or `Section X.Y: rewrite because old approach was replaced by W`.
4. Implement the updates in a separate pass (not in this week's sync).
5. Delete this file once all four docs are synced.
