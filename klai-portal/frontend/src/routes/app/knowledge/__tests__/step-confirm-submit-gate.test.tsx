/**
 * Submit gating on the knowledge-wizard confirm step.
 *
 * The create button must be inert whenever another POST can only repeat a
 * refusal: a permanent quota 403, a known-exhausted personal quota, or a
 * quota that has not resolved yet. Pure render assertions, no router or
 * query-client setup needed.
 */
import { describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import { StepConfirm } from '../new._components/-StepConfirm'
import type { WizardData, WizardErrorKey } from '../new._types'

const personalForm: WizardData = {
  name: 'Fiber',
  slug: 'fiber',
  slugManuallyEdited: false,
  description: '',
  ownerType: 'user',
  visibilityMode: 'org',
  allowContribute: true,
  initialGroups: [],
  initialUsers: [],
}

function renderConfirm(
  overrides: Partial<{
    data: WizardData
    errorKey: WizardErrorKey
    canCreateKB: boolean
    isQuotaLoading: boolean
    isPending: boolean
  }> = {},
) {
  render(
    <StepConfirm
      data={overrides.data ?? personalForm}
      isPending={overrides.isPending ?? false}
      errorKey={overrides.errorKey ?? null}
      canCreateKB={overrides.canCreateKB ?? true}
      isQuotaLoading={overrides.isQuotaLoading ?? false}
      onSubmit={() => {}}
      onEditSlug={() => {}}
    />,
  )
  return screen.getByRole<HTMLButtonElement>('button', { name: /knowledge base/i })
}

describe('StepConfirm submit gate', () => {
  it('allows submitting when the quota is resolved and has room', () => {
    expect(renderConfirm().disabled).toBe(false)
  })

  // Regression: after a permanent quota 403 the button re-enabled, because the
  // cached KB list was still stale below the cap. Retrying could only produce
  // the same 403.
  it('stays disabled after a permanent quota denial', () => {
    expect(renderConfirm({ errorKey: 'personal_quota' }).disabled).toBe(true)
  })

  it('stays disabled after an org-scope denial', () => {
    expect(
      renderConfirm({
        data: { ...personalForm, ownerType: 'org' },
        errorKey: 'org_not_allowed',
      }).disabled,
    ).toBe(true)
  })

  it('stays enabled after a generic failure, which is retryable', () => {
    expect(renderConfirm({ errorKey: 'generic' }).disabled).toBe(false)
  })

  it('stays enabled after a slug conflict, which the user can resolve', () => {
    expect(renderConfirm({ errorKey: 'conflict' }).disabled).toBe(false)
  })

  it('blocks submission while the quota is still resolving, without claiming exhaustion', () => {
    const button = renderConfirm({ isQuotaLoading: true, canCreateKB: false })

    expect(button.disabled).toBe(true)
    expect(screen.queryByText(/maximale aantal|maximum number/i)).toBeNull()
  })

  it('reports exhaustion once the quota is resolved and full', () => {
    const button = renderConfirm({ isQuotaLoading: false, canCreateKB: false })

    expect(button.disabled).toBe(true)
    expect(screen.getByText(/maximale aantal|maximum number/i)).toBeTruthy()
  })
})
