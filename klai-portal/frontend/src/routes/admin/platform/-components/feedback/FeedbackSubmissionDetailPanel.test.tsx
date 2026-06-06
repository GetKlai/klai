import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  PlatformFeedbackActionResult,
  PlatformFeedbackItem,
  PlatformFeedbackSubmission,
} from '../../-types'

const navigate = vi.hoisted(() => vi.fn())
const onClose = vi.hoisted(() => vi.fn())
const linkMutate = vi.hoisted(() => vi.fn())

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

vi.mock('@/paraglide/messages', () => ({
  platform_back_to_feedback: () => 'Back to feedback',
  platform_col_status: () => 'Status',
  platform_delete: () => 'Delete',
  platform_feedback_action_dismiss: () => 'Dismiss',
  platform_feedback_advice_match: ({ title }: { title?: string } = {}) =>
    `Klai thinks this matches ${title ?? 'an item'}.`,
  platform_feedback_back: () => 'Back',
  platform_feedback_choice_dismiss_hint: () => 'Dismiss this feedback.',
  platform_feedback_choice_product: () => 'Product item',
  platform_feedback_choice_product_hint: () => 'Link or create a product item.',
  platform_feedback_choice_status: () => 'Change status',
  platform_feedback_choice_support: () => 'Support pattern',
  platform_feedback_choice_support_hint: () => 'Track as support.',
  platform_feedback_create_new_fallback: () => 'No matching item?',
  platform_feedback_create_new_title: () => 'Create new item',
  platform_feedback_delete_submission_description: () => 'Delete this feedback.',
  platform_feedback_delete_submission_title: () => 'Delete feedback?',
  platform_feedback_dismiss_help: () => 'Dismiss this feedback.',
  platform_feedback_klai_suggestion: () => 'Klai suggestion',
  platform_feedback_link: () => 'Link',
  platform_feedback_product_step_title: () => 'Link to existing item',
  platform_feedback_reopen_linked_item: () => 'Reopen this item when linking',
  platform_feedback_search_placeholder: () => 'Search feedback items',
  platform_feedback_status_dismissed: () => 'Dismissed',
  platform_feedback_status_new: () => 'New',
  platform_feedback_status_open: () => 'Open',
  platform_feedback_status_resolved: () => 'Resolved',
  platform_feedback_status_support: () => 'Support',
  platform_feedback_triage_question: () => 'What should happen with this feedback?',
  platform_feedback_triage_title: () => 'Feedback triage',
  platform_feedback_unknown_organization: () => 'Unknown organization',
  admin_shared_save: () => 'Save',
  admin_users_cancel: () => 'Cancel',
}))

vi.mock('../../-hooks', () => ({
  usePlatformFeedbackItems: () => ({ data: [], isFetching: false }),
  usePlatformFeedbackUpdateSubmission: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  usePlatformFeedbackDeleteSubmission: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  usePlatformFeedbackDismiss: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  usePlatformFeedbackCreateItem: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  usePlatformFeedbackLinkItem: () => ({
    mutate: linkMutate,
    isPending: false,
  }),
}))

import { FeedbackSubmissionDetailPanel } from './FeedbackSubmissionDetailPanel'

function submissionWithCandidate(): PlatformFeedbackSubmission {
  const candidate: PlatformFeedbackItem = {
    id: 12,
    kind: 'bug',
    title: 'Tekst verdwijnt bij compleet antwoord',
    summary: 'Answer disappears after stream completes.',
    status: 'resolved',
    area: 'chat_interface',
    priority_score: 22,
    org_count: 2,
    user_count: 2,
    shipped_at: null,
    resolution_summary: null,
    resolved_at: null,
    resolved_by: null,
    notification_state: null,
    reporter_orgs: [],
    created_at: '2026-06-05T21:15:00Z',
    updated_at: '2026-06-06T09:00:00Z',
  }

  return {
    id: 99,
    org_id: 1,
    org_name: 'Stageplein',
    org_slug: 'stageplein',
    user_id: 'user-1',
    user_email: 'gertjan@example.test',
    user_display_name: 'Gertjan Jansen',
    event_type: 'klai_assistant.problem_report',
    status: 'new',
    raw_text: 'AI antwoord is niet zichtbaar.',
    feedback_type: null,
    severity: 'blocked',
    page_url: 'https://stageplein-37493602.getklai.com/app/chat',
    route_id: '/app/chat',
    locale: 'en',
    viewport: '1362x895',
    created_at: '2026-06-06T09:18:00Z',
    triage_suggestion: {
      classification: 'bug',
      summary: 'Chat answer disappears after completion',
      suggested_area: 'chat_interface',
      suggested_severity: 'blocked',
      suggested_action: 'link',
      duplicate_candidates: [
        {
          item_id: candidate.id,
          confidence: 0.92,
          reason: 'Same chat completion symptom.',
          title: candidate.title,
          kind: candidate.kind,
          status: candidate.status,
          area: candidate.area,
        },
      ],
      model: 'test',
      created_at: '2026-06-06T09:20:00Z',
    },
    linked_item_id: null,
    linked_item_title: null,
    linked_item_status: null,
  }
}

beforeEach(() => {
  navigate.mockReset()
  onClose.mockReset()
  linkMutate.mockReset()
  linkMutate.mockImplementation(
    (
      _vars: unknown,
      options?: { onSuccess?: (result: PlatformFeedbackActionResult) => void },
    ) => {
      options?.onSuccess?.({ ok: true, submission_id: 99, status: 'open', item_id: 12 })
    },
  )
})

describe('FeedbackSubmissionDetailPanel link flow', () => {
  it('reopens closed linked feedback items by default and navigates to the item', () => {
    render(
      <FeedbackSubmissionDetailPanel
        item={submissionWithCandidate()}
        fmtDate={(value) => value ?? '-'}
        onClose={onClose}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Product item/ }))
    expect(screen.getByLabelText<HTMLInputElement>('Reopen this item when linking').checked).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: /Link/ }))

    expect(linkMutate).toHaveBeenCalledWith(
      { submissionId: 99, item_id: 12, link_type: 'bug_repro', reopen_item: true },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
    expect(navigate).toHaveBeenCalledWith({
      to: '/admin/platform/feedback/items/$itemId',
      params: { itemId: '12' },
    })
    expect(onClose).not.toHaveBeenCalled()
  })

  it('can link a closed feedback item without reopening it', () => {
    render(
      <FeedbackSubmissionDetailPanel
        item={submissionWithCandidate()}
        fmtDate={(value) => value ?? '-'}
        onClose={onClose}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: /Product item/ }))
    fireEvent.click(screen.getByLabelText('Reopen this item when linking'))
    fireEvent.click(screen.getByRole('button', { name: /Link/ }))

    expect(linkMutate).toHaveBeenCalledWith(
      { submissionId: 99, item_id: 12, link_type: 'bug_repro', reopen_item: false },
      expect.objectContaining({ onSuccess: expect.any(Function) }),
    )
  })
})
