import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { Loader2, BookMarked, Globe, Lock, Pencil, ExternalLink, ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
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

  const countLabel =
    accessibleKbs.length === 1
      ? m.docs_kbs_count_one()
      : m.docs_kbs_count({ count: String(accessibleKbs.length) })

  return (
    <div className="mx-auto max-w-3xl px-6 py-10 space-y-8">
      <div className="space-y-1">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          {m.docs_kbs_title()}
        </h1>
        <p className="text-sm text-gray-400">
          {!isLoading && countLabel}
        </p>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-gray-400" />
        </div>
      ) : kbs.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <BookMarked className="h-10 w-10 text-gray-400 opacity-40" />
          <div className="space-y-1">
            <p className="font-medium text-gray-900">
              {m.docs_kb_empty_heading()}
            </p>
            <p className="text-sm text-gray-400">
              {m.docs_kb_empty_body()}
            </p>
          </div>
        </div>
      ) : (
        <div data-help-id="docs-list" className="divide-y divide-gray-200 border-t border-b border-gray-200">
          {/* Accessible KBs */}
          {accessibleKbs.map((kb) => (
            <div
              key={kb.id}
              className="group flex items-center gap-3 px-2 py-3.5 hover:bg-[var(--color-rl-cream)] transition-colors cursor-pointer"
              onClick={() => void navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })}
            >
              <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
                <BookMarked className="h-4 w-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="text-[15px] font-display text-gray-900 truncate">
                    {kb.name}
                  </span>
                  <span className="inline-flex items-center gap-1 text-xs text-gray-400">
                    {kb.visibility === 'public' ? <Globe size={11} /> : <Lock size={11} />}
                    {kb.visibility === 'public' ? m.docs_kb_visibility_public() : m.docs_kb_visibility_private()}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {kb.visibility === 'public' && (
                  <Tooltip label={m.docs_kb_view_public()}>
                    <a
                      href={`/docs/${kb.slug}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(e) => e.stopPropagation()}
                      aria-label={m.docs_kb_view_public()}
                      className="inline-flex items-center justify-center text-gray-400 transition-opacity hover:opacity-70"
                    >
                      <ExternalLink className="h-4 w-4" />
                    </a>
                  </Tooltip>
                )}
                <Tooltip label={m.docs_kb_edit_label()}>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      void navigate({ to: '/app/docs/$kbSlug/edit', params: { kbSlug: kb.slug } })
                    }}
                    aria-label={m.docs_kb_edit_label()}
                    className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                </Tooltip>
                <ChevronRight className="h-4 w-4 text-gray-300" />
              </div>
            </div>
          ))}

          {/* Locked KBs — same row shape, faded, no actions */}
          {lockedKbs.map((kb) => (
            <Tooltip key={kb.id} label={m.docs_kb_locked_tooltip()}>
              <div className="flex items-center gap-3 px-2 py-3.5 opacity-60 cursor-default">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center text-gray-400">
                  <Lock className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 flex-wrap">
                    <span className="text-[15px] font-display text-gray-400 truncate">{kb.name}</span>
                    <Badge variant="outline" className="text-[10px] py-0 px-1.5">{m.docs_kb_locked_badge()}</Badge>
                  </div>
                </div>
              </div>
            </Tooltip>
          ))}
        </div>
      )}
    </div>
  )
}
