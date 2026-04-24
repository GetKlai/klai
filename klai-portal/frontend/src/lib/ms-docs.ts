/**
 * Shared helpers for the Microsoft 365 (ms_docs) connector wizard.
 *
 * SPEC-KB-MS-DOCS-001: a SharePoint site URL must be of the form
 *   https://{tenant}.sharepoint.com/sites/{site}
 * so the server can resolve it to a Graph site-id via
 *   GET /sites/{hostname}:/sites/{site}
 */

/**
 * Client-side validation regex for the SharePoint site URL field.
 *
 * Tenant names: lowercase letters, digits, hyphens (Microsoft allows `-` but not
 * subdomains-of-subdomains on the sharepoint.com apex). The site segment may
 * contain anything that is URL-safe except `/`. A trailing slash is tolerated.
 *
 * Keep in sync with the server-side `_parse_site_url` split in
 * `klai-connector/app/adapters/ms_docs.py` — that function expects exactly the
 * `hostname` + `/sites/{site}` shape this regex accepts.
 */
export const MS_SITE_URL_PATTERN =
  /^https:\/\/[a-z0-9-]+\.sharepoint\.com\/sites\/[^/]+\/?$/
