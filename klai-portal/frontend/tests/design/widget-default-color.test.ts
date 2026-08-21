/**
 * The widget default primary colour is a literal by necessity (it ships to an
 * embedded surface that cannot resolve portal CSS vars), so nothing in the
 * type system ties it to the brand. This test does.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'
import { WIDGET_DEFAULT_PRIMARY_COLOR } from '@/features/widgets/config/appearance'

const SRC_ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', '..', 'src')

describe('WIDGET_DEFAULT_PRIMARY_COLOR', () => {
  it('matches --color-rl-accent in index.css', () => {
    const css = fs.readFileSync(path.join(SRC_ROOT, 'index.css'), 'utf8')
    const match = /--color-rl-accent:\s*(#[0-9a-fA-F]{6})\s*;/.exec(css)

    expect(match, 'index.css must define --color-rl-accent').not.toBeNull()
    expect(WIDGET_DEFAULT_PRIMARY_COLOR.toLowerCase()).toBe(match![1].toLowerCase())
  })

  it('is the only place the literal is defined', () => {
    // Guards the dedupe: a re-introduced literal elsewhere in src/ means the
    // four-copy drift is back.
    const offenders: string[] = []

    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name)
        if (entry.isDirectory()) {
          if (entry.name === 'paraglide' || entry.name === '__tests__') continue
          walk(full)
          continue
        }
        if (!/\.tsx?$/.test(entry.name)) continue
        if (full.endsWith(path.join('config', 'appearance.ts'))) continue
        if (fs.readFileSync(full, 'utf8').includes(`'${WIDGET_DEFAULT_PRIMARY_COLOR}'`)) {
          offenders.push(path.relative(SRC_ROOT, full))
        }
      }
    }
    walk(SRC_ROOT)

    expect(
      offenders,
      'import WIDGET_DEFAULT_PRIMARY_COLOR instead of retyping the hex',
    ).toEqual([])
  })
})
