import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useAuth } from 'react-oidc-context'
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
    <div className="p-6 space-y-6 max-w-5xl">
      <div className="flex items-start justify-between">
        <div className="space-y-1">
          <h1 className="page-title text-xl/none font-semibold text-gray-900">
            {m.docs_kbs_title()}
          </h1>
          <p className="text-sm text-gray-400">
            {!isLoading && countLabel}
          </p>
        </div>
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
        <table data-help-id="docs-list" className="w-full text-sm table-fixed border-t border-b border-gray-200">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em]">
                {m.docs_kb_name_label()}
              </th>
              <th className="py-3 pr-4 text-left text-xs font-medium text-gray-400 uppercase tracking-[0.04em] w-32">
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
                className="border-b border-gray-200 last:border-b-0"
              >
                <td
                  className="py-4 pr-4 align-top text-gray-900 font-medium cursor-pointer hover:underline"
                  onClick={() =>
                    navigate({ to: '/app/docs/$kbSlug', params: { kbSlug: kb.slug } })
                  }
                >
                  {kb.name}
                </td>
                <td className="py-4 pr-4 align-top w-32">
                  <span className="inline-flex items-center gap-1.5 text-xs text-gray-400">
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
                          className="inline-flex items-center justify-center text-gray-400 transition-opacity hover:opacity-70"
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
                className="border-b border-gray-200 last:border-b-0 opacity-60"
              >
                <td className="py-4 pr-4 align-top text-gray-400 font-medium">
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
                      className="flex items-center gap-1 text-xs text-gray-400 opacity-50 cursor-not-allowed px-2 py-1 rounded border border-gray-200"
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
