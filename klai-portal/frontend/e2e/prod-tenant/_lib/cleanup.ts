/**
 * Per-journey cleanup helpers.
 *
 * Called from each journey's `test.afterEach` (or `afterAll`) to leave
 * the tenant in a known-clean state. Without this, J03/J05/J06/J09
 * accumulate orphan KBs/templates/transcripts/settings.
 *
 * SAFETY GUARD (critical when running in voys-attached mode against a
 * real customer tenant): every cleanup function refuses to delete an
 * artifact whose NAME does not start with `E2E_NAME_GUARD` (`e2e-`).
 * This is defence-in-depth against accidentally wiping genuine user
 * data. Per-id helpers (where the caller passes an id, not a name)
 * REQUIRE the caller to have created the artifact during this run —
 * the spec convention is to capture the id from the create-response
 * and pass it to cleanup. See _lib/fixtures.ts::e2ePrefix() for the
 * naming contract.
 *
 * NOTE: full tenant-delete is a separate concern (SPEC-INFRA-TENANT-DELETE-001).
 * These helpers only clean journey-scoped artifacts, NEVER the tenant.
 *
 * docs/testing/test-suite-plan.md §4 + §11.
 */
import type { APIRequestContext, Page } from '@playwright/test'
import { E2E_NAME_GUARD } from './fixtures'

function assertE2ENamed(name: string, kind: string): void {
  if (!name.startsWith(E2E_NAME_GUARD)) {
    throw new Error(
      `[cleanup] refused to delete ${kind} '${name}' — name does not start ` +
        `with '${E2E_NAME_GUARD}'. Cleanup is scoped to test-created artifacts only.`,
    )
  }
}

/**
 * Remove a knowledge base by id. Idempotent — 404 is not an error.
 *
 * REQUIRES the caller to pass `expectedName` so we can verify the KB's
 * name starts with `e2e-` before deleting. This is a hard guard against
 * deleting customer KBs in voys-attached mode.
 */
export async function deleteKnowledgeBase(
  request: APIRequestContext,
  kbId: string,
  expectedName: string,
): Promise<void> {
  assertE2ENamed(expectedName, 'knowledge base')
  const r = await request.delete(`/api/knowledge/bases/${kbId}`)
  if (r.status() !== 200 && r.status() !== 204 && r.status() !== 404) {
    throw new Error(`deleteKnowledgeBase(${kbId}) returned ${r.status()}`)
  }
}

/** Deactivate + delete a prompt template by id. Idempotent. */
export async function deleteTemplate(
  request: APIRequestContext,
  templateId: string,
  expectedName: string,
): Promise<void> {
  assertE2ENamed(expectedName, 'template')
  // Best-effort deactivate first; ignore failure (may already be inactive)
  await request.patch(`/api/templates/${templateId}`, { data: { active: false } }).catch(() => {})
  const r = await request.delete(`/api/templates/${templateId}`)
  if (r.status() !== 200 && r.status() !== 204 && r.status() !== 404) {
    throw new Error(`deleteTemplate(${templateId}) returned ${r.status()}`)
  }
}

/** Delete a scribe transcript by id. Idempotent. */
export async function deleteTranscript(
  request: APIRequestContext,
  transcriptId: string,
  expectedName: string,
): Promise<void> {
  assertE2ENamed(expectedName, 'transcript')
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
