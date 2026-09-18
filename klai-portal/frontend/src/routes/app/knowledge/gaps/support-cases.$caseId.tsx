// Support-case evidence detail (support-gap-detection.md "Existing inbox and
// transcript input"). One page per imported case, question-first: each analyser
// finding leads with the question, the source excerpts it was drawn from, and
// the articles it was compared against, so a reviewer reads the judgement next
// to its evidence. The full original conversation is available collapsed below.
// A finding is not always a gap — "already covered", "uncertain" and "not a
// knowledge question" are outcomes and are shown as such, never as gaps. Each
// finding carries a human review (correct / incorrect / uncertain + a note)
// stored separately from the machine analysis. Literal evidence is only
// returned for telemetry_level=full orgs; the page renders whatever the
// authorised response contains and shows explicit loading, pending, failed and
// empty states otherwise. Internal model IDs are deliberately not rendered.
import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ChevronDown, ChevronRight, ExternalLink, Loader2, RotateCw } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import * as m from '@/paraglide/messages'
import { ApiError, apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import {
  diagnosisLabel,
  findingKind,
  invalidateCaseCaches,
  supportCaseStatusBadge,
  type CaseMessage,
  type CaseReference,
} from './-support-helpers'
import { CaseFindingReview, type FindingReviewValue } from './_components/CaseFindingReview'
import { CaseRoleReview } from './_components/CaseRoleReview'
import { CaseReferenceForm } from './_components/CaseReferenceForm'
import { CaseStalePrompt } from './_components/CaseStalePrompt'

interface CasePayload {
  source: string
  account_id: string
  external_id: string
  subject: string
  language: string | null
  source_url: string | null
  source_updated_at: string | null
  complete: boolean
  incomplete_reasons: string[]
  messages: CaseMessage[]
  metadata: Record<string, unknown>
}

interface CaseArticle {
  source_url: string | null
  text: string
}

interface CaseFinding {
  question: string
  language: string | null
  diagnosis: string
  rationale: string
  missing_information: string
  audience: string | null
  message_ids: string[]
  articles: CaseArticle[]
  gap_type: string | null
  top_score: number | null
  review: FindingReviewValue | null
  // Optional analyser follow-through, rendered only when returned: the concrete
  // change it suggests, and why its knowledge-base comparison was limited.
  proposed_change?: string | null
  comparison_limitations?: string[] | null
  search_queries?: string[] | null
}

interface SupportCaseDetail {
  id: string
  kb_slug: string
  payload: CasePayload
  status: string
  analysis: CaseFinding[] | null
  analysis_version: string
  analysis_revision: string | null
  content_hash: string
  reference: CaseReference | null
  imported_at: string
}

export const Route = createFileRoute('/app/knowledge/gaps/support-cases/$caseId')({
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <SupportCaseDetailPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

/** Only http/https external links are followed; a backend-supplied link with
    any other scheme is dropped rather than executed. */
function safeHttpUrl(url: string | null): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

function roleLabel(role: CaseMessage['role']): string {
  if (role === 'customer') return m.support_case_role_customer()
  if (role === 'agent') return m.support_case_role_agent()
  return m.support_case_role_unknown()
}

/** Outcome pill colour: a real gap reads as an alert, "already covered" as a
    success, "uncertain" as a caution and "not a knowledge question" as neutral. */
function findingBadgeVariant(diagnosis: string): 'info' | 'success' | 'warning' | 'secondary' {
  switch (findingKind(diagnosis)) {
    case 'covered': return 'success'
    case 'uncertain': return 'warning'
    case 'non_knowledge': return 'secondary'
    default: return 'info'
  }
}

function MessageCard({ message }: { message: CaseMessage }) {
  return (
    <div className="rounded-lg border border-gray-200 p-3">
      <div className="flex flex-wrap items-center gap-2 mb-1">
        <Badge variant="secondary">{roleLabel(message.role)}</Badge>
        {message.medium && <Badge variant="outline">{message.medium}</Badge>}
        {message.visibility === 'internal' && (
          <Badge variant="outline">{m.support_case_visibility_internal()}</Badge>
        )}
        {message.start_seconds != null && message.end_seconds != null && (
          <span className="text-xs tabular-nums text-gray-500">
            {m.support_case_segment_time({
              start: String(Math.round(message.start_seconds)),
              end: String(Math.round(message.end_seconds)),
            })}
          </span>
        )}
        {message.occurred_at && (
          <span className="text-xs text-gray-500">{new Date(message.occurred_at).toLocaleString()}</span>
        )}
      </div>
      <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">{message.text}</p>
    </div>
  )
}

/** Re-runs the analyser for this case against the revision the reviewer saw.
    A failed re-analysis is surfaced as a visible failure, never a success
    toast; a 409 means the case changed underneath and prompts a refresh rather
    than discarding the reviewer's context. */
function ReanalyzeControl({
  caseId,
  kbSlug,
  analysisRevision,
}: {
  caseId: string
  kbSlug: string
  analysisRevision: string
}) {
  const queryClient = useQueryClient()
  const [stale, setStale] = useState(false)

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<{ case_id: string; status: string; changed: boolean; findings_count: number }>(
        `/api/app/knowledge-bases/${kbSlug}/support-cases/${caseId}/reanalyze`,
        { method: 'POST', body: JSON.stringify({ analysis_revision: analysisRevision }) },
      ),
    onSuccess: (res) => {
      setStale(false)
      invalidateCaseCaches(queryClient, caseId, kbSlug)
      if (res.status === 'failed') {
        toast.error(m.support_case_reanalyze_failed())
        return
      }
      toast.success(
        res.changed
          ? m.support_case_reanalyze_success({ count: String(res.findings_count) })
          : m.support_case_reanalyze_unchanged(),
      )
    },
    onError: (err: unknown) => {
      queryLogger.warn('Support case reanalyze failed', { error: err })
      if (err instanceof ApiError && err.status === 409) {
        setStale(true)
        return
      }
      if (err instanceof ApiError && err.status === 503) {
        invalidateCaseCaches(queryClient, caseId, kbSlug)
      }
      toast.error(m.support_case_reanalyze_error())
    },
  })

  return (
    <div className="flex flex-col items-end gap-2">
      <Button variant="outline" size="sm" disabled={mutation.isPending} onClick={() => mutation.mutate()}>
        {mutation.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin mr-2" />
        ) : (
          <RotateCw className="h-4 w-4 mr-2" />
        )}
        {mutation.isPending ? m.support_case_reanalyzing() : m.support_case_reanalyze()}
      </Button>
      {stale && <CaseStalePrompt caseId={caseId} message={m.support_case_reanalyze_stale()} />}
    </div>
  )
}

export function SupportCaseDetailPage() {
  const { caseId } = Route.useParams()
  const [conversationOpen, setConversationOpen] = useState(false)

  const caseQuery = useQuery<SupportCaseDetail>({
    queryKey: ['support-case', caseId],
    queryFn: async () => {
      try {
        return await apiFetch<SupportCaseDetail>(`/api/app/gaps/support-cases/${caseId}`)
      } catch (err) {
        queryLogger.warn('Support case fetch failed', { error: err })
        throw err
      }
    },
    retry: false,
  })

  const detail = caseQuery.data
  const backButton = (
    <Button variant="outline" size="sm" asChild>
      {detail ? (
        <Link to="/app/knowledge/gaps/support-cases" search={{ kbSlug: detail.kb_slug }}>
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.support_cases_back_to_list()}
        </Link>
      ) : (
        <Link to="/app/knowledge/gaps">
          <ArrowLeft className="h-4 w-4 mr-2" />
          {m.support_case_back()}
        </Link>
      )}
    </Button>
  )

  if (caseQuery.isLoading) {
    return (
      <PageContainer width="3xl">
        <ListLoadingState label={m.support_case_loading()} />
      </PageContainer>
    )
  }

  if (caseQuery.isError) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <QueryErrorState error={caseQuery.error} onRetry={() => void caseQuery.refetch()} />
      </PageContainer>
    )
  }

  if (!detail) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <ListEmptyState title={m.support_case_empty()} />
      </PageContainer>
    )
  }

  const { payload } = detail
  const callMessages = payload.messages.filter((message) => message.medium === 'call' || message.kind === 'transcript')
  const status = supportCaseStatusBadge(detail.status)
  const sourceUrl = safeHttpUrl(payload.source_url)
  const messageById = new Map(payload.messages.map((message) => [message.id, message]))
  const findings = detail.analysis ?? []

  return (
    <PageContainer width="3xl">
      <PageHeader
        title={payload.subject || m.support_case_title()}
        description={payload.language}
        actions={backButton}
      />
      <div><Badge variant={status.variant}>{status.label}</Badge></div>

      {!payload.complete && payload.incomplete_reasons.length > 0 && (
        <div className="mb-6 rounded-lg border border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5 p-3">
          <p className="text-sm font-medium text-[var(--color-warning-text)]">
            {m.support_case_incomplete_reasons()}
          </p>
          <ul className="mt-1 list-disc pl-5 text-sm text-gray-600">
            {payload.incomplete_reasons.map((reason, i) => (
              <li key={i}>{reason}</li>
            ))}
          </ul>
        </div>
      )}

      {sourceUrl && (
        <a
          href={sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="mb-6 inline-flex items-center gap-1 text-sm text-rl-dark underline decoration-rl-accent/60 underline-offset-2 hover:text-accent-text"
        >
          {m.support_case_open_source()}
          <ExternalLink className="h-3.5 w-3.5" />
        </a>
      )}

      {/* Call evidence arrives with every speaker "unknown"; the reviewer sets
          roles here before the analysis below can be trusted. */}
      {payload.complete && detail.analysis_revision && callMessages.length > 0 && (
        <details className="mb-6 rounded-xl border border-gray-200">
          <summary className="cursor-pointer rounded-xl p-4 text-sm font-semibold focus-visible:outline-2 focus-visible:outline-offset-2">
            {m.support_case_roles_heading()}
            {callMessages.some((message) => message.role === 'unknown') && (
              <Badge variant="warning" className="ml-2">{m.support_case_roles_needs_review()}</Badge>
            )}
          </summary>
          <CaseRoleReview
            caseId={caseId}
            kbSlug={detail.kb_slug}
            analysisRevision={detail.analysis_revision}
            messages={callMessages}
          />
        </details>
      )}

      {/* Analysis — question-first, one block per finding. */}
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-gray-900">{m.support_case_analysis_heading()}</h2>
        {payload.complete && detail.analysis_revision && (
          <ReanalyzeControl
            caseId={caseId}
            kbSlug={detail.kb_slug}
            analysisRevision={detail.analysis_revision}
          />
        )}
      </div>
      {detail.status === 'pending' ? (
        <ListEmptyState title={m.support_case_analysis_pending()} />
      ) : detail.status === 'failed' ? (
        <ListEmptyState title={m.support_case_analysis_failed()} />
      ) : findings.length === 0 ? (
        <ListEmptyState title={m.support_case_no_findings()} />
      ) : (
        <div className="space-y-4">
          {findings.map((finding, i) => {
            const kind = findingKind(finding.diagnosis)
            const citedMessages = finding.message_ids
              .map((id) => messageById.get(id))
              .filter((message): message is CaseMessage => message !== undefined)
            const hasCustomerCitation = citedMessages.some((message) => message.role === 'customer')
            return (
              <div key={`${caseId}:${detail.analysis_revision}:${i}`} className="rounded-xl border border-gray-200 p-4 space-y-3">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant={findingBadgeVariant(finding.diagnosis)}>
                    {diagnosisLabel(finding.diagnosis)}
                  </Badge>
                  {finding.audience && (
                    <span className="text-xs text-gray-500">
                      {m.support_case_finding_audience()}: {finding.audience}
                    </span>
                  )}
                </div>
                <p className="text-sm font-medium text-gray-900">{finding.question}</p>

                {/* Uncertain attribution without a customer message reads as a
                    caution, not a gap: explain it in the reader's language. */}
                {kind === 'uncertain' && !hasCustomerCitation && (
                  <p className="text-sm text-[var(--color-warning-text)]">
                    {m.support_case_outcome_uncertain_no_role()}
                  </p>
                )}

                {finding.rationale && (
                  <div>
                    <p className="text-xs font-medium text-gray-500">{m.support_case_finding_rationale()}</p>
                    <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">{finding.rationale}</p>
                  </div>
                )}
                {finding.missing_information && (
                  <div>
                    <p className="text-xs font-medium text-gray-500">{m.support_case_finding_missing()}</p>
                    <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">
                      {finding.missing_information}
                    </p>
                  </div>
                )}
                {finding.proposed_change && (
                  <div>
                    <p className="text-xs font-medium text-gray-500">
                      {m.support_case_finding_proposed_change()}
                    </p>
                    <p className="text-sm text-gray-900 whitespace-pre-wrap break-words">
                      {finding.proposed_change}
                    </p>
                  </div>
                )}
                {finding.comparison_limitations && finding.comparison_limitations.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500">
                      {m.support_case_finding_comparison_limitations()}
                    </p>
                    <ul className="list-disc pl-5 text-sm text-gray-900">
                      {finding.comparison_limitations.map((limit, k) => (
                        <li key={k} className="break-words">{limit}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {finding.search_queries && finding.search_queries.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500">
                      {m.support_case_finding_search_queries()}
                    </p>
                    <ul className="list-disc pl-5 text-sm text-gray-600">
                      {finding.search_queries.map((query, k) => (
                        <li key={k} className="break-words">{query}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="grid gap-3 md:grid-cols-2">
                  {citedMessages.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-gray-500">
                        {m.support_case_finding_cited_messages()}
                      </p>
                      {citedMessages.map((message) => (
                        <MessageCard key={message.id} message={message} />
                      ))}
                    </div>
                  )}
                  {finding.articles.length > 0 && (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-gray-500">{m.support_case_finding_articles()}</p>
                      {finding.articles.map((article, j) => {
                        const articleUrl = safeHttpUrl(article.source_url)
                        return (
                          <div key={j} className="rounded-lg border border-gray-200 p-3">
                            {articleUrl && (
                              <a
                                href={articleUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="mb-1 inline-flex items-center gap-1 text-xs text-rl-dark underline decoration-rl-accent/60 underline-offset-2 hover:text-accent-text"
                              >
                                {m.support_case_open_article()}
                                <ExternalLink className="h-3 w-3" />
                              </a>
                            )}
                            <p className="text-sm text-gray-600 whitespace-pre-wrap break-words">{article.text}</p>
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>

                {detail.status === 'analyzed' && detail.analysis_revision && (
                  <CaseFindingReview
                    caseId={caseId}
                    kbSlug={detail.kb_slug}
                    analysisRevision={detail.analysis_revision}
                    findingIndex={i}
                    machineDiagnosis={finding.diagnosis}
                    review={finding.review}
                  />
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Whole-case reference answer key, the reviewer's own record. */}
      {detail.content_hash && (
        <div className="mt-8">
          <CaseReferenceForm
            caseId={caseId}
            kbSlug={detail.kb_slug}
            contentHash={detail.content_hash}
            messages={payload.messages}
            reference={detail.reference}
          />
        </div>
      )}

      {/* Full original conversation, collapsed by default. */}
      {payload.messages.length > 0 && (
        <div className="mt-8">
          <button
            type="button"
            className="flex items-center gap-1 text-sm font-semibold text-gray-900 klai-hover rounded-md px-1 py-1"
            aria-expanded={conversationOpen}
            onClick={() => setConversationOpen((open) => !open)}
          >
            {conversationOpen ? (
              <ChevronDown className="h-4 w-4" />
            ) : (
              <ChevronRight className="h-4 w-4" />
            )}
            {conversationOpen ? m.support_case_conversation_hide() : m.support_case_conversation_show()}
          </button>
          {conversationOpen && (
            <div className="mt-2 space-y-2">
              {payload.messages.map((message) => (
                <MessageCard key={message.id} message={message} />
              ))}
            </div>
          )}
        </div>
      )}
    </PageContainer>
  )
}
