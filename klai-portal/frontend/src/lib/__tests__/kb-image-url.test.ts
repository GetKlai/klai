/**
 * SPEC-KB-IMAGES-V2-001 REQ-7 drift test: outputs of `lib/kb-image-url.ts`
 * are compared to fixture strings generated from the Python KbImage
 * value-class. A drift between Python and TS produces a unit-test
 * failure, not a silent runtime 404.
 *
 * Fixture strings below are exactly what
 * `klai_image_storage.kb_image.KbImage(...).public_path` returns for
 * the same inputs. If you change the URL shape in Python, update both
 * the regex on the TS side AND the fixtures here; the test will fail
 * fast either way.
 */

import { describe, expect, it } from 'vitest'

import {
  KB_IMAGE_PUBLIC_PATH_RE,
  kbImagePublicPath,
  kbImageUploadPath,
} from '@/lib/kb-image-url'

describe('kbImageUploadPath', () => {
  it('matches KbImage.UPLOAD_ROUTE_TEMPLATE for a real kb-slug', () => {
    expect(kbImageUploadPath('support')).toBe('/kb-images/support')
  })

  it('url-encodes the kb_slug (defensive - production slugs are already url-safe)', () => {
    // Defensive: production kb_slugs match /^[a-z0-9][a-z0-9-]{0,63}$/ so
    // encodeURIComponent is a no-op. But if someone ever passes a slug
    // with spaces or unicode, the encoded form must not break the route
    // matcher upstream.
    expect(kbImageUploadPath('a b')).toBe('/kb-images/a%20b')
  })
})

describe('kbImagePublicPath', () => {
  it('matches Python KbImage.public_path for Voys connector image', () => {
    const got = kbImagePublicPath({
      zitadelOrgId: '368884765035593759',
      kbSlug: 'support',
      sha256: 'dae543ab51b40c9611d14c96e1f72bbd53a1ecdc782c192fbc2ab6d0e6127dd9',
      ext: 'png',
    })
    // Fixture: the exact path served by portal-api for Voys' first
    // connector-uploaded support image (verified live 2026-05-12).
    expect(got).toBe(
      '/kb-images/368884765035593759/images/support/dae543ab51b40c9611d14c96e1f72bbd53a1ecdc782c192fbc2ab6d0e6127dd9.png',
    )
  })

  it('matches Python KbImage.public_path for Mark docs-editor upload', () => {
    const got = kbImagePublicPath({
      zitadelOrgId: '362757920133283846',
      kbSlug: 'klai-help',
      sha256: '71e67cdc4b7451885b314b848583f5a66838b1952d753f5d133b5d98dc375f5b',
      ext: 'png',
    })
    expect(got).toBe(
      '/kb-images/362757920133283846/images/klai-help/71e67cdc4b7451885b314b848583f5a66838b1952d753f5d133b5d98dc375f5b.png',
    )
  })
})

describe('KB_IMAGE_PUBLIC_PATH_RE', () => {
  it('accepts real production paths (round-trip with Python regex)', () => {
    const paths = [
      '/kb-images/368884765035593759/images/support/dae543ab51b40c9611d14c96e1f72bbd53a1ecdc782c192fbc2ab6d0e6127dd9.png',
      '/kb-images/362757920133283846/images/klai-help/71e67cdc4b7451885b314b848583f5a66838b1952d753f5d133b5d98dc375f5b.png',
      '/kb-images/org-1/images/support/' + 'a'.repeat(64) + '.jpg',
    ]
    for (const p of paths) {
      expect(KB_IMAGE_PUBLIC_PATH_RE.test(p)).toBe(true)
    }
  })

  it('rejects the v1 4-segment shape and other near-misses', () => {
    const bad = [
      '/kb-images/368884765035593759/support/abc.png', // 4 segments (old v1 shape)
      '/kb-images/foo/bar/baz.png', // missing 'images' literal
      '/kb-images/368884765035593759/imgs/support/' + 'a'.repeat(64) + '.png', // wrong literal
      '/kb-images/368884765035593759/images/support/short.png', // sha not 64 chars
      '/kb-images/368884765035593759/images/support/' + 'a'.repeat(64) + '.svg', // disallowed ext
      '/kb-images/368884765035593759/images/BadSlug/' + 'a'.repeat(64) + '.png', // caps in slug
      '/wrong-prefix/368884765035593759/images/support/' + 'a'.repeat(64) + '.png',
    ]
    for (const p of bad) {
      expect(KB_IMAGE_PUBLIC_PATH_RE.test(p)).toBe(false)
    }
  })
})
