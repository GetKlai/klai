/**
 * Tests for `klai/no-cross-route-import`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 */
import { RuleTester } from 'eslint'
import rule from '../no-cross-route-import.js'

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

ruleTester.run('klai/no-cross-route-import', rule, {
  valid: [
    // Dash-prefixed sibling — the canonical helper pattern.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug_.add-connector.tsx',
      code: `import { AuthProbeFeedback } from './-connector-feedback'`,
    },
    // Dash-prefixed file in a sibling directory — cross-directory but to a helper.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug_.add-connector.tsx',
      code: `import { CookieRow } from './$kbSlug/-kb-types'`,
    },
    // Colocation: <route>._<types> sibling file (TanStack ignores).
    {
      filename: '/repo/src/routes/app/knowledge/new.tsx',
      code: `import { WizardData } from './new._types'`,
    },
    // Colocation: <route>._components/ directory.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug_.add-source.tsx',
      code: `import { SourceTypeGrid } from './$kbSlug_.add-source._components/SourceTypeGrid'`,
    },
    // Absolute alias — never a relative cross-route concern.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/sources.tsx',
      code: `import { apiFetch } from '@/lib/apiFetch'`,
    },
    // Helper file ITSELF (importer is dash-prefixed): rule does not run.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/-kb-helpers.tsx',
      code: `import { Foo } from './sources'`,
    },
    // _components colocated file importing from sibling: rule does not run on importer.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug_.add-source._components/Foo.tsx',
      code: `import { Bar } from './Bar'`,
    },
    // File outside src/routes/: rule does not run.
    {
      filename: '/repo/src/components/ui/Foo.tsx',
      code: `import { Bar } from './Bar'`,
    },
    // routeTree.gen is the codegen consumer — explicitly allowed.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/sources.tsx',
      code: `import { Route } from './routeTree.gen'`,
    },
  ],
  invalid: [
    // The exact smell SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 eliminated.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug_.edit-connector.$connectorId.tsx',
      code: `import { AuthProbeFeedback } from './$kbSlug_.add-connector'`,
      errors: [
        {
          messageId: 'crossRoute',
          data: {
            from: '$kbSlug_.edit-connector.$connectorId.tsx',
            to: './$kbSlug_.add-connector',
          },
        },
      ],
    },
    // F-S1 in the SPEC's follow-ups — insights.tsx imports two sibling routes.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/insights.tsx',
      code: `import { TaxonomyTab } from './taxonomy'`,
      errors: [
        {
          messageId: 'crossRoute',
          data: { from: 'insights.tsx', to: './taxonomy' },
        },
      ],
    },
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/insights.tsx',
      code: `import { KBOverviewSections } from './overview'`,
      errors: [
        {
          messageId: 'crossRoute',
          data: { from: 'insights.tsx', to: './overview' },
        },
      ],
    },
    // Up-and-over cross-route: parent dir's sibling.
    {
      filename: '/repo/src/routes/app/knowledge/$kbSlug/sources.tsx',
      code: `import { Foo } from '../new'`,
      errors: [
        {
          messageId: 'crossRoute',
          data: { from: 'sources.tsx', to: '../new' },
        },
      ],
    },
  ],
})
