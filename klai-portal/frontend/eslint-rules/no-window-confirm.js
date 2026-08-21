/**
 * @fileoverview Forbid `window.confirm` / bare `confirm()` / `window.alert`
 * / `window.prompt` in portal code.
 *
 * Documented in `klai-portal/frontend/AGENTS.md` ("Do not use
 * `window.confirm`") and `.claude/rules/klai/design/portal-patterns.md`
 * § Minimum non-negotiables. The portal has owned components for this:
 * `inline-delete-confirm` for destructive confirmation inside a row, and
 * `alert-dialog` for destructive actions outside rows.
 *
 * There are currently zero violations - this rule is a regression guard, not
 * a cleanup. It is cheap precisely because the codebase is already clean: a
 * documented rule with no enforcement only holds until the next contributor
 * (human or agent) does not read the doc.
 *
 * Bare `confirm(...)` is matched only when the name is not locally bound, so
 * a helper or import named `confirm` is not reported.
 */

const FORBIDDEN = new Set(['confirm', 'alert', 'prompt'])

const REPLACEMENT = {
  confirm: '`InlineDeleteConfirm` for row-level destructive actions, or `AlertDialog` outside rows',
  alert: '`toast()` from `sonner`, or an inline `Alert`',
  prompt: 'a real form or dialog built from `components/ui/`',
}

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow blocking browser dialogs (confirm/alert/prompt). Use the portal UI components instead.',
    },
    schema: [],
    messages: {
      blockingDialog:
        "`{{call}}` is forbidden in the portal - it is an unstyled, unlocalized, blocking browser dialog. Use {{replacement}}. See klai-portal/frontend/docs/ui-standards.md.",
    },
  },
  create(context) {
    /**
     * True when the identifier resolves to a real binding in scope, i.e. it
     * is the app's own symbol rather than the browser global.
     * @param {any} node Identifier node
     */
    function isLocallyBound(node) {
      const scope = context.sourceCode.getScope(node)
      for (const ref of scope.references) {
        if (ref.identifier === node) return ref.resolved !== null
      }
      return false
    }

    return {
      CallExpression(node) {
        const callee = node.callee

        // window.confirm(...) / globalThis.alert(...)
        if (
          callee.type === 'MemberExpression' &&
          !callee.computed &&
          callee.object.type === 'Identifier' &&
          (callee.object.name === 'window' || callee.object.name === 'globalThis') &&
          callee.property.type === 'Identifier' &&
          FORBIDDEN.has(callee.property.name)
        ) {
          const name = callee.property.name
          context.report({
            node,
            messageId: 'blockingDialog',
            data: { call: `${callee.object.name}.${name}`, replacement: REPLACEMENT[name] },
          })
          return
        }

        // Bare confirm(...) - only when it is the global, not a local symbol.
        if (
          callee.type === 'Identifier' &&
          FORBIDDEN.has(callee.name) &&
          !isLocallyBound(callee)
        ) {
          context.report({
            node,
            messageId: 'blockingDialog',
            data: { call: `${callee.name}()`, replacement: REPLACEMENT[callee.name] },
          })
        }
      },
    }
  },
}
