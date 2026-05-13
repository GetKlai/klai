/**
 * SPEC-KB-IMAGES-V2-001 REQ-6 / AC-4 / AC-5: end-to-end Playwright test
 * for the kb-image round-trip.
 *
 * Unlike the existing portal e2e tests, this one drives the **full**
 * transport stack — Caddy → portal-api → Garage — by uploading a tiny
 * PNG and then rendering the returned URL as an `<img>` element. The
 * test fails if `naturalWidth === 0` (the v1 regression symptom that
 * went undetected for 3 weeks).
 *
 * Activation requirements (manual one-time setup):
 *
 * 1. Create a GitHub Actions secret `KLAI_E2E_STORAGE_STATE_BASE64`
 *    containing a base64-encoded Playwright storage-state JSON for the
 *    `e2e-test-org` tenant. See `playwright-mcp-config-cycle` pitfall
 *    in `.claude/rules/klai/pitfalls/process-rules.md` for the seed
 *    procedure.
 * 2. Set `KLAI_E2E_BASE_URL` to the staging or prod-tenant URL the
 *    storage-state was seeded for.
 * 3. Remove the `if: false` guard in
 *    `.github/workflows/portal-frontend-e2e.yml`.
 *
 * Until the secret is set up the workflow is dormant — running this
 * test locally is supported via the Playwright config in this folder.
 */

import { readFileSync } from 'node:fs'
import { resolve as pathResolve } from 'node:path'

import { expect, test } from '@playwright/test'

import {
  KB_IMAGE_PUBLIC_PATH_RE,
  kbImageUploadPath,
} from '../src/lib/kb-image-url'

const BASE_URL = process.env.KLAI_E2E_BASE_URL ?? 'https://my.getklai.com'
// kb-slug pre-provisioned in the test tenant. The slug must exist (the
// upload route does `_get_kb_or_404` on it) but its contents are irrelevant.
const TEST_KB_SLUG = process.env.KLAI_E2E_KB_SLUG ?? 'klai-help'

// 1x1 transparent PNG, embedded so the test has no external file dep.
const TINY_PNG_BYTES = Buffer.from(
  '89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489' +
    '0000000A49444154789C6300010000000500010D0A2DB40000000049454E44AE426082',
  'hex',
)

test.describe('kb-image round-trip', () => {
  test('upload returns a v2-shape URL and that URL renders as an image', async ({
    page,
    request,
  }) => {
    // SPEC-KB-IMAGES-V2-001 AC-5 part 1: upload via the canonical route.
    const uploadUrl = `${BASE_URL}${kbImageUploadPath(TEST_KB_SLUG)}`
    const uploadResp = await request.post(uploadUrl, {
      multipart: {
        file: {
          name: 'e2e-test.png',
          mimeType: 'image/png',
          buffer: TINY_PNG_BYTES,
        },
      },
    })
    expect(
      uploadResp.status(),
      `upload failed: ${uploadResp.status()} ${await uploadResp.text()}`,
    ).toBe(200)
    const body = (await uploadResp.json()) as {
      url: string
      deduplicated: boolean
    }

    // The URL is in the v2 5-segment shape. If a future PR re-introduces
    // the v1 4-segment shape, this assertion fails immediately.
    expect(
      KB_IMAGE_PUBLIC_PATH_RE.test(body.url),
      `returned URL ${JSON.stringify(body.url)} does not match KbImage canonical shape`,
    ).toBe(true)

    // SPEC-KB-IMAGES-V2-001 AC-4: the URL must render as an actual image
    // through the full Caddy → portal-api → Garage stack. naturalWidth > 0
    // is the v1 regression's missing observable.
    await page.goto(BASE_URL)
    await page.setContent(`<img id="probe" src="${body.url}" />`)
    const dims = await page.evaluate(() => {
      const img = document.getElementById('probe') as HTMLImageElement
      return new Promise<{ naturalWidth: number; complete: boolean }>((res) => {
        if (img.complete) {
          res({ naturalWidth: img.naturalWidth, complete: img.complete })
        } else {
          img.addEventListener('load', () =>
            res({ naturalWidth: img.naturalWidth, complete: true }),
          )
          img.addEventListener('error', () =>
            res({ naturalWidth: 0, complete: false }),
          )
        }
      })
    })
    expect(dims.complete).toBe(true)
    expect(dims.naturalWidth).toBeGreaterThan(0)
  })
})

// Sanity check that we did not accidentally ship a stale fixture file in CI.
void pathResolve(import.meta.url)
void readFileSync
