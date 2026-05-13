/**
 * @fileoverview Forbid one route file from importing directly from another
 * route file. Routes must communicate through `-`-prefixed sibling files
 * or `_components/`-style co-located directories at the smallest-shared
 * scope. Documented in portal-frontend.md § "File organization for shared
 * types and helpers".
 *
 * Background: SPEC-PORTAL-CONNECTOR-WIZARD-EXTRACT-001 eliminated a
 * cross-route import (`edit-connector` → `add-connector`) plus two test
 * files importing from a route file. Without enforcement, the next
 * contributor can re-introduce the smell silently. This rule prevents that.
 *
 * Heuristic for "is this a route import?":
 *   - The IMPORTING file lives under `src/routes/` AND its basename does
 *     NOT start with `-` or `_` AND does NOT contain `._` (i.e., the
 *     importer is itself a route, not a colocated helper).
 *   - The IMPORT SOURCE is a relative path (`.` or `..`) AND none of its
 *     path segments start with `-` or `_` and none contain `._` (i.e.,
 *     the target is also a route, not a `-`helper or `_components/`
 *     directory or `<route>._<something>` colocation).
 *
 * Allowed:
 *   - `from './-connector-feedback'`     (dash-prefixed sibling)
 *   - `from './$kbSlug/-kb-types'`       (dash-prefixed file in subdir)
 *   - `from './new._types'`              (.<route>._<something> colocation)
 *   - `from './add-source._components/Foo'` (._components/ colocation)
 *   - `from '@/lib/foo'`                 (absolute alias, never matches)
 *
 * Forbidden:
 *   - `from './taxonomy'` from insights.tsx
 *   - `from './$kbSlug_.add-connector'` from edit-connector
 *   - `from '../sibling-route'`
 */

function isColocationOrHelperSegment(segment) {
  if (!segment) return false
  if (segment.startsWith('-')) return true
  if (segment.startsWith('_')) return true
  // <route>._<feature> or <route>._components style — TanStack co-location
  if (segment.includes('._')) return true
  return false
}

function isAllowedTarget(importSource) {
  // We only fire on relative imports.
  if (!importSource.startsWith('.')) return true
  const segments = importSource.split('/')
  for (const seg of segments) {
    if (isColocationOrHelperSegment(seg)) return true
  }
  if (importSource.includes('/__tests__/')) return true
  if (segments[segments.length - 1] === 'routeTree.gen') return true
  return false
}

function isRouteFile(filename) {
  if (!filename) return false
  const normalized = filename.replace(/\\/g, '/')
  const routesIdx = normalized.indexOf('/src/routes/')
  if (routesIdx < 0) return false
  // Walk all path segments inside src/routes/. If ANY of them is a
  // colocation/helper marker, the file is not a route — it lives in
  // a `-`-helper file, a `_components/` directory, or a `<route>._<x>`
  // co-located file/directory.
  const after = normalized.slice(routesIdx + '/src/routes/'.length)
  const segments = after.split('/')
  if (segments.length === 0) return false
  const fileBasename = segments[segments.length - 1].replace(/\.(tsx?|jsx?)$/, '')
  if (isColocationOrHelperSegment(fileBasename)) return false
  for (let i = 0; i < segments.length - 1; i++) {
    if (isColocationOrHelperSegment(segments[i])) return false
  }
  return true
}

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow cross-route imports inside src/routes/. Routes must communicate via -prefixed sibling files or _components/ directories at the smallest-shared scope.',
    },
    schema: [],
    messages: {
      crossRoute:
        "Cross-route import: route '{{from}}' must not import directly from another route ('{{to}}'). Extract the shared symbol to a -prefixed sibling file or _components/ directory at the smallest-shared scope. See portal-frontend.md § 'File organization for shared types and helpers'.",
    },
  },
  create(context) {
    const filename = context.filename ?? context.getFilename?.() ?? ''
    if (!isRouteFile(filename)) return {}

    const fromShort = filename.slice(filename.lastIndexOf('/') + 1)

    return {
      ImportDeclaration(node) {
        const source = node.source && node.source.value
        if (typeof source !== 'string') return
        if (isAllowedTarget(source)) return
        context.report({
          node,
          messageId: 'crossRoute',
          data: { from: fromShort, to: source },
        })
      },
    }
  },
}
