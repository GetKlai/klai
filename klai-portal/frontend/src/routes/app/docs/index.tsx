import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { BookMarked, Globe, Lock } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { PageHeader, PageIntro } from '@/components/ui/page-header'
import {
  ListFrame,
  ListRow,
  ListRowActions,
  ListRowChevron,
  ListRowContent,
  ListRowIcon,
  ListRowTitle,
} from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { BorderedRowActionIconButton } from '@/components/ui/row-action'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/docs/')({
  component: () => (
    <ProductGuard product="docs">
      <DocsPage />
    </ProductGuard>
  ),
})

interface KBWithAccess {
  id: number
  slug: string
  name: string
  visibility: 'public' | 'internal'
  gitea_repo_slug: string | null
  is_accessible: boolean
}

function DocsPage() {
  const auth = useAuth()
  const navigate = useNavigate()

  const { data: kbs = [], isLoading, error, refetch } = useQuery<KBWithAccess[]>({
    queryKey: ['docs-kbs-with-access'],
    queryFn: async () => apiFetch<KBWithAccess[]>(`/api/app/knowledge-bases-with-access`),
    enabled: auth.isAuthenticated,
  })

  const accessibleKbs = kbs.filter((kb) => kb.is_accessible)
  const lockedKbs = kbs.filter((kb) => !kb.is_accessible)

  return (
    <div className="mx-auto max-w-3xl px-6 pt-4 pb-10 space-y-8">
      <PageHeader
        title={m.docs_kbs_title()}
        count={!isLoading && !error ? accessibleKbs.length : undefined}
        description={m.docs_kbs_subtitle()}
      />

      <PageIntro>
        <p>{m.docs_intro_body()}</p>
        <p>{m.docs_intro_publish()}</p>
      </PageIntro>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <ListFrame>
          <ListLoadingState label={m.knowledge_page_stat_loading()} />
        </ListFrame>
      ) : kbs.length === 0 ? (
        <ListFrame>
          <ListEmptyState
            title={m.docs_kb_empty_heading()}
            description={m.docs_kb_empty_body()}
          />
        </ListFrame>
      ) : (
        <ListFrame data-help-id="docs-list">
          {/* Accessible KBs */}
          {accessibleKbs.map((kb) => (
            <ListRow
              key={kb.id}
              interactive
              className="items-center gap-3 px-4 py-4"
              onClick={() => void navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })}
            >
              <ListRowIcon>
                <BookMarked className="h-4 w-4" />
              </ListRowIcon>
              <ListRowContent>
                <div className="flex items-baseline gap-2 flex-wrap">
                  <ListRowTitle>{kb.name}</ListRowTitle>
                  <span className="inline-flex items-center gap-1 text-xs text-gray-400">
                    {kb.visibility === 'public' ? <Globe size={11} /> : <Lock size={11} />}
                    {kb.visibility === 'public' ? m.docs_kb_visibility_public() : m.docs_kb_visibility_private()}
                  </span>
                </div>
              </ListRowContent>
              <ListRowActions onClick={(e) => e.stopPropagation()}>
                {kb.visibility === 'public' && (
                  <BorderedRowActionIconButton
                    label={m.docs_kb_view_public()}
                    action="external"
                    onClick={() =>
                      window.open(`/docs/${kb.slug}`, '_blank', 'noopener,noreferrer')
                    }
                  />
                )}
                <BorderedRowActionIconButton
                  label={m.docs_kb_edit_label()}
                  action="edit"
                  onClick={() =>
                    void navigate({
                      to: '/app/docs/$kbSlug/edit',
                      params: { kbSlug: kb.slug },
                    })
                  }
                />
              </ListRowActions>
              <ListRowChevron />
            </ListRow>
          ))}

          {/* Locked KBs - same row shape, faded, no actions */}
          {lockedKbs.map((kb) => (
            <Tooltip key={kb.id} label={m.docs_kb_locked_tooltip()}>
              <ListRow className="items-center gap-3 px-4 py-4 opacity-60">
                <ListRowIcon>
                  <Lock className="h-4 w-4" />
                </ListRowIcon>
                <ListRowContent>
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <ListRowTitle className="text-gray-400">{kb.name}</ListRowTitle>
                    <Badge variant="outline" className="text-[10px] py-0 px-1.5">{m.docs_kb_locked_badge()}</Badge>
                  </div>
                </ListRowContent>
              </ListRow>
            </Tooltip>
          ))}
        </ListFrame>
      )}
    </div>
  )
}
