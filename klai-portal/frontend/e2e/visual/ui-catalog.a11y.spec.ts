import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

type AxeResults = Awaited<ReturnType<AxeBuilder['analyze']>>
type AxeNode = AxeResults['violations'][number]['nodes'][number]

type CatalogFinding = {
  rule: string
  section: string
  target: string
  help: string
}

type DocumentedException = Pick<CatalogFinding, 'rule' | 'section' | 'target'> & {
  reason: string
}

/**
 * Existing component debt remains visible here until it is fixed. Each entry
 * identifies one exact axe node and needs an honest reason. The reverse guard
 * below fails as soon as the matching violation stops occurring.
 */
const DOCUMENTED_EXCEPTIONS: DocumentedException[] = []

function targetName(node: AxeNode): string {
  return node.target
    .map((part) => Array.isArray(part) ? part.join(' >> ') : part)
    .join(' >>> ')
}

function findingKey(finding: Pick<CatalogFinding, 'rule' | 'section' | 'target'>): string {
  return `${finding.rule}\n${finding.section}\n${finding.target}`
}

function hasRealReason(reason: string): boolean {
  const trimmed = reason.trim()
  return trimmed.length >= 20 && !/^(todo|tbd|placeholder)/i.test(trimmed)
}

test('the rendered catalog meets WCAG 2.1 A and AA', async ({ page }) => {
  await page.goto('/dev/ui')
  await expect(page.getByRole('heading', { level: 1, name: 'UI catalog' })).toBeVisible()

  const sectionNames = await page.locator('section:has(> h2)').evaluateAll((sections) =>
    sections.map((section, index) => {
      section.dataset.catalogSection = String(index)
      return section.querySelector(':scope > h2')?.textContent?.trim() ?? ''
    }),
  )

  expect(sectionNames.length, 'the catalog must render at least one h2 section').toBeGreaterThan(0)
  expect(sectionNames, 'every catalog section needs a title').not.toContain('')
  expect(new Set(sectionNames).size, 'catalog section titles must be unique').toBe(sectionNames.length)

  // One whole-page scan. Findings are attributed to their closest runtime-
  // derived catalog section afterwards; axe is not rerun section by section.
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()

  const findings: CatalogFinding[] = []
  for (const violation of results.violations) {
    for (const node of violation.nodes) {
      const target = targetName(node)
      const firstSelector = node.target[0]
      expect(
        typeof firstSelector,
        `axe target must resolve in the catalog document: ${target}`,
      ).toBe('string')

      const sectionIndex = await page.locator(firstSelector as string).first().evaluate((element) =>
        element.closest<HTMLElement>('section[data-catalog-section]')?.dataset.catalogSection,
      )
      const section = sectionIndex === undefined
        ? 'Catalog page'
        : sectionNames[Number(sectionIndex)]

      findings.push({
        rule: violation.id,
        section,
        target,
        help: violation.help,
      })
    }
  }

  const findingsByKey = new Map(findings.map((finding) => [findingKey(finding), finding]))
  const exceptionsByKey = new Map(
    DOCUMENTED_EXCEPTIONS.map((exception) => [findingKey(exception), exception]),
  )

  expect(
    exceptionsByKey.size,
    'documented axe exceptions must identify unique rule/section/target findings',
  ).toBe(DOCUMENTED_EXCEPTIONS.length)

  const orderedSections = [
    ...new Set([
      'Catalog page',
      ...sectionNames,
      ...DOCUMENTED_EXCEPTIONS.map((exception) => exception.section),
    ]),
  ]
  for (const section of orderedSections) {
    const sectionFindings = findings.filter((finding) => finding.section === section)
    const sectionExceptions = DOCUMENTED_EXCEPTIONS.filter(
      (exception) => exception.section === section,
    )
    if (sectionFindings.length === 0 && sectionExceptions.length === 0) continue

    const problems: string[] = []
    for (const finding of sectionFindings) {
      if (!exceptionsByKey.has(findingKey(finding))) {
        problems.push(`${finding.rule}: ${finding.help}\n  target: ${finding.target}`)
      }
    }
    for (const exception of sectionExceptions) {
      if (!hasRealReason(exception.reason)) {
        problems.push(`${exception.rule}: documented exception has no honest one-line reason`)
      }
      if (!findingsByKey.has(findingKey(exception))) {
        problems.push(
          `${exception.rule}: documented exception no longer occurs\n  target: ${exception.target}`,
        )
      }
    }

    await test.step(section, async () => {
      expect.soft(
        problems,
        [`catalog section "${section}" has accessibility debt:`, ...problems].join('\n'),
      ).toEqual([])
    })
  }
})
