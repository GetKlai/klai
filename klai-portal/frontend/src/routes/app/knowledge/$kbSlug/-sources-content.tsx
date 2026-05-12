/**
 * Drill-down preview for one expanded source row.
 *
 * Two completely different shapes per kind:
 *   - Connector → list of artifacts (path + chunk count)
 *   - Upload    → list of parent_chunks (preview text + position)
 *
 * One fetch contract (`/sources/{id}/content?kind=...`) drives both. Split
 * out of the route file so each kind reads as its own component.
 */
import { useQuery } from '@tanstack/react-query'
import { File, Loader2 } from 'lucide-react'
import { apiFetch } from '@/lib/apiFetch'
import { kbQueryKeys } from './-kb-query-keys'
import type { ContentResponse, Source } from './-sources-types'

interface DrillDownProps {
  kbSlug: string
  source: Source
}

function useSourceContent(kbSlug: string, source: Source) {
  return useQuery<ContentResponse>({
    queryKey: kbQueryKeys.sourceContent(kbSlug, source.kind, source.id),
    queryFn: () =>
      apiFetch<ContentResponse>(
        `/api/app/knowledge-bases/${kbSlug}/sources/${source.id}/content?kind=${source.kind}&limit=20`,
      ),
    retry: false,
  })
}

function ConnectorContent({ data }: { data: ContentResponse }) {
  const items = data.items ?? []
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
      {data.total > items.length && (
        <p className="text-xs text-gray-400 pt-1">
          En nog {data.total - items.length} meer...
        </p>
      )}
    </div>
  )
}

function UploadContent({ source, data }: { source: Source; data: ContentResponse }) {
  const chunks = data.chunks ?? []
  if (chunks.length === 0) {
    if (source.source_url) {
      return (
        <div className="pl-[44px] pr-2 pb-3 flex items-center gap-2 text-xs">
          <File className="h-3.5 w-3.5 text-gray-400 shrink-0" />
          <span className="text-gray-700 truncate">{source.source_url}</span>
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
      {data.total > chunks.length && (
        <p className="text-xs text-gray-400 pt-1">
          En nog {data.total - chunks.length} chunks meer...
        </p>
      )}
    </div>
  )
}

export function SourceContent({ kbSlug, source }: DrillDownProps) {
  const { data, isLoading, isError } = useSourceContent(kbSlug, source)

  if (isLoading) {
    return (
      <div className="pl-[44px] pr-2 pb-3 flex items-center gap-2 text-xs text-gray-400">
        <Loader2 className="h-3 w-3 animate-spin" />
        Inhoud laden...
      </div>
    )
  }

  if (isError || !data) {
    return (
      <div className="pl-[44px] pr-2 pb-3 text-xs text-[var(--color-destructive)]">
        Kon inhoud niet laden.
      </div>
    )
  }

  return source.kind === 'connector'
    ? <ConnectorContent data={data} />
    : <UploadContent source={source} data={data} />
}
