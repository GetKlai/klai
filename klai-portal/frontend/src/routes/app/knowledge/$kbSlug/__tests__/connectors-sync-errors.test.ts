import { describe, expect, it } from 'vitest'

import { summarizeConnectorSyncError } from '../-connectors-sync-errors'

describe('summarizeConnectorSyncError', () => {
  it('returns null for non-terminal statuses', () => {
    expect(summarizeConnectorSyncError({ status: 'running', error_details: [{ error: 'not yet' }] })).toBeNull()
    expect(summarizeConnectorSyncError({ status: 'completed', error_details: [{ error: 'old' }] })).toBeNull()
  })

  it('summarizes the first failed file error', () => {
    expect(
      summarizeConnectorSyncError({
        status: 'failed',
        error_details: [{ file: 'Subfolder/Plan.pdf', error: '403 Forbidden' }],
      }),
    ).toBe('Subfolder/Plan.pdf: 403 Forbidden')
  })

  it('falls back when failed documents have no details', () => {
    expect(summarizeConnectorSyncError({ status: 'failed', documents_failed: 2, error_details: [] }))
      .toBe('Sync failed, but no error details were returned.')
  })
})
