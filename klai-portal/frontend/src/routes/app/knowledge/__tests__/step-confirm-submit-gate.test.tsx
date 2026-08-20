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

  // Regression: errorKey is only cleared on submit, so a denial that outlived
  // its scope disabled the very button that clears it. Switching to a personal
  // KB after an org denial left the wizard dead until a page reload.
  it('re-enables submit when the user switches scope after an org denial', () => {
    const button = renderConfirm({
      data: { ...personalForm, ownerType: 'user' },
      errorKey: 'org_not_allowed',
    })

    expect(button.disabled).toBe(false)
  })

  it('drops the org denial message once the scope is personal', () => {
    renderConfirm({
      data: { ...personalForm, ownerType: 'user' },
      errorKey: 'org_not_allowed',
    })

    expect(screen.queryByText(/organisatie-kennisbanken|organisation knowledge bases/i)).toBeNull()
  })

  it('re-enables submit when the user switches to org after a personal quota denial', () => {
    const button = renderConfirm({
      data: { ...personalForm, ownerType: 'org' },
      errorKey: 'personal_quota',
    })

    expect(button.disabled).toBe(false)
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
