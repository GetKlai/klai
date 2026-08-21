/**
 * Tests for `klai/no-hardcoded-brand-color`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 *
 * The rule reads the palette from `src/index.css`. These tests point it at a
 * fixture stylesheet instead, so they assert rule behaviour rather than the
 * current contents of the real theme.
 */
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { RuleTester } from 'eslint'
import rule from '../no-hardcoded-brand-color.js'
import { buildHexIndex } from '../klai-tokens.js'

const FIXTURE_CSS = `
@theme inline {
  --color-rl-dark:        #191918;
  --color-rl-bg:          #fffef2;
  --color-rl-accent:      #fcaa2d;
  --color-primary:        #fcaa2d;
  --color-rl-dark-60:     #19191899;
  --color-white-ish:      #ffffff;
}
`

const cssPath = path.join(
  fs.mkdtempSync(path.join(os.tmpdir(), 'klai-tokens-')),
  'index.css',
)
fs.writeFileSync(cssPath, FIXTURE_CSS)

const options = [{ cssPath }]

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
})

ruleTester.run('klai/no-hardcoded-brand-color', rule, {
  valid: [
    // The canonical portal form.
    {
      code: `const a = <div className="bg-[var(--color-rl-dark)]" />`,
      options,
    },
    // Third-party brand mark: Google blue is not a Klai token.
    {
      code: `const a = <div className="bg-[#4285F4]" />`,
      options,
    },
    // Third-party brand mark on an SVG fill attribute - not className at all.
    {
      code: `const a = <path fill="#EA4335" d="M9 3.58z" />`,
      options,
    },
    // Tenant-configurable widget colour is runtime DATA, not styling.
    {
      code: `const config = { primary_color: '#fcaa2d' }`,
      options,
    },
    // A brand hex passed as a prop value, not a class name.
    {
      code: `const a = <WidgetPreview primaryColor="#fcaa2d" />`,
      options,
    },
    // Plain utility classes with no hex at all.
    {
      code: `const a = <div className={\`flex \${isDark ? 'bg-white' : 'bg-black'}\`} />`,
      options,
    },
  ],
  invalid: [
    // Plain string literal className.
    {
      code: `const a = <div className="bg-[#191918]" />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Template literal inside a conditional - the WidgetChatSurface shape.
    {
      code: `const a = <div className={\`flex \${isDark ? 'bg-[#191918] text-[#fffef2]' : 'bg-white'}\`} />`,
      options,
      errors: [{ messageId: 'hardcoded' }, { messageId: 'hardcoded' }],
    },
    // Opacity modifier still resolves to the base token.
    {
      code: `const a = <div className="text-[#fffef2]/55" />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Alpha-suffixed hex resolves to its opaque base token.
    {
      code: `const a = <div className="text-[#19191899]" />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Ternary directly as the className expression.
    {
      code: `const a = <div className={isDark ? 'bg-[#191918]' : 'bg-white'} />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Inside a cn() call.
    {
      code: `const a = <div className={cn('flex', 'border-[#fcaa2d]')} />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Property-prefixed arbitrary value.
    {
      code: `const a = <div className="[color:#191918]" />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
    // Shorthand hex expands to a token value.
    {
      code: `const a = <div className="bg-[#fff]" />`,
      options,
      errors: [{ messageId: 'hardcoded' }],
    },
  ],
})

describe('buildHexIndex', () => {
  it('indexes every --color-* hex from the @theme block', () => {
    const index = buildHexIndex(cssPath)
    expect(index.get('#191918')).toEqual(['color-rl-dark', 'color-rl-dark-60'])
    expect(index.get('#fffef2')).toEqual(['color-rl-bg'])
  })

  it('records every token name sharing one value', () => {
    const index = buildHexIndex(cssPath)
    expect(index.get('#fcaa2d')).toEqual(['color-rl-accent', 'color-primary'])
  })

  it('returns an empty index for a missing stylesheet, so lint never crashes', () => {
    expect(buildHexIndex('/nonexistent/index.css').size).toBe(0)
  })

  it('indexes the real portal stylesheet', () => {
    // Guards the parser against a future refactor of index.css (e.g. renaming
    // the @theme block) silently turning the rule into a no-op.
    const index = buildHexIndex()
    expect(index.size).toBeGreaterThan(10)
    expect(index.get('#191918')).toContain('color-rl-dark')
  })
})
