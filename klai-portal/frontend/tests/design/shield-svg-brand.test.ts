/**
 * Shield SVG assets are copied outside the portal stylesheet. Every hex fill
 * that mirrors a portal brand value is inventoried here. Named colours such
 * as the black wordmark remain outside this hex-fill lock.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.join(HERE, '..', '..', '..', '..')
const PORTAL_CSS = path.join(HERE, '..', '..', 'src', 'index.css')
const SHIELD_SRC = path.join(
  REPO_ROOT,
  'klai-portal',
  'backend',
  'app',
  'static',
  'shield-extension',
  'src',
)

const SVG_BRAND_ALIASES = new Map([
  ['assets/klai-mark.svg', { token: '--color-rl-accent', fills: 3 }],
])

type HexFill = {
  file: string
  line: number
  value: string
}

function requiredSource(file: string, label: string): string {
  expect(fs.existsSync(file), `${label} moved or no longer exists at ${file}`).toBe(true)
  return fs.readFileSync(file, 'utf8')
}

function hexVariables(source: string): Map<string, string> {
  const values = new Map<string, string>()
  for (const match of source.matchAll(
    /(--color-[a-z0-9-]+)\s*:\s*(#[0-9a-f]{3,8})\s*;/gi,
  )) {
    values.set(match[1], match[2].toLowerCase())
  }
  return values
}

function svgFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) files.push(...svgFiles(full))
    else if (entry.name.endsWith('.svg')) files.push(full)
  }
  return files
}

function hexFills(file: string): HexFill[] {
  const relative = path.relative(SHIELD_SRC, file).replaceAll(path.sep, '/')
  const fills: HexFill[] = []

  requiredSource(file, relative).split('\n').forEach((line, index) => {
    for (const match of line.matchAll(/fill=["'](#[0-9a-f]{3,8})["']/gi)) {
      fills.push({ file: relative, line: index + 1, value: match[1].toLowerCase() })
    }
  })

  return fills
}

describe('Shield SVG brand fills', () => {
  const portal = hexVariables(requiredSource(PORTAL_CSS, 'portal stylesheet'))
  const assets = svgFiles(SHIELD_SRC)
  const fills = assets.flatMap(hexFills)

  it('finds the SVG assets and their inventoried hex fills', () => {
    expect(assets.length, 'Shield source must contain SVG assets').toBeGreaterThan(0)
    expect(SVG_BRAND_ALIASES.size, 'the Shield SVG brand alias map must not be empty')
      .toBeGreaterThan(0)
    expect(fills.length, 'Shield SVG assets must contain hex fill attributes').toBeGreaterThan(0)
  })

  it('keeps every inventoried fill equal to its portal token', () => {
    const drift: string[] = []

    for (const [file, alias] of SVG_BRAND_ALIASES) {
      const expected = portal.get(alias.token)
      if (!expected) {
        drift.push(`index.css does not define ${alias.token}`)
        continue
      }

      const local = fills.filter((fill) => fill.file === file)
      if (local.length !== alias.fills) {
        drift.push(`${file}: expected ${alias.fills} hex fills, found ${local.length}`)
      }
      for (const fill of local) {
        if (fill.value !== expected) {
          drift.push(
            `${fill.file}:${fill.line} says ${fill.value}, ` +
              `index.css ${alias.token} says ${expected}`,
          )
        }
      }
    }

    expect(drift, drift.join('\n')).toEqual([])
  })

  it('inventories every hex fill that currently equals a portal brand value', () => {
    const portalValues = new Set(portal.values())
    const untracked = fills
      .filter((fill) => portalValues.has(fill.value))
      .filter((fill) => !SVG_BRAND_ALIASES.has(fill.file))
      .map((fill) => `${fill.file}:${fill.line} ${fill.value}`)

    expect(
      untracked,
      'add every Shield SVG fill that mirrors a portal value to SVG_BRAND_ALIASES',
    ).toEqual([])
  })
})
