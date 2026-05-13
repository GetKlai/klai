/**
 * SPEC-PORTAL-EXTENSIONS-UNIFY-001 polish — frontend i18n for extension keys.
 *
 * The backend returns only feature keys (language-agnostic). This helper maps
 * each key to its Paraglide label + description, keeping NL/EN switching
 * client-side without a server round-trip.
 *
 * Adding a new key requires three matching edits:
 *  1. `app/core/extensions_registry.py::KNOWN_FEATURES` (+ PRODUCT_FEATURES if relevant).
 *  2. `admin_extension_<key>_label` + `admin_extension_<key>_description` Paraglide
 *     messages in `klai-portal/frontend/messages/{nl,en}.json`.
 *  3. The switch arm below.
 *
 * The backend drift-guard test
 * `test_features_derive::test_known_features_consistent_with_feature_min_profile`
 * fails CI if (1) gets out of sync. (2) and (3) surface as missing-message
 * type errors at frontend build time.
 */

import * as m from '@/paraglide/messages'

export function extensionLabel(key: string): string {
  switch (key) {
    case 'partner_api':
      return m.admin_extension_partner_api_label()
    case 'widgets':
      return m.admin_extension_widgets_label()
    case 'custom_mcps':
      return m.admin_extension_custom_mcps_label()
    case 'scribe':
      return m.admin_extension_scribe_label()
    case 'docs':
      return m.admin_extension_docs_label()
    default:
      return key
  }
}

export function extensionDescription(key: string): string {
  switch (key) {
    case 'partner_api':
      return m.admin_extension_partner_api_description()
    case 'widgets':
      return m.admin_extension_widgets_description()
    case 'custom_mcps':
      return m.admin_extension_custom_mcps_description()
    case 'scribe':
      return m.admin_extension_scribe_description()
    case 'docs':
      return m.admin_extension_docs_description()
    default:
      return ''
  }
}
