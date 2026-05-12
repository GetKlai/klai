/**
 * @fileoverview Forbid hand-written queryKey array literals that start with
 * any key registered in `-kb-query-keys.ts`.
 *
 * The KB Sources tab regressed once because a `useMutation` invalidated
 * `kb-items` directly without touching `kb-sources`, so the Sources list
 * showed stale rows after add (SPEC-PORTAL-SOURCES-RENAME-001 motivation
 * paragraph "queryKey registry is a one-time fix without enforcement").
 *
 * This rule treats `kbQueryKeys.foo(...)` as the only supported way to
 * construct these keys. Any literal `queryKey: ['kb-sources', ...]` (or
 * other registered prefix) outside `-kb-query-keys.ts` itself is rejected.
 *
 * Implementation:
 *   1. Locate every `Property` whose `key.name === 'queryKey'`.
 *   2. If the value is an `ArrayExpression` AND the first element is a
 *      string `Literal` matching a registered prefix → report.
 *   3. The file `-kb-query-keys.ts` is exempt (it defines the helper).
 */

const REGISTERED_PREFIXES = [
  'kb-sources',
  'source-content',
  'kb-items',
  'personal-knowledge',
  'app-knowledge-bases-stats-summary',
  'kb-connectors-portal',
  'docs-tree',
  'docs-page-index',
  'app-knowledge-base',
]

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow direct queryKey array literals for keys registered in -kb-query-keys.ts. Use kbQueryKeys.* instead.',
    },
    schema: [],
    messages: {
      direct:
        "queryKey '{{prefix}}' must be constructed via kbQueryKeys.* helper from '-kb-query-keys.ts', not as an inline array literal.",
    },
  },
  create(context) {
    const filename = context.filename ?? context.getFilename?.() ?? ''
    if (filename.endsWith('-kb-query-keys.ts')) {
      // The helper file defines the literals — exempt.
      return {}
    }

    return {
      Property(node) {
        if (
          !node.key ||
          (node.key.type !== 'Identifier' && node.key.type !== 'Literal') ||
          (node.key.type === 'Identifier' && node.key.name !== 'queryKey') ||
          (node.key.type === 'Literal' && node.key.value !== 'queryKey')
        ) {
          return
        }
        const value = node.value
        if (!value || value.type !== 'ArrayExpression') return
        const first = value.elements[0]
        if (!first || first.type !== 'Literal' || typeof first.value !== 'string') return
        if (REGISTERED_PREFIXES.includes(first.value)) {
          context.report({
            node: value,
            messageId: 'direct',
            data: { prefix: first.value },
          })
        }
      },
    }
  },
}
