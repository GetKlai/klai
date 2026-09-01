/**
 * @fileoverview Forbid raw textual JSX `<input>` elements outside the
 * portal's owned UI component directory.
 *
 * Text-like, absent, and dynamic types must use an owned component so labels,
 * styling, and behavior stay coupled to the portal design contract. Native
 * non-text controls remain valid. The widget chat surface has two deliberate,
 * inline-disabled exceptions for its unsupported dark-mode variant.
 */

const TEXT_INPUT_TYPES = new Set([
  'text',
  'email',
  'password',
  'search',
  'url',
  'tel',
])

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
        'Disallow raw textual JSX input elements outside the owned UI component directory.',
    },
    schema: [],
    messages: {
      rawTextInput: 'use `Input` or another owned control from `@/components/ui/`.',
    },
  },
  create(context) {
    if (isOwnedUiFile(context.filename)) return {}

    return {
      JSXOpeningElement(node) {
        if (node.name.type !== 'JSXIdentifier' || node.name.name !== 'input') {
          return
        }

        const typeAttribute = node.attributes.find(
          (attribute) =>
            attribute.type === 'JSXAttribute' &&
            attribute.name.type === 'JSXIdentifier' &&
            attribute.name.name === 'type',
        )

        if (!typeAttribute || !typeAttribute.value) {
          context.report({ node, messageId: 'rawTextInput' })
          return
        }

        if (typeAttribute.value.type !== 'Literal') {
          context.report({ node, messageId: 'rawTextInput' })
          return
        }

        const type = String(typeAttribute.value.value).toLowerCase()
        if (TEXT_INPUT_TYPES.has(type)) {
          context.report({ node, messageId: 'rawTextInput' })
        }
      },
    }
  },
}
