import { expect, test } from '@playwright/test'

function snapshotName(index: number, title: string): string {
  const slug = title
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '')

  return `${String(index + 1).padStart(2, '0')}-${slug}.png`
}

test('each rendered catalog section matches its Linux baseline', async ({
  page,
}) => {
  await page.goto('/dev/ui')
  await expect(page.getByRole('heading', { level: 1, name: 'UI catalog' })).toBeVisible()
  await page.evaluate(() => document.fonts.ready.then(() => undefined))

  const sections = page.locator('section:has(> h2)')
  const sectionCount = await sections.count()
  expect(sectionCount, 'the catalog must render at least one h2 section').toBeGreaterThan(0)

  const seenNames = new Set<string>()

  for (let index = 0; index < sectionCount; index += 1) {
    const section = sections.nth(index)
    const title = (await section.locator(':scope > h2').innerText()).trim()
    const name = snapshotName(index, title)

    expect(title, `catalog section ${index + 1} needs a title`).not.toBe('')
    expect(seenNames.has(name), `catalog screenshot name must be unique: ${name}`).toBe(false)
    seenNames.add(name)

    await test.step(title, async () => {
      await expect(section, `catalog section "${title}"`).toHaveScreenshot(name, {
        animations: 'disabled',
      })
    })
  }
})
