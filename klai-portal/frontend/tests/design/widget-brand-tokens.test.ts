/**
 * The widget ships outside the portal stylesheet, so its `:host` defaults
 * carry a disconnected mirror of seven portal brand values. This lock keeps
 * that intentional mirror tied to `src/index.css`.
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.join(HERE, '..', '..', '..', '..')
const PORTAL_CSS = path.join(HERE, '..', '..', 'src', 'index.css')
const WIDGET_CSS = path.join(REPO_ROOT, 'klai-widget', 'src', 'styles', 'widget.css')

const WIDGET_ALIASES = new Map([
  ['--klai-primary-color', '--color-rl-accent'],
  ['--klai-primary-hover', '--color-rl-accent-hover'],
  ['--klai-text-color', '--color-rl-dark'],
  ['--klai-text-muted', '--color-rl-dark-60'],
  ['--klai-background-color', '--color-rl-bg'],
  ['--klai-card-color', '--color-rl-cream'],
  ['--klai-border-color', '--color-rl-border'],
])

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

function hostDefaults(source: string): Map<string, string> {
  const host = /:host\s*\{([\s\S]*?)\}/.exec(source)
  expect(host, 'widget.css must retain a parseable `:host` defaults block').not.toBeNull()
  return hexVariables(host![1])
}

describe('widget brand token mirror', () => {
  const portal = hexVariables(requiredSource(PORTAL_CSS, 'portal stylesheet'))
  const widget = hostDefaults(requiredSource(WIDGET_CSS, 'widget stylesheet'))

  it('finds the seven mirrored :host defaults', () => {
    expect(WIDGET_ALIASES.size).toBe(7)
    expect(widget.size, 'widget.css :host must yield hex colour defaults').toBeGreaterThan(0)
    expect(
      [...WIDGET_ALIASES.keys()].filter((name) => !widget.has(name)),
      'widget.css lost a mirrored :host default',
    ).toEqual([])
  })

  it('matches the aliased index.css values', () => {
    const drift = [...WIDGET_ALIASES.entries()]
      .filter(([, portalToken]) => !portal.has(portalToken))
      .map(([, portalToken]) => `index.css does not define ${portalToken}`)

    for (const [widgetToken, portalToken] of WIDGET_ALIASES) {
      if (!widget.has(widgetToken) || !portal.has(portalToken)) continue
      if (widget.get(widgetToken) !== portal.get(portalToken)) {
        drift.push(
          `${widgetToken}: widget.css says ${widget.get(widgetToken)}, ` +
            `index.css ${portalToken} says ${portal.get(portalToken)}`,
        )
      }
    }

    expect(drift, drift.join('\n')).toEqual([])
  })
})
