/**
 * Per-journey cleanup helpers.
 *
 * Called from each journey's `test.afterEach` (or `afterAll`) to leave
 * the e2e tenant in a known-clean state. Without this, J03/J05/J06/J09
 * accumulate orphan KBs/templates/transcripts/settings inside the bot
 * tenant.
 *
 * NOTE: full tenant-delete is a separate concern handled by the
 * SPEC-INFRA-TENANT-DELETE-001 endpoint. These helpers only clean
 * journey-scoped artifacts, not the tenant itself.
 *
 * docs/testing/test-suite-plan.md §4 + §11.
 */
import type { APIRequestContext, Page } from '@playwright/test'

/** Remove a knowledge base by id. Idempotent — 404 is not an error. */
export async function deleteKnowledgeBase(
  request: APIRequestContext,
  kbId: string,
): Promise<void> {
  const r = await request.delete(`/api/knowledge/bases/${kbId}`)
  if (r.status() !== 200 && r.status() !== 204 && r.status() !== 404) {
    throw new Error(`deleteKnowledgeBase(${kbId}) returned ${r.status()}`)
  }
}

/** Deactivate + delete a prompt template by id. Idempotent. */
export async function deleteTemplate(request: APIRequestContext, templateId: string): Promise<void> {
  // Best-effort deactivate first; ignore failure (may already be inactive)
  await request.patch(`/api/templates/${templateId}`, { data: { active: false } }).catch(() => {})
  const r = await request.delete(`/api/templates/${templateId}`)
  if (r.status() !== 200 && r.status() !== 204 && r.status() !== 404) {
    throw new Error(`deleteTemplate(${templateId}) returned ${r.status()}`)
  }
}

/** Delete a scribe transcript by id. Idempotent. */
export async function deleteTranscript(request: APIRequestContext, transcriptId: string): Promise<void> {
  const r = await request.delete(`/api/scribe/transcripts/${transcriptId}`)
  if (r.status() !== 200 && r.status() !== 204 && r.status() !== 404) {
    throw new Error(`deleteTranscript(${transcriptId}) returned ${r.status()}`)
  }
}

/**
 * Restore the org's display-name to the default (used by J09).
 * Defensive: if the default isn't known yet, the journey can pass
 * the original value via `previous` instead.
 */
export async function restoreOrgDisplayName(
  request: APIRequestContext,
  previousDisplayName: string,
): Promise<void> {
  const r = await request.patch('/api/admin/settings', {
    data: { display_name: previousDisplayName },
  })
  if (!r.ok()) {
    throw new Error(`restoreOrgDisplayName returned ${r.status()}: ${await r.text()}`)
  }
}

/**
 * Convenience: read the user's currently set org display-name so that
 * a journey can capture it for `restoreOrgDisplayName` later.
 */
export async function getCurrentOrgDisplayName(page: Page): Promise<string> {
  const r = await page.request.get('/api/admin/settings')
  if (!r.ok()) throw new Error(`getCurrentOrgDisplayName: ${r.status()}`)
  const data = (await r.json()) as { display_name?: string }
  return data.display_name ?? ''
}
