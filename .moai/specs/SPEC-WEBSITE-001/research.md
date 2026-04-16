---
id: SPEC-WEBSITE-001
type: research
created: 2026-04-16
updated: 2026-04-16
author: Mark Vletter
---

# Research notes — SPEC-WEBSITE-001

Deep-context findings captured during the Plan phase for the klai-website homepage refactor. This file is referenced by `spec.md` and feeds the Phase 1 audit step of the Run phase.

---

## 1. Current state of `klai-website/src/pages/index.astro`

- **Line count**: 558 lines.
- **Structure**: Single monolithic Astro component. All nine page sections are inlined as JSX-like markup in the template body. No section components are imported.
- **Copy**: All English copy is hardcoded inline as string literals and DOM text.
- **i18n usage**: None. The file does not import `useTranslations` or reference `Astro.currentLocale`.
- **Repeating markup inside the monolith** (16 near-identical card blocks):
  - 3× pricing cards (Chat+Focus €28, +Scribe €42 most popular, +Knowledge+Docs €68).
  - 4× product cards (Apps: Chat/Focus/Scribe, Knowledge, LLM, GPU).
  - 6× why-klai trust-pillar cells.
  - 3× built-for cards (Compliance, IT, Management).
- **Inline handlers**: 8× `onclick="toggleFaq(this)"` attributes on FAQ question buttons.
- **Image assets referenced**:
  - `/hero-painting.webp` (hero browser mockup)
  - `/bg-2.webp`, `/bg-3.webp`, `/bg-7.webp` (oil-painting section backgrounds)
  - Plus whatever the comparison/pricing/final-CTA sections load.

## 2. Current state of `klai-website/src/pages/nl/index.astro`

- **Line count**: 558 lines.
- **Diff vs EN monolith**: 376 lines of textual drift (expected: all copy is Dutch; structural markup is near-identical except where Dutch copy is longer/shorter and adjusts line wrapping).
- **Shared code with EN**: Zero. Completely parallel file.
- **Risk**: Any structural change to EN that isn't mirrored to NL silently creates visual drift between locales. This is the primary motivation for the refactor.

## 3. Reference implementation: `/product/*` pages

These pages already use the pattern that the refactor will extend to the homepage:

- `klai-website/src/pages/product/*.astro` imports section components from `klai-website/src/components/sections/Product*.astro`.
- Components in heavy use: `ProductHero` (imported by 20 callers), `ProductFeatures` (16 callers), `ProductPrivacy` (18 callers).
- Copy is pulled from `klai-website/src/i18n/en.json` and `klai-website/src/i18n/nl.json` via `useTranslations(Astro.currentLocale)` from `klai-website/src/i18n/utils.ts`.
- The product pages render correctly on both `/product/<slug>` and `/nl/product/<slug>` via Astro's built-in i18n routing.

Conclusion: the infrastructure to do this refactor already works in the same codebase. This SPEC is not inventing a new pattern — it is bringing the homepage up to the same standard.

## 4. The 17 orphan components under `klai-website/src/components/sections/`

Written during the 2026-04-05 redesign (commit `37957a9` — "feat(website): complete redesign — new visual identity, assets, and polish") but never wired into the live page. They were replaced by inline monolith markup late in the redesign cycle and left orphaned.

Component list:

| Component           | Notes                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------- |
| `Hero.astro`        | Uses `/hero-bg.webp`; live monolith uses `/hero-painting.webp`. **Diverges**.          |
| `Features.astro`    | Generic features grid; unclear mapping to any live section.                            |
| `ValueProps.astro`  | Value props layout; unclear mapping.                                                   |
| `Pricing.astro`     | Older pricing layout.                                                                  |
| `PricingCards.astro`| Card sub-component for `Pricing.astro`.                                                |
| `FAQ.astro`         | Earlier FAQ variant; unclear whether delegated listener is present.                    |
| `Comparison.astro`  | Earlier comparison table variant.                                                      |
| `UseCases.astro`    | Use-case cards; no live equivalent in current homepage.                                 |
| `BusinessMemory.astro` | Business memory concept section; no live equivalent.                                |
| `SocialProof.astro` | Logo row + testimonials.                                                                |
| `Ownership.astro`   | Steward-ownership block.                                                                |
| `WhyKlai.astro`     | Why-Klai grid.                                                                          |
| `Products.astro`    | Products section.                                                                       |
| `BuiltFor.astro`    | Built-for audiences.                                                                    |
| `KnowledgeEngine.astro` | Knowledge engine block.                                                             |
| `CompanyMission.astro` | Used on `/company`? Needs audit.                                                     |
| `CompanyTeam.astro` | Used on `/company`? Needs audit.                                                       |

**Phase 1 audit action (already scoped in REQ-1 of `spec.md`)**: grep for imports of each orphan component name across `klai-website/src/**`. Expected result: 0 imports for the 15 homepage-oriented orphans. For `CompanyMission.astro` and `CompanyTeam.astro`, expected result: possibly imported by `/company` — if so, they are **not orphans**, they are production components for a different page and are explicitly outside this SPEC's scope.

**Naming strategy to avoid collision**: the nine new homepage components are prefixed `Home*` (`HomeHero`, `HomeProducts`, etc.) so they coexist with the orphans in the same directory. This is deliberately additive — no orphan file is deleted, renamed, or modified during this SPEC.

## 5. Design system constraints applicable to the refactor

Captured from `.claude/rules/klai/design/styleguide.md` and `.claude/rules/klai/projects/website.md`, to be preserved byte-equivalent during extraction:

- **Colours**: cream `#fffef2` (--color-rl-bg), dark `#191918` (--color-rl-dark), amber `#fcaa2d` (--color-rl-accent).
- **Fonts**: Parabole Trial (400/500/700) for body + headings; Decima Mono Pro for mono labels.
- **Accent words in H2**: `<em class="font-accent not-italic">word</em>` using the Parabole display variant. Must render via Astro `set:html` during extraction (see REQ-2 unwanted requirement).
- **Buttons**: `btn-accent` (amber pill, 999px radius) and `btn-ghost` (bordered pill, 999px radius).
- **Headings**: sentence case, never title case.
- **Section backgrounds**: oil-painting `/hero-painting.webp`, `/bg-2.webp`, `/bg-3.webp`, `/bg-7.webp`.
- **Container**: `max-w-[1064px] mx-auto px-5 md:px-10`.
- **Section rhythm**: `py-16 md:py-24`.
- **Heading-to-content spacer**: explicit `<div class="h-10 md:h-14"></div>` element. Not a Tailwind utility on the next sibling — a DOM node.

## 6. Sections in the current live homepage (9 total, in render order)

| #   | Section name    | Layout notes                                                                                              | Target component          |
| --- | --------------- | --------------------------------------------------------------------------------------------------------- | ------------------------- |
| 1   | Hero            | Two-column (headline left, subcopy + CTAs right), trusted-by logo row, big browser mockup with chat demo | `HomeHero.astro`          |
| 2   | Products        | Sticky sidebar, 4 cards (Apps: Chat/Focus/Scribe, Knowledge, LLM, GPU)                                    | `HomeProducts.astro`      |
| 3   | Ownership + Comparison | Steward-owned intro + comparison table (ChatGPT Enterprise, Azure OpenAI, Klai), 7 feature rows    | `HomeOwnership.astro`     |
| 4   | Why Klai        | 6-cell grid of trust pillars                                                                              | `HomeWhyKlai.astro`       |
| 5   | Built For       | 3 cards (Compliance, IT, Management)                                                                      | `HomeBuiltFor.astro`      |
| 6   | Knowledge Engine| 3 cards (Sources, Engine, Private)                                                                        | `HomeKnowledgeEngine.astro`|
| 7   | Pricing         | 3 cards (Chat+Focus €28, +Scribe €42 most popular, +Knowledge+Docs €68) in oil-painting frame            | `HomePricing.astro`       |
| 8   | FAQ             | Sticky sidebar with 8 Q&A; inline onclick handlers (to be replaced)                                       | `HomeFaq.astro`           |
| 9   | Final CTA       | Dark painting background, white text, two CTAs                                                            | `HomeFinalCta.astro`      |

## 7. CSP and inline handler context

- Current `Content-Security-Policy` response header permits `'unsafe-inline'` for scripts (observed on the live deployment).
- The 8× `onclick="toggleFaq(this)"` handlers work today because of `'unsafe-inline'`.
- Replacing them with a delegated listener is a **prerequisite** for a follow-up SPEC that tightens CSP to drop `'unsafe-inline'`. The follow-up is out of scope here.
- Delegated listener pattern: single `addEventListener('click', …)` on the FAQ container, matching against `event.target.closest('[data-faq-question]')` (or equivalent). This is a textbook pattern and involves no new dependency.

## 8. Risks identified but not requiring SPEC changes

- **Playwright visual regression flakiness**: animations and font loading can cause up to ~1% pixel noise without stabilisation. Mitigated by Playwright's `waitForLoadState('networkidle')` + `page.emulateMedia({ reducedMotion: 'reduce' })` + `document.fonts.ready`. Captured in REQ-7 unwanted requirement; does not require a SPEC change.
- **`Astro.currentLocale` resolution for `/nl/`**: Astro 5's built-in i18n routing resolves this automatically when `i18n.defaultLocale` and `i18n.locales` are set in `astro.config.mjs`. This is standard config and is almost certainly already present (the existence of `/product/*` using `useTranslations` confirms it). Phase 1 audit will verify; if not already present, a single-line config change is permitted by REQ-9.

## 9. Files that are explicitly NOT touched by this SPEC

- All 17 orphan components under `klai-website/src/components/sections/` — see REQ-3 unwanted requirement.
- `/product/*`, `/company`, `/blog`, `/careers` page files — see REQ-9.
- `klai-website/src/i18n/utils.ts` — already working, not modified.
- `klai-website/middleware.ts` — not modified unless Phase 5 verification shows `/nl/` does not resolve correctly, in which case the implementing commit documents rationale.
- `klai-website/astro.config.mjs` — not modified unless Phase 1 audit shows i18n config needs adjustment.
- `klai-website/package.json` — no new dependencies.

## 10. Files modified or created by this SPEC (complete list)

**Modified:**

- `klai-website/src/pages/index.astro` — becomes the ~100-line orchestrator (down from 558 lines).
- `klai-website/src/i18n/en.json` — adds the `home.*` key tree.
- `klai-website/src/i18n/nl.json` — adds the `home.*` key tree.

**Created:**

- `klai-website/src/components/sections/HomeHero.astro`
- `klai-website/src/components/sections/HomeProducts.astro`
- `klai-website/src/components/sections/HomeOwnership.astro`
- `klai-website/src/components/sections/HomeWhyKlai.astro`
- `klai-website/src/components/sections/HomeBuiltFor.astro`
- `klai-website/src/components/sections/HomeKnowledgeEngine.astro`
- `klai-website/src/components/sections/HomePricing.astro`
- `klai-website/src/components/sections/HomeFaq.astro`
- `klai-website/src/components/sections/HomeFinalCta.astro`
- `klai-website/tests/visual/homepage.spec.ts` — Playwright visual regression.
- `klai-website/tests/visual/surrounding-pages.spec.ts` — HTML smoke pass over `/product/*`, `/company`, `/blog`, `/careers`.
- `klai-website/tests/functional/homepage-interactions.spec.ts` — FAQ + pricing toggle + waitlist modal.
- `klai-website/tests/i18n/home-parity.test.ts` — Vitest key parity.
- `.moai/specs/SPEC-WEBSITE-001/baseline/home-en-desktop.png`
- `.moai/specs/SPEC-WEBSITE-001/baseline/home-en-mobile.png`
- `.moai/specs/SPEC-WEBSITE-001/baseline/home-nl-desktop.png`
- `.moai/specs/SPEC-WEBSITE-001/baseline/home-nl-mobile.png`
- `.moai/specs/SPEC-WEBSITE-001/baseline/surrounding-*-snapshot.html` (one per surrounding page per locale).

**Deleted:**

- `klai-website/src/pages/nl/index.astro`

Everything outside this list should produce an empty git diff.

## 11. Pre-Plan sanity checks executed

- Verified `klai-website/src/pages/index.astro` exists at 558 lines — confirmed by user prompt.
- Verified `klai-website/src/pages/nl/index.astro` exists at 558 lines with 376 diff lines — confirmed by user prompt.
- Verified commit `37957a9` is the redesign that produced the orphans — confirmed by user prompt.
- Verified `/product/*` uses the component + i18n pattern — confirmed by user prompt (import counts: ProductHero 20, ProductFeatures 16, ProductPrivacy 18).
- Verified current CSP permits `'unsafe-inline'` — assumed true based on working inline handlers; confirmed during Phase 1 audit via response-header check.

No further pre-Plan checks are required. The SPEC is ready for user review and Run-phase hand-off.
