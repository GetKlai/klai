# CodeIndex integration

CodeIndex is a precomputed graph, not a source of truth. Use it for broad
architecture, impact, hotspot, SPEC, and test-coverage discovery, then verify
every affected file directly in the current worktree.

## Freshness first

Run `codeindex status` before relying on results. Report its indexed commit and
freshness verdict; do not copy node or symbol counts into agent instructions.
Counts such as `0 symbols` and package-version install paths become stale.

| Situation | Command |
|---|---|
| Incremental refresh | `codeindex update && node scripts/codeindex-enrich.mjs` |
| Full refresh | `./scripts/codeindex-analyze-and-enrich.sh --force` |
| Freshness check | `codeindex status` |

The installed CodeIndex package owns its CLI and workflow documentation under
`.claude/skills/codeindex/`. Follow the current CodeIndex repository README for
installation; do not restore the removed `klai-private/tools/*.tgz` path.

## Klai enrichment

Klai's enrichment layer adds git hotspots, SPEC links, test relationships, and
PageRank metadata to graph descriptions. Query these fields with
`codeindex cypher`, and run `scripts/codeindex-enrich.mjs` after refreshing the
base graph.

Project-local hooks are installed in `.claude/settings.local.json` and are not
committed. A missing local hook is a setup issue, not a reason to invent graph
statistics or Serena availability.
