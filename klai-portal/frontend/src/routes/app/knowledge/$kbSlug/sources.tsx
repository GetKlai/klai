/**
 * SPEC-PORTAL-KENNIS-001 Phase E - Sources tab (route shell).
 * SPEC-PORTAL-KENNIS-002 Track 2 - per-row delete + reauth.
 * SPEC-PORTAL-SOURCES-RENAME-001 Phase 3 - split out of bronnen.tsx.
 *
 * "Alles is een bron." One unified list of every source in this KB
 * (connector aggregates + direct uploads), same row shape, same actions.
 *
 * Components live in `_components`/`-sources-*` files; this file only
 * owns data fetching + layout assembly.
 */
import { createFileRoute } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { ListFrame } from '@/components/ui/list'
import { ListEmptyState, ListLoadingState } from '@/components/ui/list-state'
import { QueryErrorState } from '@/components/ui/query-error-state'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { DOCS_BASE, getOrgSlug } from '@/lib/kb-editor/tree-utils'
import * as m from '@/paraglide/messages'
import { editablePageIdForSource, shouldPollSource } from './-sources-helpers'
import { SourcesActionBar } from './-sources-actionbar'
import { SourceRow } from './-sources-row'
import { kbQueryKeys } from '@/lib/kb-query-keys'
import type { PageIndexEntry, SourcesResponse } from './-sources-types'

export const Route = createFileRoute('/app/knowledge/$kbSlug/sources')({
  component: SourcesTab,
})

function SourcesTab() {
  const { kbSlug } = Route.useParams()
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, error, refetch } = useQuery<SourcesResponse>({
    queryKey: kbQueryKeys.sources(kbSlug),
    queryFn: () => apiFetch<SourcesResponse>(`/api/app/knowledge-bases/${kbSlug}/sources`),
    retry: false,
    // Poll while any connector is syncing so the badge updates without refresh.
    refetchInterval: (query) => {
      const list = query.state.data?.sources ?? []
      const anyPending = list.some(shouldPollSource)
      return anyPending ? 4000 : false
    },
  })

  const sources = data?.sources ?? []
  const connectorSources = sources.filter((s) => s.kind === 'connector')

  // KB data is already cached by the route shell; reuse same query key.
  const { data: kb } = useQuery<{ docs_enabled: boolean }>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: () => apiFetch<{ docs_enabled: boolean }>(`/api/app/knowledge-bases/${kbSlug}`),
  })

  // Sources and docs-editor pages are two separate stores: sources live in
  // knowledge.artifacts (Postgres + RAG chunks), docs pages live in the KB's
  // Gitea repo. An upload here does NOT create a Gitea page. We only surface
  // "Open in editor" when the editor actually has something to show - i.e.
  // when the docs-tree endpoint returns at least one node. Same query key
  // as route.tsx so a tree hit here warms the cache for the editor route.
  const { user } = useCurrentUser()
  const orgSlug = getOrgSlug(user?.workspace_url)
  const { data: docsTree } = useQuery<{ id: string }[]>({
    queryKey: kbQueryKeys.docsTree(orgSlug, kbSlug),
    queryFn: () => apiFetch<{ id: string }[]>(`${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/tree`),
    enabled: !!kb?.docs_enabled && !!orgSlug,
    // 404 / forbidden / empty repo: fall back to "no pages" silently.
    retry: false,
  })
  const hasEditorPages = !!docsTree && docsTree.length > 0

  // Page-index: list of {id, slug, title} pairs for THIS KB's Gitea pages.
  // We use it to decide whether to render the per-row 'Bewerken in editor'
  // pencil - only show when the source's name strips to a slug that exists
  // in the page-index. Prevents 'Pagina niet gevonden' on click. Stable
  // key with route.tsx so a hit here warms the editor's cache.
  const { data: pageIndex } = useQuery<PageIndexEntry[]>({
    queryKey: kbQueryKeys.docsPageIndex(orgSlug, kbSlug),
    queryFn: () =>
      apiFetch<PageIndexEntry[]>(
        `${DOCS_BASE}/orgs/${orgSlug}/kbs/${kbSlug}/page-index`,
      ),
    enabled: !!kb?.docs_enabled && !!orgSlug && hasEditorPages,
    retry: false,
  })
  // Build a slug → pageId map. resolveSlug in the docs editor matches by id
  // OR slug; we pass the slug as pageId because it is what the URL renders
  // and what resolves bidirectionally.
  const slugToPageId = useMemo(() => {
    const map = new Map<string, string>()
    for (const entry of pageIndex ?? []) {
      map.set(entry.slug, entry.slug)
    }
    return map
  }, [pageIndex])

  return (
    <div className="space-y-6">
      <SourcesActionBar
        kbSlug={kbSlug}
        sources={sources}
        connectorSources={connectorSources}
        showEditorLink={!!kb?.docs_enabled && hasEditorPages}
      />

      {isLoading ? (
        <ListFrame>
          <ListLoadingState label={m.kb_sources_loading()} />
        </ListFrame>
      ) : error ? (
        <QueryErrorState
          error={error instanceof Error ? error : new Error(m.kb_sources_list_error())}
          onRetry={() => void refetch()}
        />
      ) : sources.length === 0 ? (
        <ListFrame>
          <ListEmptyState
            title={m.kb_sources_empty_title()}
            description={m.kb_sources_empty_subtitle()}
          />
        </ListFrame>
      ) : (
        <ListFrame>
          {sources.map((source) => {
            const editablePageId = editablePageIdForSource(source, slugToPageId)
            return (
              <SourceRow
                key={`${source.kind}-${source.id}`}
                source={source}
                expanded={expandedId === `${source.kind}-${source.id}`}
                onToggle={() =>
                  setExpandedId(expandedId === `${source.kind}-${source.id}` ? null : `${source.kind}-${source.id}`)
                }
                kbSlug={kbSlug}
                editablePageId={editablePageId}
              />
            )
          })}
        </ListFrame>
      )}
    </div>
  )
}
