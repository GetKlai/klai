/**
 * J05 - Replacing a knowledge-base source overwrites it, not duplicates it.
 *
 * This journey exists because the whole replace feature rests on one
 * assumption that unit tests cannot check: that ingesting under an existing
 * document path makes knowledge-ingest supersede the artifact living there
 * (close it, create the replacement, clear the old Qdrant points) instead of
 * adding a second source next to it. Everything else about the feature is
 * mocked-ingest logic; this is the part that is only true if the real
 * pipeline behaves the way the code assumes.
 *
 * The journey drives the API from an authenticated browser context rather
 * than the UI: what is under test is the pipeline contract, and a click-path
 * would only add flakiness on top of it.
 *
 * Flow: upload a markdown source carrying canary A -> replace it with the
 * same logical document carrying canary B -> assert the KB still holds ONE
 * source, that its content is B, and that A is gone. Then delete it.
 */
import { test, expect, type Page } from '@playwright/test'
import { e2ePrefix } from './_lib/fixtures'

const RUN = e2ePrefix()
const FILENAME = `${RUN}replace-journey.md`
const CANARY_BEFORE = `klai-e2e-replace-before-${Date.now()}`
const CANARY_AFTER = `klai-e2e-replace-after-${Date.now()}`

interface KnowledgeBase {
  slug: string
  owner_type?: string
}

interface Source {
  kind: string
  id: string
  name: string
  index_status?: string | null
  can_replace?: boolean
}

/** Upload one markdown file and return the raw response body. */
async function uploadSource(page: Page, kbSlug: string, filename: string, body: string) {
  return page.evaluate(
    async ({ kbSlug, filename, body, csrfName }) => {
      const csrf =
        document.cookie
          .split(';')
          .find((c) => c.trimStart().startsWith(csrfName))
          ?.trimStart()
          .split('=')
          .slice(1)
          .join('=') || ''
      const form = new FormData()
      form.append('files', new File([body], filename, { type: 'text/markdown' }))
      const res = await fetch(`/api/app/knowledge-bases/${kbSlug}/sources/file`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        body: form,
        credentials: 'include',
      })
      return { status: res.status, body: await res.text() }
    },
    { kbSlug, filename, body, csrfName: '__Secure-klai_csrf=' },
  )
}

async function replaceSource(
  page: Page,
  kbSlug: string,
  artifactId: string,
  filename: string,
  body: string,
) {
  return page.evaluate(
    async ({ kbSlug, artifactId, filename, body, csrfName }) => {
      const csrf =
        document.cookie
          .split(';')
          .find((c) => c.trimStart().startsWith(csrfName))
          ?.trimStart()
          .split('=')
          .slice(1)
          .join('=') || ''
      const form = new FormData()
      form.append('file', new File([body], filename, { type: 'text/markdown' }))
      const res = await fetch(`/api/app/knowledge-bases/${kbSlug}/uploads/${artifactId}/replace`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrf },
        body: form,
        credentials: 'include',
      })
      return { status: res.status, body: await res.text() }
    },
    { kbSlug, artifactId, filename, body, csrfName: '__Secure-klai_csrf=' },
  )
}

async function getJson<T>(page: Page, path: string): Promise<T> {
  return page.evaluate(async (p) => {
    const res = await fetch(p, { credentials: 'include' })
    return res.json()
  }, path) as Promise<T>
}

async function deleteSource(page: Page, kbSlug: string, id: string): Promise<number> {
  return page.evaluate(
    async ({ kbSlug, id, csrfName }) => {
      const csrf =
        document.cookie
          .split(';')
          .find((c) => c.trimStart().startsWith(csrfName))
          ?.trimStart()
          .split('=')
          .slice(1)
          .join('=') || ''
      const res = await fetch(`/api/app/knowledge-bases/${kbSlug}/uploads/${id}`, {
        method: 'DELETE',
        headers: { 'X-CSRF-Token': csrf },
        credentials: 'include',
      })
      return res.status
    },
    { kbSlug, id, csrfName: '__Secure-klai_csrf=' },
  )
}

/** Poll the sources list until our file appears as a settled upload. */
async function waitForSource(page: Page, kbSlug: string, name: string): Promise<Source> {
  let last: Source | undefined
  await expect
    .poll(
      async () => {
        const data = await getJson<{ sources: Source[] }>(
          page,
          `/api/app/knowledge-bases/${kbSlug}/sources`,
        )
        last = data.sources.find((s) => s.name === name)
        return last?.index_status ?? 'absent'
      },
      { timeout: 90_000, intervals: [2000] },
    )
    .toBe('synced')
  if (!last) throw new Error(`source ${name} never appeared`)
  return last
}

/**
 * Resolve a concrete KB slug to work in.
 *
 * Deliberately not the ``personal`` magic slug: ``/sources/file`` re-resolves
 * the slug itself and only matches a literal row, so the magic slugs the
 * route firewall understands 404 there. Using a real slug keeps this journey
 * about the replace contract instead of about that quirk.
 */
async function resolveKbSlug(page: Page): Promise<string> {
  const data = await getJson<{ knowledge_bases: KnowledgeBase[] }>(
    page,
    '/api/app/knowledge-bases',
  )
  const personal = data.knowledge_bases.find((kb) => kb.slug.startsWith('personal-'))
  const slug = personal?.slug ?? data.knowledge_bases[0]?.slug
  if (!slug) throw new Error('no knowledge base available for the e2e user')
  return slug
}

test.describe('J05 - replace a knowledge-base source', () => {
  test('overwrites the source in place instead of adding a second one', async ({ page }) => {
    test.slow()
    await page.goto('/app')
    const KB_SLUG = await resolveKbSlug(page)

    // Baseline: how many sources does this KB hold before we touch it?
    const before = await getJson<{ sources: Source[] }>(
      page,
      `/api/app/knowledge-bases/${KB_SLUG}/sources`,
    )
    const baselineCount = before.sources.length

    // 1. Add the source.
    const upload = await uploadSource(
      page,
      KB_SLUG,
      FILENAME,
      `# Replace journey\n\n${CANARY_BEFORE}\n`,
    )
    expect(upload.status, upload.body).toBe(202)

    const original = await waitForSource(page, KB_SLUG, FILENAME)
    expect(original.can_replace, 'a file-backed source must offer replace').toBe(true)

    const afterUpload = await getJson<{ sources: Source[] }>(
      page,
      `/api/app/knowledge-bases/${KB_SLUG}/sources`,
    )
    expect(afterUpload.sources.length).toBe(baselineCount + 1)

    // 2. Replace it. Same logical document, different content.
    const replaced = await replaceSource(
      page,
      KB_SLUG,
      original.id,
      FILENAME,
      `# Replace journey\n\n${CANARY_AFTER}\n`,
    )
    expect(replaced.status, replaced.body).toBe(202)

    // 3. The claim under test: still ONE source, not two.
    const current = await waitForSource(page, KB_SLUG, FILENAME)
    const afterReplace = await getJson<{ sources: Source[] }>(
      page,
      `/api/app/knowledge-bases/${KB_SLUG}/sources`,
    )
    expect(
      afterReplace.sources.filter((s) => s.name === FILENAME).length,
      'replacing must not leave the old version behind',
    ).toBe(1)
    expect(afterReplace.sources.length).toBe(baselineCount + 1)

    // 4. And it is the NEW content that is indexed.
    const content = await getJson<{ chunks: { text: string }[] }>(
      page,
      `/api/app/knowledge-bases/${KB_SLUG}/sources/${current.id}/content?kind=upload`,
    )
    const text = content.chunks.map((c) => c.text).join('\n')
    expect(text, 'the replacement content must be indexed').toContain(CANARY_AFTER)
    expect(text, 'the superseded content must be gone').not.toContain(CANARY_BEFORE)

    // 5. Clean up after ourselves — e2e-prefixed artifact only.
    expect(FILENAME.startsWith('e2e-')).toBe(true)
    expect(await deleteSource(page, KB_SLUG, current.id)).toBe(204)
  })
})
