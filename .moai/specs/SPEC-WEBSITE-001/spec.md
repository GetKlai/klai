---
id: SPEC-WEBSITE-001
version: 0.1.0
status: draft
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
priority: high
issue_number: 0
---

# SPEC-WEBSITE-001 — Refactor klai-website homepage from monolith to component architecture

## HISTORY

- **v0.1.0 (2026-04-16)**: Initial draft. Refactors `klai-website/src/pages/index.astro` (558 lines, EN) and `klai-website/src/pages/nl/index.astro` (558 lines, NL, 376 diff-lines vs EN) from two parallel monoliths into a single component-based orchestrator (~100 lines) that drives both locales via Astro's built-in i18n routing and the existing `useTranslations(Astro.currentLocale)` pattern. Extracts 9 page sections into typed Astro components under `src/components/sections/`, pulls all copy into `src/i18n/en.json` and `src/i18n/nl.json`, replaces 8 inline `onclick="toggleFaq(this)"` handlers with a delegated listener (CSP hygiene), and guarantees pixel-identical rendering via Playwright visual regression per viewport per locale. The 17 orphan components in `src/components/sections/` (Hero, Features, ValueProps, Pricing, PricingCards, FAQ, Comparison, UseCases, BusinessMemory, SocialProof, Ownership, WhyKlai, Products, BuiltFor, KnowledgeEngine, CompanyMission, CompanyTeam) written during the 2026-04-05 redesign (commit `37957a9`) but never wired into the live page are left untouched — their audit and removal are deferred to a follow-up SPEC. Methodology: Domain-Driven Development (ANALYZE-PRESERVE-IMPROVE) rather than the project default TDD, because this is a pure refactor with no behavioural change. Behaviour preservation is enforced by Playwright visual regression (<0.5% pixel delta per viewport) plus functional tests for the three interactive elements (FAQ expand/collapse, pricing toggle, waitlist modal). Methodology rationale is documented in the "Methodology Choice" section below.

---

## Goal

Eliminate the maintenance burden of the parallel EN/NL homepage monoliths by refactoring `klai-website/src/pages/index.astro` (and deleting its sibling `klai-website/src/pages/nl/index.astro`) into a single ~100-line orchestrator that composes nine typed section components and pulls all copy from `src/i18n/{en,nl}.json` via `useTranslations(Astro.currentLocale)`.

After this SPEC lands:

1. A single `klai-website/src/pages/index.astro` file (~100 lines) drives both `/` (English) and `/nl/` (Dutch) rendering through Astro's built-in i18n routing and the existing `useTranslations` helper (already used by `/product/*`).
2. `klai-website/src/pages/nl/index.astro` no longer exists.
3. Each of the nine homepage sections lives in its own Astro component under `klai-website/src/components/sections/` with an explicit `Props` interface and no hardcoded copy.
4. All homepage copy is in `klai-website/src/i18n/en.json` and `klai-website/src/i18n/nl.json`, retrievable via `useTranslations`.
5. Rendering is pixel-identical to the current live site (<0.5% pixel delta) on desktop (1440×900) and mobile (375×812) viewports, per locale.
6. Inline `onclick="toggleFaq(this)"` handlers are gone; the FAQ uses a delegated event listener. CSP can safely drop `'unsafe-inline'` for scripts in a follow-up SPEC.

Scope clarification: this is a **refactor**, not a redesign. Visual output, copy, animations, imagery, and interactive behaviour remain exactly as they are today. The only user-visible change should be zero.

## Success Criteria

- `klai-website/src/pages/index.astro` is ≤120 lines and contains no inline section markup — only component imports, `useTranslations` wiring, and component calls with props.
- `klai-website/src/pages/nl/index.astro` no longer exists; `/nl/` renders through the same orchestrator via `Astro.currentLocale`.
- Nine components exist under `klai-website/src/components/sections/` wired into the orchestrator in this order: `HomeHero`, `HomeProducts`, `HomeOwnership`, `HomeWhyKlai`, `HomeBuiltFor`, `HomeKnowledgeEngine`, `HomePricing`, `HomeFaq`, `HomeFinalCta`. Each component has an explicit `Props` interface with string types (no `any`, no implicit types).
- All homepage-specific copy exists as keys in `klai-website/src/i18n/en.json` and `klai-website/src/i18n/nl.json`. No component contains hardcoded user-facing copy. Translation parity is verified by a Vitest test comparing key sets.
- Playwright visual regression: `/` and `/nl/` screenshots on desktop 1440×900 and mobile 375×812 (four total) show <0.5% pixel delta vs baselines captured in Phase 1.
- Zero inline `onclick` attributes in the rendered homepage HTML. Grep `inlineHandlers = /<[^>]+on\w+\s*=\s*"[^"]+"/gi` against the built HTML returns zero matches for the homepage routes.
- FAQ expand/collapse, pricing toggle (if present), and waitlist modal continue to work on both `/` and `/nl/` — verified by Playwright functional tests.
- `/product/*`, `/company`, `/blog`, `/careers` continue to render unchanged — verified by their existing build-time checks and a Playwright smoke pass.
- TypeScript compiles with no new errors; `astro check` passes; `tsc --noEmit` passes.
- The 17 orphan components under `klai-website/src/components/sections/` (Hero, Features, ValueProps, Pricing, PricingCards, FAQ, Comparison, UseCases, BusinessMemory, SocialProof, Ownership, WhyKlai, Products, BuiltFor, KnowledgeEngine, CompanyMission, CompanyTeam) remain untouched — git diff on those files is empty.
- No new runtime dependencies added to `klai-website/package.json`. Bundle size (`dist/client/_astro/*`) does not grow; Astro ships zero JS for server-rendered components by default, so the refactor should be bundle-neutral or slightly smaller.

## Environment

This SPEC modifies the `klai-website/` subdirectory of the Klai monorepo:

- **Framework**: Astro 5 (see `klai-website/package.json` for exact version) with Tailwind v4 and Keystatic CMS.
- **i18n**: `klai-website/src/i18n/utils.ts` exposes `useTranslations(locale)` and is already the pattern used by `/product/*` pages. Astro's built-in i18n routing handles `/nl/` via `astro.config.mjs` locale configuration.
- **Design system**: Strict styleguide enforced in `.claude/rules/klai/design/styleguide.md` and `.claude/rules/klai/projects/website.md` — cream `#fffef2` bg, dark `#191918` text, amber `#fcaa2d` accent; Parabole Trial + Decima Mono Pro fonts; sentence-case headings; accent words via `<em class="font-accent not-italic">`; `btn-accent` and `btn-ghost` pills; oil-painting backgrounds; container `max-w-[1064px] mx-auto px-5 md:px-10`; section rhythm `py-16 md:py-24`; explicit `<div class="h-10 md:h-14"></div>` spacers between heading and content.
- **Current state** (as of commit `db817c29` on `main`):
  - `klai-website/src/pages/index.astro` = 558 lines, monolith, all sections inlined.
  - `klai-website/src/pages/nl/index.astro` = 558 lines, parallel monolith with 376 lines of drift vs EN.
  - `klai-website/src/components/sections/` already contains 17 orphan components from the 2026-04-05 redesign (commit `37957a9`) that were never used — they diverge from live markup on details like `hero-bg.webp` vs `hero-painting.webp`. They are out of scope for this SPEC.
  - 16 near-identical card-markup blocks inside the monolith (3× pricing, 4× product, 6× why-klai, 3× built-for).
  - 8 inline `onclick="toggleFaq(this)"` handlers in the FAQ section; CSP currently permits `'unsafe-inline'` for scripts.
- **Reference implementation**: `/product/*` pages (ProductHero: imported by 20 call sites, ProductFeatures: 16, ProductPrivacy: 18) already use the component + i18n pattern correctly — their structure is the template for this refactor.
- **Deployment**: Coolify push-triggered; no CI workflow changes needed.
- **Test runner**: Vitest for unit/integration; Playwright for visual regression and functional E2E (Playwright binary already installed in the repo).

## Assumptions

- Astro's `Astro.currentLocale` returns `"en"` for `/` and `"nl"` for `/nl/` given the existing `astro.config.mjs` i18n configuration. If this assumption is wrong, the orchestrator needs a manual `getLocale()` shim and `astro.config.mjs` is updated — the rationale is documented in the implementing commit.
- The 17 orphan components under `klai-website/src/components/sections/` have no consumers outside the homepage refactor target. Grep for each orphan component name across `klai-website/src/**` returns zero imports. If any orphan is imported elsewhere, it is explicitly excluded from the refactor (kept as-is) and the finding is added to `research.md`.
- `/product/*`, `/company`, `/blog`, `/careers` pages do not import from the homepage file and are therefore isolated from this refactor. Grep `from.*pages/index` and `from.*pages/nl/index` across `klai-website/src/**` returns zero matches.
- Playwright screenshots taken in Phase 1 against the live deploy (or a fresh build of the current `main`) are the ground truth. Minor anti-aliasing and font-rendering noise is accepted up to the 0.5% threshold.
- The FAQ's inline `onclick="toggleFaq(this)"` pattern can be replaced by a single delegated listener attached to the FAQ container, without touching the visual structure. No behavioural difference is expected.
- Current CSP already permits `'unsafe-inline'` for scripts (confirmed by visiting the live site and checking response headers); this SPEC does **not** tighten CSP. Tightening CSP to drop `'unsafe-inline'` is a follow-up SPEC after this one lands.
- Tailwind utility class strings inside component markup remain valid without any config change (components live under the same `klai-website/` root and inherit the existing Tailwind config).

## Out of Scope

- The `/new` experimental page — separate work, untouched by this SPEC.
- Any copy change, messaging rewrite, or content update — this is a refactor; English and Dutch copy is moved verbatim from the monolith into `en.json` / `nl.json`.
- Visual redesign, new animations, new imagery, new sections — the rendered output must be pixel-identical.
- Performance optimisation beyond what the refactor naturally produces — no new preloading, no new image-optimisation pipeline, no lazy-loading strategy changes.
- Cleanup of the 17 orphan components under `klai-website/src/components/sections/` — their audit and removal is a follow-up SPEC (tentative: SPEC-WEBSITE-002).
- Tightening the CSP to drop `'unsafe-inline'` for scripts — follow-up SPEC after this one removes the last inline handler.
- Refactor of `/product/*`, `/company`, `/blog`, `/careers` — they already follow the component pattern and are not targets here.
- Any change to `astro.config.mjs` or `middleware.ts` unless strictly necessary. If a change is required, the rationale is documented in the implementing commit and referenced in `research.md`.
- New npm dependencies. The refactor uses only Astro 5, Tailwind v4, Keystatic, and the existing i18n utilities.

---

## Requirements

All requirements use EARS notation. Each requirement has a stable ID (`REQ-N`) and a category: **NEW** (introduces behaviour/structure), **MODIFY** (changes existing), **REMOVE** (deletes existing behaviour/file).

### REQ-1: Capture visual regression baseline [NEW]

**Event-Driven**: **When** Phase 1 executes, the system **shall** capture Playwright screenshots of `/` and `/nl/` on desktop (1440×900) and mobile (375×812), producing four baseline PNGs stored under `.moai/specs/SPEC-WEBSITE-001/baseline/`.

**Ubiquitous**: Each baseline screenshot **shall** be captured against the current live rendering (i.e., a build of `main` at commit `db817c29` or later before any refactor work begins).

**Ubiquitous**: The Playwright test file that produces the baselines **shall** live at `klai-website/tests/visual/homepage.spec.ts` (new file) and **shall** be reusable in Phase 7 to compare post-refactor output against the same baselines.

**Unwanted**: **If** Phase 1 completes without all four baseline PNGs present in `.moai/specs/SPEC-WEBSITE-001/baseline/`, **then** the Phase 1 gate **shall** fail and subsequent phases **shall not** start.

### REQ-2: Extract all homepage copy to i18n JSON files [NEW]

**Ubiquitous**: All user-facing copy from the current monolith homepage (both EN and NL variants) **shall** be extracted into keys under `klai-website/src/i18n/en.json` and `klai-website/src/i18n/nl.json`, grouped under a top-level `home` namespace with sub-namespaces per section (`home.hero`, `home.products`, `home.ownership`, `home.whyKlai`, `home.builtFor`, `home.knowledgeEngine`, `home.pricing`, `home.faq`, `home.finalCta`).

**Ubiquitous**: Every key present in `home.*` in `en.json` **shall** have a corresponding key in `nl.json`, and vice versa. Key parity is verified by a Vitest test.

**Event-Driven**: **When** the test suite runs, the system **shall** execute a translation-parity test that fails if any `home.*` key exists in one locale file but not the other.

**Ubiquitous**: Copy **shall not** be paraphrased, shortened, or cleaned up during extraction — it is moved verbatim from the current monolith markup. A copy audit comparing extracted strings against the pre-refactor monolith markup is documented in `research.md`.

**Unwanted**: **If** an extracted string contains embedded HTML (e.g., the `<em class="font-accent not-italic">` accent-word pattern), **then** the component **shall** render it via Astro's `set:html` directive and the JSON value **shall** preserve the exact HTML substring.

### REQ-3: Build nine section components with typed props [NEW]

**Ubiquitous**: The system **shall** define exactly nine Astro components under `klai-website/src/components/sections/`, named: `HomeHero.astro`, `HomeProducts.astro`, `HomeOwnership.astro`, `HomeWhyKlai.astro`, `HomeBuiltFor.astro`, `HomeKnowledgeEngine.astro`, `HomePricing.astro`, `HomeFaq.astro`, `HomeFinalCta.astro`.

**Ubiquitous**: Each component **shall** export a `Props` interface declaring every field with an explicit string (or string-array, or structured) type. No `any`, no implicit types, no optional fields without default.

**Ubiquitous**: Each component **shall** receive all copy via its `Props` interface. No component **shall** import from `klai-website/src/i18n/*.json` directly — copy is passed in by the orchestrator.

**Ubiquitous**: The rendered markup of each component **shall** be byte-equivalent (allowing whitespace normalisation) to the corresponding section's current markup in the monolith — including Tailwind utility class order, `<em class="font-accent not-italic">` accent-word blocks, oil-painting background markup, section rhythm `py-16 md:py-24`, and the explicit `<div class="h-10 md:h-14"></div>` spacer between heading and content.

**Ubiquitous**: `HomeHero.astro` **shall** render the current `/hero-painting.webp` browser-mockup-with-chat-demo layout — not the orphan `Hero.astro` which uses `/hero-bg.webp`.

**State-Driven**: **While** a section's Props-derived content contains repeating cards (3× pricing, 4× product, 6× why-klai, 3× built-for), the component **shall** iterate over a typed array instead of hand-writing each card block.

**Unwanted**: **If** any of the 17 orphan components (Hero, Features, ValueProps, Pricing, PricingCards, FAQ, Comparison, UseCases, BusinessMemory, SocialProof, Ownership, WhyKlai, Products, BuiltFor, KnowledgeEngine, CompanyMission, CompanyTeam) is modified during this SPEC, **then** the change **shall** be rejected in code review.

### REQ-4: Refactor `index.astro` into component orchestrator [MODIFY]

**Ubiquitous**: `klai-website/src/pages/index.astro` **shall** be ≤120 lines after this SPEC, consisting only of: frontmatter script (imports, `useTranslations(Astro.currentLocale)` call, variable binding from the translation object), layout wrapper, and the nine section component calls with props.

**Ubiquitous**: `index.astro` **shall not** contain inline section markup, hardcoded user-facing copy, or inline Tailwind-styled DOM blocks for any of the nine sections.

**Event-Driven**: **When** a request arrives at `/`, the orchestrator **shall** resolve `Astro.currentLocale` to `"en"` and render with `en.json` translations.

**Event-Driven**: **When** a request arrives at `/nl/`, the orchestrator **shall** resolve `Astro.currentLocale` to `"nl"` and render with `nl.json` translations using the exact same orchestrator file.

**Ubiquitous**: The nine components **shall** be called in exactly this order: `HomeHero`, `HomeProducts`, `HomeOwnership`, `HomeWhyKlai`, `HomeBuiltFor`, `HomeKnowledgeEngine`, `HomePricing`, `HomeFaq`, `HomeFinalCta`.

### REQ-5: Delete `nl/index.astro` [REMOVE]

**Ubiquitous**: The file `klai-website/src/pages/nl/index.astro` **shall not** exist after this SPEC.

**Event-Driven**: **When** a request arrives at `/nl/`, Astro's built-in i18n routing **shall** resolve the request to the single orchestrator at `klai-website/src/pages/index.astro` and render the Dutch translation.

**Unwanted**: **If** the build produces any artefact under `dist/nl/index.*` that was generated from a `nl/index.astro` source file rather than from the single orchestrator, **then** the build **shall** fail.

### REQ-6: Replace inline FAQ onclick handlers with delegated listener [MODIFY]

**Ubiquitous**: The rendered HTML of `HomeFaq.astro` **shall not** contain any inline `on*=` attribute. The eight `onclick="toggleFaq(this)"` handlers currently inlined on FAQ question buttons **shall** be replaced by a single delegated event listener attached to the FAQ container.

**Ubiquitous**: The delegated listener **shall** live in a `<script>` block inside `HomeFaq.astro` that Astro will bundle and ship as a static asset (not inlined into each rendered button).

**Event-Driven**: **When** a user clicks a FAQ question button on `/` or `/nl/`, the answer panel **shall** expand or collapse with the same visual transition as today.

**Unwanted**: **If** a grep of the built HTML for the homepage routes matches the regex `<[^>]+on\w+\s*=\s*"[^"]+"`, **then** the build **shall** fail.

### REQ-7: Visual regression gate [NEW]

**Event-Driven**: **When** Phase 7 runs, the system **shall** execute Playwright visual regression comparing post-refactor screenshots of `/` and `/nl/` (desktop 1440×900 and mobile 375×812) against the Phase 1 baselines.

**Ubiquitous**: Pixel delta per viewport per locale **shall** be <0.5%. The gate **shall** fail if any of the four comparisons exceeds 0.5%.

**State-Driven**: **While** a diff exceeds 0.5%, the implementation **shall not** proceed to the merge checkpoint — the offending component is fixed, Phase 7 re-runs, and only a passing gate unblocks Phase 8.

**Unwanted**: **If** the diff is caused by font rendering noise, anti-aliasing drift, or animated elements in mid-transition at screenshot time, **then** Playwright's stabilisation options (wait for fonts, disable animations, wait for network idle) **shall** be applied rather than raising the threshold.

### REQ-8: Functional regression gate [NEW]

**Event-Driven**: **When** Phase 7 runs, the system **shall** execute Playwright functional tests for:

1. FAQ expand/collapse on `/` and `/nl/` — clicking a question toggles its answer panel; clicking again collapses it.
2. Pricing toggle (if present in the current homepage) — toggling yearly/monthly (or equivalent) updates the displayed prices.
3. Waitlist modal — clicking the primary CTA opens the waitlist modal on both `/` and `/nl/`; closing it returns focus to the CTA.

**Ubiquitous**: Each functional test **shall** run on both `/` and `/nl/`.

**Unwanted**: **If** any functional test fails, **then** the gate **shall** fail and Phase 8 **shall not** proceed.

### REQ-9: Preserve surrounding pages [MODIFY]

**Ubiquitous**: No file outside `klai-website/src/pages/index.astro`, `klai-website/src/pages/nl/index.astro`, `klai-website/src/components/sections/Home*.astro`, `klai-website/src/i18n/en.json`, `klai-website/src/i18n/nl.json`, and `klai-website/tests/visual/` **shall** be modified by this SPEC — with one explicit exception: `astro.config.mjs` may be edited if and only if the existing i18n config does not already resolve `/nl/` to the shared orchestrator; the rationale **shall** be documented in the implementing commit and in `research.md`.

**Event-Driven**: **When** Phase 7 runs, a Playwright smoke pass **shall** navigate to `/product/scribe`, `/product/chat`, `/product/knowledge`, `/company`, `/blog`, and `/careers` on both `/en` (default) and `/nl/` (where translated) and assert the rendered HTML snapshot matches the pre-refactor snapshot taken in Phase 1.

**Unwanted**: **If** the smoke pass detects any unintended change in the listed routes, **then** the gate **shall** fail.

### REQ-10: Type safety enforcement [NEW]

**Ubiquitous**: All nine section components **shall** pass `astro check` with zero errors and zero warnings after this SPEC.

**Ubiquitous**: `tsc --noEmit` over `klai-website/src/**` **shall** pass with zero errors after this SPEC.

**Ubiquitous**: Each component's `Props` interface **shall** be self-contained — no imports of loose `any`-typed translation objects. If a component needs a structured prop (e.g., an array of cards), the array item type **shall** be declared as a named interface within the component file or a sibling `types.ts`.

**Unwanted**: **If** a component uses `any`, `Record<string, any>`, `as unknown as`, or `@ts-ignore` after this SPEC, **then** the change **shall** be rejected in code review unless the exception is explicitly justified in a comment referencing this SPEC.

### REQ-11: Bundle size neutrality [NEW]

**Ubiquitous**: The total size of `dist/client/_astro/*` produced by `npm run build` in `klai-website/` **shall not** exceed the pre-refactor bundle size by more than 2%.

**Event-Driven**: **When** Phase 7 runs, the system **shall** build both the pre-refactor commit and the post-refactor commit, compute bundle sizes, and fail the gate if the post-refactor bundle exceeds the pre-refactor bundle by more than 2%.

**Ubiquitous**: No new runtime npm dependencies **shall** be added to `klai-website/package.json`.

### REQ-12: Phased checkpoints and working-state guarantee [NEW]

**Ubiquitous**: Each of the eight phases (Phase 1 through Phase 8) **shall** leave the website in a buildable, deployable state. At no intermediate phase is the site allowed to be broken.

**Event-Driven**: **When** a phase completes, the implementation **shall** run `npm run build` in `klai-website/` and verify it exits with code 0 before opening the next phase.

**State-Driven**: **While** components are being built one at a time in Phase 3, the orchestrator in Phase 4 is not yet refactored — the monolith is still the source of truth until Phase 4 flips to the component-based orchestrator in a single commit.

**Unwanted**: **If** `npm run build` fails at any phase boundary, **then** the next phase **shall not** start until the build is green.

### REQ-13: MX annotations on orchestrator and components [NEW]

**Ubiquitous**: The new orchestrator at `klai-website/src/pages/index.astro` **shall** carry a `@MX:ANCHOR` annotation documenting its role as the single entry point driving both locales, because it will have fan_in ≥ 3 (imported by Astro's router for `/`, `/nl/`, and any future locale).

**Ubiquitous**: Each of the nine section components **shall** carry a `@MX:NOTE` annotation documenting its role as a homepage section and its associated i18n namespace.

**Ubiquitous**: The delegated FAQ listener **shall** carry a `@MX:NOTE` annotation documenting why inline handlers were replaced (CSP hygiene, follow-up SPEC to drop `'unsafe-inline'`).

---

## Methodology Choice

The project default is **TDD** (`development_mode: tdd` in `.moai/config/sections/quality.yaml`). For this SPEC, **DDD (Domain-Driven Development with ANALYZE-PRESERVE-IMPROVE)** is the appropriate choice. Rationale:

1. **No behaviour is added.** The rendered HTML, visual output, interactive affordances, and copy are all unchanged. TDD's RED phase ("write a failing test for new behaviour") does not apply to pure refactoring — the behaviour under test already exists and already passes.
2. **Characterisation tests preserve existing behaviour.** The Playwright visual regression baselines (Phase 1) are textbook characterisation tests: they capture current rendering, the refactor preserves it, and any deviation fails the gate. This maps cleanly onto DDD's PRESERVE phase.
3. **ANALYZE-PRESERVE-IMPROVE fits the workflow.** Phase 1 (baseline) + audit of orphans is ANALYZE. Phases 2–6 (extract copy, build components, refactor orchestrator, delete NL file, delegated listener) run iteratively under the PRESERVE harness of visual regression. Phase 7 (regression gate) + Phase 8 (merge) is IMPROVE completion with coverage of characterisation tests.
4. **Coverage exemption.** DDD explicitly allows coverage flexibility for brownfield refactors. This SPEC targets ≥0% new-unit-test coverage (no new logic to test) but **100% visual-regression coverage** of the nine homepage sections — which is the real quality bar for a pure UI refactor.

"Behaviour preservation" in this SPEC specifically means:

- **Visual**: Pixel-identical rendering within 0.5% threshold on desktop and mobile, per locale (REQ-7).
- **Functional**: FAQ expand/collapse, pricing toggle, waitlist modal continue to work on both locales (REQ-8).
- **Structural**: `/product/*`, `/company`, `/blog`, `/careers` are unchanged (REQ-9).
- **Performance**: Bundle size neutral within 2% (REQ-11).

## Phased Implementation

Each phase is a discrete, verifiable unit. Phases run in order; the site must build green at every phase boundary (REQ-12).

### Phase 1 — Baseline and audit (ANALYZE)

- Capture Playwright screenshots of `/` and `/nl/` on 1440×900 and 375×812 into `.moai/specs/SPEC-WEBSITE-001/baseline/` (REQ-1).
- Capture HTML snapshots of `/product/*`, `/company`, `/blog`, `/careers` into the same directory for the REQ-9 smoke pass.
- Audit the 17 orphan components: grep for imports of each across `klai-website/src/**`; record findings in `research.md`.
- Audit whether `astro.config.mjs` already routes `/nl/` to the shared orchestrator; record in `research.md`.
- **Gate**: Four baseline PNGs present; audit findings recorded.

### Phase 2 — Extract copy to i18n JSON (PRESERVE)

- Move all homepage copy from the EN monolith into `klai-website/src/i18n/en.json` under the `home.*` namespace tree (REQ-2).
- Move all homepage copy from the NL monolith into `klai-website/src/i18n/nl.json` under the same key tree.
- Add Vitest test `klai-website/tests/i18n/home-parity.test.ts` verifying `home.*` key parity between EN and NL.
- Monolith continues to render; orchestrator not yet refactored.
- **Gate**: Parity test green; `npm run build` green; visual regression still passes (copy has not moved from the monolith yet, this phase only populates JSON).

### Phase 3 — Build nine components (PRESERVE, one component at a time)

Build each component and verify in isolation via a temporary `/preview/<component>` route (discarded at phase end):

1. `HomeHero.astro` — two-column layout, `/hero-painting.webp` browser mockup, trusted-by logo row.
2. `HomeProducts.astro` — sticky sidebar, 4 cards (Apps, Knowledge, LLM, GPU).
3. `HomeOwnership.astro` — steward-owned intro + comparison table (ChatGPT Enterprise, Azure OpenAI, Klai) with 7 feature rows.
4. `HomeWhyKlai.astro` — 6-cell trust-pillar grid.
5. `HomeBuiltFor.astro` — 3 cards (Compliance, IT, Management).
6. `HomeKnowledgeEngine.astro` — 3 cards (Sources, Engine, Private).
7. `HomePricing.astro` — 3 cards in oil-painting frame.
8. `HomeFaq.astro` — sticky sidebar, 8 Q&A, **delegated listener** (REQ-6).
9. `HomeFinalCta.astro` — dark painting bg, white text, two CTAs.

Each component has an explicit `Props` interface (REQ-3, REQ-10) and receives copy via props, never imports i18n directly.

- **Gate**: `npm run build` green after each component; `astro check` green; temporary preview routes removed before phase close.

### Phase 4 — Refactor `index.astro` to orchestrator (IMPROVE)

- Replace monolith content in `klai-website/src/pages/index.astro` with the ~100-line orchestrator that imports the nine components and wires `useTranslations(Astro.currentLocale)` (REQ-4).
- The NL monolith at `klai-website/src/pages/nl/index.astro` is **still present** in this phase to keep the site working during transition.
- **Gate**: `/` renders the new component tree; visual regression passes; `npm run build` green.

### Phase 5 — Delete `nl/index.astro` (REMOVE)

- Verify that `/nl/` already routes to the shared orchestrator via `Astro.currentLocale` resolving to `"nl"` (test locally).
- Delete `klai-website/src/pages/nl/index.astro` (REQ-5).
- If `astro.config.mjs` needs a tweak, apply it with commit rationale.
- **Gate**: `/nl/` renders via the shared orchestrator; visual regression passes for `/nl/`; `npm run build` green.

### Phase 6 — Delegated FAQ listener (IMPROVE)

This phase formally lands the listener. If Phase 3 already built the delegated listener into `HomeFaq.astro`, this phase is a verification gate rather than a code-writing phase.

- Grep the built HTML for inline `on*=` attributes on homepage routes; assert zero matches (REQ-6).
- Verify FAQ expand/collapse works in Playwright on both locales.
- **Gate**: Zero inline handlers in built HTML; FAQ functional test green.

### Phase 7 — Full regression pass (IMPROVE)

- Run visual regression on `/` and `/nl/`, desktop + mobile (REQ-7).
- Run functional regression for FAQ, pricing toggle (if present), waitlist modal (REQ-8).
- Run `/product/*`, `/company`, `/blog`, `/careers` HTML smoke pass against Phase 1 snapshots (REQ-9).
- Run bundle size comparison (REQ-11).
- Fix any diffs by tweaking component markup — no relaxation of the <0.5% threshold or the 2% bundle budget.
- **Gate**: All four gates green.

### Phase 8 — Merge checkpoint

- Code review of all new components and the refactored orchestrator against the design system rules in `.claude/rules/klai/design/styleguide.md` and `.claude/rules/klai/projects/website.md`.
- Verify MX annotations in place (REQ-13).
- Verify git diff touches only the files in scope per REQ-9.
- Final `astro check` and `tsc --noEmit` green.
- Merge.

---

## Verification Summary

Each requirement maps to one or more acceptance scenarios in `acceptance.md` (to be produced alongside this SPEC during implementation kickoff). Key verification pathways:

| REQ    | Verification mechanism                                                             |
| ------ | ---------------------------------------------------------------------------------- |
| REQ-1  | Phase 1 artefact check — four baseline PNGs present                                |
| REQ-2  | Vitest parity test `home-parity.test.ts`                                           |
| REQ-3  | `astro check` + manual component review + orphan-untouched git-diff assertion     |
| REQ-4  | Line-count assertion on `index.astro`; manual review                               |
| REQ-5  | File-absence assertion; `/nl/` routing test                                        |
| REQ-6  | HTML grep for inline `on*=`; Playwright FAQ functional test                        |
| REQ-7  | Playwright visual regression gate                                                  |
| REQ-8  | Playwright functional regression gate                                              |
| REQ-9  | HTML snapshot smoke pass over surrounding pages                                    |
| REQ-10 | `astro check` + `tsc --noEmit` + lint grep for `any`/`@ts-ignore`                  |
| REQ-11 | Bundle-size diff check                                                             |
| REQ-12 | `npm run build` exit-code check at each phase boundary                             |
| REQ-13 | MX tag grep over new files                                                         |

## Risks

| Risk                                                                 | Severity | Likelihood | Mitigation                                                                                                                                                |
| -------------------------------------------------------------------- | -------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Visual regression flakiness (font-rendering noise, animations)       | High     | Medium     | REQ-7 unwanted requirement mandates Playwright stabilisation (wait-for-fonts, disable animations, network idle) rather than raising the 0.5% threshold |
| Inline `onclick` handler replacement breaks FAQ behaviour             | Medium   | Low        | REQ-8 Playwright functional test runs on both locales; delegated listener is a textbook pattern; no structural change                                     |
| `/product/*` page unexpected regression                              | High     | Low        | REQ-9 HTML snapshot smoke pass; paths outside scope are frozen by diff review                                                                              |
| Translation coverage gap (missing key in NL or EN)                   | Medium   | Medium     | REQ-2 Vitest parity test fails CI on any key-set drift                                                                                                    |
| `Astro.currentLocale` does not resolve `/nl/` without config change  | Medium   | Low        | Phase 1 audit of `astro.config.mjs` resolves this before implementation; if a config change is needed, rationale is documented                            |
| Orphan component cleanup accidentally happens in this SPEC           | Low      | Medium     | REQ-3 unwanted requirement; diff review asserts the 17 orphan files are untouched                                                                          |
| Embedded HTML in copy (accent-word `<em>` blocks) escaped incorrectly | Medium   | Medium     | REQ-2 requires `set:html` rendering; a small unit test per component verifies the accent-word pattern renders correctly                                  |
| Bundle size regression from repeated component overhead              | Low      | Low        | Astro ships zero JS for server-rendered components; REQ-11 enforces the 2% ceiling with a CI-blocking check                                              |
| Phase 4 big-bang commit introduces a broken intermediate state       | Medium   | Low        | REQ-12 requires `npm run build` green at every phase boundary; Phase 3 builds components before Phase 4 flips the orchestrator                            |

## Traceability

- Related historical work: commit `37957a9` (2026-04-05) — full redesign that produced the 17 orphan components under `klai-website/src/components/sections/`.
- Reference implementation for component + i18n pattern: `klai-website/src/pages/product/*.astro` (ProductHero, ProductFeatures, ProductPrivacy).
- Design system rules: `.claude/rules/klai/design/styleguide.md`, `.claude/rules/klai/projects/website.md`.
- Follow-up SPEC (tentative): SPEC-WEBSITE-002 — audit and remove the 17 orphan components now that live markup has been componentised.
- Follow-up SPEC (tentative): SPEC-WEBSITE-003 — tighten CSP to drop `'unsafe-inline'` for scripts now that the last inline handler is gone.

## Decisions resolved (v0.1.0)

1. **Methodology**: DDD (ANALYZE-PRESERVE-IMPROVE), not the project default TDD. Rationale in the "Methodology Choice" section above.
2. **Orphan component cleanup**: Out of scope. Follow-up SPEC after this one lands.
3. **CSP hardening**: Out of scope. Follow-up SPEC. This SPEC does not tighten CSP — it only removes the last inline handler, enabling the follow-up.
4. **`nl/index.astro` removal strategy**: Hard delete after Phase 5 verification that `/nl/` resolves through the shared orchestrator via `Astro.currentLocale`. No transitional symlink, no redirect.
5. **Component naming prefix**: `Home*` (e.g., `HomeHero`) to disambiguate from the 17 orphan components that use the unprefixed names (`Hero`, `Products`, etc.). This prevents import collisions and makes the refactor additive rather than destructive.
6. **i18n key namespace**: `home.*` with sub-namespaces per section. Keeps keys short, scoped, and easy to diff.
7. **Bundle size budget**: 2% ceiling. Astro server-rendered components should be neutral; 2% allows for JSON-file overhead from extracted copy.

No open decisions remain. The Plan phase output is unblocked; the Run phase executor (manager-ddd subagent) has everything needed to start Phase 1.
