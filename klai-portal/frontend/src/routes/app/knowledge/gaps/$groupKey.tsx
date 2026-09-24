// Detail screen for one gap group (SPEC-KNOWLEDGE-ACTIVITY-001 §4.9 two-screen
// redesign). A group has no persisted row id -- it is a computed aggregate
// over (source, query_text, gap_type, language, diagnosis, nearest_kb_slug,
// audience, group_key) -- so the route param is a one-way digest of that tuple
// (-gap-identity.ts explains why it is a digest and not the tuple itself). This
// page re-fetches the list widened to the full 90-day/include-resolved range
// and locates the matching group client-side, per the "use the existing API
// as-is" contract (no gap-by-id endpoint exists).
import { useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowLeft, FileText, MessageCircle } from 'lucide-react'
import { toast } from 'sonner'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { StatCard } from '@/components/ui/stat-card'
import { ListLoadingState, ListEmptyState } from '@/components/ui/list-state'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { fetchMe } from '@/lib/api-me'
import { queryLogger } from '@/lib/logger'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { Tooltip } from '@/components/ui/tooltip'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { appNavActivityIsVisible, appNavGapsIsVisible } from '@/routes/app/-app-tools'
import { diagnosisLabel } from './-support-helpers'
import { findGapByDigest } from './-gap-identity'
import { closerLabel, formatRelativeTime, type GapRow, type GapsResponse } from './-gap-types'

export const Route = createFileRoute('/app/knowledge/gaps/$groupKey')({
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <GapDetailPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

const backButton = (
  <Button variant="outline" size="sm" asChild>
    <Link to="/app/knowledge/gaps">
      <ArrowLeft className="h-4 w-4 mr-2" />
      {m.support_cases_back_to_inbox()}
    </Link>
  </Button>
)

function gapTypeLabel(gapType: string): string {
  if (gapType === 'hard') return m.gaps_type_hard()
  if (gapType === 'content') return m.gaps_type_content()
  return m.gaps_type_soft()
}

export function GapDetailPage() {
  const { groupKey } = Route.useParams()
  const auth = useAuth()
  const { user } = useCurrentUser()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)

  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  const hasActivityCapability = user?.hasCapability('kb.activity') === true
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasGapsCapability,
  })
  const me = meQuery.data
  const access = {
    hasCapability: (cap: string) => user?.hasCapability(cap) === true,
    unlockedFeatures: me?.platform_unlocked_features ?? [],
  }
  const gapsUnlocked = appNavGapsIsVisible(access)
  const canDrillIntoActivity = hasActivityCapability && appNavActivityIsVisible(access)


  // Widened to the full supported range and every status: this page has no
  // by-id endpoint to call, so it re-fetches the list and finds its one row by
  // digest (see file header). The digest cannot be read back into a filter, and
  // that is the point — the visitor's question stays out of the URL.
  const detailQuery = useQuery<GapsResponse>({
    queryKey: ['app-gap-detail', groupKey],
    queryFn: async () => {
      const params = new URLSearchParams({ days: '90', limit: '200', include_resolved: 'true' })
      try {
        return await apiFetch<GapsResponse>(`/api/app/gaps?${params}`)
      } catch (err) {
        queryLogger.warn('Gap detail fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated && hasGapsCapability && gapsUnlocked && groupKey !== '',
    retry: false,
  })

  const gap: GapRow | undefined = findGapByDigest(detailQuery.data?.gaps ?? [], groupKey)

  // One body shape for every source: the backend treats group_key as
  // authoritative when present (it matches every folded finding sharing that
  // persisted key, regardless of wording), and falls back to the exact-text
  // legacy match only when both group_key and diagnosis are absent -- which is
  // the case for every automatic/review row today. Branching on source here
  // would silently drop group_key for those rows once they start carrying one.
  const resolveMutation = useMutation({
    mutationFn: () => {
      if (!gap) throw new Error('no gap loaded')
      const body = {
        query_text: gap.query_text,
        gap_type: gap.gap_type,
        language: gap.language,
        diagnosis: gap.diagnosis,
        audience: gap.audience,
        nearest_kb_slug: gap.nearest_kb_slug,
        group_key: gap.group_key,
      }
      return apiFetch<{ resolved: number }>('/api/app/gaps/resolve', {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['app-gaps'] })
      void queryClient.invalidateQueries({ queryKey: ['app-gap-detail', groupKey] })
    },
    onError: (err: unknown) => {
      queryLogger.warn('Gap close failed', { error: err })
      toast.error(m.gaps_close_failed())
    },
    onSettled: () => setConfirming(false),
  })

  if (!hasGapsCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">{m.gaps_page_title()}</h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">{m.capability_tooltip_knowledge_only()}</p>
        </Tooltip>
      </div>
    )
  }

  if (meQuery.isLoading) {
    return (
      <PageContainer width="3xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (meQuery.isError) {
    return (
      <PageContainer width="3xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }
  if (me !== undefined && !gapsUnlocked) {
    return (
      <PageContainer width="3xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.gaps_unlock_required()} />
      </PageContainer>
    )
  }

  if (detailQuery.isLoading) {
    return (
      <PageContainer width="3xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (detailQuery.isError) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <QueryErrorState error={detailQuery.error} onRetry={() => void detailQuery.refetch()} />
      </PageContainer>
    )
  }
  if (!gap) {
    return (
      <PageContainer width="3xl" gap="6">
        <div className="flex justify-end">{backButton}</div>
        <ListEmptyState icon={AlertTriangle} title={m.gaps_detail_not_found()} />
      </PageContainer>
    )
  }

  const isResolved = gap.resolved_at != null
  const topScorePct = gap.top_score != null ? Math.round(gap.top_score * 100) : null

  return (
    <PageContainer width="3xl" gap="6">
      <PageHeader title={gap.query_text} actions={backButton} />

      <div className="grid gap-3 sm:grid-cols-3">
        <StatCard label={m.gaps_detail_stat_conversations()} value={gap.occurrence_count} />
        <StatCard
          label={m.gaps_detail_stat_issue()}
          value={
            <Badge variant={gap.source === 'support' ? 'info' : gap.gap_type === 'hard' ? 'destructive' : 'warning'}>
              {gap.source === 'support' && gap.diagnosis ? diagnosisLabel(gap.diagnosis) : gapTypeLabel(gap.gap_type)}
            </Badge>
          }
        />
        <StatCard
          label={m.gaps_detail_stat_knowledge()}
          value={gap.nearest_kb_slug ?? undefined}
          sub={topScorePct != null ? m.gaps_detail_top_score({ score: String(topScorePct) }) : undefined}
        />
      </div>

      <div>
        <Badge variant={isResolved ? 'secondary' : 'outline'}>
          {isResolved ? m.gaps_status_resolved() : m.gaps_status_open()}
        </Badge>
        {isResolved && (
          <span className="ml-2 text-sm text-gray-600">
            {closerLabel(gap)
              ? m.gaps_resolved_line({ time: formatRelativeTime(gap.resolved_at!), closer: closerLabel(gap) })
              : m.gaps_resolved_line_no_actor({ time: formatRelativeTime(gap.resolved_at!) })}
          </span>
        )}
      </div>

      {(gap.support_case_ids.length > 0 || (gap.conversation_id != null && canDrillIntoActivity)) && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-gray-900">{m.gaps_detail_evidence_heading()}</h2>
          <div className="flex flex-wrap gap-2">
            {gap.support_case_ids.map((caseId) => (
              <Button key={caseId} variant="outline" size="sm" asChild>
                <Link to="/app/knowledge/gaps/support-cases/$caseId" params={{ caseId: String(caseId) }}>
                  <FileText className="h-4 w-4 mr-2" />
                  {m.gaps_detail_evidence_case_link({ id: String(caseId) })}
                </Link>
              </Button>
            ))}
            {gap.conversation_id != null && canDrillIntoActivity && (
              <Button variant="outline" size="sm" asChild>
                <Link to="/app/knowledge/activity/$conversationId" params={{ conversationId: String(gap.conversation_id) }}>
                  <MessageCircle className="h-4 w-4 mr-2" />
                  {m.gaps_action_open_conversation()}
                </Link>
              </Button>
            )}
          </div>
        </div>
      )}

      {!isResolved && (
        <div className="flex items-center gap-2">
          {confirming ? (
            <>
              <span className="text-sm text-gray-600">{m.gaps_close_confirm_prompt()}</span>
              <Button size="sm" disabled={resolveMutation.isPending} onClick={() => resolveMutation.mutate()}>
                {m.gaps_close_confirm()}
              </Button>
              <Button variant="outline" size="sm" disabled={resolveMutation.isPending} onClick={() => setConfirming(false)}>
                {m.gaps_close_cancel()}
              </Button>
            </>
          ) : (
            <Button variant="outline" size="sm" onClick={() => setConfirming(true)}>
              {m.gaps_action_close()}
            </Button>
          )}
        </div>
      )}
    </PageContainer>
  )
}
