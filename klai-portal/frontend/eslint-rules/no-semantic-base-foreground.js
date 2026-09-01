/**
 * @fileoverview Forbid semantic base colour tokens as text foregrounds.
 *
 * The portal's semantic base tokens are for fills and borders. On light
 * surfaces, success, warning and info foregrounds need their darker `-text`
 * variants. The destructive base token is deliberately exempt: it clears AA
 * and the Colors contract keeps existing destructive foregrounds valid.
 *
 * Scope is Tailwind arbitrary-value `text-` utilities in `className`/`class`.
 * Other utilities such as `bg-`, `border-` and `ring-` remain valid.
 */

const SEMANTIC_BASE_FOREGROUND =
  /(?:^|[\s:])!?text-\[var\(--color-(success|warning|info)\)\]/g

/** @type {import('eslint').Rule.RuleModule} */
export default {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow success, warning and info base tokens in Tailwind text utilities. Use the matching -text token.',
    },
    schema: [],
    messages: {
      baseForeground:
        'The semantic base token `--color-{{token}}` is for fills and borders. Use `text-[var(--color-{{token}}-text)]` for a foreground on light surfaces.',
    },
  },
  create(context) {
    /**
     * Report every semantic base foreground inside one string of classes.
     * @param {import('eslint').Rule.Node} node node to anchor the report on
     * @param {string} value raw class string
     */
    function checkClassString(node, value) {
      if (typeof value !== 'string' || !value.includes('--color-')) return

      for (const match of value.matchAll(SEMANTIC_BASE_FOREGROUND)) {
        context.report({
          node,
          messageId: 'baseForeground',
          data: { token: match[1] },
        })
      }
    }

    /**
     * Walk the expression forms used for portal class names.
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
          for (const expression of node.expressions) walkClassValue(expression)
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
          for (const argument of node.arguments) walkClassValue(argument)
          break
        case 'ArrayExpression':
          for (const element of node.elements) walkClassValue(element)
          break
        case 'ObjectExpression':
          for (const property of node.properties) {
            if (property.type === 'Property') walkClassValue(property.key)
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
