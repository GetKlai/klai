/**
 * Tests for `klai/no-direct-kb-querykey` (SPEC-PORTAL-SOURCES-RENAME-001 REQ-4).
 *
 * Run via vitest. RuleTester from `eslint` registers its own describe/it
 * blocks against vitest's globals, so it MUST be called at the top level
 * of the file - never inside an `it()`.
 */
import { RuleTester } from 'eslint'
import rule from '../no-direct-kb-querykey.js'

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

ruleTester.run('klai/no-direct-kb-querykey', rule, {
  valid: [
    // Helper call - the supported pattern.
    {
      filename: 'src/routes/app/knowledge/$kbSlug/sources.tsx',
      code: `useQuery({ queryKey: kbQueryKeys.sources(kbSlug) })`,
    },
    // Inside the registry file itself - exempt.
    {
      filename: 'src/lib/kb-query-keys.ts',
      code: `const k = { queryKey: ['kb-sources', kbSlug] }`,
    },
    // Unrelated literal key - out of scope for this rule.
    {
      filename: 'src/routes/foo.tsx',
      code: `useQuery({ queryKey: ['admin-users', orgId] })`,
    },
    // Non-array queryKey value - out of scope (rule only fires on array literals).
    {
      filename: 'src/routes/foo.tsx',
      code: `useQuery({ queryKey: someKey })`,
    },
  ],
  invalid: [
    {
      filename: 'src/routes/app/knowledge/$kbSlug/sources.tsx',
      code: `queryClient.invalidateQueries({ queryKey: ['kb-sources', kbSlug] })`,
      errors: [{ messageId: 'direct', data: { prefix: 'kb-sources' } }],
    },
    {
      filename: 'src/routes/app/knowledge/index.tsx',
      code: `useQuery({ queryKey: ['app-knowledge-bases-stats-summary'] })`,
      errors: [{ messageId: 'direct', data: { prefix: 'app-knowledge-bases-stats-summary' } }],
    },
    {
      filename: 'src/routes/app/connectors.tsx',
      code: `queryClient.invalidateQueries({ queryKey: ['kb-connectors-portal', slug] })`,
      errors: [{ messageId: 'direct', data: { prefix: 'kb-connectors-portal' } }],
    },
    {
      filename: 'src/routes/app/docs.tsx',
      code: `useQuery({ queryKey: ['docs-tree', orgSlug, kbSlug] })`,
      errors: [{ messageId: 'direct', data: { prefix: 'docs-tree' } }],
    },
  ],
})
