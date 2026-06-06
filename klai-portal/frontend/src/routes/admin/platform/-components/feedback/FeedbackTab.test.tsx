import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('@/paraglide/messages', () => {
  const fixed = (value: string) => () => value
  return {
    platform_feedback_view_inbox: fixed('Inbox'),
    platform_feedback_view_items: fixed('Items'),
    platform_feedback_filter_status: fixed('Status'),
    platform_feedback_filter_all_statuses: fixed('All statuses'),
    platform_feedback_status_new: fixed('New'),
    platform_feedback_status_open: fixed('Open'),
    platform_feedback_status_resolved: fixed('Resolved'),
    platform_feedback_status_support: fixed('Support'),
    platform_feedback_status_dismissed: fixed('Dismissed'),
    platform_feedback_filter_type: fixed('Type'),
    platform_feedback_filter_all_types: fixed('All types'),
    platform_feedback_kind_feedback: fixed('Feedback'),
    platform_feedback_kind_problem: fixed('Problem'),
    platform_feedback_kind_question: fixed('Question'),
    admin_shared_loading: fixed('Loading'),
    platform_empty_feedback: fixed('No feedback'),
  }
})

vi.mock('../../-hooks', () => ({
  usePlatformFeedbackSubmissions: () => ({ data: [], isLoading: false }),
  usePlatformFeedbackItems: () => ({ data: [], isLoading: false }),
}))

import { FeedbackTab } from './FeedbackTab'

describe('FeedbackTab filters', () => {
  it('keeps select width on the Select wrapper so the chevron stays inside the control', () => {
    render(<FeedbackTab search="" fmtDate={(value) => value ?? '-'} />)

    const statusSelect = screen.getByLabelText('Status')
    const typeSelect = screen.getByLabelText('Type')

    expect(statusSelect.className).not.toContain('w-48')
    expect(typeSelect.className).not.toContain('w-48')
    expect(statusSelect.parentElement?.className).toContain('sm:w-48')
    expect(typeSelect.parentElement?.className).toContain('sm:w-48')
  })
})
