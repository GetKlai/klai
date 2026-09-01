/**
 * The backend-rendered OAuth consent page cannot resolve the portal SPA's
 * stylesheet, so `klai-portal/backend/app/static/oauth/consent.css` must carry
 * its own copy of the portal palette. That disconnected copy already drifted
 * once: on 2026-05-07 the consent page's brand colours diverged because the
 * design-rule path glob did not cover backend HTML.
 *
 * There is no drift today: all 11 copied `--color-*` tokens match
 * `src/index.css`. This is a lock, not a clean-up.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it, expect } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.join(HERE, '..', '..', '..', '..')
const PORTAL_CSS = path.join(HERE, '..', '..', 'src', 'index.css')
const CONSENT_CSS = path.join(
  REPO_ROOT,
  'klai-portal',
  'backend',
  'app',
  'static',
  'oauth',
  'consent.css',
)

/** Every `--color-*` declaration, including non-hex values. */
function declaredColorNames(css: string): string[] {
  return [...css.matchAll(/(--color-[a-z0-9-]+)\s*:/gi)].map((m) => m[1])
}

/** Every `--color-*: #hex;` declaration, compared case-insensitively. */
function hexColors(css: string): Map<string, string> {
  const colors = new Map<string, string>()
  for (const m of css.matchAll(
    /(--color-[a-z0-9-]+)\s*:\s*(#[0-9a-f]{3,8})\s*;/gi,
  )) {
    colors.set(m[1], m[2].toLowerCase())
  }
  return colors
}

describe('OAuth consent page palette mirror', () => {
  const portalSource = fs.readFileSync(PORTAL_CSS, 'utf8')
  const consentSource = fs.readFileSync(CONSENT_CSS, 'utf8')
  const portal = hexColors(portalSource)
  const consent = hexColors(consentSource)
  const portalNames = new Set(declaredColorNames(portalSource))

  it('finds mirrored colour tokens', () => {
    const mirrored = [...consent.keys()].filter((name) => portalNames.has(name))

    // If consent.css moves or its declaration shape changes, the comparisons
    // below must not become a vacuous pass.
    expect(mirrored, 'consent.css must mirror at least one portal colour token')
      .not.toHaveLength(0)
  })

  it('declares only colour tokens owned by index.css', () => {
    const unknown = declaredColorNames(consentSource).filter(
      (name) => !portalNames.has(name),
    )

    expect(
      unknown,
      'Add a colour token to src/index.css first, then mirror it in consent.css.',
    ).toEqual([])
  })

  it('matches index.css values', () => {
    const drift = [...consent.entries()]
      .filter(([name]) => portalNames.has(name))
      .filter(([name, value]) => portal.get(name) !== value)
      .map(
        ([name, value]) =>
          `${name}: consent.css says ${value}, index.css says ${portal.get(name)}`,
      )

    expect(drift, drift.join('\n')).toEqual([])
  })
})
