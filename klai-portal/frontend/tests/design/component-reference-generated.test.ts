/**
 * The Component Library Reference is generated from every source module in
 * `src/components/ui/`. The old completeness test's two assertions — no
 * missing module and no row without a module — are therefore impossible by
 * construction. This check instead fails when the committed generated table
 * is stale relative to source comments or `cva(...)` variants.
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
  'generate-component-reference.mjs',
)

describe('generated Component Library Reference', () => {
  it('matches the UI module source comments and variants', () => {
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
