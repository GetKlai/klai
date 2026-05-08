/**
 * SPEC-PORTAL-KENNIS-001 Phase E — Bronnen tab (refactored).
 *
 * Senior UI pass:
 *   - Type icons get a subtle color tint per provider (Notion red, GitHub
 *     dark, Drive blue, etc.). Visual hierarchy without shouting.
 *   - Status surfaces as a colored dot, never as a badge — only Bezig/
 *     Probleem are visible. Klaar = no clutter.
 *   - Hover reveals row actions: Sync (connectors), Open (uploads), Delete.
 *   - Expanded view shows chunks as numbered cards with subtle borders,
 *     not flat paragraphs.
 *   - Filter chips at the top: All / Bezig / Probleem.
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  ChevronRight,
  ExternalLink,
  File,
  FileText,
  Globe,
  Image,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  Type,
  Zap,
} from 'lucide-react'
import { SiAirtable, SiConfluence, SiGithub, SiGoogledrive, SiNotion } from '@icons-pack/react-simple-icons'
import { Button } from '@/components/ui/button'
import { Tooltip } from '@/components/ui/tooltip'
import { InlineDeleteConfirm } from '@/components/ui/inline-delete-confirm'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'
import { queryLogger } from '@/lib/logger'

export const Route = createFileRoute('/app/knowledge/$kbSlug/bronnen')({
  component: BronnenTab,
})

// -- Types ------------------------------------------------------------------

interface Bron {
  kind: 'connector' | 'upload'
  id: string
  name: string
  type_label: string
  connector_type: string | null
  items_count: number
  chunks_count: number
  status: string | null
  last_sync_at: string | null
  created_at: string | null
}

interface BronnenResponse {
  bronnen: Bron[]
}

interface ConnectorItem {
  id: string
  path: string
  content_type: string
  chunks_count: number
  created_at: string
}

interface UploadChunk {
  id: number
  position: number
  text: string
  token_count: number
}

interface ContentResponse {
  kind: 'connector' | 'upload'
  items: ConnectorItem[]
  chunks: UploadChunk[]
  total: number
  limit: number
  offset: number
}

// -- Status -----------------------------------------------------------------

type Status = 'klaar' | 'bezig' | 'probleem' | 'leeg'

function mapStatus(bron: Bron): Status {
  if (bron.kind === 'upload') {
    return bron.chunks_count > 0 ? 'klaar' : 'bezig'
  }
  const s = (bron.status ?? '').toLowerCase()
  if (s.includes('error') || s.includes('failed') || s === 'auth_error' || s === 'orphan') return 'probleem'
  if (s === 'running' || s === 'pending' || s === 'syncing') return 'bezig'
  if (bron.chunks_count > 0) return 'klaar'
  return bron.items_count > 0 ? 'klaar' : 'leeg'
}

function StatusDot({ status }: { status: Status }) {
  if (status === 'klaar') return null
  const colorMap = {
    bezig: 'bg-amber-500',
    probleem: 'bg-red-500',
    leeg: 'bg-gray-300',
  }
  const labelMap = {
    bezig: m.kb_status_bezig(),
    probleem: m.kb_status_probleem(),
    leeg: m.kb_status_leeg(),
  } as const
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-gray-500" title={labelMap[status]}>
      <span className={`h-2 w-2 rounded-full ${colorMap[status]}`} aria-hidden />
      {labelMap[status]}
    </span>
  )
}

// -- Bron icon (per type, subtle color tint) -------------------------------

function BronIcon({ bron }: { bron: Bron }) {
  if (bron.kind === 'connector') {
    const t = bron.connector_type ?? ''
    if (t === 'github') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-900 text-white">
          <SiGithub className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'notion') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-900">
          <SiNotion className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'google_drive') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
          <SiGoogledrive className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'airtable') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-yellow-50 text-yellow-700">
          <SiAirtable className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'confluence') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-700">
          <SiConfluence className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'web_crawler') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
          <Globe className="h-4 w-4" />
        </div>
      )
    }
    if (t === 'ms_docs') {
      return (
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-blue-50 text-blue-600">
          <FileText className="h-4 w-4" />
        </div>
      )
    }
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-purple-50 text-purple-600">
        <Zap className="h-4 w-4" />
      </div>
    )
  }
  // upload
  const path = bron.name.toLowerCase()
  const ct = (bron.connector_type ?? '').toLowerCase()
  if (path.endsWith('.pdf') || ct === 'pdf') {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-red-50 text-red-600">
        <FileText className="h-4 w-4" />
      </div>
    )
  }
  if (path.startsWith('http') || ct === 'url' || ct === 'html') {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
        <Globe className="h-4 w-4" />
      </div>
    )
  }
  if (ct.startsWith('image') || /\.(png|jpe?g|gif|webp|svg)$/i.test(path)) {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-pink-50 text-pink-600">
        <Image className="h-4 w-4" />
      </div>
    )
  }
  if (ct === 'text' || ct === 'markdown') {
    return (
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-slate-50 text-slate-700">
        <Type className="h-4 w-4" />
      </div>
    )
  }
  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-500">
      <File className="h-4 w-4" />
    </div>
  )
}

// -- Bron content (drill-down) ---------------------------------------------

function BronContent({ kbSlug, bron }: { kbSlug: string; bron: Bron }) {
  const { data, isLoading, isError } = useQuery<ContentResponse>({
    queryKey: ['bron-content', kbSlug, bron.kind, bron.id],
    queryFn: () =>
      apiFetch<ContentResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/${bron.id}/content?kind=${bron.kind}&limit=20`,
      ),
    retry: false,
  })

  if (isLoading) {
    return (
      <div className="pl-[48px] pr-2 pb-4 flex items-center gap-2 text-xs text-gray-400">
        <Loader2 className="h-3 w-3 animate-spin" />
        Inhoud laden...
      </div>
    )
  }

  if (isError) {
    return (
      <div className="pl-[48px] pr-2 pb-4 text-xs text-[var(--color-destructive)]">
        Kon inhoud niet laden.
      </div>
    )
  }

  if (bron.kind === 'connector') {
    const items = data?.items ?? []
    if (items.length === 0) {
      return (
        <div className="pl-[48px] pr-2 pb-4 text-xs text-gray-400 italic">
          Nog geen items in deze bron — synchroniseer om inhoud op te halen.
        </div>
      )
    }
    return (
      <div className="pl-[48px] pr-2 pb-4 space-y-1">
        {items.map((item) => (
          <div
            key={item.id}
            className="flex items-center gap-2 text-xs py-1.5 hover:bg-gray-50 -mx-2 px-2 rounded"
          >
            <File className="h-3.5 w-3.5 text-gray-400 shrink-0" />
            <span className="text-gray-700 flex-1 truncate" title={item.path}>
              {item.path}
            </span>
            <span className="text-gray-400 shrink-0 tabular-nums">
              {item.chunks_count}
            </span>
          </div>
        ))}
        {data && data.total > items.length && (
          <p className="text-xs text-gray-400 italic pt-2">
            En nog {data.total - items.length} items meer...
          </p>
        )}
      </div>
    )
  }

  // upload — chunks as numbered cards
  const chunks = data?.chunks ?? []
  if (chunks.length === 0) {
    return (
      <div className="pl-[48px] pr-2 pb-4 text-xs text-gray-400 italic">
        Nog geen chunks geïndexeerd.
      </div>
    )
  }
  return (
    <div className="pl-[48px] pr-2 pb-4 space-y-2">
      {chunks.map((chunk) => (
        <div
          key={chunk.id}
          className="rounded-lg border border-gray-200 bg-gray-50/50 px-3 py-2"
        >
          <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-gray-400 mb-1">
            <span className="font-medium">Deel {chunk.position + 1}</span>
            <span>·</span>
            <span>{chunk.token_count} tokens</span>
          </div>
          <p className="text-xs text-gray-700 line-clamp-3">{chunk.text}</p>
        </div>
      ))}
      {data && data.total > chunks.length && (
        <p className="text-xs text-gray-400 italic pt-1">
          En nog {data.total - chunks.length} delen meer...
        </p>
      )}
    </div>
  )
}

// -- Bron row (with hover actions) -----------------------------------------

function BronRow({
  bron,
  expanded,
  onToggle,
  kbSlug,
}: {
  bron: Bron
  expanded: boolean
  onToggle: () => void
  kbSlug: string
}) {
  const queryClient = useQueryClient()
  const status = mapStatus(bron)
  const [confirmDelete, setConfirmDelete] = useState(false)

  // Sync mutation — only relevant for connector bronnen
  const syncMutation = useMutation({
    mutationFn: async () =>
      apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${bron.id}/sync`, { method: 'POST' }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-bronnen', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['bron-content', kbSlug, bron.kind, bron.id] })
    },
    onError: (err) => queryLogger.error('Bron sync failed', { kbSlug, bronId: bron.id, err }),
  })

  // Delete mutation — endpoint differs per kind
  const deleteMutation = useMutation({
    mutationFn: async () => {
      if (bron.kind === 'connector') {
        return apiFetch(`/api/app/knowledge-bases/${kbSlug}/connectors/${bron.id}`, {
          method: 'DELETE',
        })
      }
      return apiFetch(`/api/knowledge/personal/items/${bron.id}`, { method: 'DELETE' })
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['kb-bronnen', kbSlug] })
      void queryClient.invalidateQueries({ queryKey: ['app-knowledge-bases-stats-summary'] })
      setConfirmDelete(false)
    },
    onError: (err) => queryLogger.error('Bron delete failed', { kbSlug, bronId: bron.id, err }),
  })

  // Open original — file / URL only meaningful for uploads with a usable path
  const canOpenOriginal = bron.kind === 'upload' && bron.name.startsWith('http')

  return (
    <InlineDeleteConfirm
      isConfirming={confirmDelete}
      isPending={deleteMutation.isPending}
      label={`"${bron.name}" verwijderen?`}
      cancelLabel="Annuleren"
      onConfirm={() => deleteMutation.mutate()}
      onCancel={() => setConfirmDelete(false)}
    >
      <div className="group">
        <button
          type="button"
          onClick={onToggle}
          className="w-full flex items-center gap-3 px-2 py-3 hover:bg-gray-50 transition-colors text-left"
          aria-expanded={expanded}
        >
          <BronIcon bron={bron} />
          <div className="min-w-0 flex-1">
            <p className="text-[15px] font-display text-gray-900 truncate" title={bron.name}>
              {bron.name}
            </p>
            <div className="flex items-center gap-2 text-xs text-gray-400 mt-0.5">
              <span>{bron.type_label}</span>
              {bron.kind === 'connector' && bron.items_count > 0 && (
                <>
                  <span>·</span>
                  <span>{bron.items_count} items</span>
                </>
              )}
              {status !== 'klaar' && (
                <>
                  <span>·</span>
                  <StatusDot status={status} />
                </>
              )}
            </div>
          </div>

          {/* Hover actions — appear inline on hover, replace nothing */}
          <div className="hidden group-hover:flex items-center gap-1 shrink-0">
            {bron.kind === 'connector' && (
              <Tooltip label="Synchroniseren">
                <span
                  role="button"
                  tabIndex={0}
                  onClick={(e) => {
                    e.stopPropagation()
                    syncMutation.mutate()
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault()
                      syncMutation.mutate()
                    }
                  }}
                  aria-label="Synchroniseer bron"
                  className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors cursor-pointer"
                >
                  {syncMutation.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                </span>
              </Tooltip>
            )}
            {canOpenOriginal && (
              <Tooltip label="Origineel openen">
                <a
                  href={bron.name}
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={(e) => e.stopPropagation()}
                  aria-label="Open origineel"
                  className="rounded-lg p-1.5 text-gray-400 hover:text-gray-900 hover:bg-gray-100 transition-colors"
                >
                  <ExternalLink className="h-4 w-4" />
                </a>
              </Tooltip>
            )}
            <Tooltip label="Verwijder bron">
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation()
                  setConfirmDelete(true)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    setConfirmDelete(true)
                  }
                }}
                aria-label="Verwijder bron"
                className="rounded-lg p-1.5 text-gray-400 hover:text-[var(--color-destructive)] hover:bg-gray-100 transition-colors cursor-pointer"
              >
                <Trash2 className="h-4 w-4" />
              </span>
            </Tooltip>
          </div>

          <ChevronRight
            className={`h-4 w-4 text-gray-300 shrink-0 transition-transform ${
              expanded ? 'rotate-90 text-gray-500' : 'group-hover:text-gray-500'
            }`}
          />
        </button>
        {expanded && <BronContent kbSlug={kbSlug} bron={bron} />}
      </div>
    </InlineDeleteConfirm>
  )
}

// -- Filter chips ----------------------------------------------------------

type Filter = 'alle' | 'bezig' | 'probleem'

function FilterChips({
  filter,
  onChange,
  counts,
}: {
  filter: Filter
  onChange: (f: Filter) => void
  counts: { alle: number; bezig: number; probleem: number }
}) {
  const items: { id: Filter; label: string; count: number }[] = [
    { id: 'alle', label: 'Alle', count: counts.alle },
    { id: 'bezig', label: m.kb_status_bezig(), count: counts.bezig },
    { id: 'probleem', label: m.kb_status_probleem(), count: counts.probleem },
  ]
  return (
    <div className="flex items-center gap-1.5">
      {items.map(({ id, label, count }) => {
        if (id !== 'alle' && count === 0) return null
        const active = filter === id
        return (
          <button
            key={id}
            type="button"
            onClick={() => onChange(id)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
              active
                ? 'bg-gray-900 text-white'
                : 'bg-gray-50 text-gray-600 hover:bg-gray-100'
            }`}
          >
            {label}
            <span className={`ml-1.5 tabular-nums ${active ? 'text-white/70' : 'text-gray-400'}`}>
              {count}
            </span>
          </button>
        )
      })}
    </div>
  )
}

// -- Tab page --------------------------------------------------------------

function BronnenTab() {
  const { kbSlug } = Route.useParams()
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('alle')

  const { data, isLoading, isError } = useQuery<BronnenResponse>({
    queryKey: ['kb-bronnen', kbSlug],
    queryFn: () => apiFetch<BronnenResponse>(`/api/app/knowledge-bases/${kbSlug}/sources`),
    retry: false,
  })

  const allBronnen = data?.bronnen ?? []
  const counts = {
    alle: allBronnen.length,
    bezig: allBronnen.filter((b) => mapStatus(b) === 'bezig').length,
    probleem: allBronnen.filter((b) => mapStatus(b) === 'probleem').length,
  }
  const bronnen = filter === 'alle' ? allBronnen : allBronnen.filter((b) => mapStatus(b) === filter)

  return (
    <div>
      {/* Action bar — filter chips left, primary action moved to KB header */}
      {allBronnen.length > 0 && (
        <div className="flex items-center justify-between gap-4 mb-4">
          <FilterChips filter={filter} onChange={setFilter} counts={counts} />
        </div>
      )}

      {/* List */}
      {isLoading ? (
        <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
          {[1, 2, 3].map((i) => (
            <div key={i} className="flex items-center gap-3 px-2 py-3">
              <div className="h-9 w-9 rounded-lg bg-gray-50 animate-pulse" />
              <div className="flex-1 space-y-1.5">
                <div className="h-3 w-48 rounded bg-gray-50 animate-pulse" />
                <div className="h-2.5 w-24 rounded bg-gray-50 animate-pulse" />
              </div>
            </div>
          ))}
        </div>
      ) : isError ? (
        <div className="border-t border-b border-gray-200 py-10 text-center text-sm text-[var(--color-destructive)]">
          Kon bronnen niet laden.
        </div>
      ) : allBronnen.length === 0 ? (
        <div className="rounded-xl border border-dashed border-gray-200 py-12 text-center">
          <Zap className="h-8 w-8 text-gray-300 mx-auto mb-3" />
          <p className="text-base font-display text-gray-900">Nog geen bronnen</p>
          <p className="text-sm text-gray-400 mt-1 max-w-sm mx-auto">
            Voeg bestanden, links of koppelingen toe — alles wat in deze collectie hoort.
          </p>
          <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }} className="inline-block mt-4">
            <Button variant="default">
              <Plus className="h-4 w-4" />
              Eerste bron toevoegen
            </Button>
          </Link>
        </div>
      ) : bronnen.length === 0 ? (
        <div className="border-t border-b border-gray-200 py-8 text-center text-sm text-gray-400">
          Geen bronnen in deze categorie.
        </div>
      ) : (
        <div className="border-t border-b border-gray-200 divide-y divide-gray-200">
          {bronnen.map((bron) => (
            <BronRow
              key={`${bron.kind}-${bron.id}`}
              bron={bron}
              expanded={expandedId === `${bron.kind}-${bron.id}`}
              onToggle={() =>
                setExpandedId(expandedId === `${bron.kind}-${bron.id}` ? null : `${bron.kind}-${bron.id}`)
              }
              kbSlug={kbSlug}
            />
          ))}
        </div>
      )}
    </div>
  )
}
