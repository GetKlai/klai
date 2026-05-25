/**
 * KB-image URL constants - TypeScript mirror of
 * ``klai-libs/image-storage/klai_image_storage/kb_image.py``.
 *
 * SPEC-KB-IMAGES-V2-001 REQ-7: single source of truth for the kb-image
 * URL shape, mirrored here so the portal frontend (the only TS consumer)
 * doesn't hardcode the path. A drift between this module and the Python
 * KbImage is caught at unit-test time by the fixture in
 * `__tests__/kb-image-url.test.ts` - the test compares this module's
 * outputs to known-good Python-generated strings.
 *
 * Why mirror instead of fetching at runtime: the URL is needed before
 * any network call (the upload route itself), so a runtime fetch would
 * recurse. Mirror + drift-test is the standard pattern (same as how
 * Paraglide compiles i18n keys to TS modules).
 */

// Production tenant ids are numeric snowflake (e.g. "368884765035593759").
// Dev / test tenants use the kb_slug alphabet (e.g. "org-1"). Both shapes
// are accepted in the path regex below - must match KbImage._PATH_RE.
const ZITADEL_SEGMENT_RE = /([a-z0-9][a-z0-9-]{0,63}|[0-9]{1,20})/
const KB_SLUG_SEGMENT_RE = /([a-z0-9][a-z0-9-]{0,63})/
const SHA256_SEGMENT_RE = /([0-9a-f]{64})/
const EXT_SEGMENT_RE = /(jpg|png|gif|webp)/

/**
 * Build the relative POST URL for uploading a new kb-image.
 *
 * Mirror of: ``KbImage.UPLOAD_ROUTE_TEMPLATE`` (Python).
 */
export function kbImageUploadPath(kbSlug: string): string {
  return `/kb-images/${encodeURIComponent(kbSlug)}`
}

/**
 * Regex that parses a kb-image public URL into its components.
 *
 * Mirror of: ``KbImage._PATH_RE`` (Python).
 */
export const KB_IMAGE_PUBLIC_PATH_RE = new RegExp(
  '^/kb-images/' +
    ZITADEL_SEGMENT_RE.source +
    '/images/' +
    KB_SLUG_SEGMENT_RE.source +
    '/' +
    SHA256_SEGMENT_RE.source +
    '\\.' +
    EXT_SEGMENT_RE.source +
    '$',
)

/**
 * Build the relative GET URL for fetching a stored kb-image.
 *
 * Mirror of: ``KbImage.public_path`` (Python).
 */
export function kbImagePublicPath(args: {
  zitadelOrgId: string
  kbSlug: string
  sha256: string
  ext: 'jpg' | 'png' | 'gif' | 'webp'
}): string {
  return `/kb-images/${args.zitadelOrgId}/images/${args.kbSlug}/${args.sha256}.${args.ext}`
}
