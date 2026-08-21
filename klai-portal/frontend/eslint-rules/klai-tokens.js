/**
 * @fileoverview Parse the `@theme` block in `src/index.css` into a
 * hex -> token-name index, so design lint rules never carry their own copy
 * of the palette.
 *
 * Background: `.claude/rules/klai/design/tokens.md` is a hand-maintained
 * markdown mirror of `index.css`. Any rule that hardcoded the palette would
 * become a third copy and drift the same way. This module reads the real
 * stylesheet at lint time instead, so adding a token to `index.css` is the
 * only step needed to have it enforced.
 *
 * Only `--color-*` declarations are indexed; a hex is only interesting to a
 * design rule when it is a colour someone should have referenced by name.
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const THEME_BLOCK = /@theme[^{]*\{([\s\S]*?)\n\}/
const COLOR_DECL = /--(color-[a-z0-9-]+)\s*:\s*([^;]+);/g
const HEX = /^#[0-9a-fA-F]{3,8}$/

function defaultCssPath() {
  const here = path.dirname(fileURLToPath(import.meta.url))
  return path.join(here, '..', 'src', 'index.css')
}

/**
 * Build the index.
 *
 * Returns a Map keyed on the lowercased 6-digit hex, valued with the list of
 * token names carrying that value. A value can have several names (e.g.
 * `#fcaa2d` is both `--color-rl-accent` and `--color-primary`); the rule
 * reports all of them and lets the author pick the semantically right one.
 *
 * @param {string} [cssPath] override, for tests
 * @returns {Map<string, string[]>}
 */
export function buildHexIndex(cssPath = defaultCssPath()) {
  const index = new Map()

  let source
  try {
    source = fs.readFileSync(cssPath, 'utf8')
  } catch {
    // A missing stylesheet must not crash the whole lint run. An empty index
    // makes the rule a no-op, which is the safe direction to fail.
    return index
  }

  const themeMatch = THEME_BLOCK.exec(source)
  if (!themeMatch) return index

  for (const [, name, rawValue] of themeMatch[1].matchAll(COLOR_DECL)) {
    const value = rawValue.trim()
    if (!HEX.test(value)) continue
    // Alpha-suffixed tokens (`#19191899`) are stored under their opaque base
    // too, so `text-[#191918]/60` still resolves to a named token.
    const base = `#${value.slice(1, 7)}`.toLowerCase()
    const names = index.get(base) ?? []
    if (!names.includes(name)) names.push(name)
    index.set(base, names)
  }

  return index
}

/**
 * Suggest the CSS-var form the portal actually uses.
 *
 * The portal convention is the Tailwind arbitrary value wrapping a var
 * (`bg-[var(--color-rl-dark)]`), not a generated utility (`bg-rl-dark`):
 * `var(--color-destructive)` alone appears 191 times in `src/`.
 *
 * @param {string[]} tokenNames
 * @returns {string}
 */
export function suggestionFor(tokenNames) {
  return tokenNames.map((name) => `var(--${name})`).join(' or ')
}
