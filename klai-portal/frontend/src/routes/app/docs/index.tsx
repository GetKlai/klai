import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Loader2, BookMarked, Globe, Lock, Pencil, ExternalLink } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
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
  const token = auth.user?.access_token
  const navigate = useNavigate()

  const { data: kbs = [], isLoading, error, refetch } = useQuery<KBWithAccess[]>({
    queryKey: ['docs-kbs-with-access'],
    queryFn: async () => apiFetch<KBWithAccess[]>(`/api/app/knowledge-bases-with-access`, token),
    enabled: !!token,
  })

  const accessibleKbs = kbs.filter((kb) => kb.is_accessible)
  const lockedKbs = kbs.filter((kb) => !kb.is_accessible)

  const countLabel =
    accessibleKbs.length === 1
      ? m.docs_kbs_count_one()
      : m.docs_kbs_count({ count: String(accessibleKbs.length) })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-[var(--color-foreground)]">
            {m.docs_kbs_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
      </div>

      {error ? (
        <QueryErrorState error={error instanceof Error ? error : new Error(String(error))} onRetry={() => void refetch()} />
      ) : <Card data-help-id="docs-list">
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
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
            <table className="w-full text-sm table-fixed">
              <thead>
                <tr className="border-b border-[var(--color-border)]">
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide">
                    {m.docs_kb_name_label()}
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-[var(--color-muted-foreground)] uppercase tracking-wide w-32">
                    {m.docs_kb_visibility_label()}
                  </th>
                  <th className="px-3 py-3 w-24" />
                </tr>
              </thead>
              <tbody>
                {/* Accessible KBs */}
                {accessibleKbs.map((kb, i) => (
                  <tr
                    key={kb.id}
                    className={i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'}
                  >
                    <td
                      className="px-6 py-3 text-[var(--color-foreground)] font-medium cursor-pointer hover:underline"
                      onClick={() =>
                        navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })
                      }
                    >
                      {kb.name}
                    </td>
                    <td className="px-6 py-3">
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
                    <td className="px-3 py-3 w-24 text-right">
                      <div className="flex items-center justify-end gap-1">
                        {kb.visibility === 'public' && (
                          <Tooltip label={m.docs_kb_view_public()}>
                            <a
                              href={`/docs/${kb.slug}`}
                              target="_blank"
                              rel="noopener noreferrer"
                              aria-label={m.docs_kb_view_public()}
                              className="flex h-7 w-7 items-center justify-center text-[var(--color-muted-foreground)] transition-opacity hover:opacity-70"
                            >
                              <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                          </Tooltip>
                        )}
                        <Tooltip label={m.docs_kb_edit_label()}>
                          <button
                            onClick={() =>
                              navigate({ to: '/app/docs/$kbSlug/edit', params: { kbSlug: kb.slug } })
                            }
                            aria-label={m.docs_kb_edit_label()}
                            className="flex h-7 w-7 items-center justify-center text-[var(--color-warning)] transition-opacity hover:opacity-70"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </button>
                        </Tooltip>
                      </div>
                    </td>
                  </tr>
                ))}

                {/* Locked KBs */}
                {lockedKbs.map((kb, i) => (
                  <tr
                    key={kb.id}
                    className={
                      (accessibleKbs.length + i) % 2 === 0
                        ? 'bg-[var(--color-card)] opacity-60'
                        : 'bg-[var(--color-secondary)] opacity-60'
                    }
                  >
                    <td className="px-6 py-3 text-[var(--color-muted-foreground)] font-medium">
                      <Tooltip label={m.docs_kb_locked_tooltip()}>
                        <span className="inline-flex items-center gap-2 cursor-default">
                          <Lock size={12} className="shrink-0" />
                          {kb.name}
                        </span>
                      </Tooltip>
                    </td>
                    <td className="px-6 py-3">
                      <Badge variant="outline" className="text-xs">{m.docs_kb_locked_badge()}</Badge>
                    </td>
                    <td className="px-3 py-3 w-24 text-right">
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
        </CardContent>
      </Card>}
    </div>
  )
}
