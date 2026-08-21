/**
 * `docs/ui-standards.md` is the canonical UI contract - it is what an agent
 * or a new contributor reads before building a screen, and it opens by
 * declaring that it wins over any other design document. Its Component
 * Library Reference table is maintained by hand.
 *
 * A hand-maintained inventory of a directory drifts the moment someone adds a
 * component and forgets the table. The failure is silent and asymmetric: the
 * component exists and works, so nothing breaks, but every future reader is
 * told the portal has no such primitive and hand-rolls a replacement. That is
 * how a component library grows a second, undocumented half.
 *
 * This test makes the table's completeness a build-time fact.
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
const UI_DIR = path.join(FRONTEND_ROOT, 'src', 'components', 'ui')
const STANDARDS = path.join(FRONTEND_ROOT, 'docs', 'ui-standards.md')

/** Every shared UI module, by the name a reader would look up. */
function ownedComponents(): string[] {
  return fs
    .readdirSync(UI_DIR, { withFileTypes: true })
    .filter((e) => e.isFile() && /\.tsx?$/.test(e.name))
    .map((e) => e.name.replace(/\.tsx?$/, ''))
    .sort()
}

/**
 * The doc has several tables (action tones, layout containers, reference
 * screens). Only the Component Library Reference one is an inventory of
 * `src/components/ui/`, so the staleness check must read that section alone.
 */
function componentSection(doc: string): string {
  const start = doc.indexOf('## Component Library Reference')
  if (start < 0) throw new Error('ui-standards.md lost its Component Library Reference heading')
  const next = doc.indexOf('\n## ', start + 1)
  return doc.slice(start, next < 0 ? undefined : next)
}

describe('ui-standards.md Component Library Reference', () => {
  const doc = fs.readFileSync(STANDARDS, 'utf8')

  it('names every module in src/components/ui/', () => {
    const undocumented = ownedComponents().filter(
      (name) => !doc.includes(`\`${name}\``),
    )

    expect(
      undocumented,
      `Add these to the Component Library Reference table in docs/ui-standards.md ` +
        `(and to the /dev/ui catalog if they render): ${undocumented.join(', ')}`,
    ).toEqual([])
  })

  it('does not describe components that no longer exist', () => {
    // The other drift direction: a component gets deleted or renamed and the
    // table keeps advertising it, so an agent imports something that is gone.
    const owned = new Set(ownedComponents())
    const tableRows = componentSection(doc).matchAll(/^\|\s*`([a-z0-9-]+)`[^|]*\|/gim)

    const stale = [...tableRows]
      .map((m) => m[1])
      .filter((name) => !owned.has(name))

    expect(
      stale,
      `These are documented in docs/ui-standards.md but absent from ` +
        `src/components/ui/: ${stale.join(', ')}`,
    ).toEqual([])
  })
})
