import { fireEvent, render, screen } from '@testing-library/react'
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
    platform_feedback_filter_active: fixed('Active'),
    platform_feedback_filter_all: fixed('All'),
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
    platform_feedback_item_kind_bug: fixed('Bug'),
    platform_feedback_item_kind_feature: fixed('Feature'),
    platform_feedback_item_kind_ux: fixed('UX'),
    platform_feedback_item_kind_docs: fixed('Docs'),
    platform_feedback_item_kind_support: fixed('Support pattern'),
    admin_shared_loading: fixed('Loading'),
    platform_empty_feedback: fixed('No feedback'),
    platform_feedback_items_empty: fixed('No feedback items'),
  }
})

vi.mock('../../-hooks', () => ({
  usePlatformFeedbackSubmissions: () => ({ data: [], isLoading: false }),
  usePlatformFeedbackItems: () => ({ data: [], isLoading: false }),
}))

import { FeedbackTab } from './FeedbackTab'

describe('FeedbackTab filters', () => {
  function expectSelectWidthsOnWrapper() {
    const statusSelect = screen.getByLabelText('Status')
    const typeSelect = screen.getByLabelText('Type')

    expect(statusSelect.className).not.toContain('w-48')
    expect(typeSelect.className).not.toContain('w-48')
    expect(statusSelect.parentElement?.className).toContain('sm:w-48')
    expect(typeSelect.parentElement?.className).toContain('sm:w-48')
  }

  it('keeps select width on the wrapper in both feedback views so chevrons stay inside controls', () => {
    render(<FeedbackTab search="" fmtDate={(value) => value ?? '-'} />)

    expectSelectWidthsOnWrapper()

    fireEvent.click(screen.getByRole('button', { name: 'Items' }))

    expectSelectWidthsOnWrapper()
    expect(screen.queryByText('Closed items are hidden by default.')).toBeNull()
  })
})
