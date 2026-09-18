// Support-case list (support-gap-detection.md "Existing inbox and transcript
// input"). Every imported case for one organisation KB, whatever the analysis
// outcome: cases that produced content gaps, cases the KB already covered,
// uncertain cases and cases whose analysis is still pending or failed. The gap
// inbox only shows the questions that became gap rows; this overview is where a
// reviewer sees the full set and opens a case to judge its analysis. The KB
// picker offers organisation KBs only (no hardcoded slug) and drives the fetch.
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { AlertTriangle, FileText } from 'lucide-react'
import { useAuth } from '@/lib/auth'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { PageContainer } from '@/components/ui/page-container'
import { PageHeader } from '@/components/ui/page-header'
import { Pagination } from '@/components/ui/pagination'
import {
  DataTable,
  DataTableBody,
  DataTableCell,
  DataTableHead,
  DataTableHeader,
  DataTableRow,
} from '@/components/ui/data-table'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { RoleGuard } from '@/components/layout/RoleGuard'
import { Tooltip } from '@/components/ui/tooltip'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { fetchMe } from '@/lib/api-me'
import { appNavGapsIsVisible } from '@/routes/app/-app-tools'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'
import * as m from '@/paraglide/messages'
import { getLocale } from '@/paraglide/runtime'
import { caseSourceLabel, supportCaseStatusBadge } from './-support-helpers'

const PAGE_SIZE = 25

type SupportCasesSearch = { kbSlug?: string; offset?: number }

interface SupportCaseListItem {
  id: number
  kb_slug: string
  subject: string
  source: string
  status: string
  imported_at: string
  mediums: string[]
  question_count: number
  uncertain_count: number
  reviewed_count: number
}

interface SupportCasesResponse {
  cases: SupportCaseListItem[]
  total: number
}

interface KnowledgeBase {
  id: number
  name: string
  slug: string
  owner_type: string
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

export const Route = createFileRoute('/app/knowledge/gaps/support-cases/')({
  validateSearch: (search: Record<string, unknown>): SupportCasesSearch => ({
    kbSlug: typeof search.kbSlug === 'string' && search.kbSlug ? search.kbSlug : undefined,
    offset: Number.isInteger(Number(search.offset)) && Number(search.offset) > 0 ? Number(search.offset) : undefined,
  }),
  component: () => (
    <ProductGuard product="knowledge">
      <RoleGuard minRole="kb_manager">
        <SupportCasesListPage />
      </RoleGuard>
    </ProductGuard>
  ),
})

export function SupportCasesListPage() {
  const auth = useAuth()
  const { user } = useCurrentUser()
  const hasGapsCapability = user?.hasCapability('kb.gaps') === true
  const navigate = useNavigate({ from: '/app/knowledge/gaps/support-cases/' })
  const { kbSlug: kbSlugParam, offset: offsetParam } = Route.useSearch()
  const offset = offsetParam ?? 0

  // The unlock read mirrors the gaps inbox: the sidebar and the screen agree on
  // when the feature is on (knowledge_gaps unlock), so a capable user without
  // the unlock sees the same locked state, not an empty list.
  const meQuery = useQuery({
    queryKey: ['me'],
    queryFn: ({ signal }) => fetchMe(signal),
    enabled: hasGapsCapability,
  })
  const gapsUnlocked = appNavGapsIsVisible({
    hasCapability: (cap) => user?.hasCapability(cap) === true,
    unlockedFeatures: meQuery.data?.platform_unlocked_features ?? [],
  })

  // Same query key and endpoint the gaps inbox uses for its KB picker, so the
  // two screens share one cache entry (shared UI contract).
  const kbsQuery = useQuery<KBsResponse>({
    queryKey: ['app-knowledge-bases-for-gaps'],
    queryFn: () => apiFetch<KBsResponse>('/api/app/knowledge-bases'),
    enabled: auth.isAuthenticated && hasGapsCapability && gapsUnlocked,
    retry: false,
  })
  const orgKbs = (kbsQuery.data?.knowledge_bases ?? []).filter((kb) => kb.owner_type === 'org')

  // Default to the first organisation KB when the URL names none, so the
  // overview is never empty just because no slug was passed in.
  const kbSlug = kbSlugParam ?? orgKbs[0]?.slug ?? ''

  const casesQuery = useQuery<SupportCasesResponse>({
    queryKey: ['support-cases', kbSlug, offset],
    queryFn: async () => {
      try {
        return await apiFetch<SupportCasesResponse>(
          `/api/app/knowledge-bases/${kbSlug}/support-cases?limit=${PAGE_SIZE}&offset=${offset}`,
        )
      } catch (err) {
        queryLogger.warn('Support cases fetch failed', { error: err })
        throw err
      }
    },
    enabled: auth.isAuthenticated && hasGapsCapability && gapsUnlocked && !!kbSlug,
    retry: false,
  })

  if (!hasGapsCapability) {
    return (
      <div className="p-6 max-w-2xl opacity-50 cursor-default select-none" aria-disabled="true">
        <div className="flex items-start gap-3 mb-4">
          <AlertTriangle className="h-7 w-7 text-gray-900" />
          <h1 className="text-xl/none font-semibold text-gray-900">
            {m.support_cases_page_title()}
          </h1>
        </div>
        <Tooltip label={m.capability_tooltip_knowledge_only()}>
          <p className="text-sm text-gray-600">{m.capability_tooltip_knowledge_only()}</p>
        </Tooltip>
      </div>
    )
  }

  if (meQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (meQuery.isError) {
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={meQuery.error} onRetry={() => void meQuery.refetch()} />
      </PageContainer>
    )
  }
  if (meQuery.data !== undefined && !gapsUnlocked) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListEmptyState icon={AlertTriangle} title={m.gaps_unlock_required()} />
      </PageContainer>
    )
  }

  if (kbsQuery.isLoading) {
    return (
      <PageContainer width="6xl" gap="6">
        <ListLoadingState label={m.admin_shared_loading()} />
      </PageContainer>
    )
  }
  if (kbsQuery.isError) {
    return (
      <PageContainer width="6xl" gap="6">
        <QueryErrorState error={kbsQuery.error} onRetry={() => void kbsQuery.refetch()} />
      </PageContainer>
    )
  }

  const cases = casesQuery.data?.cases ?? []
  const total = casesQuery.data?.total ?? 0
  const page = Math.floor(offset / PAGE_SIZE) + 1
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const backButton = (
    <Button variant="outline" size="sm" asChild>
      <Link to="/app/knowledge/gaps">{m.support_cases_back_to_inbox()}</Link>
    </Button>
  )

  return (
    <PageContainer width="6xl" gap="6">
      <PageHeader
        title={m.support_cases_page_title()}
        description={m.support_cases_page_intro()}
        actions={backButton}
      />

      <div className="space-y-1.5">
        <Label htmlFor="support-cases-kb">{m.support_cases_kb_label()}</Label>
        <Select
          id="support-cases-kb"
          value={kbSlug}
          onChange={(e) =>
            void navigate({ search: () => ({ kbSlug: e.target.value || undefined, offset: undefined }) })
          }
          className="w-auto"
        >
          {orgKbs.length === 0 && <option value="">{m.support_cases_kb_none()}</option>}
          {orgKbs.map((kb) => (
            <option key={kb.id} value={kb.slug}>
              {kb.name}
            </option>
          ))}
        </Select>
      </div>

      {casesQuery.isError ? (
        <QueryErrorState error={casesQuery.error} onRetry={() => void casesQuery.refetch()} />
      ) : casesQuery.isLoading ? (
        <ListLoadingState label={m.admin_shared_loading()} />
      ) : cases.length === 0 ? (
        <ListEmptyState icon={FileText} title={m.support_cases_empty()} />
      ) : (
        <>
          <div className="overflow-x-auto">
            <DataTable className="table-fixed min-w-4xl">
              <DataTableHeader>
                <DataTableRow>
                  <DataTableHead>{m.support_cases_col_subject()}</DataTableHead>
                  <DataTableHead className="w-32">{m.support_cases_col_source()}</DataTableHead>
                  <DataTableHead className="w-32">{m.support_cases_col_status()}</DataTableHead>
                  <DataTableHead align="right" className="w-24">{m.support_cases_col_questions()}</DataTableHead>
                  <DataTableHead align="right" className="w-24">{m.support_cases_col_uncertain()}</DataTableHead>
                  <DataTableHead align="right" className="w-24">{m.support_cases_col_reviewed()}</DataTableHead>
                  <DataTableHead align="right" className="w-28">{m.support_cases_col_imported()}</DataTableHead>
                </DataTableRow>
              </DataTableHeader>
              <DataTableBody>
                {cases.map((item) => {
                  const status = supportCaseStatusBadge(item.status)
                  return (
                    <DataTableRow
                      key={item.id}
                      interactive
                      onClick={() =>
                        void navigate({
                          to: '/app/knowledge/gaps/support-cases/$caseId',
                          params: { caseId: String(item.id) },
                        })
                      }
                    >
                      <DataTableCell className="truncate" title={item.subject}>
                        <Link
                          to="/app/knowledge/gaps/support-cases/$caseId"
                          params={{ caseId: String(item.id) }}
                          className="block truncate text-rl-dark"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {item.subject || m.support_case_title()}
                        </Link>
                      </DataTableCell>
                      <DataTableCell className="text-gray-600">
                        <Badge variant="secondary">{caseSourceLabel(item.source)}</Badge>
                        {item.mediums.length > 0 && (
                          <div className="mt-1 truncate text-xs text-gray-500">
                            {item.mediums.join(', ')}
                          </div>
                        )}
                      </DataTableCell>
                      <DataTableCell>
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </DataTableCell>
                      <DataTableCell align="right" className="tabular-nums">
                        {item.question_count}
                      </DataTableCell>
                      <DataTableCell align="right" className="tabular-nums text-gray-600">
                        {item.uncertain_count}
                      </DataTableCell>
                      <DataTableCell align="right" className="tabular-nums text-gray-600">
                        {item.reviewed_count}
                      </DataTableCell>
                      <DataTableCell align="right" className="whitespace-nowrap tabular-nums text-gray-600">
                        {new Date(item.imported_at).toLocaleDateString(getLocale())}
                      </DataTableCell>
                    </DataTableRow>
                  )
                })}
              </DataTableBody>
            </DataTable>
          </div>
          {pageCount > 1 && (
            <Pagination
              page={page}
              pageCount={pageCount}
              onPageChange={(next) =>
                void navigate({
                  search: (prev) => ({ ...prev, offset: (next - 1) * PAGE_SIZE || undefined }),
                })
              }
            />
          )}
        </>
      )}
    </PageContainer>
  )
}
