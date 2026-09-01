---
paths:
  - "klai-portal/frontend/**"
  - "klai-portal/backend/app/templates/**"
  - "klai-portal/backend/app/static/**"
---

# Portal UI Authoring Entry Point

Before writing portal UI, read both sources:

- `klai-portal/frontend/DESIGN.md` is the generated design contract for tokens,
  type scale, component variants, and guidelines. Never edit it by hand;
  `scripts/generate-design-md.mjs` emits it and the build rejects stale output.
- `klai-portal/frontend/docs/ui-standards.md` contains the Rules Ledger. Every
  rule has a stable ID, level, and verification mode; component-owned rules
  originate as `@guideline` comments on their UI components.

Choose colours while authoring, not after lint: open the Colors section of
`DESIGN.md` and apply its computed primary, secondary, non-text, and
decorative-only gray roles. Semantic foregrounds use the documented `-text`
tokens. Do not estimate contrast or pick a semantic foreground from its hue.

Before introducing a layout or interaction, name and inspect the nearest
comparable portal screen. Use the component contract in `DESIGN.md` instead of
remembered variants or copied examples.

After adding or changing a shared component:

1. Add or update its header `@purpose`; put any component-owned normative rule
   there as `@guideline` with its ledger ID and level.
2. Render the changed purpose, state, or variant in `/dev/ui`.
3. From `klai-portal/frontend/`, run `npm run docs:components` and then
   `npm run docs:design`; commit both generated outputs with the component.
