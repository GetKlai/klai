import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  PlatformFeedbackItem,
  PlatformFeedbackResolveResult,
} from '../../-types'

const testState = vi.hoisted(() => ({
  item: null as PlatformFeedbackItem | null,
}))

const resolveMutate = vi.hoisted(() => vi.fn())
const clipboardWriteText = vi.hoisted(() => vi.fn())

vi.mock('../../-hooks', () => ({
  usePlatformFeedbackItem: () => ({
    isLoading: false,
    data: testState.item ? { item: testState.item, submissions: [] } : null,
  }),
  usePlatformFeedbackUpdateItem: () => ({
    mutate: vi.fn(),
    isPending: false,
    isSuccess: false,
  }),
  usePlatformFeedbackResolveItem: () => ({
    mutate: resolveMutate,
    isPending: false,
  }),
  usePlatformFeedbackDeleteItem: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}))

vi.mock('@/paraglide/messages', () => {
  const fixed = (value: string) => () => value
  return {
    admin_shared_loading: fixed('Loading'),
    admin_shared_save: fixed('Save'),
    admin_shared_wizard_next: fixed('Next'),
    admin_shared_wizard_previous: fixed('Previous'),
    admin_users_cancel: fixed('Cancel'),
    area: fixed('Area'),
    created_at: fixed('Created at'),
    id: fixed('ID'),
    kind: fixed('Kind'),
    notification_state: fixed('Notification state'),
    org_count: fixed('Org count'),
    priority_score: fixed('Priority score'),
    resolution_summary: fixed('Resolution summary'),
    status: fixed('Status'),
    summary: fixed('Summary'),
    title: fixed('Title'),
    updated_at: fixed('Updated at'),
    user_count: fixed('User count'),
    platform_back_to_feedback: fixed('Back to feedback'),
    platform_col_created: fixed('Created'),
    platform_col_status: fixed('Status'),
    platform_delete: fixed('Delete'),
    platform_feedback_channel_email: fixed('Also prepare email'),
    platform_feedback_channel_in_app: fixed('In-app message'),
    platform_feedback_col_updated: fixed('Updated'),
    platform_feedback_copy_debug_button: fixed('Copy debug instructions'),
    platform_feedback_copy_debug_copied: fixed('Debug instructions copied'),
    platform_feedback_copy_debug_description: fixed('Copy debug instructions.'),
    platform_feedback_copy_debug_failed: fixed('Copy failed'),
    platform_feedback_copy_debug_title: fixed('Debug instructions'),
    platform_feedback_copy_feature_prompt_button: fixed('Copy feature prompt for LLM'),
    platform_feedback_copy_feature_prompt_copied: fixed('Feature prompt copied'),
    platform_feedback_copy_feature_prompt_description: fixed('Copy feature prompt.'),
    platform_feedback_copy_feature_prompt_title: fixed('Feature prompt'),
    platform_feedback_default_resolution_bug: ({ title }: { title?: string } = {}) =>
      `Resolved ${title ?? 'item'}`,
    platform_feedback_default_resolution_feature: ({ title }: { title?: string } = {}) =>
      `Feature shipped ${title ?? 'item'}`,
    platform_feedback_delete_item: fixed('Delete item'),
    platform_feedback_delete_item_description: fixed('Delete this item.'),
    platform_feedback_delete_item_title: fixed('Delete open item?'),
    platform_feedback_edit_item_title: fixed('Edit item'),
    platform_feedback_item_description: fixed('Bundled feedback and updates.'),
    platform_feedback_item_details: fixed('Item'),
    platform_feedback_item_not_found: fixed('Item not found'),
    platform_feedback_item_title: fixed('Open item'),
    platform_feedback_item_kind_bug: fixed('Bug'),
    platform_feedback_item_kind_feature: fixed('Feature'),
    platform_feedback_linked_feedback: ({ count }: { count?: string } = {}) =>
      `Linked feedback (${count ?? '0'})`,
    platform_feedback_no_description: fixed('No description'),
    platform_feedback_no_linked_feedback_warning: fixed('No linked feedback yet.'),
    platform_feedback_reporter_counts: ({
      orgs,
      users,
    }: {
      orgs?: string
      users?: string
    } = {}) =>
      `${orgs ?? '0'} orgs, ${users ?? '0'} users`,
    platform_feedback_resend_update: fixed('Resend update'),
    platform_feedback_resolution_placeholder: fixed('Personal message for the reporter'),
    platform_feedback_resolve_bug_button: fixed('Close bug and message user'),
    platform_feedback_resolve_bug_subject: fixed('Bug resolved'),
    platform_feedback_resolve_bug_title: fixed('Close bug'),
    platform_feedback_resolve_description: fixed(
      'Close this item and send linked reporters an update.',
    ),
    platform_feedback_resolving: fixed('Closing'),
    platform_feedback_resolve_feature_button: fixed('Mark as shipped and message user'),
    platform_feedback_resolve_feature_subject: fixed('Feature available'),
    platform_feedback_resolve_feature_title: fixed('Complete feature'),
    platform_feedback_score: ({ score }: { score?: string } = {}) =>
      `Score ${score ?? '0'}`,
    platform_feedback_short_note_placeholder: fixed('Short note'),
    platform_feedback_status_dismissed: fixed('Dismissed'),
    platform_feedback_status_open: fixed('Open'),
    platform_feedback_status_resolved: fixed('Resolved'),
    platform_feedback_title_placeholder: fixed('Title'),
    platform_feedback_unknown_organization: fixed('Unknown organization'),
    platform_feedback_update_created: ({ count }: { count?: string } = {}) =>
      `Update created for ${count ?? '0'} recipients.`,
    platform_feedback_update_creating: fixed('Creating update'),
  }
})

import { FeedbackItemDetailPanel } from './FeedbackItemDetailPanel'

function itemWithNotificationState(
  notificationState: PlatformFeedbackItem['notification_state'],
  overrides: Partial<PlatformFeedbackItem> = {},
): PlatformFeedbackItem {
  return {
    id: 12,
    kind: 'bug',
    title: 'Chat completion disappeared',
    summary: 'Existing answer got reset.',
    status: 'resolved',
    area: 'chat',
    priority_score: 10,
    org_count: 1,
    user_count: 1,
    shipped_at: null,
    resolution_summary: null,
    resolved_at: null,
    resolved_by: null,
    notification_state: notificationState,
    reporter_orgs: [],
    created_at: '2026-06-01T00:00:00Z',
    updated_at: '2026-06-02T00:00:00Z',
    ...overrides,
  }
}

function renderPanel(notificationState: PlatformFeedbackItem['notification_state']) {
  testState.item = itemWithNotificationState(notificationState)
  render(
    <FeedbackItemDetailPanel
      itemId={12}
      fmtDate={(value) => value ?? '-'}
      onClose={vi.fn()}
    />,
  )
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
  fireEvent.click(screen.getByRole('button', { name: 'Next' }))
}

beforeEach(() => {
  clipboardWriteText.mockReset()
  clipboardWriteText.mockResolvedValue(undefined)
  Object.assign(navigator, {
    clipboard: { writeText: clipboardWriteText },
  })
  resolveMutate.mockReset()
  resolveMutate.mockImplementation(
    (
      _vars: unknown,
      options?: { onSuccess?: (result: PlatformFeedbackResolveResult) => void },
    ) => {
      options?.onSuccess?.({
        item: itemWithNotificationState('sent'),
        notifications: [],
        recipient_count: 1,
      })
    },
  )
})

describe('FeedbackItemDetailPanel update button label', () => {
  it('uses the first-send label until an update has actually been created', () => {
    renderPanel('not_needed')

    expect(
      screen.getByRole('button', { name: /Close bug and message user/ }),
    ).toBeTruthy()
    expect(screen.queryByRole('button', { name: /Resend update/ })).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /Close bug and message user/ }))

    expect(screen.getByRole('button', { name: /Resend update/ })).toBeTruthy()
  })

  it('uses the resend label for items that already have a created update', () => {
    renderPanel('sent')

    expect(screen.getByRole('button', { name: /Resend update/ })).toBeTruthy()
  })
})

describe('FeedbackItemDetailPanel feature prompt', () => {
  it('adds an LLM prompt step before completing a feature request', async () => {
    testState.item = itemWithNotificationState('not_needed', {
      kind: 'feature',
      title: 'Allow personal KBs for API keys',
      summary: 'Users want API keys to read personal KBs.',
      status: 'open',
    })

    render(
      <FeedbackItemDetailPanel
        itemId={12}
        fmtDate={(value) => value ?? '-'}
        onClose={vi.fn()}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByRole('heading', { name: 'Feature prompt' })).toBeTruthy()
    expect(
      screen.getByRole('button', { name: 'Copy feature prompt for LLM' }),
    ).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Copy feature prompt for LLM' }))

    await waitFor(() => {
      expect(clipboardWriteText).toHaveBeenCalledWith(
        expect.stringContaining('implementing a Klai product feature request'),
      )
    })
    expect(clipboardWriteText).toHaveBeenCalledWith(
      expect.stringContaining('Allow personal KBs for API keys'),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    expect(screen.getByRole('heading', { name: 'Complete feature' })).toBeTruthy()
  })
})
