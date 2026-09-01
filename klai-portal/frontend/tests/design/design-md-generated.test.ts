/**
 * DESIGN.md is a committed build artefact rendered from the portal theme,
 * component metadata, and UI standards. This check prevents hand edits and
 * catches source changes that have not been regenerated.
 */
import { spawnSync } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const FRONTEND_ROOT = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
  '..',
)
const GENERATOR = path.join(
  FRONTEND_ROOT,
  'scripts',
  'generate-design-md.mjs',
)

describe('generated DESIGN.md', () => {
  it('matches the theme, UI module metadata, and UI standards', () => {
    const result = spawnSync(process.execPath, [GENERATOR, '--check'], {
      cwd: FRONTEND_ROOT,
      encoding: 'utf8',
    })

    expect(
      result.status,
      `${result.stdout}${result.stderr}`,
    ).toBe(0)
  })
})
