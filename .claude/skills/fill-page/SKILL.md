---
name: fill-page
description: Fill an Astro page with copy extracted from a reference HTML file. Replaces text content in Astro section components and i18n files while preserving all styling, layout, and component structure. Usage - /fill-page REF=<path> PAGE=<astro-page>
user-invocable: true
argument-hint: REF=clone-output/index_example.html PAGE=website/src/pages/index.astro
---

# Fill Page -- Reference to Astro

You are a senior copywriter. Take a reference HTML file (e.g. a cloned website) and use its copy to fill an Astro page's section components. The visual design and component structure stay exactly as-is.

## What you CHANGE (copy only)
- Text content in Astro section component props: headings, descriptions, button labels, list items, FAQ Q&A
- Inline text inside Astro components: paragraphs, spans, headings
- Data arrays passed as props (e.g. `whyKlaiItems`, `products`, `faqItems`)
- `<title>` and meta description in the Base layout frontmatter
- `href` values on links and buttons
- `alt` attributes on images
- i18n strings in `website/src/i18n/en.json` and `nl.json` if the page uses translated keys

## What you NEVER touch
- CSS, Tailwind classes, `@apply` directives, `<style>` blocks
- Colors, fonts, font sizes, spacing, layout, grid structure
- Component file structure (do not rename, delete, or create `.astro` files)
- `class` or `class:list` attributes
- Images, SVGs, videos, background images, icon imports
- JavaScript/TypeScript logic, imports, conditional rendering
- Astro config, Keystatic config, build config
- HTML structure, element nesting, slot usage

**The `<style>` blocks and `<script>` blocks in every `.astro` file must remain untouched.**

---

## Arguments

**REF=`<path-to-reference-html>`** -- The source HTML file to extract copy from.
**PAGE=`<path-to-astro-page>`** -- The target Astro page to fill (e.g. `website/src/pages/index.astro`).

**If REF is omitted:** List HTML files in `clone-output/` and `website/` and ask the user to pick one.
**If PAGE is omitted:** List Astro page files in `website/src/pages/` and ask the user to pick one.

---

## Phase 1 -- Analyze the Reference HTML

Read the REF file completely. For every section, extract:
- All text content: headings, subheadings, descriptions, button labels, card titles, card descriptions, FAQ Q&A, footer text, nav labels
- The number of items per section (e.g. 3 feature cards, 5 FAQ items, 4 pricing tiers)
- Text emphasis patterns (bold words, colored spans, highlighted phrases)
- Link targets and CTA destinations

Build a section-by-section copy map.

---

## Phase 2 -- Analyze the Target Astro Page

Read the PAGE file and all section components it imports. For each section component:

1. Read the component file (e.g. `website/src/components/sections/Hero.astro`)
2. Identify how copy is provided:
   - **Props with data arrays** (defined in the page frontmatter and passed as props)
   - **Inline text** (hardcoded in the component template)
   - **i18n keys** (imported from `../i18n/utils.ts` or similar)
   - **Slot content** (passed between component tags)
3. Note how many slots/items each section expects

Map each section's copy delivery mechanism so you know exactly where to write.

---

## Phase 3 -- Map Reference Copy to Astro Sections

Match reference sections to Astro components:

| Reference section | Likely Astro component | Copy target |
|---|---|---|
| Navigation | `Nav.astro` | Nav link labels, CTA button text |
| Hero | `Hero.astro` | Heading, subheading, CTA labels |
| Features / Why section | `WhyKlai.astro`, `Features.astro`, `ValueProps.astro` | Data array items in page frontmatter |
| Product cards | `Products.astro` | Product data array in page frontmatter |
| Social proof / Testimonials | `SocialProof.astro` | Quote text, attribution |
| Comparison | `Comparison.astro` | Column headers, row labels |
| FAQ | `FAQ.astro` | Q&A array in page frontmatter |
| Pricing | `Pricing.astro`, `PricingCards.astro` | Tier names, prices, feature lists |
| Final CTA | `FinalCTA.astro` | Heading, description, button label |
| Footer | `Footer.astro` | Link labels, copyright text |

If counts differ (reference has 6 features, Astro component expects 4), pick the best N items. Never add or remove array slots -- match the existing structure.

---

## Phase 4 -- Apply Copy

### 4.1 Page frontmatter data arrays

For data defined in the page file (e.g. `const whyKlaiItems = [...]`), replace the string values inside the existing array structure. Keep the TypeScript types, variable names, and object keys identical.

```ts
// BEFORE (original)
const whyKlaiItems = [
  { title: 'Old title', description: 'Old description' },
];

// AFTER (filled with reference copy)
const whyKlaiItems = [
  { title: 'New title from reference', description: 'New description from reference' },
];
```

### 4.2 Inline text in components

For text hardcoded inside `.astro` component templates, use Edit to replace only the text content. Keep all surrounding HTML, classes, and Astro expressions.

### 4.3 i18n strings

If the page uses i18n keys, update `website/src/i18n/en.json` (and `nl.json` if Dutch copy is available in the reference). Only update keys that correspond to sections being filled.

### 4.4 Meta and title

Update the page title and meta description passed to the Base layout.

---

## Phase 5 -- Validation

Before finishing, verify:
- [ ] All text content matches the reference -- no original placeholder text remains in filled sections
- [ ] All Tailwind classes and `<style>` blocks are unchanged (diff the class attributes)
- [ ] All images, SVGs, and media references are unchanged
- [ ] Component imports and file structure are unchanged
- [ ] Data array lengths match the original (no items added or removed)
- [ ] TypeScript types are preserved (no type errors introduced)
- [ ] i18n keys are consistent between en.json and nl.json (if both updated)
- [ ] Brand name from reference is adapted to "Klai" where appropriate
- [ ] Links point to valid routes within the Astro site structure

Run `cd website && npm run build` to verify no build errors were introduced.

---

## Usage

```
/fill-page REF=clone-output/index_competitor.html PAGE=website/src/pages/index.astro
```

Without arguments, lists available reference files and Astro pages for selection.
