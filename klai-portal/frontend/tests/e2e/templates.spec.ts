/**
 * Playwright e2e spec for SPEC-CHAT-TEMPLATES-002.
 *
 * STATUS: STUB — klai-portal/frontend has no Playwright harness checked in
 * yet. Activation requires a separate follow-up that adds:
 *  - `@playwright/test` to devDependencies
 *  - `playwright.config.ts` with baseURL, `webServer` for the vite dev server
 *  - Zitadel auth helper (storageState via OIDC flow, pattern from
 *    `scripts/playwright-launcher.mjs` + `.claude/playwright-config-win.json`)
 *  - CI job wired to run this spec against a dev tenant
 *
 * Until that harness lands this file documents the contracted scenarios. The
 * unit tests in `src/routes/app/templates/__tests__/*.test.tsx` cover the
 * same assertions at the component level. Manual smoke testing on the dev
 * tenant remains the stop-gap validation before merge.
 *
 * See SPEC-CHAT-TEMPLATES-002 acceptance.md SCEN-E2E-1 … 4 for the full
 * scripts this stub will flesh out.
 */

// @ts-expect-error — @playwright/test not yet installed in this project.
// Remove the comment once the harness lands.
import { test, expect } from '@playwright/test'

test.describe('SPEC-CHAT-TEMPLATES-002 — Prompt Templates frontend CRUD', () => {
  test.skip(
    true,
    'Playwright harness not configured in klai-portal/frontend. Unit tests cover component-level behaviour until the harness lands.',
  )

  /**
   * SCEN-E2E-1 — Create template as admin → visible in list.
   *
   * Preconditions:
   *  - Tenant is freshly provisioned (expect 4 default templates in list).
   *  - User is signed in as org admin.
   *
   * Steps:
   *  1. goto('/app/templates')
   *  2. Assert 4 default rows (klantenservice, formeel, creatief, samenvatter)
   *  3. Click "Nieuwe template"
   *  4. Fill name / beschrijving / prompt_text; pick scope="Organisatie"
   *  5. Click "Opslaan"
   *  6. Expect redirect to /app/templates
   *  7. Expect new row present with correct name + "Organisatie" badge
   */
  test('SCEN-E2E-1 admin creates org template', async ({ page }) => {
    // TODO: implement after Playwright harness lands.
    expect(page).toBeDefined()
  })

  /**
   * SCEN-E2E-2 — Edit an existing template updates prompt_text.
   *
   * Steps:
   *  1. goto('/app/templates')
   *  2. Click edit on "Klantenservice" row
   *  3. Expect form pre-filled with existing prompt_text
   *  4. Append text; click Opslaan
   *  5. Back on list; optional: verify backend GET returns new prompt_text
   */
  test('SCEN-E2E-2 admin edits template', async ({ page }) => {
    // TODO
    expect(page).toBeDefined()
  })

  /**
   * SCEN-E2E-3 — Delete template via InlineDeleteConfirm.
   *
   * Steps:
   *  1. goto('/app/templates')
   *  2. Click delete icon → expect confirmation overlay
   *  3. Click "Verwijderen" again to confirm
   *  4. Expect row removed
   *  5. Expect DELETE /api/app/templates/{slug} to have been called
   */
  test('SCEN-E2E-3 admin deletes template via inline confirm', async ({ page }) => {
    // TODO
    expect(page).toBeDefined()
  })

  /**
   * SCEN-E2E-4 — Chat-integration: template injection reaches LiteLLM.
   *
   * End-to-end smoke: frontend activation flows into the LiteLLM hook.
   *
   * Steps:
   *  1. goto('/app/templates/new'); create "Formeel proef" template.
   *  2. goto('/app') → ChatConfigBar visible
   *  3. Open template picker; tick "Formeel proef"
   *  4. Type a chat message in the embedded LibreChat iframe
   *  5. Poll VictoriaLogs (or the service-level LiteLLM log) for the next
   *     `_klai_kb_meta` entry tied to this user session; expect the system
   *     message portion of the LiteLLM request to include the template's
   *     prompt_text prepended before the KB-context block.
   *
   * This step validates REQ-TEMPLATES-HOOK E1 end-to-end and closes the
   * product promise of the feature.
   */
  test('SCEN-E2E-4 activated template reaches LiteLLM system prompt', async ({ page }) => {
    // TODO
    expect(page).toBeDefined()
  })
})
