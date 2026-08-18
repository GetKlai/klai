import { describe, expect, it } from 'vitest'
import { buildFeedbackDebugInstructions } from './-feedback-helpers'
import type {
  PlatformFeedbackItem,
  PlatformFeedbackLinkedSubmission,
} from '../../-types'

function makeItem(): PlatformFeedbackItem {
  return {
    id: 24,
    kind: 'bug',
    title: 'Assistent geeft verkeerde antwoorden',
    summary: 'Assistent geeft verkeerde antwoorden.',
    status: 'open',
    area: 'assistant_core',
    priority_score: 11,
    org_count: 1,
    user_count: 1,
    shipped_at: null,
    resolution_summary: null,
    resolved_at: null,
    resolved_by: null,
    notification_state: null,
    reporter_orgs: [],
    created_at: '2026-08-17T12:00:00Z',
    updated_at: '2026-08-17T12:00:00Z',
  }
}

function makeSubmission(
  overrides: Partial<PlatformFeedbackLinkedSubmission> = {},
): PlatformFeedbackLinkedSubmission {
  return {
    id: 29,
    org_id: 8,
    org_name: 'Acme',
    org_slug: 'acme',
    user_id: '1000000000000000001',
    user_email: 'ada@example.test',
    user_display_name: 'Ada Acme',
    event_type: 'klai_assistant.problem_report',
    status: 'open',
    raw_text: 'Assistent geeft verkeerde antwoorden.',
    feedback_type: 'confusing',
    severity: null,
    page_url: 'https://acme.getklai.com/app/chat',
    route_id: '/app/chat',
    locale: 'en',
    viewport: '3440x1271',
    chat_context: null,
    created_at: '2026-08-17T12:00:08Z',
    triage_suggestion: null,
    linked_item_id: 24,
    linked_item_title: null,
    linked_item_status: null,
    link_type: 'evidence',
    linked_at: '2026-08-17T12:00:08Z',
    ...overrides,
  }
}

describe('buildFeedbackDebugInstructions chat context', () => {
  it('lists the reporter conversations captured at report time', () => {
    const prompt = buildFeedbackDebugInstructions(
      makeItem(),
      [
        makeSubmission({
          chat_context: {
            recent_conversations: [
              {
                conversation_id: '00000000-0000-4000-8000-000000000001',
                title: 'Routing question',
                model: 'klai-primary',
                url: 'https://chat-acme.getklai.com/c/00000000-0000-4000-8000-000000000001',
                created_at: '2026-01-10T09:12:29+00:00',
                updated_at: '2026-01-10T11:54:32+00:00',
              },
            ],
          },
        }),
      ],
      (value) => value ?? '-',
    )

    expect(prompt).toContain('Recent chat conversations at report time (recency-based candidates; the report may concern an older conversation not listed here):')
    expect(prompt).toContain('"Routing question"')
    expect(prompt).toContain('last activity 2026-01-10T11:54:32+00:00')
    expect(prompt).toContain(
      'https://chat-acme.getklai.com/c/00000000-0000-4000-8000-000000000001',
    )
  })

  it('omits the chat section when no chat context was captured', () => {
    const prompt = buildFeedbackDebugInstructions(
      makeItem(),
      [makeSubmission()],
      (value) => value ?? '-',
    )

    expect(prompt).not.toContain('Recent chat conversations at report time')
    expect(prompt).toContain('Assistent geeft verkeerde antwoorden.')
  })
})
