import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from '@/lib/auth'
import { useQuery } from '@tanstack/react-query'
import { Loader2, BookMarked, Globe, Lock, Pencil, ExternalLink } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Tooltip } from '@/components/ui/tooltip'
import { QueryErrorState } from '@/components/ui/query-error-state'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { apiFetch } from '@/lib/apiFetch'

export const Route = createFileRoute('/app/docs/')({
  component: () => (
    <ProductGuard product="knowledge">
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
    <div className="mx-auto max-w-5xl px-6 py-10 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-[26px] font-display-bold text-gray-900">
            {m.docs_kbs_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
        </div>
      ) : kbs.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-16 text-center">
          <BookMarked className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
          <div className="space-y-1">
            <p className="font-medium text-[var(--color-foreground)]">
              {m.docs_kb_empty_heading()}
            </p>
            <p className="text-sm text-[var(--color-muted-foreground)]">
              {m.docs_kb_empty_body()}
            </p>
          </div>
        </div>
      ) : (
        <table data-help-id="docs-list" className="w-full text-sm table-fixed border-t border-b border-[var(--color-border)]">
          <thead>
            <tr className="border-b border-[var(--color-border)]">
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                {m.docs_kb_name_label()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide w-32">
                {m.docs_kb_visibility_label()}
              </th>
              <th className="py-3 text-right w-24" />
            </tr>
          </thead>
          <tbody>
            {/* Accessible KBs */}
            {accessibleKbs.map((kb) => (
              <tr
                key={kb.id}
                className="border-b border-[var(--color-border)] last:border-b-0"
              >
                <td
                  className="py-4 pr-4 align-top text-[var(--color-foreground)] font-medium cursor-pointer hover:underline"
                  onClick={() =>
                    navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })
                  }
                >
                  {kb.name}
                </td>
                <td className="py-4 pr-4 align-top w-32">
                  <span className="inline-flex items-center gap-1.5 text-xs text-[var(--color-muted-foreground)]">
                    {kb.visibility === 'public' ? (
                      <Globe size={12} />
                    ) : (
                      <Lock size={12} />
                    )}
                    {kb.visibility === 'public'
                      ? m.docs_kb_visibility_public()
                      : m.docs_kb_visibility_private()}
                  </span>
                </td>
                <td className="py-4 align-top text-right w-24">
                  <div className="flex items-start justify-end gap-2 mt-px">
                    {kb.visibility === 'public' && (
                      <Tooltip label={m.docs_kb_view_public()}>
                        <a
                          href={`/docs/${kb.slug}`}
                          target="_blank"
                          rel="noopener noreferrer"
                          aria-label={m.docs_kb_view_public()}
                          className="inline-flex items-center justify-center text-[var(--color-muted-foreground)] transition-opacity hover:opacity-70"
                        >
                          <ExternalLink className="h-4 w-4" />
                        </a>
                      </Tooltip>
                    )}
                    <Tooltip label={m.docs_kb_edit_label()}>
                      <button
                        onClick={() =>
                          navigate({ to: '/app/docs/$kbSlug/edit', params: { kbSlug: kb.slug } })
                        }
                        aria-label={m.docs_kb_edit_label()}
                        className="inline-flex items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                    </Tooltip>
                  </div>
                </td>
              </tr>
            ))}

            {/* Locked KBs */}
            {lockedKbs.map((kb) => (
              <tr
                key={kb.id}
                className="border-b border-[var(--color-border)] last:border-b-0 opacity-60"
              >
                <td className="py-4 pr-4 align-top text-[var(--color-muted-foreground)] font-medium">
                  <Tooltip label={m.docs_kb_locked_tooltip()}>
                    <span className="inline-flex items-center gap-2 cursor-default">
                      <Lock size={12} className="shrink-0" />
                      {kb.name}
                    </span>
                  </Tooltip>
                </td>
                <td className="py-4 pr-4 align-top w-32">
                  <Badge variant="outline" className="text-xs">{m.docs_kb_locked_badge()}</Badge>
                </td>
                <td className="py-4 align-top text-right w-24">
                  <Tooltip label={m.docs_kb_locked_tooltip()}>
                    <button
                      disabled
                      aria-label={m.docs_kb_request_access()}
                      className="flex items-center gap-1 text-xs text-[var(--color-muted-foreground)] opacity-50 cursor-not-allowed px-2 py-1 rounded border border-[var(--color-border)]"
                    >
                      {m.docs_kb_request_access()}
                    </button>
                  </Tooltip>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
