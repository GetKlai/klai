/**
 * SPEC-PORTAL-KENNIS-001 Phase E — Bronnen tab.
 *
 * "Alles is een bron." One unified list of every source in this KB
 * (connector aggregates + direct uploads), same row shape, same actions.
 *
 * Click a bron → expand inline to show its content:
 *   - For connectors: list of artifacts (items) with chunk counts
 *   - For direct uploads: parent_chunks with text preview
 */
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import {
  ChevronRight,
  File,
  FileText,
  Globe,
  Image,
  Loader2,
  Plus,
  Type,
  Zap,
} from 'lucide-react'
import { SiAirtable, SiConfluence, SiGithub, SiGoogledrive, SiNotion } from '@icons-pack/react-simple-icons'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import * as m from '@/paraglide/messages'
import { apiFetch } from '@/lib/apiFetch'

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

// -- Status mapping ---------------------------------------------------------

type Status = 'klaar' | 'bezig' | 'probleem' | 'leeg'

function mapStatus(bron: Bron): Status {
  if (bron.kind === 'upload') {
    // An upload that's listed in /sources has been ingested — it's klaar.
    // chunks_count from parent_chunks is unreliable (often 0 even for
    // fully-indexed KBs), so we don't use it as a "bezig" signal here.
    return 'klaar'
  }
  // connector — last_sync_status is the source of truth
  const s = (bron.status ?? '').toLowerCase()
  if (s.includes('error') || s.includes('failed') || s === 'auth_error' || s === 'orphan') return 'probleem'
  if (s === 'running' || s === 'pending' || s === 'syncing') return 'bezig'
  if (bron.items_count > 0 || bron.chunks_count > 0) return 'klaar'
  return 'leeg'
}

function StatusBadge({ status }: { status: Status }) {
  const labelMap = {
    klaar: m.kb_status_klaar(),
    bezig: m.kb_status_bezig(),
    probleem: m.kb_status_probleem(),
    leeg: m.kb_status_leeg(),
  } as const
  // Only Probleem stands out. Klaar / Bezig / Leeg are subtle.
  const variantMap = {
    klaar: 'success' as const,
    bezig: 'secondary' as const,
    probleem: 'destructive' as const,
    leeg: 'secondary' as const,
  }
  return <Badge variant={variantMap[status]}>{labelMap[status]}</Badge>
}

// -- Bron icon --------------------------------------------------------------

function BronIcon({ bron }: { bron: Bron }) {
  if (bron.kind === 'connector') {
    const t = bron.connector_type ?? ''
    if (t === 'github') return <SiGithub className="h-4 w-4" />
    if (t === 'notion') return <SiNotion className="h-4 w-4" />
    if (t === 'google_drive') return <SiGoogledrive className="h-4 w-4" />
    if (t === 'airtable') return <SiAirtable className="h-4 w-4" />
    if (t === 'confluence') return <SiConfluence className="h-4 w-4" />
    if (t === 'web_crawler') return <Globe className="h-4 w-4" />
    if (t === 'ms_docs') return <FileText className="h-4 w-4" />
    return <Zap className="h-4 w-4" />
  }
  // upload
  const ct = (bron.connector_type ?? '').toLowerCase()
  const path = bron.name.toLowerCase()
  if (path.endsWith('.pdf') || ct === 'pdf') return <FileText className="h-4 w-4" />
  if (path.startsWith('http') || ct === 'url' || ct === 'html') return <Globe className="h-4 w-4" />
  if (ct.startsWith('image') || /\.(png|jpe?g|gif|webp|svg)$/i.test(path)) return <Image className="h-4 w-4" />
  if (ct === 'text' || ct === 'markdown') return <Type className="h-4 w-4" />
  return <File className="h-4 w-4" />
}

// -- Bron content (drill-down) ----------------------------------------------

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
    return (
      <div className="pl-[44px] pr-2 pb-3 text-xs text-gray-400">
        Nog geen chunks geïndexeerd.
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
}: {
  bron: Bron
  expanded: boolean
  onToggle: () => void
  kbSlug: string
}) {
  const status = mapStatus(bron)
  const meta = `${bron.type_label} · ${bron.chunks_count} chunks${
    bron.kind === 'connector' && bron.items_count > 0 ? ` · ${bron.items_count} items` : ''
  }`

  return (
    <div>
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-2 py-3.5 hover:bg-gray-50 transition-colors text-left"
        aria-expanded={expanded}
      >
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-gray-50 text-gray-400">
          <BronIcon bron={bron} />
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span className="text-[15px] font-display text-gray-900 truncate">{bron.name}</span>
            <span className="text-xs text-gray-400">{meta}</span>
          </div>
        </div>
        <StatusBadge status={status} />
        <ChevronRight
          className={`h-4 w-4 text-gray-300 shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`}
        />
      </button>
      {expanded && <BronContent kbSlug={kbSlug} bron={bron} />}
    </div>
  )
}

// -- Tab page ---------------------------------------------------------------

function BronnenTab() {
  const { kbSlug } = Route.useParams()
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const { data, isLoading, isError } = useQuery<BronnenResponse>({
    queryKey: ['kb-bronnen', kbSlug],
    queryFn: () => apiFetch<BronnenResponse>(`/api/app/knowledge-bases/${kbSlug}/sources`),
    retry: false,
  })

  const bronnen = data?.bronnen ?? []

  return (
    <div>
      {/* Action bar */}
      <div className="flex items-center justify-between gap-4 mb-4">
        <p className="text-sm text-gray-400">
          {bronnen.length === 1
            ? m.kb_count_bron_singular()
            : m.kb_count_bronnen({ count: String(bronnen.length) })}
        </p>
        <Link to="/app/knowledge/$kbSlug/add-source" params={{ kbSlug }}>
          <Button variant="default" size="sm">
            <Plus className="h-4 w-4" />
            Bron toevoegen
          </Button>
        </Link>
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
