/**
 * `src/index.css` gives every element a neutral default border colour. That
 * reset must stay in `@layer base`: an unlayered declaration outranks every
 * explicit cascade layer and would silently turn Tailwind's coloured border
 * utilities neutral grey. The failure is easy to miss because that grey is
 * close to `gray-200`.
 *
 * There is no drift today: the reset is layered and no `.tsx` file fixes it
 * with an inline `style={{ borderColor }}`. This is a lock, not a clean-up.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'

const FRONTEND_ROOT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
)
const CSS = path.join(FRONTEND_ROOT, 'src', 'index.css')
const SRC_ROOT = path.join(FRONTEND_ROOT, 'src')

type BlockRange = { start: number; end: number }

/** Remove comments without changing offsets used to compare block ranges. */
function withoutComments(css: string): string {
  return css.replace(
    /\/\*[\s\S]*?\*\//g,
    (comment) => ' '.repeat(comment.length),
  )
}

/** Find the closing brace for a block whose opening brace is at `start`. */
function closingBrace(css: string, start: number): number {
  let depth = 0
  for (let i = start; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    if (css[i] === '}') depth -= 1
    if (depth === 0) return i
  }
  throw new Error('src/index.css contains an unclosed block')
}

function layerRanges(css: string, name?: string): BlockRange[] {
  const source = withoutComments(css)
  const suffix = name ? `\\s+${name}\\b` : '\\s+[^;{]+'
  const pattern = new RegExp(`@layer${suffix}\\s*\\{`, 'g')

  return [...source.matchAll(pattern)].map((match) => {
    const start = match.index + match[0].lastIndexOf('{')
    return { start, end: closingBrace(source, start) }
  })
}

function universalBorderResets(css: string): number[] {
  const source = withoutComments(css)
  return [...source.matchAll(/(?:^|[}\s])\*\s*\{([^{}]*)\}/gm)]
    .filter((match) => /\bborder-color\s*:/.test(match[1]))
    .map((match) => match.index)
}

function tsxFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) return tsxFiles(full)
    return entry.isFile() && entry.name.endsWith('.tsx') ? [full] : []
  })
}

describe('default border-colour reset', () => {
  const css = fs.readFileSync(CSS, 'utf8')
  const resets = universalBorderResets(css)

  it('stays inside @layer base', () => {
    const baseLayers = layerRanges(css, 'base')
    const layeredResets = resets.filter((reset) =>
      baseLayers.some((layer) => reset > layer.start && reset < layer.end),
    )

    expect(
      layeredResets,
      'src/index.css must keep its universal border-color reset inside @layer base',
    ).not.toHaveLength(0)
  })

  it('has no second unlayered universal border-colour reset', () => {
    const layers = layerRanges(css)
    const unlayered = resets.filter(
      (reset) => !layers.some((layer) => reset > layer.start && reset < layer.end),
    )

    expect(
      unlayered,
      'an unlayered * { border-color } overrides Tailwind utility layers',
    ).toEqual([])
  })

  it('has no inline borderColor fix in src/**/*.tsx', () => {
    const inlineBorderColor =
      /\bstyle\s*=\s*\{\s*\{[^}]*\bborderColor\s*:/s
    const offenders = tsxFiles(SRC_ROOT)
      .filter((file) => inlineBorderColor.test(fs.readFileSync(file, 'utf8')))
      .map((file) => path.relative(FRONTEND_ROOT, file))

    expect(
      offenders,
      'use a border-* utility instead of inline style={{ borderColor }}',
    ).toEqual([])
  })
})
