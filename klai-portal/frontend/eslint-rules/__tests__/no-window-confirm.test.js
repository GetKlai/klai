/**
 * Tests for `klai/no-window-confirm`.
 *
 * RuleTester from `eslint` registers its own describe/it blocks against
 * vitest's globals and MUST be called at module top-level.
 */
import { RuleTester } from 'eslint'
import rule from '../no-window-confirm.js'

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: 'module' },
})

ruleTester.run('klai/no-window-confirm', rule, {
  valid: [
    // The i18n message keys that make a naive grep look like a violation.
    `const label = m.kb_sources_row_delete_confirm({ name })`,
    `const step = m.admin_users_wizard_step_confirm()`,
    // A locally-defined helper named `confirm` is the app's own symbol.
    `function confirm(x) { return x }\nconfirm(1)`,
    // An imported symbol named `confirm` likewise resolves locally.
    `import { confirm } from './dialogs'\nconfirm({ title: 'x' })`,
    // A method named confirm on something that is not window.
    `dialog.confirm('are you sure')`,
    // Shadowed by a parameter.
    `function run(confirm) { confirm() }`,
  ],
  invalid: [
    {
      code: `window.confirm('Delete this?')`,
      errors: [{ messageId: 'blockingDialog' }],
    },
    {
      code: `if (window.confirm(m.delete_prompt())) remove()`,
      errors: [{ messageId: 'blockingDialog' }],
    },
    {
      code: `confirm('Delete this?')`,
      errors: [{ messageId: 'blockingDialog' }],
    },
    {
      code: `window.alert('saved')`,
      errors: [{ messageId: 'blockingDialog' }],
    },
    {
      code: `const name = window.prompt('Name?')`,
      errors: [{ messageId: 'blockingDialog' }],
    },
    {
      code: `globalThis.confirm('x')`,
      errors: [{ messageId: 'blockingDialog' }],
    },
  ],
})
