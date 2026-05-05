import { defaultSchema, type Options as SanitizeSchema } from "rehype-sanitize";

/**
 * Sanitize schema for tenant-authored markdown content.
 *
 * Source: GitHub-flavoured `defaultSchema` from rehype-sanitize, extended to
 * allow the `data-wikilink` and `data-title` attributes on <a> elements so
 * that the `resolveWikilinks()` pre-processor's output survives sanitization.
 * Note: hast-util-sanitize uses camelCase property names, so `data-wikilink`
 * is encoded as `dataWikilink`.
 *
 * SPEC-CODEBASE-AUDIT-001 (TP-FE-1): closes the stored XSS vector where
 * tenant editors could inject <script>, <iframe>, on*-handlers, or
 * `javascript:` hrefs through Gitea-hosted markdown.
 */
export const sanitizeSchema: SanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...(defaultSchema.attributes ?? {}),
    a: [
      ...(defaultSchema.attributes?.a ?? []),
      "dataWikilink",
      "dataTitle",
    ],
  },
};
