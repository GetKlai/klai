import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'

// Paraglide compiles to src/paraglide, which is generated rather than
// committed; resolve every message key the component uses to its own name.
vi.mock('@/paraglide/messages', () => ({
  app_transcribe_status_processing: () => 'app_transcribe_status_processing',
  app_transcribe_processing_hint: () => 'app_transcribe_processing_hint',
  app_transcribe_queued_body: () => 'app_transcribe_queued_body',
  app_transcribe_go_to_list: () => 'app_transcribe_go_to_list',
  app_transcribe_status_failed: () => 'app_transcribe_status_failed',
  app_transcribe_failed_hint: () => 'app_transcribe_failed_hint',
  app_transcribe_retry_button: () => 'app_transcribe_retry_button',
  app_transcribe_processing: () => 'app_transcribe_processing',
  app_transcribe_back: () => 'app_transcribe_back',
  app_transcribe_result_title: () => 'app_transcribe_result_title',
  app_transcribe_result_meta: () => 'app_transcribe_result_meta',
}))

import { TranscriptionResultCards } from '../_components/-TranscriptionResultCards'
import type { TranscriptionResponse } from '../-add-hooks'

function makeResult(status: string): TranscriptionResponse {
  return {
    id: 'txn-1',
    name: null,
    status,
    text: null,
    language: null,
    duration_seconds: null,
    inference_time_seconds: null,
    summary_json: null,
    created_at: '2026-01-01T00:00:00Z',
  }
}

function renderCards(
  result: TranscriptionResponse,
  handlers: { onRetry?: (id: string) => void; onGoToList?: () => void } = {},
) {
  return render(
    <TranscriptionResultCards
      result={result}
      isRetrying={false}
      onRetry={handlers.onRetry ?? vi.fn()}
      onBack={vi.fn()}
      onGoToList={handlers.onGoToList ?? vi.fn()}
    />,
  )
}

describe('TranscriptionResultCards', () => {
  it('shows the queued body and a go-to-list button for a processing result', () => {
    const onGoToList = vi.fn()
    renderCards(makeResult('processing'), { onGoToList })

    expect(screen.getByText('app_transcribe_queued_body')).toBeTruthy()
    expect(screen.queryByText('app_transcribe_retry_button')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'app_transcribe_go_to_list' }))
    expect(onGoToList).toHaveBeenCalledTimes(1)
  })

  it('keeps the retry button for a failed result', () => {
    const onRetry = vi.fn()
    renderCards(makeResult('failed'), { onRetry })

    expect(screen.queryByText('app_transcribe_go_to_list')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'app_transcribe_retry_button' }))
    expect(onRetry).toHaveBeenCalledWith('txn-1')
  })
})
