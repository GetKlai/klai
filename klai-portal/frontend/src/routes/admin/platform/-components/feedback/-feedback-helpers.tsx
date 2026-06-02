import { type ReactNode } from "react"
import * as m from "@/paraglide/messages"
import type {
  PlatformFeedbackItem,
  PlatformFeedbackLinkedSubmission,
  PlatformFeedbackSubmission,
} from "../../-types"

/** Feedback item statuses considered closed (hidden from the active filter). */
export const CLOSED_FEEDBACK_ITEM_STATUSES = new Set(["resolved", "dismissed"])

export function feedbackKindLabel(eventType: string): string {
  if (eventType === 'klai_assistant.question') return m.platform_feedback_kind_question()
  if (eventType === 'klai_assistant.problem_report') return m.platform_feedback_kind_problem()
  return m.platform_feedback_kind_feedback()
}

export function feedbackSubmissionReporterLabel(item: PlatformFeedbackSubmission): string | null {
  return item.user_display_name || item.user_email || item.user_id || null
}

/**
 * Human label for a submission's reporter-chosen signal: the feedback type
 * (idea/improvement/...) or the problem severity (blocked/workaround/minor).
 * Returns null for unknown codes so raw enum values never leak into the UI.
 */
export function feedbackSignalLabel(item: PlatformFeedbackSubmission): string | null {
  switch (item.feedback_type) {
    case 'idea':
      return m.klai_assistant_feedback_type_idea()
    case 'improvement':
      return m.klai_assistant_feedback_type_improvement()
    case 'confusing':
      return m.klai_assistant_feedback_type_confusing()
    case 'missing':
      return m.klai_assistant_feedback_type_missing()
    case 'compliment':
      return m.klai_assistant_feedback_type_compliment()
  }
  switch (item.severity) {
    case 'blocked':
      return m.klai_assistant_problem_severity_blocked()
    case 'workaround':
      return m.klai_assistant_problem_severity_workaround()
    case 'minor':
      return m.klai_assistant_problem_severity_minor()
  }
  return null
}

export function feedbackStatusLabel(status: string): string {
  if (status === 'open') return m.platform_feedback_status_open()
  if (status === 'resolved') return m.platform_feedback_status_resolved()
  if (status === 'dismissed') return m.platform_feedback_status_dismissed()
  if (status === 'support') return m.platform_feedback_status_support()
  return m.platform_feedback_status_new()
}

export function feedbackItemStatusLabel(status: string): string {
  if (status === 'resolved') return m.platform_feedback_status_resolved()
  if (status === 'dismissed') return m.platform_feedback_status_dismissed()
  return m.platform_feedback_status_open()
}

export function feedbackItemReporterSummary(item: PlatformFeedbackItem): string {
  const names = item.reporter_orgs
    .map((org) => org.org_name ?? org.org_slug ?? (org.org_id ? `#${org.org_id}` : null))
    .filter((name): name is string => Boolean(name))

  if (names.length === 0) {
    return item.org_count > 0
      ? m.platform_feedback_org_count({ count: item.org_count })
      : '-'
  }
  if (names.length <= 2) return names.join(', ')
  return `${names.slice(0, 2).join(', ')} +${names.length - 2}`
}

export function feedbackItemKindLabel(kind: string): string {
  if (kind === 'bug') return m.platform_feedback_item_kind_bug()
  if (kind === 'ux_confusion') return m.platform_feedback_item_kind_ux()
  if (kind === 'docs') return m.platform_feedback_item_kind_docs()
  if (kind === 'support_pattern') return m.platform_feedback_item_kind_support()
  return m.platform_feedback_item_kind_feature()
}

export function feedbackLinkTypeLabel(linkType: string): string {
  if (linkType === 'upvote') return m.platform_feedback_link_type_upvote()
  if (linkType === 'bug_repro') return m.platform_feedback_link_type_bug_repro()
  if (linkType === 'support_signal') return m.platform_feedback_link_type_support_signal()
  return m.platform_feedback_link_type_evidence()
}

export function feedbackSuggestionActionLabel(action: string | null | undefined): string {
  if (action === 'link_existing') return m.platform_feedback_action_link_existing()
  if (action === 'create_item') return m.platform_feedback_action_create_item()
  if (action === 'support') return m.platform_feedback_action_support()
  if (action === 'dismiss') return m.platform_feedback_action_dismiss()
  if (action === 'review') return m.platform_feedback_action_review()
  return m.platform_feedback_action_review()
}

export function feedbackSuggestionPrimaryLabel(
  action: string | null | undefined,
  candidateTitle: string | null | undefined,
  kind: string,
): string {
  if (action === 'link_existing') {
    const shortTitle =
      candidateTitle && candidateTitle.length > 44
        ? `${candidateTitle.slice(0, 41)}...`
        : candidateTitle
    return shortTitle
      ? m.platform_feedback_primary_link_to({ title: shortTitle })
      : m.platform_feedback_primary_link_existing()
  }
  if (action === 'support') return m.platform_feedback_primary_support()
  if (action === 'dismiss') return m.platform_feedback_primary_dismiss()
  if (action === 'review') return m.platform_feedback_primary_review()
  return m.platform_feedback_primary_create({ kind: feedbackItemKindLabel(kind).toLowerCase() })
}

export function feedbackItemSearchTerm(
  item: PlatformFeedbackSubmission,
  suggestion: PlatformFeedbackSubmission['triage_suggestion'],
): string {
  const candidateTitle = suggestion?.duplicate_candidates[0]?.title
  if (candidateTitle) return candidateTitle.slice(0, 80)

  const source = suggestion?.summary || item.raw_text || suggestion?.suggested_area || ''
  const words = source
    .toLowerCase()
    .replace(/[^a-z0-9_ -]+/g, ' ')
    .split(/\s+/)
    .filter((word) => word.length >= 4)
    .filter(
      (word) =>
        ![
          'voor',
          'door',
          'naar',
          'niet',
          'geen',
          'deze',
          'daar',
          'hier',
          'kunnen',
          'willen',
          'moeten',
          'zodat',
          'voordat',
          'soms',
          'eens',
          'with',
          'from',
          'that',
          'this',
        ].includes(word),
    )

  const search = words.slice(0, 2).join(' ')
  return search || source.slice(0, 80)
}

export function feedbackFallbackSummary(item: PlatformFeedbackSubmission): string {
  if (item.event_type === 'klai_assistant.problem_report') {
    return m.platform_feedback_fallback_bug({
      text: item.raw_text || m.platform_feedback_no_description(),
    })
  }
  return item.raw_text || m.platform_feedback_no_description()
}

export function normalizedFeedbackKind(kind: string | null | undefined, fallback: string): string {
  if (
    kind &&
    ['feature', 'bug', 'ux_confusion', 'docs', 'support_pattern'].includes(kind)
  ) {
    return kind
  }
  return fallback
}

export function feedbackResolveLabel(kind: string) {
  if (kind === 'bug') {
    return {
      title: m.platform_feedback_resolve_bug_title(),
      button: m.platform_feedback_resolve_bug_button(),
      subject: m.platform_feedback_resolve_bug_subject(),
    }
  }
  if (kind === 'feature') {
    return {
      title: m.platform_feedback_resolve_feature_title(),
      button: m.platform_feedback_resolve_feature_button(),
      subject: m.platform_feedback_resolve_feature_subject(),
    }
  }
  return {
    title: m.platform_feedback_resolve_report_title(),
    button: m.platform_feedback_resolve_report_button(),
    subject: m.platform_feedback_resolve_report_subject(),
  }
}

export function feedbackActionErrorMessage(error: unknown): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return m.platform_feedback_action_failed()
}

export function defaultResolutionSummary(item: PlatformFeedbackItem) {
  if (item.kind === 'bug') {
    return m.platform_feedback_default_resolution_bug({ title: item.title })
  }
  if (item.kind === 'feature') {
    return m.platform_feedback_default_resolution_feature({ title: item.title })
  }
  return m.platform_feedback_default_resolution_report({ title: item.title })
}

export function FeedbackMetaRow({
  label,
  value,
}: {
  label: string
  value: ReactNode
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium text-gray-400">{label}</p>
      <div className="text-sm text-gray-900">{value || '-'}</div>
    </div>
  )
}

export function buildFeedbackDebugInstructions(
  item: PlatformFeedbackItem,
  submissions: PlatformFeedbackLinkedSubmission[],
  fmtDate: (s: string | null) => string,
) {
  const distinct = (values: Array<string | null | undefined>) => [
    ...new Set(values.map((value) => (value ?? '').trim()).filter(Boolean)),
  ]
  const routes = distinct(submissions.map((submission) => submission.route_id))
  const urls = distinct(submissions.map((submission) => submission.page_url))
  const locales = distinct(submissions.map((submission) => submission.locale))

  const location = [
    `- Area: ${item.area || 'unknown'}`,
    `- Routes: ${routes.length ? routes.join(', ') : 'unknown'}`,
    `- Page URLs: ${urls.length ? urls.join(', ') : 'unknown'}`,
    `- Locales seen: ${locales.length ? locales.join(', ') : 'unknown'}`,
  ].join('\n')

  const evidence = submissions.length
    ? submissions
        .map((submission, index) =>
          [
            `${index + 1}. ${submission.raw_text || '(empty)'}`,
            `   Org: ${submission.org_name ?? submission.org_slug ?? 'unknown'}`,
            `   Reporter: ${feedbackSubmissionReporterLabel(submission) || submission.user_id || 'unknown'}`,
            `   Type/severity: ${[submission.feedback_type, submission.severity].filter(Boolean).join(' / ') || 'unknown'}`,
            `   URL: ${submission.page_url || 'unknown'}`,
            `   Route: ${submission.route_id || 'unknown'}`,
            `   Locale/viewport: ${[submission.locale, submission.viewport].filter(Boolean).join(' / ') || 'unknown'}`,
            `   Submitted: ${fmtDate(submission.created_at)}`,
          ].join('\n'),
        )
        .join('\n\n')
    : 'No linked feedback evidence yet.'

  return [
    'You are fixing a Klai production bug from the Platform feedback workflow.',
    '',
    'Goal:',
    `Fix the ${item.kind} item #${item.id}: ${item.title}`,
    '',
    'Affected location (where to look first):',
    location,
    '',
    'Current item state:',
    `- Kind: ${item.kind}`,
    `- Status: ${item.status}`,
    `- Priority score: ${item.priority_score}`,
    `- Reporter signal: ${item.org_count} org(s), ${item.user_count} user(s)`,
    '',
    'Internal note:',
    item.summary || '(empty)',
    '',
    'Linked customer evidence:',
    evidence,
    '',
    'Approach (leave the code better than you found it):',
    '1. Start from the Area and Routes above to locate the affected module.',
    '2. Reproduce or trace the issue from the linked URL, route, and raw customer text. Confirm the real root cause before changing anything; treat the customer report as a symptom, not the diagnosis.',
    '3. Fix the root cause with the cleanest solution that fits the existing architecture and patterns. Do not patch around the symptom.',
    '4. If the root cause is a deeper design or architectural problem, investigate it and fix it properly. If a full fix is genuinely out of scope, write down exactly what the underlying problem is and what the right fix would be.',
    '5. Leave the code you touch better than you found it (clearer names, less duplication, better structure), while keeping the change proportionate to the problem. Elegant, not a sprawling rewrite.',
    '6. Add or update focused regression coverage for the fixed behavior.',
    '7. Report the root cause, changed files, any architectural findings, tests run, and residual risk.',
  ].join('\n')
}
