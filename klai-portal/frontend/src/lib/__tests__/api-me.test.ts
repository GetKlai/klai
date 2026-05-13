/**
 * SPEC-INFRA-TENANT-DELETE-003 Bug 3 — provisioning status helpers.
 *
 * The backend state machine emits `failed_rollback_pending`,
 * `failed_rollback_complete`, or `failed_deprovisioning` as terminal
 * failure states. It NEVER emits the literal `'failed'`. Old polling
 * code that compared `status === 'failed'` silently timed out after
 * 5 minutes instead of surfacing the failure — these helpers + the
 * tests below lock in the correct contract so the regression cannot
 * come back via a `=== 'failed'` literal slipping into a new route.
 */

import { describe, expect, it } from 'vitest'
import { isFailedProvisioningStatus, isInFlightProvisioningStatus } from '../api-me'

describe('isFailedProvisioningStatus', () => {
  it.each([
    'failed_rollback_pending',
    'failed_rollback_complete',
    'failed_deprovisioning',
  ])('treats backend terminal failure state %s as a fatal status', (status) => {
    expect(isFailedProvisioningStatus(status)).toBe(true)
  })

  it.each([
    'ready',
    'pending',
    'queued',
    'active',
    'deprovisioning',
    'creating_zitadel_app',
    'starting_container',
  ])('does NOT treat non-failure state %s as fatal', (status) => {
    expect(isFailedProvisioningStatus(status)).toBe(false)
  })

  it('handles undefined gracefully (no /api/me response field)', () => {
    expect(isFailedProvisioningStatus(undefined)).toBe(false)
  })

  it('handles empty string gracefully', () => {
    expect(isFailedProvisioningStatus('')).toBe(false)
  })
})

describe('isInFlightProvisioningStatus', () => {
  it('treats pending as in-flight (initial signup state)', () => {
    expect(isInFlightProvisioningStatus('pending')).toBe(true)
  })

  it.each([
    'failed_rollback_pending',
    'failed_rollback_complete',
    'failed_deprovisioning',
  ])('treats terminal failure state %s as in-flight (routes to /provisioning for actionable error)', (status) => {
    expect(isInFlightProvisioningStatus(status)).toBe(true)
  })

  it('treats ready as NOT in-flight (provisioning complete)', () => {
    expect(isInFlightProvisioningStatus('ready')).toBe(false)
  })

  it('treats active as NOT in-flight (post-provisioning steady state)', () => {
    expect(isInFlightProvisioningStatus('active')).toBe(false)
  })

  it('treats deprovisioning as NOT in-flight (different lifecycle event)', () => {
    // deprovisioning is handled separately via the `tenant_deleting` 403
    // in `_get_caller_org`; the /provisioning page must not pick it up.
    expect(isInFlightProvisioningStatus('deprovisioning')).toBe(false)
  })

  it('handles undefined gracefully', () => {
    expect(isInFlightProvisioningStatus(undefined)).toBe(false)
  })
})
