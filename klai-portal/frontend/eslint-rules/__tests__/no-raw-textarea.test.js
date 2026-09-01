/**
 * Tests for `klai/no-raw-textarea`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 */
import { RuleTester } from 'eslint'
import rule from '../no-raw-textarea.js'

const ruleTester = new RuleTester({
  languageOptions: {
    ecmaVersion: 2022,
    sourceType: 'module',
    parserOptions: { ecmaFeatures: { jsx: true } },
  },
})

ruleTester.run('klai/no-raw-textarea', rule, {
  valid: [
    {
      code: `import { Textarea } from '@/components/ui/textarea'\nconst field = <Textarea />`,
      filename: '/repo/src/routes/example.tsx',
    },
    {
      code: `const field = <textarea />`,
      filename: '/repo/src/components/ui/textarea.tsx',
    },
    {
      code: `const field = <input />`,
      filename: '/repo/src/routes/login.tsx',
    },
  ],
  invalid: [
    {
      code: `const field = <textarea />`,
      filename: '/repo/src/routes/example.tsx',
      errors: [{ messageId: 'rawTextarea' }],
    },
    {
      code: `const field = <textarea rows={3}></textarea>`,
      filename: '/repo/src/routes/admin/example.tsx',
      errors: [
        {
          message: 'use `Textarea` from `@/components/ui/textarea`.',
        },
      ],
    },
  ],
})
