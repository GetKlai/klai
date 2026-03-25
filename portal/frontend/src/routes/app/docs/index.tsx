import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
import { useQuery } from '@tanstack/react-query'
import { Loader2, BookMarked, Globe, Lock, Pencil } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { ProductGuard } from '@/components/layout/ProductGuard'
import { API_BASE } from '@/lib/api'

export const Route = createFileRoute('/app/docs/')({
  component: () => (
    <ProductGuard product="knowledge">
      <DocsPage />
    </ProductGuard>
  ),
})

interface KnowledgeBase {
  id: number
  slug: string
  name: string
  visibility: 'public' | 'internal'
  gitea_repo_slug: string | null
}

interface KBsResponse {
  knowledge_bases: KnowledgeBase[]
}

function DocsPage() {
  const auth = useAuth()
  const token = auth.user?.access_token
  const navigate = useNavigate()

  const { data: kbs = [], isLoading, error } = useQuery<KnowledgeBase[]>({
    queryKey: ['docs-kbs', window.location.hostname],
    queryFn: async () => {
      const res = await fetch(`${API_BASE}/api/app/knowledge-bases?docs_only=true`, {
        headers: { Authorization: `Bearer ${token}` },
      })
      if (!res.ok) throw new Error('Laden mislukt')
      const data: KBsResponse = await res.json()
      return data.knowledge_bases
    },
    enabled: !!token,
  })

  const countLabel =
    kbs.length === 1
      ? m.docs_kbs_count_one()
      : m.docs_kbs_count({ count: String(kbs.length) })

  return (
    <div className="p-8 space-y-6">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="font-serif text-2xl font-bold text-[var(--color-purple-deep)]">
            {m.docs_kbs_title()}
          </h1>
          <p className="text-sm text-[var(--color-muted-foreground)]">
            {!isLoading && countLabel}
          </p>
        </div>
      </div>

      {error && (
        <p className="text-sm text-[var(--color-destructive)]">
          {error instanceof Error ? error.message : 'Laden mislukt'}
        </p>
      )}

      <Card data-help-id="docs-list">
        <CardContent className="pt-0 px-0 pb-0 overflow-hidden rounded-xl">
          {isLoading ? (
            <div className="flex justify-center py-8">
              <Loader2 className="h-5 w-5 animate-spin text-[var(--color-muted-foreground)]" />
            </div>
          ) : kbs.length === 0 ? (
            <div className="flex flex-col items-center gap-3 py-16 text-center">
              <BookMarked className="h-10 w-10 text-[var(--color-muted-foreground)] opacity-40" />
              <div className="space-y-1">
                <p className="font-medium text-[var(--color-purple-deep)]">
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
                  <th className="px-3 py-3 w-20" />
                </tr>
              </thead>
              <tbody>
                {kbs.map((kb, i) => (
                  <tr
                    key={kb.id}
                    className={
                      i % 2 === 0 ? 'bg-[var(--color-card)]' : 'bg-[var(--color-secondary)]'
                    }
                  >
                    <td
                      className="px-6 py-3 text-[var(--color-purple-deep)] font-medium cursor-pointer hover:underline"
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
                    <td className="px-3 py-3 w-20 text-right">
                      <div className="flex items-center justify-end gap-1">
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
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
