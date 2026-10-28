/**
 * J07 - The crawler wizard's sign-in step detects the sign-in chain of a site
 * and asks for cookies per host.
 *
 * The chain detection is a portal route that asks knowledge-ingest to follow
 * the site's redirects (`/connectors/auth-chain`). Unit tests mock that call
 * on both sides; this journey proves the deployed portal, its backend and
 * knowledge-ingest agree on it, which is the part that broke silently before:
 * a wizard that looked fine while the keep-alive never held the cookies of
 * the host the site signs in through.
 *
 * A public site has no chain, so the step offers exactly one paste box, the
 * one for the site's own host. Nothing is created: the journey stops on the
 * sign-in step and never submits the wizard.
 */
import { test, expect, type Page } from '@playwright/test'
import { e2ePrefix } from './_lib/fixtures'

const SITE = 'https://example.com'
const SITE_HOST = 'example.com'

interface KnowledgeBase {
  slug: string
}

async function resolveKbSlug(page: Page): Promise<string> {
  const data = await page.evaluate(async () => {
    const res = await fetch('/api/app/knowledge-bases', { credentials: 'include' })
    return (await res.json()) as { knowledge_bases: KnowledgeBase[] }
  })
  const personal = data.knowledge_bases.find((kb) => kb.slug.startsWith('personal-'))
  const slug = personal?.slug ?? data.knowledge_bases[0]?.slug
  if (!slug) throw new Error('no knowledge base available for the e2e user')
  return slug
}

test.describe('J07 - crawler wizard sign-in chain', () => {
  test('the sign-in step detects the chain and offers a paste box per host', async ({ page }) => {
    await page.goto('/app')
    const kbSlug = await resolveKbSlug(page)
    await page.goto(`/app/knowledge/${kbSlug}/add-connector`)

    await page.getByRole('button', { name: /^Website$/ }).click()
    await page.locator('#wc-name').fill(`${e2ePrefix()}auth-chain`)
    await page.locator('#wc-base-url').fill(SITE)
    await page.getByRole('button', { name: /^(Next|Volgende)$/ }).click()

    await page.getByRole('button', { name: /^(Login required|Login vereist)$/ }).click()
    const detection = page.waitForResponse(
      (res) => res.url().includes('/connectors/auth-chain') && res.request().method() === 'POST',
    )
    await page.getByRole('button', { name: /^(Next|Volgende)$/ }).click()

    const response = await detection
    expect(response.status(), await response.text()).toBe(200)
    const body = (await response.json()) as { hosts: string[] }
    expect(body.hosts).toEqual([])

    const pasteBoxes = page.getByRole('textbox', { name: /Cookie-Editor/ })
    await expect(pasteBoxes).toHaveCount(1)
    await expect(pasteBoxes.first()).toHaveAccessibleName(new RegExp(SITE_HOST))
  })
})
