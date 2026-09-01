/**
 * Tests for `klai/no-raw-text-input`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 */
import { RuleTester } from 'eslint'
import rule from '../no-raw-text-input.js'

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
})

ruleTester.run('klai/no-raw-text-input', rule, {
  valid: [
    {
      code: `import { Input } from '@/components/ui/input'\nconst field = <Input type="email" />`,
      filename: '/repo/src/routes/login.tsx',
    },
    {
      code: `const field = <input type="text" />`,
      filename: '/repo/src/components/ui/input.tsx',
    },
    {
      code: `const controls = <><input type="checkbox" /><input type="radio" /><input type="file" /><input type="hidden" /></>`,
      filename: '/repo/src/routes/example.tsx',
    },
    {
      code: `const field = <input type="number" />`,
      filename: '/repo/src/routes/example.tsx',
    },
  ],
  invalid: [
    ...['text', 'email', 'password', 'search', 'url', 'tel'].map((type) => ({
      code: `const field = <input type="${type}" />`,
      filename: '/repo/src/routes/example.tsx',
      errors: [{ messageId: 'rawTextInput' }],
    })),
    {
      code: `const field = <input />`,
      filename: '/repo/src/routes/example.tsx',
      errors: [{ messageId: 'rawTextInput' }],
    },
    {
      code: `const type = 'email'\nconst field = <input type={type} />`,
      filename: '/repo/src/routes/example.tsx',
      errors: [
        {
          message: 'use `Input` or another owned control from `@/components/ui/`.',
        },
      ],
    },
  ],
})
