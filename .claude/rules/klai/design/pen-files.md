---
paths:
  - "klai-portal/frontend/design/**"
  - "klai-portal/frontend/src/index.css"
  - "klai-portal/frontend/scripts/generate-pen-variables.mjs"
---
# pen.dev design files (klai-portal/frontend/design/)

Portal design lives in git next to the code as pen.dev `.pen` files (JSON).

| Path | What it is |
|---|---|
| `klai-portal/frontend/design/klai.lib.pen` | The design library: pen components mirroring `src/components/ui/`, one pen component per code file (`button.tsx/default`, `badge.tsx/success`, `card.tsx`, ...) |
| `klai-portal/frontend/design/screens/*.pen` | Screen files. They `imports` the library and instance its components via `ref: "klai:<componentId>"` |

Three rules, in order of how expensive they are to get wrong:

- **Tokens are one-way: code is the source, pen follows.** Every `.pen` file's
  `variables` map is generated from the `@theme inline` block in
  `src/index.css` by `scripts/generate-pen-variables.mjs`, run from
  `klai-portal/frontend`. Never hand-edit `variables` in a
  `.pen` file, and never edit `src/index.css` to make a design match. Run the
  generator with `--check` to detect drift. Note the portal root font size is
  110%, so `1rem = 17.6px`; the generator converts radii and spacing at that
  root, and pen values are px.
- **Fonts on the canvas are a substitute, and only on the canvas.** pen.dev
  renders Google Fonts only, so it cannot load the self-hosted brand faces.
  The generator therefore emits a second set of variables — `font-sans-preview`
  (Schibsted Grotesk), `font-display-preview`, `font-display-bold-preview`,
  `font-mono-preview` (DM Mono) — chosen by measuring x-height, cap-height and
  n/o/H/i/M advance widths against the real font binaries, so text occupies
  realistic space. Text nodes reference the `-preview` variables; the truthful
  `font-sans` / `font-mono` tokens stay in the file as the code contract and
  must never be repointed. A preview family is never a reason to change
  `src/index.css`.
- **Opacity is a variable, not a property.** pen.dev has no fill opacity, so a
  Tailwind modifier like `bg-[var(--color-success)]/10` maps to its own
  generated variable `color-success-tint-10`. Those tints are derived from the
  modifiers actually used under `src/`, so if a tint you need is missing it is
  because no component uses it yet — add the usage in code first, then
  regenerate. Never hand-write a tinted hex.
- **The library is the source for new screens.** Build a screen from library
  instances, not from fresh frames. Use variables (`$color-rl-accent`), never
  a hardcoded hex.
- **Generated code is a measurement, not a deliverable.** Code exported or
  generated from a `.pen` file must never be committed over a hand-written
  component. It inlines hex, converts every rem to an arbitrary px value, and
  loses the component boundary, semantics, i18n and responsive behaviour.

Commands an agent may use (auth: `set -a; . ~/.mcp/pen/pen.env; set +a`, then
`pen --workspace klai`):

| Goal | Command |
|---|---|
| Read/modify a `.pen` file deterministically, no model cost | `pen interactive --in <f>.pen --out <f>.pen` then `execute({...})` / `save()` |
| Add a repo SVG as a canvas asset | one reusable `path` node: concatenate the file's `d` attributes, set `viewBox`, set `fill` to a token |
| Regenerate variables from `src/index.css` | `node scripts/generate-pen-variables.mjs` |
| Detect token drift | `node scripts/generate-pen-variables.mjs --check` |
| Let an agent design or generate code | `pen --in <f>.pen --out <f>.pen --model claude-haiku-4-5 --usage ./pen-usage.json --prompt "..."` |

CI enforces both rules: the `quality` job in `.github/workflows/portal-frontend.yml`
runs `--check`, which fails on token drift AND on a committed `fileToken`.

Two pen.dev behaviours that cost time to discover, so do not rediscover them:
a `descendants` override on an instance of an IMPORTED component needs the alias
in the key (`instanceId/klai:childId`) and fails silently without it; and a
`layout: "none"` frame does not resize its children, so overlapping vector
artwork belongs in a single `path` node rather than a frame of paths.

Prefer `pen interactive`: it is a plain MCP bridge and costs no model quota.
Escalate to `--model` only when the task genuinely needs an agent, use
`claude-haiku-4-5` for bulk work, and always log usage. `.pen` files carry a
`fileToken` that ties them to the cloud workspace — this repo is public, so the
generator strips it on write; run the generator before committing a `.pen`
file a designer saved.
