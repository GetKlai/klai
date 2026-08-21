/**
 * @fileoverview Forbid Tailwind arbitrary-value hex colours in `className`
 * when the hex is a Klai design token. Documented in
 * `.claude/rules/klai/design/tokens.md` § Anti-patterns ("Hardcoded hex in
 * any .tsx/.html/.css/.j2 - use var(--color-rl-accent) etc.").
 *
 * Background: the design language was prose-only. A repo scan found ~25
 * arbitrary-value brand hexes concentrated in `WidgetChatSurface.tsx`
 * (`bg-[#191918]`, `text-[#fffef2]/55`) - exactly the drift the anti-pattern
 * warns about, with nothing to catch it. The `PreToolUse` hook prints a
 * reminder but always exits 0, so it cannot fail anything.
 *
 * The rule deliberately fires ONLY when the hex equals a token value defined
 * in `src/index.css` (see `klai-tokens.js`). That precision is what keeps it
 * usable, because plenty of hardcoded hex in this codebase is legitimate:
 *
 *   - Third-party brand marks: Google `#4285F4`, Microsoft `#F25022`,
 *     HubSpot `#ff7a59`. These are other companies' colours; they are not
 *     ours to tokenize and they never match a Klai token.
 *   - Tenant-configurable widget colours (`primary_color: '#fcaa2d'`) are
 *     runtime DATA sent to an embedded surface, not styling. A CSS var would
 *     not resolve there. Those live outside `className` and so never match.
 *
 * Scope is the `className`/`class` JSX attribute only - including template
 * literals and conditional expressions nested inside it, which is where the
 * real violations live.
 */

import { buildHexIndex, suggestionFor } from './klai-tokens.js'

// `bg-[#191918]`, `text-[#fffef2]/55`, `border-[#e3e2d8]`, `[color:#191918]`.
const ARBITRARY_HEX = /\[(?:[a-z-]+:)?(#[0-9a-fA-F]{3,8})\]/g

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow hardcoded Klai brand hex values in Tailwind arbitrary className values. Reference the design token instead.',
    },
    schema: [
      {
        type: 'object',
        properties: { cssPath: { type: 'string' } },
        additionalProperties: false,
      },
    ],
    messages: {
      hardcoded:
        "Hardcoded brand hex '{{hex}}' in className is the value of {{tokens}}. Use `{{suggestion}}` instead (e.g. `bg-[var(--color-rl-dark)]`), so a token change propagates. See .claude/rules/klai/design/tokens.md.",
    },
  },
  create(context) {
    const options = context.options?.[0] ?? {}
    const hexIndex = buildHexIndex(options.cssPath)
    if (hexIndex.size === 0) return {}

    /**
     * Report every tokenized hex inside one string of class names.
     * @param {import('eslint').Rule.Node} node node to anchor the report on
     * @param {string} value raw class string
     */
    function checkClassString(node, value) {
      if (typeof value !== 'string' || !value.includes('#')) return

      for (const [, hex] of value.matchAll(ARBITRARY_HEX)) {
        // Normalize to the opaque 6-digit base so `#191918cc` and the
        // shorthand `#fff` both resolve against the index.
        const expanded =
          hex.length === 4
            ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`
            : hex
        const base = expanded.slice(0, 7).toLowerCase()

        const tokens = hexIndex.get(base)
        if (!tokens) continue

        context.report({
          node,
          messageId: 'hardcoded',
          data: {
            hex,
            tokens: tokens.map((t) => `--${t}`).join(' / '),
            suggestion: suggestionFor(tokens),
          },
        })
      }
    }

    /**
     * Walk a className value, descending through the expression forms the
     * portal actually uses: plain strings, template literals, `cn(...)`
     * arguments, ternaries, and `&&` chains.
     * @param {any} node
     */
    function walkClassValue(node) {
      if (!node) return

      switch (node.type) {
        case 'Literal':
          checkClassString(node, node.value)
          break
        case 'TemplateLiteral':
          for (const quasi of node.quasis) {
            checkClassString(quasi, quasi.value.raw)
          }
          for (const expr of node.expressions) walkClassValue(expr)
          break
        case 'JSXExpressionContainer':
          walkClassValue(node.expression)
          break
        case 'ConditionalExpression':
          walkClassValue(node.consequent)
          walkClassValue(node.alternate)
          break
        case 'LogicalExpression':
        case 'BinaryExpression':
          walkClassValue(node.left)
          walkClassValue(node.right)
          break
        case 'CallExpression':
          for (const arg of node.arguments) walkClassValue(arg)
          break
        case 'ArrayExpression':
          for (const el of node.elements) walkClassValue(el)
          break
        case 'ObjectExpression':
          for (const prop of node.properties) {
            if (prop.type === 'Property') walkClassValue(prop.key)
          }
          break
        default:
          break
      }
    }

    return {
      JSXAttribute(node) {
        const name = node.name && node.name.name
        if (name !== 'className' && name !== 'class') return
        walkClassValue(node.value)
      },
    }
  },
}
