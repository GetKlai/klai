/**
 * SPEC-PORTAL-KENNIS-001 Phase E — Bronnen tab.
 * SPEC-PORTAL-KENNIS-002 Track 2 — per-row delete + Verbind opnieuw bij auth_error.
 *
 * "Alles is een bron." One unified list of every source in this KB
 * (connector aggregates + direct uploads), same row shape, same actions.
 *
 * Click a bron → expand inline to show its content:
 *   - For connectors: list of artifacts (items) with chunk counts
 *   - For direct uploads: parent_chunks with text preview
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import {
  Check,
  ChevronRight,
  File,
  Link as LinkIcon,
  Loader2,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import { Tooltip } from '@/components/ui/tooltip'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { useCurrentUser } from '@/hooks/useCurrentUser'
import { DOCS_BASE, getOrgSlug } from '@/lib/kb-editor/tree-utils'
import { queryLogger } from '@/lib/logger'
import type { Bron, BronnenResponse, ContentResponse, PageIndexEntry } from './-bronnen-types'
import { BronIcon, editablePageIdForBron, mapBronStatus, StatusBadge } from './-bronnen-helpers'
import { kbQueryKeys } from './-kb-query-keys'

export const Route = createFileRoute('/app/knowledge/$kbSlug/bronnen')({
  component: BronnenTab,
})

// -- Bron content (drill-down) ----------------------------------------------

function BronContent({ kbSlug, bron }: { kbSlug: string; bron: Bron }) {
  const { data, isLoading, isError } = useQuery<ContentResponse>({
    queryKey: kbQueryKeys.bronContent(kbSlug, bron.kind, bron.id),
    queryFn: () =>
      apiFetch<ContentResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/${bron.id}/content?kind=${bron.kind}&limit=20`,
      ),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="pl-[44px] pr-2 pb-3 flex items-center gap-2 text-xs text-gray-400">
        <Loader2 className="h-3 w-3 animate-spin" />
        Inhoud laden...
      </div>
    )
  }

  if (isError) {
    return (
      <div className="pl-[44px] pr-2 pb-3 text-xs text-[var(--color-destructive)]">
        Kon inhoud niet laden.
      </div>
    )
  }

  if (bron.kind === 'connector') {
    const items = data?.items ?? []
    if (items.length === 0) {
      return (
        <div className="pl-[44px] pr-2 pb-3 text-xs text-gray-400">
          Nog geen items in deze bron.
        </div>
      )
    }
    return (
      <div className="pl-[44px] pr-2 pb-3 space-y-1">
        {items.map((item) => (
          <div key={item.id} className="flex items-center gap-2 text-xs py-1.5">
            <File className="h-3.5 w-3.5 text-gray-400 shrink-0" />
            <span className="text-gray-700 flex-1 truncate">{item.path}</span>
            <span className="text-gray-400 shrink-0">{item.chunks_count} chunks</span>
          </div>
        ))}
        {data && data.total > items.length && (
          <p className="text-xs text-gray-400 pt-1">
            En nog {data.total - items.length} meer...
          </p>
        )}
      </div>
    )
  }

  // upload
  const chunks = data?.chunks ?? []
  if (chunks.length === 0) {
    if (bron.source_url) {
      return (
        <div className="pl-[44px] pr-2 pb-3 flex items-center gap-2 text-xs">
          <File className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          <span className="text-gray-700 truncate">{bron.source_url}</span>
          <span className="text-gray-400 shrink-0">URL</span>
        </div>
      )
    }
    // Two cases produce empty chunks here:
    //  1) Truly unindexed — index_status='pending'/'failed' on the row above.
    //  2) Indexed via docs/graphiti path — vectors live in Qdrant, no
    //     parent_chunks row exists for preview. Badge above says 'Gesynct'.
    // Don't claim "no chunks indexed" in the indexed-Gesynct case — that
    // contradicts the badge. Speak to the preview gap instead.
    return (
      <div className="pl-[44px] pr-2 pb-3 text-xs text-gray-400">
        Geen tekst-preview beschikbaar — open de bron in de editor om de inhoud te bekijken.
      </div>
    )
  }
  return (
    <div className="pl-[44px] pr-2 pb-3 space-y-2">
      {chunks.map((chunk) => (
        <div key={chunk.id} className="text-xs py-1.5">
          <div className="flex items-center gap-2 text-gray-400 mb-0.5">
            <span>Deel {chunk.position + 1}</span>
            <span>·</span>
            <span>{chunk.token_count} tokens</span>
          </div>
          <p className="text-gray-700 line-clamp-3">{chunk.text}</p>
        </div>
      ))}
      {data && data.total > chunks.length && (
        <p className="text-xs text-gray-400 pt-1">
          En nog {data.total - chunks.length} chunks meer...
        </p>
      )}
    </div>
  )
}

// -- Bron row ---------------------------------------------------------------

function BronRow({
  bron,
  expanded,
  onToggle,
  kbSlug,
  editablePageId,
}: {
  bron: Bron
  expanded: boolean
  onToggle: () => void
  kbSlug: string
  /** Gitea page slug for this bron, when one exists. Null = no editor link. */
  editablePageId: string | null
}) {
  const queryClient = useQueryClient()
  const status = mapBronStatus(bron)
  const [confirmingDelete, setConfirmingDelete] = useState(false)
  const [reauthError, setReauthError] = useState(false)
  const [reauthPending, setReauthPending] = useState(false)
  const [isRenaming, setIsRenaming] = useState(false)
  const [draftName, setDraftName] = useState(bron.name)

  // REQ-13: per-row sync/reindex button.
  // For connectors → POST /connectors/{id}/sync (full source-side resync).
  // For uploads → POST /uploads/{artifact_id}/reindex (re-enqueue chunking).
  const syncEndpoint =
    bron.kind === 'upload'
      ? `/api/app/knowledge-bases/${kbSlug}/uploads/${bron.id}/reindex`
      : `/api/app/knowledge-bases/${kbSlug}/connectors/${bron.id}/sync`
  const syncMutation = useMutation({
    mutationFn: async () => apiFetch(syncEndpoint, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) => queryLogger.error('Bron sync failed', { kbSlug, bronId: bron.id, kind: bron.kind, err }),
  })

  // REQ-15: delete upload artifact.
  const deleteUploadMutation = useMutation({
    mutationFn: async () =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/uploads/${bron.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) => queryLogger.error('Bron delete (upload) failed', { kbSlug, bronId: bron.id, err }),
  })

  // REQ-15: delete connector source.
  const deleteConnectorMutation = useMutation({
    mutationFn: async () =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${bron.id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) => queryLogger.error('Bron delete (connector) failed', { kbSlug, bronId: bron.id, err }),
  })

  const deleteMutation = bron.kind === 'upload' ? deleteUploadMutation : deleteConnectorMutation
  const isDeleting = deleteMutation.isPending

  const renameMutation = useMutation({
    mutationFn: async (name: string) =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/uploads/${bron.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ name }),
      }),
    onSuccess: () => {
      setIsRenaming(false)
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
    },
    onError: (err) => queryLogger.error('Bron rename failed', { kbSlug, bronId: bron.id, err }),
  })

  // Q4: "Verbind opnieuw" — connector has auth_error status.
  const isAuthError = bron.kind === 'connector' && (bron.status ?? '').toLowerCase().includes('auth')

  async function handleReauth() {
    // Use the existing OAuth authorize endpoint — same pattern as
    // connectors.tsx::handleReconnect. The endpoint needs the connector_type
    // (notion / google_drive / etc.), not a dedicated /reauth route.
    if (!bron.connector_type) {
      setReauthError(true)
      return
    }
    setReauthError(false)
    setReauthPending(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        `/api/oauth/${encodeURIComponent(bron.connector_type)}/authorize` +
          `?kb_slug=${encodeURIComponent(kbSlug)}` +
          `&connector_id=${encodeURIComponent(bron.id)}`,
      )
      window.location.assign(authorize_url)
      // Stay pending: page redirects away; spinner stays until navigation.
    } catch (err) {
      setReauthPending(false)
      setReauthError(true)
      queryLogger.error('Connector reauth failed', { kbSlug, bronId: bron.id, err })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
    }
  }

  // REQ-3/Q4: sync is disabled while auth_error is active (must reauth first).
  const isSyncing = syncMutation.isPending || status === 'pending'
  const syncDisabled = isSyncing || isAuthError

  // Meta line: type label, optional item count for connectors. Drop the
  // chunk count — the parent_chunks number is unreliable per-row.
  const metaParts: string[] = [bron.type_label]
  if (bron.kind === 'connector' && bron.items_count > 0) {
    metaParts.push(`${bron.items_count} items`)
  }
  const meta = metaParts.join(' · ')

  return (
    <div>
      <div className="group flex items-center gap-2 pr-2 hover:bg-black/[0.03] transition-colors">
        <div className="flex flex-1 min-w-0 items-center gap-3 px-2 py-3.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
            <BronIcon bron={bron} />
          </div>
          <div className="min-w-0 flex-1">
            {isRenaming ? (
              <div className="flex items-center gap-1 min-w-[220px] max-w-full">
                <input
                  value={draftName}
                  autoFocus
                  onChange={(e) => setDraftName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') {
                      setDraftName(bron.name)
                      setIsRenaming(false)
                    }
                    if (e.key === 'Enter' && draftName.trim()) {
                      renameMutation.mutate(draftName.trim())
                    }
                  }}
                  className="h-8 min-w-0 flex-1 rounded-md border border-gray-200 bg-white px-2 text-sm text-gray-900 outline-none focus:border-gray-400"
                />
                <button
                  type="button"
                  aria-label="Naam opslaan"
                  disabled={!draftName.trim() || renameMutation.isPending}
                  onClick={() => {
                    if (draftName.trim()) renameMutation.mutate(draftName.trim())
                  }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 disabled:opacity-50"
                >
                  {renameMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                </button>
                <button
                  type="button"
                  aria-label="Naam bewerken annuleren"
                  onClick={() => {
                    setDraftName(bron.name)
                    setIsRenaming(false)
                  }}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={onToggle}
                className="min-w-0 w-full text-left"
                aria-expanded={expanded}
              >
                <div className="flex items-baseline gap-2 min-w-0">
                  <span className="text-[15px] font-display text-gray-900 truncate min-w-0 flex-1">{bron.name}</span>
                  <span className="text-xs text-gray-400 shrink-0">{meta}</span>
                </div>
              </button>
            )}
          </div>
        </div>
        <StatusBadge status={status} />

        {/* Q4: "Verbind opnieuw" — only for connectors in auth_error state. */}
        {isAuthError && (
          <div className="flex flex-col items-end gap-0.5">
            <Tooltip label="Verbind opnieuw met de externe dienst">
              <button
                type="button"
                onClick={() => void handleReauth()}
                disabled={reauthPending}
                aria-label="Verbind opnieuw"
                className="inline-flex h-8 items-center gap-1.5 px-2 rounded-md text-xs font-medium text-[var(--color-rl-accent-dark)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {reauthPending ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <LinkIcon className="h-3.5 w-3.5" />
                )}
                Verbind opnieuw
              </button>
            </Tooltip>
            {reauthError && (
              <span className="text-[10px] text-[var(--color-destructive)] px-2">
                Verbinden mislukt
              </span>
            )}
          </div>
        )}

        {/* REQ-13: sync button — for connectors AND uploads.
            Connector path: full resync at the source. Auth_error → reauth first.
            Upload path: re-enqueue chunking via /uploads/{id}/reindex. */}
        {(bron.kind === 'connector' || bron.kind === 'upload') && (
          <Tooltip
            label={
              isAuthError
                ? 'Eerst opnieuw verbinden'
                : bron.kind === 'upload'
                  ? 'Herindexeer bron'
                  : 'Synchroniseer bron'
            }
          >
            <button
              type="button"
              onClick={() => { if (!syncDisabled) syncMutation.mutate() }}
              disabled={syncDisabled}
              aria-label={
                isAuthError
                  ? 'Eerst opnieuw verbinden'
                  : bron.kind === 'upload'
                    ? 'Herindexeer bron'
                    : 'Synchroniseer bron'
              }
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSyncing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
            </button>
          </Tooltip>
        )}

        {/* REQ-15: delete button — always visible, inline-confirm pattern. */}
        <InlineDeleteConfirm
          isConfirming={confirmingDelete}
          isPending={isDeleting}
          label={`Verwijder '${bron.name}'?`}
          cancelLabel="Annuleren"
          onConfirm={() => { deleteMutation.mutate(); setConfirmingDelete(false) }}
          onCancel={() => setConfirmingDelete(false)}
        >
          <Tooltip label="Verwijder bron">
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              disabled={isDeleting}
              aria-label="Verwijder bron"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </Tooltip>
        </InlineDeleteConfirm>

        {bron.kind === 'connector' && (
          <Tooltip label="Bewerk bron">
            <Link
              to="/app/knowledge/$kbSlug/edit-connector/$connectorId"
              params={{ kbSlug, connectorId: bron.id }}
              aria-label="Bewerk bron"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </Link>
          </Tooltip>
        )}

        {bron.kind === 'upload' && (
          <Tooltip label="Naam aanpassen">
            <button
              type="button"
              onClick={() => {
                setDraftName(bron.name)
                setIsRenaming(true)
              }}
              aria-label="Naam aanpassen"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </button>
          </Tooltip>
        )}

        {/* Per-row "Bewerken in editor" — only rendered when the bron name
            actually maps to a Gitea page slug in the KB's page-index. The
            mapping is computed once at KB level (see BronnenTab) so each
            row only renders when there's a confirmed click target. */}
        {editablePageId !== null && (
          <Tooltip label="Bewerken in editor">
            <Link
              to="/app/docs/$kbSlug/$pageId"
              params={{ kbSlug, pageId: editablePageId }}
              aria-label="Bewerken in editor"
              className="inline-flex h-8 w-8 items-center justify-center rounded-md text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </Link>
          </Tooltip>
        )}

        <button
          type="button"
          onClick={onToggle}
          aria-label={expanded ? 'Inhoud verbergen' : 'Inhoud tonen'}
          className="inline-flex h-8 w-8 items-center justify-center text-gray-300 hover:text-gray-500 transition-colors"
        >
          <ChevronRight
            className={`h-4 w-4 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
          />
        </button>
      </div>
      {expanded && <BronContent kbSlug={kbSlug} bron={bron} />}
    </div>
  )
}

// -- Tab page ---------------------------------------------------------------

function BronnenTab() {
  const { kbSlug } = Route.useParams()
  const queryClient = useQueryClient()
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery<BronnenResponse>({
    queryKey: kbQueryKeys.bronnen(kbSlug),
    queryFn: () => apiFetch<BronnenResponse>(`/api/app/knowledge-bases/${kbSlug}/sources`),
    retry: false,
    // Poll while any connector is syncing so the badge updates without refresh.
    refetchInterval: (query) => {
      const list = query.state.data?.bronnen ?? []
      const anyPending = list.some((b) => mapBronStatus(b) === 'pending')
      return anyPending ? 4000 : false
    },
  })

  const bronnen = data?.bronnen ?? []
  const connectorBronnen = bronnen.filter((b) => b.kind === 'connector')

  // KB data is already cached by the route shell; reuse same query key.
  const { data: kb } = useQuery<{ docs_enabled: boolean }>({
    queryKey: kbQueryKeys.knowledgeBase(kbSlug),
    queryFn: () => apiFetch<{ docs_enabled: boolean }>(`/api/app/knowledge-bases/${kbSlug}`),
  })

  // Bronnen and docs-editor pages are two separate stores: bronnen live in
  // knowledge.artifacts (Postgres + RAG chunks), docs pages live in the KB's
  // Gitea repo. An upload here does NOT create a Gitea page. We only surface
  // "Open in editor" when the editor actually has something to show — i.e.
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
  // pencil — only show when the bron's name strips to a slug that exists
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

  // Sync-alles: fan out one POST per connector. We don't wait for completion;
  // each row will pick up the running status on the next poll.
  const syncAllMutation = useMutation({
    mutationFn: async () => {
      const results = await Promise.allSettled(
        connectorBronnen.map((b) =>
          apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${b.id}/sync`, {
            method: 'POST',
          }),
        ),
      )
      return results
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.bronnen(kbSlug) })
      void queryClient.invalidateQueries({ queryKey: kbQueryKeys.statsSummary() })
    },
    onError: (err) => queryLogger.error('Sync-all failed', { kbSlug, err }),
  })

  return (
    <div>
      {/* Action bar */}
      <div className="flex items-center justify-between gap-4 mb-4">
        <p className="text-sm text-gray-400">
          {bronnen.length === 1
            ? m.kb_count_bron_singular()
            : m.kb_count_bronnen({ count: String(bronnen.length) })}
        </p>
        <div className="flex items-center gap-2">
          {kb?.docs_enabled && hasEditorPages && (
            <Link to="/app/docs/$kbSlug" params={{ kbSlug }}>
              <Button variant="ghost" size="sm">
                <Pencil className="h-4 w-4" />
                Open in editor
              </Button>
            </Link>
          )}
          {connectorBronnen.length > 0 && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => syncAllMutation.mutate()}
              disabled={syncAllMutation.isPending}
            >
              {syncAllMutation.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Synchroniseer alles
            </Button>
          )}
          <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
            <Button variant="default" size="sm">
              <Plus className="h-4 w-4" />
              Bron toevoegen
            </Button>
          </Link>
        </div>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
          {[1, 2, 3].map((i) => (
            <div key={i} className="h-[60px] bg-gray-50 animate-pulse" />
          ))}
        </div>
      ) : isError ? (
        <div className="border-t border-b border-gray-200 py-10 text-center text-sm text-[var(--color-destructive)]">
          Kon bronnen niet laden.
        </div>
      ) : bronnen.length === 0 ? (
        <div className="rounded-lg border border-dashed border-gray-200 py-12 text-center">
          <Zap className="h-8 w-8 text-gray-300 mx-auto mb-3" />
          <p className="text-sm font-medium text-gray-900">Nog geen bronnen</p>
          <p className="text-xs text-gray-400 mt-1 max-w-sm mx-auto">
            Voeg bestanden, links of koppelingen toe om kennis aan deze collectie toe te voegen.
          </p>
          <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }} className="inline-block mt-4">
            <Button variant="default">
              <Plus className="h-4 w-4" />
              Eerste bron toevoegen
            </Button>
          </Link>
        </div>
      ) : (
        <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
          {bronnen.map((bron) => {
            const editablePageId = editablePageIdForBron(bron, slugToPageId)
            return (
              <BronRow
                key={`${bron.kind}-${bron.id}`}
                bron={bron}
                expanded={expandedId === `${bron.kind}-${bron.id}`}
                onToggle={() =>
                  setExpandedId(expandedId === `${bron.kind}-${bron.id}` ? null : `${bron.kind}-${bron.id}`)
                }
                kbSlug={kbSlug}
                editablePageId={editablePageId}
              />
            )
          })}
        </div>
      )}
    </div>
  )
}
