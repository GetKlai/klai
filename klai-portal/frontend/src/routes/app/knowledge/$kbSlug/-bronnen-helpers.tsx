import {
  File,
  FileText,
  Globe,
  Image,
  Type,
  Zap,
} from 'lucide-react'
import { SiAirtable, SiConfluence, SiGithub, SiGoogledrive, SiNotion } from '@icons-pack/react-simple-icons'
import { Badge } from '@/components/ui/badge'
import * as m from '@/paraglide/messages'
import type { Bron } from './-bronnen-types'

export type BronStatus = 'synced' | 'pending' | 'not_synced'

export function mapBronStatus(bron: Bron): BronStatus {
  const s = (bron.status ?? '').toLowerCase()
  if (bron.kind === 'upload') {
    const idx = (bron.index_status ?? '').toLowerCase()
    if (idx === 'pending' || s === 'processing' || s === 'ingesting') return 'pending'
    if (idx === 'failed' || s === 'failed' || s.includes('error')) return 'not_synced'
    if (idx === 'synced') return 'synced'
    if (bron.chunks_count === 0 && !idx) return 'not_synced'
    return 'synced'
  }
  if (s === 'running' || s === 'pending' || s === 'syncing') return 'pending'
  if (s.includes('error') || s.includes('failed') || s === 'auth_error' || s === 'orphan') {
    return 'not_synced'
  }
  if (s === 'success' || s === 'completed' || s === 'ok') return 'synced'
  if (bron.items_count > 0 || bron.chunks_count > 0) return 'synced'
  return 'not_synced'
}

export function StatusBadge({ status }: { status: BronStatus }) {
  const labelMap = {
    synced: m.kb_status_klaar(),
    pending: m.kb_status_bezig(),
    not_synced: m.kb_status_leeg(),
  } as const
  const variantMap = {
    synced: 'success' as const,
    pending: 'secondary' as const,
    not_synced: 'secondary' as const,
  }
  return <Badge variant={variantMap[status]}>{labelMap[status]}</Badge>
}

export function BronIcon({ bron }: { bron: Bron }) {
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
  const ct = (bron.type_label ?? '').toLowerCase()
  const path = bron.name.toLowerCase()
  if (path.endsWith('.pdf') || ct === 'pdf') return <FileText className="h-4 w-4" />
  if (path.startsWith('http') || bron.source_url || ct === 'website' || ct === 'link') {
    return <Globe className="h-4 w-4" />
  }
  if (ct.startsWith('afbeelding') || /\.(png|jpe?g|gif|webp|svg)$/i.test(path)) return <Image className="h-4 w-4" />
  if (ct === 'tekst') return <Type className="h-4 w-4" />
  return <File className="h-4 w-4" />
}

export function editablePageIdForBron(
  bron: Bron,
  slugToPageId: Map<string, string>,
): string | null {
  if (bron.kind !== 'upload') return null
  const stripped = bron.name.replace(/\.md$/i, '')
  return slugToPageId.get(stripped) ?? slugToPageId.get(bron.name) ?? null
}
