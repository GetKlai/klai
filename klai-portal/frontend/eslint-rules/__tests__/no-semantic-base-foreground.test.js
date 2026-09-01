/**
 * Tests for `klai/no-semantic-base-foreground`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 */
import { RuleTester } from 'eslint'
import rule from '../no-semantic-base-foreground.js'

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
})

ruleTester.run('klai/no-semantic-base-foreground', rule, {
  valid: [
    `const a = <span className="text-[var(--color-success-text)]" />`,
    `const a = <span className="text-[var(--color-warning-text)]" />`,
    `const a = <span className="text-[var(--color-info-text)]" />`,
    // Destructive is a documented exception because its base clears AA.
    `const a = <span className="text-[var(--color-destructive)]" />`,
    // Semantic base tokens remain valid for non-text utilities.
    `const a = <div className="bg-[var(--color-success)] border-[var(--color-warning)] ring-[var(--color-info)]" />`,
    // Token data outside a class attribute is not styling.
    `const token = '--color-success'`,
  ],
  invalid: [
    {
      code: `const a = <span className="text-[var(--color-success)]" />`,
      errors: [{ messageId: 'baseForeground', data: { token: 'success' } }],
    },
    {
      code: `const a = <span className="hover:text-[var(--color-warning)]" />`,
      errors: [{ messageId: 'baseForeground', data: { token: 'warning' } }],
    },
    {
      code: `const a = <span className="text-[var(--color-info)]/80" />`,
      errors: [{ messageId: 'baseForeground', data: { token: 'info' } }],
    },
    {
      code: `const a = <span className={cn('text-sm', active && 'text-[var(--color-success)]')} />`,
      errors: [{ messageId: 'baseForeground', data: { token: 'success' } }],
    },
    {
      code: `const a = <span className={\`text-sm \${tone === 'warning' ? 'text-[var(--color-warning)]' : 'text-[var(--color-info)]'}\`} />`,
      errors: [
        { messageId: 'baseForeground', data: { token: 'warning' } },
        { messageId: 'baseForeground', data: { token: 'info' } },
      ],
    },
  ],
})
