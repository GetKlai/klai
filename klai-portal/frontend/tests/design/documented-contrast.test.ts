/**
 * The Colors section in `docs/ui-standards.md` tells portal authors which
 * foreground colours to use, but that guidance is only useful when the text
 * remains legible on the portal's two standard surfaces.
 *
 * Known failures stay visible in DOCUMENTED_EXCEPTIONS with a required
 * reason. The reverse check removes an exception once its foreground reaches
 * WCAG AA on both surfaces, so this list cannot quietly become permanent.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { buildHexIndex, contrastRatio } from '../../eslint-rules/klai-tokens.js'

const FRONTEND_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const STANDARDS = path.join(FRONTEND_ROOT, 'docs', 'ui-standards.md')
const CSS = path.join(FRONTEND_ROOT, 'src', 'index.css')
const AA_NORMAL_TEXT = 4.5

const TAILWIND_TEXT_COLORS = new Map([
  ['text-gray-900', '#111827'],
  ['text-gray-600', '#4b5563'],
  ['text-gray-400', '#9ca3af'],
])

const PORTAL_SURFACES = ['--color-background', '--color-secondary']

const DOCUMENTED_EXCEPTIONS = new Map([
  [
    'text-gray-400',
    "The portal's default secondary colour at ~605 uses. Replacing it is a counted migration, not a sweep; see SPEC-DESIGN-SOURCE-001.",
  ],
  [
    '--color-success',
    'Used as an icon and badge tone, which is non-text and needs 3:1 rather than 4.5:1. At 2.73:1 it does not clear 3:1 either, so this is a real defect.',
  ],
  [
    '--color-warning',
    'Used as an icon and badge tone, which is non-text and needs 3:1 rather than 4.5:1.',
  ],
])

type NamedColor = {
  name: string
  hex: string
}

function documentedForegroundNames(source: string): string[] {
  const heading = /^## Colors\s*$/m.exec(source)
  if (!heading) throw new Error('ui-standards.md lost its exact `## Colors` heading')

  const afterHeading = source.slice(heading.index + heading[0].length)
  const nextSection = /^##\s+/m.exec(afterHeading)
  const colorsSection = afterHeading.slice(0, nextSection?.index)
  const useHeading = /^Use:\s*$/m.exec(colorsSection)
  if (!useHeading) throw new Error('the Colors section lost its `Use:` foreground list')

  const afterUse = colorsSection.slice(useHeading.index + useHeading[0].length)
  const end = /^Do not use\b/m.exec(afterUse)
  if (!end) throw new Error('the Colors `Use:` foreground list lost its `Do not use` boundary')

  const names = [...afterUse.slice(0, end.index).matchAll(/`([^`]+)`/g)]
    .map((match) => match[1])
    .filter((name) => /^text-[a-z0-9-]+$/.test(name) || /^var\(--color-[a-z0-9-]+\)$/.test(name))
    .map((name) => name.startsWith('var(') ? name.slice(4, -1) : name)

  if (names.length === 0) {
    throw new Error('the Colors `Use:` list yielded zero documented foreground colours')
  }

  return [...new Set(names)]
}

function stylesheetColors(): Map<string, string> {
  const colors = new Map<string, string>()

  for (const [hex, tokenNames] of buildHexIndex(CSS)) {
    for (const tokenName of tokenNames) colors.set(`--${tokenName}`, hex)
  }

  if (colors.size === 0) {
    throw new Error('index.css yielded zero @theme colours')
  }

  return colors
}

function resolveColors(names: string[], tokens: Map<string, string>): NamedColor[] {
  return names.map((name) => {
    const hex = name.startsWith('text-') ? TAILWIND_TEXT_COLORS.get(name) : tokens.get(name)
    if (!hex) throw new Error(`no hex value found for documented foreground ${name}`)
    return { name, hex }
  })
}

function contrastFailure(foreground: NamedColor, surface: NamedColor, ratio: number): string {
  return `${foreground.name} (${foreground.hex}) on ${surface.name} (${surface.hex}): ${ratio.toFixed(2)}:1 (requires ${AA_NORMAL_TEXT.toFixed(2)}:1)`
}

function contrastProblems(
  foregrounds: NamedColor[],
  surfaces: NamedColor[],
  exceptions: Map<string, string>,
): string[] {
  const problems: string[] = []
  const documentedNames = new Set(foregrounds.map((foreground) => foreground.name))

  for (const [name, reason] of exceptions) {
    if (!documentedNames.has(name)) {
      problems.push(`${name} has a documented contrast exception but is no longer in the Colors foreground list`)
    }
    if (reason.trim().length === 0 || /^(todo|tbd|placeholder)$/i.test(reason.trim())) {
      problems.push(`${name} has a contrast exception without a real reason`)
    }
  }

  for (const foreground of foregrounds) {
    const ratios = surfaces.map((surface) => ({
      surface,
      ratio: contrastRatio(foreground.hex, surface.hex),
    }))

    for (const { surface, ratio } of ratios) {
      if (ratio < AA_NORMAL_TEXT && !exceptions.has(foreground.name)) {
        problems.push(contrastFailure(foreground, surface, ratio))
      }
    }

    if (exceptions.has(foreground.name) && ratios.every(({ ratio }) => ratio >= AA_NORMAL_TEXT)) {
      problems.push(`${foreground.name} has a documented contrast exception but now passes 4.50:1 on every portal surface`)
    }
  }

  return problems
}

describe('documented portal text contrast', () => {
  it('reports an actionable failure for a below-AA fixture', () => {
    const problems = contrastProblems(
      [{ name: 'text-fixture', hex: '#777777' }],
      [{ name: '--color-fixture-surface', hex: '#ffffff' }],
      new Map(),
    )

    expect(problems).toEqual([
      'text-fixture (#777777) on --color-fixture-surface (#ffffff): 4.48:1 (requires 4.50:1)',
    ])
  })

  it('rejects an exception after its foreground reaches AA', () => {
    const problems = contrastProblems(
      [{ name: 'text-fixture', hex: '#111827' }],
      [{ name: '--color-fixture-surface', hex: '#ffffff' }],
      new Map([['text-fixture', 'A real reason that should be removed with a stale exception.']]),
    )

    expect(problems).toEqual([
      'text-fixture has a documented contrast exception but now passes 4.50:1 on every portal surface',
    ])
  })

  it('meets WCAG AA or carries a current documented exception', () => {
    const tokens = stylesheetColors()
    const foregrounds = resolveColors(
      documentedForegroundNames(fs.readFileSync(STANDARDS, 'utf8')),
      tokens,
    )
    const surfaces = resolveColors(PORTAL_SURFACES, tokens)
    const problems = contrastProblems(foregrounds, surfaces, DOCUMENTED_EXCEPTIONS)

    expect(problems, problems.join('\n')).toEqual([])
  })
})
