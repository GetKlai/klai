export interface Source {
  kind: 'connector' | 'upload'
  id: string
  name: string
  type_label: string
  connector_type: string | null
  source_url?: string | null
  items_count: number
  chunks_count: number
  status: string | null
  last_sync_at: string | null
  created_at: string | null
  /** Upload-only: pending / synced / failed from Track 3 backend. */
  index_status?: string | null
}

export interface SourcesResponse {
  sources: Source[]
}

export interface ConnectorItem {
  id: string
  path: string
  content_type: string
  chunks_count: number
  created_at: string
}

export interface UploadChunk {
  id: number
  position: number
  text: string
  token_count: number
}

export interface ContentResponse {
  kind: 'connector' | 'upload'
  items: ConnectorItem[]
  chunks: UploadChunk[]
  total: number
  limit: number
  offset: number
}

export interface PageIndexEntry {
  id: string | null
  slug: string
}
