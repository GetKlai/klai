---
id: SPEC-PORTAL-SOURCES-RENAME-001
version: 0.1.0
status: draft
created: 2026-05-12
author: Mark Vletter
priority: medium
parent: SPEC-PORTAL-KENNIS-001 (Phase E follow-up — cleanup + rename)
related:
  - SPEC-KB-SOURCES-001 (wire-contract owner — already English)
  - SPEC-PORTAL-KENNIS-002 (per-row delete + reauth)
---

# SPEC-PORTAL-SOURCES-RENAME-001 — Rename Bron → Source and untangle the bronnen-tab god-component

## Goal

Make the bronnen-tab code identifiers consistent with the rest of the Klai KB
codebase (English "source") and split the 657-line `bronnen.tsx` god-component
into a route-orchestrator plus single-responsibility files. Add the structural
guards that prevent the original bugs (stale list after add, drift in queryKey
literals) from coming back.

## Motivation

After PR #574 (Fix knowledge source labels and refresh) closed the user-visible
bugs reported on 2026-05-12, the bronnen-tab area still has three classes of
debt that compound risk:

### 1. NL/EN naming split inside one feature

The bronnen-tab is the only area in the KB code that uses Dutch identifiers.
Tally on origin/main, scope `klai-portal/frontend/src/routes/app/knowledge`
plus `klai-portal/backend/app/api/app_knowledge_bases.py` plus
`klai-knowledge-ingest/knowledge_ingest/routes/kb_sources.py`:

| Convention | Count | Examples |
|---|---|---|
| English "source" identifiers | ~27 in KB folder | `useSourceSubmit`, `SourceKind`, `SourceIngestedResponse`, `SourceTypeGrid`, `SourceTypeTile`, `UrlSourceForm`, `TextSourceForm`, `add-source.tsx`, `source-types.ts`, `invalidateKnowledgeSourceLists` |
| English "source" in wire-contract | all endpoints | `/knowledge-bases/{kb_slug}/sources`, `/sources/{id}/content`, `/sources/url`, `/sources/text`, `/sources/file` |
| English "source" in ingest service | full module | `routes/kb_sources.py`, `pg_store.list_kb_sources`, `compute_source_label` |
| Dutch "bron" identifiers | ~10 in two files | `BronOut`, `BronnenResponse`, `BronContentItem/Chunk/Response`, `list_kb_bronnen`, `get_bron_content`, `Bron`, `BronRow`, `BronContent`, `BronIcon`, `mapBronStatus`, `kbQueryKeys.bronnen`, queryKeys `'kb-bronnen'` / `'bron-content'`, paraglide `kb_count_bron*` |

Same endpoint: wire-name `GET /sources`, Python function `list_kb_bronnen`,
response model `BronnenResponse`. The name flips across the function boundary.
That makes ripgrep + refactor tools unreliable, makes onboarding harder, and
makes the next "sources / bronnen" decision needlessly political.

### 2. `bronnen.tsx` is a god-component

657 lines, 3 component-types, 4 mutations, OAuth reauth, page-index probing,
and a docs-tree branch all live in one file:

| Concern | Lines | Should live in |
|---|---|---|
| `BronContent` (drill-down: connector + upload branches) | ~100 | own file, split per kind |
| `BronRow` (icon, name, 4 mutations, inline rename, reauth) | ~330 | own file + own hooks |
| `BronnenTab` (action bar, data fetching, docs-tree, page-index) | ~180 | route file only |
| Imports + boilerplate | ~50 | — |

This violates the rule documented in
`.claude/rules/klai/projects/portal-frontend.md`:
"Route component owns data fetching + page layout. Extract sub-components at
~50 lines JSX."

### 3. UI rules silently violated

The 2026-05-12 bug-fix round introduced new UI primitives in `bronnen.tsx`
that contradict portal-wide standards:

- **Inline rename rolls its own primitives.** A raw `<input>` + two custom
  `<button>` elements for save/cancel — instead of the canonical `InlineEdit`
  component documented in `portal-frontend.md` § "Inline rename (InlineEdit)".
  Other tabs (transcribe, members, taxonomy) use `InlineEdit`. The new
  bronnen rename is the outlier.
- **~25 hardcoded NL strings.** `Verbind opnieuw`, `Synchroniseer bron`,
  `Naam aanpassen`, `Bewerken in editor`, etc. The portal CLAUDE.md says
  "all UI strings via Paraglide" — not followed in `bronnen.tsx`,
  `-bronnen-helpers.tsx`, `-bronnen-types.ts`.
- **Same pencil icon, three different behaviours.** Connector pencil →
  navigate to `/edit-connector/$connectorId`. Upload pencil → inline rename.
  Docs-editor pencil → navigate to `/docs/$kbSlug/$pageId`. One icon, three
  contracts — exactly the affordance ambiguity the user reported in the
  original review.

### 4. The queryKey registry is a one-time fix without enforcement

PR #574 added `kbQueryKeys` + `invalidateKnowledgeSourceLists`. Nothing
mechanical prevents the next contributor from writing
`queryClient.invalidateQueries({ queryKey: ['kb-bronnen', kbSlug] })`
directly and re-introducing the stale-list bug. We need a lint rule.

## Scope

### In

**Frontend (`klai-portal/frontend/src/routes/app/knowledge/`):**

- Rename file `$kbSlug/bronnen.tsx` → `$kbSlug/sources.tsx`.
- Split into: `sources.tsx` (route only, < 200 lines), `-sources-row.tsx`,
  `-sources-content.tsx`, `-sources-actionbar.tsx`, `-sources-hooks.ts`.
- Rename `-bronnen-types.ts` → `-sources-types.ts`,
  `-bronnen-helpers.tsx` → `-sources-helpers.tsx`.
- Rename all `Bron*` identifiers to `Source*` (types, components, helpers).
- Update `kbQueryKeys`: `.bronnen` → `.sources`, `.bronContent` →
  `.sourceContent`, query-key literals `'kb-bronnen'` → `'kb-sources'`,
  `'bron-content'` → `'source-content'`.
- Replace inline rename with `<InlineEdit>` from `components/ui/inline-edit`.
- Migrate all NL strings in the renamed files to Paraglide (NL + EN).
- Disambiguate pencil icon: pencil = rename-inline (upload only). Connector
  edit → text link "Bewerken" + `Settings` icon. Docs-editor link →
  `NotebookPen` icon (lucide-react).
- Add ESLint `no-restricted-syntax` rule blocking direct queryKey array
  literals starting with any registered key from `-kb-query-keys.ts` outside
  that file itself.

**Backend (`klai-portal/backend/app/api/app_knowledge_bases.py`):**

- Rename Python identifiers: `BronOut` → `SourceOut`, `BronnenResponse` →
  `SourcesResponse`, `BronContentItem/Chunk/Response` →
  `SourceContentItem/Chunk/Response`, `list_kb_bronnen` → `list_kb_sources`,
  `get_bron_content` → `get_source_content`.
- Update response shape: top-level field `bronnen: list[...]` →
  `sources: list[...]`. **Breaking wire change** — single in-repo consumer
  (frontend in same PR).
- Update existing tests in `klai-portal/backend/tests/test_app_knowledge_bronnen.py`
  + rename file to `test_app_knowledge_sources.py`.

**Router:**

- Frontend URL `/app/knowledge/$kbSlug/bronnen` → `/app/knowledge/$kbSlug/sources`.
- Add legacy redirect: route with `beforeLoad` on `/bronnen` that throws
  `redirect({ to: '/sources', ... })` (`route.tsx` `TAB_PATH_MAP` updated).
- `routeTree.gen.ts` regenerated via `npx @tanstack/router-cli generate`.

**Tests:**

- New characterization tests for `SourceRow` covering: sync mutation (upload
  + connector branches), delete mutation, reindex (upload), rename mutation,
  reauth (OAuth authorize) flow, status-pending refetchInterval.
- New ESLint rule unit test (uses `RuleTester`) verifying the queryKey guard
  rejects out-of-file `['kb-sources', …]` literals and accepts
  `kbQueryKeys.sources(slug)`.
- Existing `-kb-query-keys.test.ts` retained — assertions updated for renamed
  keys.

### Out (explicit)

- **No DB migration.** `extra::jsonb -> 'source_url'` and
  `extra::jsonb -> 'display_name'` already shipped via PR #574.
- **Refactor of `add-connector.tsx` (1415 lines) and
  `edit-connector.$connectorId.tsx` (1272 lines).** Same god-component
  pattern but separate UX surface, separate SPEC.
- **Refactor of `taxonomy.tsx` (1126 lines).** Out — Geavanceerd-tab scope.
- **Rename of the `Bronnen` user-facing tab label** to `Sources` in the
  NL locale. The Dutch product keeps "Bronnen" as the user-facing word;
  only EN locale shows "Sources". This SPEC renames code, not product
  copy.
- **API backward-compat alias for `bronnen` response field.** Not needed:
  one in-repo consumer, ships in same PR. If a third-party integration
  surfaces during PR review, fall back to pydantic `Field(alias=…)` —
  noted as a defer-condition under § Open Questions.
- **Refactor of `mapBronStatus` / `mapSourceStatus` into per-kind
  functions.** Nice-to-have but out of scope for this SPEC; trace the
  function as-is and leave a follow-up `@MX:NOTE`.

### Backend changes summary

No alembic migration. No new endpoint. No new business logic. Pure rename
plus an internal pydantic-field rename.

## Requirements (EARS)

### Functional

- **REQ-1**: When a user navigates to `/app/knowledge/<slug>/bronnen` after
  this SPEC ships, the router shall 301-redirect them to
  `/app/knowledge/<slug>/sources` while preserving any query parameters.
- **REQ-2**: When `useSourceSubmit` or `FileUploadForm` completes a
  successful submit, the system shall invalidate the `kb-sources` query
  (via the renamed `invalidateKnowledgeSourceLists`) AND navigate the user
  to `/app/knowledge/<slug>/sources`. Behaviour identical to today's
  bronnen tab, only the path differs.
- **REQ-3**: When a contributor opens `bronnen.tsx` after this SPEC, the
  file shall not exist; the equivalent file `sources.tsx` shall be
  ≤ 200 lines and contain only route definition + data-fetching hooks +
  layout assembly.
- **REQ-4**: When a contributor writes
  `queryClient.invalidateQueries({ queryKey: ['kb-sources', slug] })`
  outside `-kb-query-keys.ts`, the ESLint rule
  `klai/no-direct-kb-querykey` shall report an error pointing them at
  `kbQueryKeys.sources(slug)`.
- **REQ-5**: When a user with a contributor or owner role clicks the
  pencil icon on an upload-row, the system shall open an inline rename
  using the canonical `<InlineEdit>` component, NOT a hand-rolled input.
- **REQ-6**: When a user with a contributor or owner role clicks the
  edit affordance on a connector-row, the system shall navigate to
  `/app/knowledge/<slug>/edit-connector/<connectorId>` via a labelled
  text link or `Settings`-icon button — NOT a `Pencil` icon (which
  is reserved for inline rename per REQ-5).
- **REQ-7**: When the docs-editor link is shown for a source-row whose
  slug exists in the page-index, the system shall render a
  `NotebookPen` icon (not `Pencil`) with tooltip "Bewerken in editor"
  (NL) / "Open in editor" (EN).
- **REQ-8**: Every user-visible string in `sources.tsx`,
  `-sources-row.tsx`, `-sources-content.tsx`, `-sources-actionbar.tsx`
  and `-sources-helpers.tsx` shall come from Paraglide
  (`@/paraglide/messages`). No literal NL or EN strings in JSX.

### Non-functional

- **REQ-9**: After this SPEC the wire response for
  `GET /api/app/knowledge-bases/{slug}/sources` shall return
  `{ "sources": [...] }`. No top-level `bronnen` field.
- **REQ-10**: `tsc --noEmit` on `klai-portal/frontend` shall pass with
  zero errors. `bun run lint` shall pass with zero errors (including
  the new `klai/no-direct-kb-querykey` rule).
- **REQ-11**: `pytest klai-portal/backend/tests/test_app_knowledge_sources.py`
  shall pass with the same assertion count as the pre-rename
  `test_app_knowledge_bronnen.py`. No assertion deleted; every
  `BronOut`-shaped assertion translated to `SourceOut`-shaped.
- **REQ-12**: The Playwright smoke-flow documented in
  `.claude/rules/klai/lang/testing.md` (open KB → click Bronnen tab
  → add a URL source → see it appear without hard refresh) shall pass
  using the new `/sources` URL.

## Acceptance Criteria

1. `grep -r "Bron" klai-portal/frontend/src klai-portal/backend/app` returns
   zero hits (case-sensitive, anchored at word boundary). User-facing NL
   tab label "Bronnen" lives only in `messages/nl.json`.
2. `grep -r "list_kb_bronnen\|BronOut\|BronnenResponse\|kb-bronnen\|bron-content"`
   returns zero hits across both repos.
3. `wc -l klai-portal/frontend/src/routes/app/knowledge/$kbSlug/sources.tsx`
   reports ≤ 200 lines.
4. `wc -l klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-sources-row.tsx`
   reports ≤ 200 lines.
5. `wc -l klai-portal/frontend/src/routes/app/knowledge/$kbSlug/-sources-content.tsx`
   reports ≤ 120 lines.
6. `bun run lint` reports zero errors, the new `klai/no-direct-kb-querykey`
   rule has at least one passing accept-case test and at least one passing
   reject-case test.
7. Visiting `https://my.getklai.com/app/knowledge/<slug>/bronnen` issues a
   301 to `…/sources` (verified via Playwright `browser_network_requests`).
8. Adding a URL or file source lands the user on `/sources` and the new
   source is visible in the list within 500ms of the submit response.
9. `git diff --stat` of the merge commit shows the Python rename
   (`app_knowledge_bases.py` + `test_app_knowledge_sources.py`), the
   frontend split (8-10 files), the lint-rule files (3 files), the
   paraglide message JSON delta (NL + EN), and the `routeTree.gen.ts`
   regeneration. No unrelated drift.

## Implementation Plan

The SPEC ships as one PR with ordered commits. Each commit is independently
green (`tsc --noEmit` + lint + tests).

**Methodology**: DDD (ANALYZE-PRESERVE-IMPROVE). The bronnen tab has live
behaviour that production users depend on; we lock it down with
characterization tests before touching identifiers.

### Phase 0 — Worktree + characterization tests

- `git worktree add ../klai-sources-rename -b feat/SPEC-PORTAL-SOURCES-RENAME-001 origin/main`
- Add `klai-portal/frontend/src/routes/app/knowledge/$kbSlug/__tests__/sources-row.test.tsx`
  (initially imports `bronnen` modules — renamed in Phase 3).
- Test cases: sync mutation success, delete (upload + connector), rename
  (upload), reauth (mocked window.location), refetchInterval activates on
  pending status.
- Verify all tests green against the current code before any rename.

### Phase 1 — Backend rename (single commit)

- `BronOut`/`BronnenResponse`/`BronContent*` → `Source*` in
  `app_knowledge_bases.py`.
- `list_kb_bronnen` → `list_kb_sources`; `get_bron_content` →
  `get_source_content`.
- Response top-level field `bronnen` → `sources` (breaking).
- Test file rename + assertion update.
- Smoke test: `pytest klai-portal/backend/tests/test_app_knowledge_sources.py`.

### Phase 2 — Frontend type rename (single commit)

- `-bronnen-types.ts` → `-sources-types.ts`,
  `-bronnen-helpers.tsx` → `-sources-helpers.tsx`.
- `Bron` → `Source`, `BronnenResponse` → `SourcesResponse`,
  `mapBronStatus` → `mapSourceStatus`, `BronStatus` → `SourceStatus`,
  `BronIcon` → `SourceIcon`, `editablePageIdForBron` →
  `editablePageIdForSource`.
- Update `kbQueryKeys` keys + helper signatures.
- `-kb-query-keys.test.ts` assertions updated.
- Wire `bronnen.tsx` against renamed imports (file itself not yet split).
- Smoke: `tsc --noEmit` green; characterization tests green.

### Phase 3 — Frontend file split (multiple commits, one per layer)

For each commit: extract → import → drop original → `tsc --noEmit` →
characterization tests green.

1. `-sources-hooks.ts` — `useSourceSync`, `useSourceDelete`,
   `useSourceRename`, `useSourceReauth`.
2. `-sources-content.tsx` — drill-down split into
   `<ConnectorContent>` + `<UploadContent>` sub-components within
   the file.
3. `-sources-row.tsx` — `SourceRow` consuming the hooks.
4. `-sources-actionbar.tsx` — top-of-tab action bar (sync-all, open
   editor, add source, count text).
5. Rename `bronnen.tsx` → `sources.tsx`, reduce body to data-fetching +
   layout assembly (target ≤ 200 lines).
6. Add legacy `/bronnen` URL redirect in `$kbSlug/route.tsx`
   `TAB_PATH_MAP`.

### Phase 4 — UI primitive alignment (single commit)

- Replace hand-rolled inline rename in `-sources-row.tsx` with
  `<InlineEdit>` from `components/ui/inline-edit`. Save/cancel use the
  documented `h-6 text-[10px]` Button pattern from
  `portal-frontend.md`.
- Disambiguate icons:
  - Pencil → upload rename only (REQ-5).
  - Connector edit → text link "Bewerken" with `Settings` icon (REQ-6).
  - Docs-editor → `NotebookPen` icon (REQ-7).

### Phase 5 — Paraglide migration (single commit)

- Extract ~25 strings from `sources.tsx` + new component files into
  `messages/nl.json` and `messages/en.json` under the `kb_sources_*` /
  `kb_source_*` namespace (matching existing `knowledge_add_source_*`
  convention).
- Replace literals with `m.kb_sources_*()` calls.
- Verify Paraglide compile succeeds (`bun run build`).
- Rename `kb_count_bron_singular` → `kb_count_source_singular`,
  `kb_count_bronnen` → `kb_count_sources` in both locales.

### Phase 6 — ESLint guard (single commit)

- New rule file: `klai-portal/frontend/eslint-rules/no-direct-kb-querykey.js`.
- Wire into `klai-portal/frontend/eslint.config.js` via
  `plugins: { klai: { rules: { 'no-direct-kb-querykey': ruleModule } } }`.
- Rule logic: detects `ObjectExpression` with `Property.key.name === 'queryKey'`
  whose value is an `ArrayExpression` starting with a `Literal` matching
  any key prefix from `-kb-query-keys.ts` (`'kb-sources'`, `'source-content'`,
  `'kb-items'`, `'personal-knowledge'`, `'app-knowledge-bases-stats-summary'`,
  `'kb-connectors-portal'`, `'docs-tree'`, `'docs-page-index'`,
  `'app-knowledge-base'`). Reports unless `context.filename` ends with
  `-kb-query-keys.ts`.
- Unit test using `RuleTester` with 4 cases: reject `['kb-sources', slug]`
  outside the helper, accept `kbQueryKeys.sources(slug)`, accept inside
  `-kb-query-keys.ts`, accept unrelated `['some-other-thing']`.

### Phase 7 — QA + ship

- `bun run lint` zero errors.
- `tsc --noEmit` zero errors.
- `bun run build` green (Paraglide compiles + `routeTree.gen.ts` regenerated).
- `pytest klai-portal/backend/tests/test_app_knowledge_sources.py` green.
- Playwright smoke (Playwright MCP): list page → KB → Bronnen tab →
  add URL source → land on /sources → see source in list. Plus: visit
  `/bronnen` directly, observe 301 to `/sources`.
- PR description checklist below.

## Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Third-party API consumer relies on `{"bronnen": [...]}` shape | Low — single in-repo client | High if true | PR description requires explicit confirmation no external integration exists. If unknown: ship pydantic `Field(alias="bronnen")` for one release. |
| `routeTree.gen.ts` conflict on rebase | High — file changes often | Low | Regenerate after merge. Phase 7 step. |
| ESLint rule overzealous, flags valid uses | Medium | Low | Filename-exemption for `-kb-query-keys.ts` + tests covering accept-cases. |
| Inline rename → `InlineEdit` regresses keyboard behaviour | Medium | Medium | Characterization test from Phase 0 covers Enter/Escape; verify against same test post-swap. |
| User-bookmark hits `/bronnen` after PR ships, 301 doesn't fire | Low | Medium | Verify the redirect in `route.tsx` `TAB_PATH_MAP` is the FIRST handler in `beforeLoad`. Smoke-test it in Playwright. |
| Paraglide build cache holds stale `kb_count_bron*` keys | Low | Low | Phase 5 includes `rm -rf src/paraglide && bun run build` — same pattern as commit `25803fac build(frontend): nuke paraglide + tsbuildinfo before each build`. |
| `add-source` wizard navigates to `/bronnen` from somewhere we missed | Medium | Medium | `git grep -E "'/app/knowledge/\\\$kbSlug/bronnen'\|/bronnen[\"\\\b]"` before merge; all hits must land in renamed code or in the legacy-redirect entry. |

## PR Description Checklist

- [ ] No alembic migration. No model change. No new endpoint.
- [ ] `grep -rn "BronOut\|BronnenResponse\|BronContent\|list_kb_bronnen\|get_bron_content\|kb-bronnen\|bron-content\|kbQueryKeys\.bron" klai-portal klai-knowledge-ingest` returns zero hits.
- [ ] `grep -rn "Bron[A-Z]" klai-portal/frontend/src klai-portal/backend/app klai-knowledge-ingest/knowledge_ingest` returns zero hits.
- [ ] `bronnen.tsx` does not exist; `sources.tsx` is ≤ 200 lines.
- [ ] `-sources-row.tsx` is ≤ 200 lines; `-sources-content.tsx` ≤ 120 lines.
- [ ] User-visible NL/EN strings in `sources.tsx` and renamed component files come only from Paraglide.
- [ ] `klai/no-direct-kb-querykey` ESLint rule lives in
      `klai-portal/frontend/eslint-rules/` with passing accept + reject unit tests.
- [ ] `route.tsx` `TAB_PATH_MAP` redirects `/bronnen` → `/sources` (verified in Playwright).
- [ ] `tsc --noEmit` green. `bun run lint` green. `bun run build` green.
- [ ] All existing tests still pass with renamed assertions.
- [ ] `routeTree.gen.ts` regenerated and committed.
- [ ] No `console.log` calls (lint enforces). All logging via `queryLogger`.
- [ ] Frontend MCP smoke verified: add URL source from add-source page lands on `/sources` with the new row visible without hard refresh.

## Open Questions

1. **External consumers of `{"bronnen": [...]}` shape.** Default assumption:
   the in-repo frontend is the only consumer. If review surfaces a public
   API user (mobile, third-party widget), this SPEC switches REQ-9 to a
   pydantic field-alias instead of a breaking rename, with a sunset
   window noted in the next version of this document.
2. **Tab label "Bronnen" in NL locale.** This SPEC leaves the user-facing
   Dutch word alone. If product wants to expose "Bronnen" → "Sources"
   in NL too (English loanword), open a separate copy-only PR after this
   one ships. Don't bundle with the code rename.
3. **`mapBronStatus` → `mapSourceStatus` split per kind.** The function
   currently glues upload-status and connector-status logic with an
   if/else. Splitting into `mapUploadStatus` + `mapConnectorStatus` is a
   clean-up nice-to-have. Out of scope here; tracked as a `@MX:NOTE` in
   the renamed `-sources-helpers.tsx`.

## See Also

- `.moai/specs/SPEC-PORTAL-KENNIS-001/spec.md` — Phase E source of "alles
  is een bron" + introduced the Dutch identifiers this SPEC retires.
- `.moai/specs/SPEC-KB-SOURCES-001/spec.md` — already-English wire-contract
  for `/sources/{kind}` add-source endpoints; consistent with the rename.
- `.claude/rules/klai/projects/portal-frontend.md` — `InlineEdit`,
  `InlineDeleteConfirm`, action-icon patterns this SPEC aligns with.
- `.claude/rules/klai/design/portal-patterns.md` — v1 design constraints
  (sentence-case, `rounded-full`, Paraglide-only strings).
- `.claude/rules/klai/lang/typescript.md` — `tsc --noEmit` after refactors,
  search-broadly-when-changing default rules.
