/**
 * `.claude/rules/klai/design/*.md` quotes hex values from `index.css` in
 * markdown tables. `tokens.md` carries `paths: "**"`, so it loads into EVERY
 * agent session in this monorepo, for every file. It is the single most-read
 * design document we have, and nothing connects it to the stylesheet it
 * mirrors.
 *
 * A wrong value there is worse than a missing one. An agent that reads
 * `--color-rl-accent` is `#fcaa2d` when the stylesheet says otherwise will
 * confidently hardcode the stale value, and the brand-colour lint rule will
 * not catch it: that rule compares against `index.css`, so a hex matching a
 * stale doc is simply an unknown colour to it.
 *
 * There is no drift today. This is a lock, not a clean-up.
 *
 * Values documented but absent from the portal stylesheet are skipped rather
 * than failed: `styleguide.md` covers the website too, and website-only
 * tokens (`--color-rl-dark-30`, `--color-rl-muted`, ...) live in
 * `klai-website/src/styles/global.css`, which is a separate repo.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.join(HERE, '..', '..', '..', '..')
const CSS = path.join(HERE, '..', '..', 'src', 'index.css')
const RULE_DOCS_DIR = path.join(REPO_ROOT, '.claude', 'rules', 'klai', 'design')
const RULE_DOCS = fs
  .readdirSync(RULE_DOCS_DIR, { withFileTypes: true })
  .filter((entry) => entry.isFile() && entry.name.endsWith('.md'))
  .map((entry) => path.relative(REPO_ROOT, path.join(RULE_DOCS_DIR, entry.name)))
  .sort()

/** Every `--color-*: #hex;` declaration in the portal stylesheet. */
function stylesheetColors(): Map<string, string> {
  const css = fs.readFileSync(CSS, 'utf8')
  const out = new Map<string, string>()
  for (const m of css.matchAll(/(--color-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    out.set(m[1], m[2].toLowerCase())
  }
  return out
}

/**
 * Table rows that name at least one token AND at least one hex.
 * A row may carry several of each (`--color-rl-cream` / `--color-secondary`
 * with two values), so a row passes when the stylesheet value appears
 * anywhere in that row.
 */
function documentedPairs(docPath: string): Array<{ token: string; hexes: string[]; line: number }> {
  const lines = fs.readFileSync(path.join(REPO_ROOT, docPath), 'utf8').split('\n')
  const pairs: Array<{ token: string; hexes: string[]; line: number }> = []

  lines.forEach((line, i) => {
    if (!line.startsWith('|')) return
    const tokens = [...line.matchAll(/`(--color-[a-z0-9-]+)`/g)].map((m) => m[1])
    const hexes = [...line.matchAll(/`(#[0-9a-fA-F]{3,8})`/g)].map((m) => m[1].toLowerCase())
    if (!tokens.length || !hexes.length) return
    for (const token of tokens) pairs.push({ token, hexes, line: i + 1 })
  })

  return pairs
}

describe('design rule docs quote real token values', () => {
  const css = stylesheetColors()

  it('finds the portal stylesheet tokens', () => {
    // If index.css is restructured and this parser stops matching, every
    // assertion below would vacuously pass. Fail loudly instead.
    expect(css.size).toBeGreaterThan(20)
    expect(css.get('--color-rl-accent')).toBeDefined()
  })

  for (const doc of RULE_DOCS) {
    it(`${doc} matches index.css`, () => {
      const drift = documentedPairs(doc)
        .filter((p) => css.has(p.token))
        .filter((p) => !p.hexes.includes(css.get(p.token)!))
        .map((p) => `${doc}:${p.line} ${p.token} documented as ${p.hexes.join('/')}, index.css says ${css.get(p.token)}`)

      expect(drift, drift.join('\n')).toEqual([])
    })
  }
})
