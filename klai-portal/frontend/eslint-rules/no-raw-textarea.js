/**
 * @fileoverview Forbid raw JSX `<textarea>` elements outside the portal's
 * owned UI component directory.
 *
 * Documented in `klai-portal/frontend/docs/ui-standards.md` § Component
 * Library Reference. Portal forms use `Textarea` from
 * `@/components/ui/textarea`; the native element remains the implementation
 * detail of components in `src/components/ui/`.
 *
 * There are currently zero violations - this rule is a regression guard, not
 * a cleanup. The raw textarea backlog was counted and removed before this rule
 * was enabled, so the check does not turn known debt into permanent lint noise.
 */

/**
 * @param {string} filename Absolute or project-relative filename from ESLint
 */
function isOwnedUiFile(filename) {
  const normalized = filename.replaceAll('\\', '/')
  return /(?:^|\/)src\/components\/ui(?:\/|$)/.test(normalized)
}

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow raw JSX textarea elements outside the owned UI component directory.',
    },
    schema: [],
    messages: {
      rawTextarea: 'use `Textarea` from `@/components/ui/textarea`.',
    },
  },
  create(context) {
    if (isOwnedUiFile(context.filename)) return {}

    return {
      JSXOpeningElement(node) {
        if (node.name.type !== 'JSXIdentifier' || node.name.name !== 'textarea') {
          return
        }

        context.report({ node, messageId: 'rawTextarea' })
      },
    }
  },
}
