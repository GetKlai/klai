/**
 * Klai Shield cannot resolve the portal stylesheet, so its popup and
 * sidepanel carry local copies of the core brand palette. The sidepanel also
 * mirrors its `--ink` role as `--text-primary` and calls the popup's `--page`
 * role `--surface-page`.
 *
 * Boundary: Shield's deliberately divergent success, warning, danger and
 * info/blue semantic palette is its own system and is not locked here.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.join(HERE, '..', '..', '..', '..')
const PORTAL_CSS = path.join(HERE, '..', '..', 'src', 'index.css')
const SHIELD_ROOT = path.join(
  REPO_ROOT,
  'klai-portal',
  'backend',
  'app',
  'static',
  'shield-extension',
  'src',
)

const SHIELD_FILES = [
  {
    label: 'popup/index.html',
    path: path.join(SHIELD_ROOT, 'popup', 'index.html'),
    aliases: new Map([
      ['--accent', '--color-rl-accent'],
      ['--ink', '--color-rl-dark'],
      ['--surface', '--color-popover'],
      ['--page', '--color-background'],
      ['--border', '--color-border'],
    ]),
  },
  {
    label: 'sidepanel/sidepanel.css',
    path: path.join(SHIELD_ROOT, 'sidepanel', 'sidepanel.css'),
    aliases: new Map([
      ['--accent', '--color-rl-accent'],
      ['--ink', '--color-rl-dark'],
      ['--text-primary', '--color-rl-dark'],
      ['--surface', '--color-popover'],
      ['--surface-page', '--color-background'],
      ['--border', '--color-border'],
    ]),
  },
]

function requiredSource(file: string, label: string): string {
  expect(fs.existsSync(file), `${label} moved or no longer exists at ${file}`).toBe(true)
  return fs.readFileSync(file, 'utf8')
}

function hexVariables(source: string): Map<string, string> {
  const values = new Map<string, string>()
  for (const match of source.matchAll(
    /(--[a-z0-9-]+)\s*:\s*(#[0-9a-f]{3,8})\s*;/gi,
  )) {
    values.set(match[1], match[2].toLowerCase())
  }
  return values
}

describe('Shield core brand token mirrors', () => {
  const portal = hexVariables(requiredSource(PORTAL_CSS, 'portal stylesheet'))

  for (const shield of SHIELD_FILES) {
    describe(shield.label, () => {
      const local = hexVariables(requiredSource(shield.path, shield.label))

      it('finds the explicit core alias set', () => {
        expect(shield.aliases.size, `${shield.label} alias map must not be empty`)
          .toBeGreaterThan(0)
        expect(local.size, `${shield.label} must yield hex colour declarations`).toBeGreaterThan(0)
        expect(
          [...shield.aliases.keys()].filter((name) => !local.has(name)),
          `${shield.label} lost a mirrored core token`,
        ).toEqual([])
      })

      it('matches the aliased index.css values', () => {
        const drift = [...shield.aliases.entries()]
          .filter(([, portalToken]) => !portal.has(portalToken))
          .map(([, portalToken]) => `index.css does not define ${portalToken}`)

        for (const [shieldToken, portalToken] of shield.aliases) {
          if (!local.has(shieldToken) || !portal.has(portalToken)) continue
          if (local.get(shieldToken) !== portal.get(portalToken)) {
            drift.push(
              `${shield.label} ${shieldToken} says ${local.get(shieldToken)}, ` +
                `index.css ${portalToken} says ${portal.get(portalToken)}`,
            )
          }
        }

        expect(drift, drift.join('\n')).toEqual([])
      })
    })
  }
})
